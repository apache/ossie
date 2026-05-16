"""Column / metric translation helpers for the planner.

Pure functions that convert :mod:`osi.parsing.models` entities (Fields,
Metrics) into algebra-level :class:`Column` / :class:`AggregateInfo`
values. The planner uses them at SOURCE and AGGREGATE step construction
time.

These live in their own module so :mod:`osi.planning.planner` stays
focused on *topology* — what flows where — rather than on the mechanics
of building individual columns.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.field_deps import field_inter_field_dependencies
from osi.parsing.models import Field, FieldRole, Metric
from osi.planning.algebra.state import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
)
from osi.planning.resolve import ResolvedMetric


def field_to_column(
    field: Field,
    *,
    sibling_field_names: Iterable[Identifier] = (),
) -> Column:
    """Convert a parsed :class:`Field` into an algebra :class:`Column`.

    ``sibling_field_names`` is the set of every field declared on the
    home dataset (including ``field`` itself). It is consulted by
    :func:`osi.parsing.field_deps.field_inter_field_dependencies` to
    distinguish references to other fields (which become real algebra
    dependencies) from references to physical columns of the dataset
    (which do not).

    Default value of ``sibling_field_names`` is the empty tuple,
    preserving the legacy "no sibling resolution" behaviour for the
    handful of internal call sites that pre-date the staged-CTE
    planner. New call sites should always pass the dataset's full
    field-name set so ``add_columns`` staging can topologically
    sort fields by their inter-field dependencies (see
    :func:`osi.planning.steps.source_step`).
    """
    deps = field_inter_field_dependencies(field, sibling_field_names)
    return Column(
        name=field.name,
        expression=field.expression,
        dependencies=deps,
        kind=(
            ColumnKind.FACT if field.role is FieldRole.FACT else ColumnKind.DIMENSION
        ),
    )


def parse_metric_aggregate(
    metric: Metric,
) -> tuple[AggregateFunction, tuple[exp.Column, ...]]:
    """Split a top-level aggregate metric into (function, arg columns).

    Raises ``E1206_METRIC_IN_RAW_AGGREGATE`` if the expression is not a
    single top-level aggregate. Composite metrics are routed through
    :func:`osi.planning.metric_shape.classify_metric` instead; callers
    that may be handed a composite should classify first.
    """
    # Reuse the canonical classifier so one shape recogniser lives in
    # one place. ``namespace`` is unused for pure aggregate detection,
    # so a sentinel-Namespace is not required: instead we re-parse the
    # top-level node directly.
    from osi.planning.metric_shape import _as_top_level_aggregate

    agg = _as_top_level_aggregate(metric.expression.expr)
    if agg is None:
        raise OSIPlanningError(
            ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE,
            f"metric {metric.name!r} must be a single top-level aggregate",
            context={
                "metric": metric.name,
                "expression": metric.expression.canonical,
            },
        )
    return agg.function, agg.arg_columns


def metric_to_aggregate_column(
    metric: ResolvedMetric, state: CalculationState
) -> Column:
    """Build the :class:`Column` that AGGREGATE emits for ``metric``.

    ``metric`` must be an aggregate-shape metric. Composites are
    expanded earlier in the planner; calling this on a composite is a
    programming error (not a user error) — the planner is expected to
    split them into base + derived before reaching codegen helpers.
    """
    function, arg_columns = parse_metric_aggregate(metric.metric)
    deps: set[Identifier] = set()
    for col in arg_columns:
        name = normalize_identifier(col.name)
        if name not in state.column_names:
            raise OSIPlanningError(
                ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
                f"metric {metric.metric.name!r} reads column {name!r} that "
                "is not addressable at the measure-group state",
                context={"metric": metric.metric.name, "column": name},
            )
        deps.add(name)
    argument = aggregate_argument(metric.metric, arg_columns)
    return Column(
        name=metric.metric.name,
        expression=metric.metric.expression,
        dependencies=frozenset(deps),
        kind=ColumnKind.AGGREGATE,
        aggregate=AggregateInfo(function=function, argument=argument),
    )


def metric_to_aggregate_column_from_metric(
    metric: Metric, dataset: Identifier, state: CalculationState
) -> Column:
    """Like :func:`metric_to_aggregate_column` but takes raw metric + dataset.

    Used when expanding composite metrics: the declared-metric
    references that become base aggregates don't correspond to user
    query references, so no :class:`ResolvedMetric` exists.
    """
    resolved = ResolvedMetric(dataset=dataset, metric=metric)
    return metric_to_aggregate_column(resolved, state)


def composite_to_derived_column(
    name: Identifier,
    metric: Metric,
    dependency_names: frozenset[Identifier],
) -> Column:
    """Build a derived post-aggregate :class:`Column` for a composite metric.

    The algebra disallows aggregates inside ``add_columns``, so the
    composite's expression is copied verbatim (its leaves already
    reference aggregate column names declared on the prior AGGREGATE
    step's output). ``dependency_names`` must contain every base
    aggregate name that the expression reads.
    """
    # `exp.copy()` is safe here — ``add_columns`` will inspect this
    # expression structurally but never mutate it.
    return Column(
        name=name,
        expression=FrozenSQL.of(metric.expression.expr.copy()),
        dependencies=dependency_names,
        kind=ColumnKind.FACT,
    )


def composite_leaf_dependencies(metric: Metric) -> frozenset[Identifier]:
    """Return the base-aggregate column names a composite metric reads.

    These are exactly the dataset-qualified or bare metric references
    already resolved by :func:`classify_metric`; pulling them out of
    the AST is simpler than threading a reference list through the
    codebase.
    """
    names: set[Identifier] = set()
    for col in metric.expression.expr.find_all(exp.Column):
        names.add(normalize_identifier(col.name))
    return frozenset(names)


def strip_column_qualifiers(expression: FrozenSQL) -> FrozenSQL:
    """Return ``expression`` with every column reference's qualifier removed.

    Composite metric expressions are written as ``orders.total_revenue
    / NULLIF(orders.order_count, 0)`` but are rendered downstream
    against the current CTE (``step_00N``) whose columns are already
    named ``total_revenue`` / ``order_count``. Stripping qualifiers in
    the plan representation keeps rendering simple and keeps the
    algebra dependency set consistent (one name per column).
    """
    copy = expression.expr.copy()
    for col in copy.find_all(exp.Column):
        col.set("table", None)
    return FrozenSQL.of(copy)


def aggregate_argument(
    metric: Metric, arg_columns: tuple[exp.Column, ...]
) -> FrozenSQL:
    """Return the argument expression that AGGREGATE should receive.

    For an aggregate of shape ``F(<arg>)`` the returned ``FrozenSQL`` is
    a *deep copy* of ``<arg>`` exactly as the user wrote it — including
    compound expressions like ``price * qty`` or ``CASE WHEN x THEN
    amount END``. ``COUNT(*)`` is the one exception: codegen rewrites it
    to ``COUNT(1)``, so we record the literal here.

    We deep-copy the subtree because codegen later mutates AST nodes
    (qualifier rewriting in :func:`osi.codegen.transpiler._qualify_columns`).
    The plan-side argument must not alias into the source-of-truth metric
    expression.

    ``arg_columns`` is the flat list of columns the dependency analyser
    extracted from the argument; it is consulted only as a fallback for
    ``COUNT(DISTINCT)`` shapes whose argument lives one level deeper in
    the AST.
    """
    top = metric.expression.expr
    if isinstance(top, exp.Count):
        inner = top.this
        if isinstance(inner, exp.Star):
            return FrozenSQL.of(exp.Literal.number(1))
        if isinstance(inner, exp.Distinct):
            # COUNT(DISTINCT <expr>) — store the inner ``<expr>``; codegen
            # re-wraps it in ``Distinct(...)``. The Foundation forces a
            # single inner expression at parse time, so we take it
            # directly. (If the parser's contract widens to multi-arg
            # ``DISTINCT``, this branch must too.)
            inner_exprs = list(inner.expressions)
            if len(inner_exprs) == 1:
                return FrozenSQL.of(inner_exprs[0].copy())
            # Defensive: keep the whole Distinct so codegen can render it
            # with all expressions, even though the path is unreachable
            # in the current Foundation.
            return FrozenSQL.of(inner.copy())  # pragma: no cover
    _ = arg_columns
    return FrozenSQL.of(top.this.copy())


__all__ = [
    "aggregate_argument",
    "field_to_column",
    "metric_to_aggregate_column",
    "parse_metric_aggregate",
]
