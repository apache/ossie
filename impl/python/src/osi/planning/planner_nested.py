"""Nested cross-grain aggregate planner (D-020 + D-024, `I-S5-impl`).

The Foundation accepts an explicit nested-aggregate metric like
``AVG(AVG(orders.amount))`` as the *per-row-first* alternative to the
single-step interpretation pinned by D-020. The compilation contract:

1. Aggregate the inner expression at an **intermediate grain** that
   captures the per-row dimension (e.g. one row per ``customer_id``
   for ``AVG(AVG(orders.amount))`` queried by region).
2. Aggregate the result at the **query grain** with the outer
   function applied to the intermediate column.

This module is the planner branch that emits the two-aggregate
shape. It is deliberately narrow: only one outer aggregate, one
inner aggregate, one foreign dataset reachable via a single safe
N:1 step. Anything outside that envelope is left for follow-up
sprints (composes with the bridge planner in S-19 and the
home-grain rewrite in S-20).
"""

from __future__ import annotations

from typing import Sequence

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIParseError, OSIPlanningError
from osi.parsing.models import Metric
from osi.planning.algebra.operations import aggregate
from osi.planning.algebra.state import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
)
from osi.planning.metric_shape import _AGG_BY_AST
from osi.planning.plan import (
    AggregatePayload,
    PlanOperation,
    PlanStep,
)
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import ResolvedDimension, ResolvedMetric
from osi.planning.steps import PlanBuilder


def is_nested_aggregate(metric: Metric) -> bool:
    """Return True iff the metric is a top-level aggregate of an aggregate.

    The inner aggregate must be a Foundation function and reference at
    least one column. Two-level only — nesting beyond two is rejected
    upstream as ``E1206_METRIC_IN_RAW_AGGREGATE`` per the existing
    contract.
    """
    top = metric.expression.expr
    if type(top) not in _AGG_BY_AST:
        return False
    inner = top.this
    if not isinstance(inner, exp.Expression):
        return False
    if type(inner) not in _AGG_BY_AST:
        return False
    return True


def parse_nested(
    metric: Metric,
) -> tuple[AggregateFunction, AggregateFunction, exp.Expression]:
    """Return (outer_fn, inner_fn, inner_arg_expression).

    The inner argument is the raw AST node fed to the inner
    aggregate; for ``AVG(AVG(orders.amount))`` it is the ``Column``
    ``orders.amount``.
    """
    top = metric.expression.expr
    outer_fn = _AGG_BY_AST[type(top)]
    inner = top.this
    inner_fn = _AGG_BY_AST[type(inner)]
    inner_arg = inner.this
    return outer_fn, inner_fn, inner_arg


def insert_nested_aggregate(
    *,
    parent: PlanStep,
    measure: ResolvedMetric,
    dimensions: Sequence[ResolvedDimension],
    intermediate_grain: frozenset[Identifier],
    builder: PlanBuilder,
    context: PlannerContext,
) -> PlanStep:
    """Append intermediate AGG + final AGG for a single nested measure.

    ``parent`` is the post-enrichment state at the natural fact grain.
    ``intermediate_grain`` is the dim set at which the inner aggregate
    runs (typically ``{join_key, *query_dims}``). The function returns
    the final aggregate step ready for downstream PROJECT.
    """
    outer_fn, inner_fn, inner_arg_expr = parse_nested(measure.metric)
    inner_dependencies = _collect_dependencies(inner_arg_expr, parent.state)
    inner_arg_sql = FrozenSQL.of(inner_arg_expr.copy())
    intermediate_col = Column(
        name=measure.metric.name,
        expression=measure.metric.expression,
        dependencies=inner_dependencies,
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(function=inner_fn, argument=inner_arg_sql),
    )
    intermediate = builder.add(
        PlanOperation.AGGREGATE,
        inputs=(parent.step_id,),
        state=aggregate(parent.state, intermediate_grain, (intermediate_col,)),
        payload=AggregatePayload(
            new_grain=intermediate_grain, aggregations=(intermediate_col,)
        ),
    )
    final_grain = _query_grain(dimensions, intermediate.state)
    outer_arg_sql = FrozenSQL.of(exp.column(str(measure.metric.name)))
    final_col = Column(
        name=measure.metric.name,
        expression=measure.metric.expression,
        dependencies=frozenset({measure.metric.name}),
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(function=outer_fn, argument=outer_arg_sql),
    )
    return builder.add(
        PlanOperation.AGGREGATE,
        inputs=(intermediate.step_id,),
        state=aggregate(intermediate.state, final_grain, (final_col,)),
        payload=AggregatePayload(new_grain=final_grain, aggregations=(final_col,)),
    )


# ---------------------------------------------------------------------------
# Intermediate-grain inference
# ---------------------------------------------------------------------------


def infer_intermediate_grain(
    *,
    fact_dataset: Identifier,
    dimensions: Sequence[ResolvedDimension],
    state_columns: frozenset[Identifier],
    context: PlannerContext,
) -> frozenset[Identifier]:
    """Pick the intermediate grain for the inner aggregate.

    The grain is ``{join_key_on_fact_side, *query_dim_columns_on_state}``.
    The join key is taken from the unique safe N:1 edge whose N-side
    is ``fact_dataset``. If that edge is ambiguous or absent we fall
    back to the query dim columns alone — which the algebra will
    accept iff every dim is single-valued on the fact grain (the
    typical case for region-level rollups over per-row aggregates).
    """
    join_keys: list[Identifier] = []
    edges = context.graph.neighbors(fact_dataset)
    n1_to_dim: list[tuple[Identifier, ...]] = []
    dim_datasets = {d.dataset for d in dimensions}
    for edge in edges:
        if edge.from_dataset != fact_dataset:
            continue
        if edge.to_dataset in dim_datasets:
            n1_to_dim.append(tuple(edge.from_columns))
    if len(n1_to_dim) == 1:
        join_keys.extend(n1_to_dim[0])
    dim_columns = [d.field.name for d in dimensions if d.field.name in state_columns]
    return frozenset([*join_keys, *dim_columns])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _query_grain(
    dimensions: Sequence[ResolvedDimension], state: CalculationState
) -> frozenset[Identifier]:
    return frozenset(
        d.field.name for d in dimensions if d.field.name in state.column_names
    )


def _collect_dependencies(
    arg: exp.Expression, state: CalculationState
) -> frozenset[Identifier]:
    deps: set[Identifier] = set()
    for col in arg.find_all(exp.Column):
        try:
            name = normalize_identifier(col.name)
        except OSIParseError:
            continue
        if name in state.column_names:
            deps.add(name)
    if not deps:
        raise OSIPlanningError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            (
                "nested aggregate inner expression references no column "
                "addressable on the post-enrichment state"
            ),
        )
    return frozenset(deps)


__all__ = [
    "infer_intermediate_grain",
    "insert_nested_aggregate",
    "is_nested_aggregate",
    "parse_nested",
]
