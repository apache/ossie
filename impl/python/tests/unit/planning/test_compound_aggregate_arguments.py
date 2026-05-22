"""TDD tests for compound aggregate arguments (Issue 2.1).

The Foundation's metric expressions are constrained to a single
top-level aggregate, but the *argument* to that aggregate may be a
non-trivial scalar expression: ``SUM(price * qty)``, ``AVG(amount -
discount)``, ``MIN(CASE WHEN status = 'open' THEN amount END)``, etc.

These tests pin the contract on
:func:`osi.planning.columns.aggregate_argument` and
:func:`metric_to_aggregate_column`:

1. The argument carried into ``AggregateInfo.argument`` must be the
   *whole* top-level argument subtree, structurally equal to what the
   user wrote.
2. The set of column dependencies recorded on the aggregate column
   must equal the set of all columns referenced anywhere in that
   subtree.
3. End-to-end planning for a metric with a compound argument must
   succeed and produce an aggregate step whose payload mentions every
   referenced column.

Each test is written so it fails today (the current implementation
truncates the argument to ``arg_columns[0].copy()``) and passes once
``aggregate_argument`` returns the full argument subtree.
"""

from __future__ import annotations

import textwrap

import pytest
from sqlglot import expressions as exp

from osi.common.identifiers import normalize_identifier
from osi.parsing.parser import parse_semantic_model
from osi.planning.algebra.state import AggregateFunction, ColumnKind
from osi.planning.columns import (
    aggregate_argument,
    metric_to_aggregate_column,
    parse_metric_aggregate,
)
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import ResolvedMetric


def _build_model_with_metric(metric_expr: str) -> tuple[PlannerContext, str]:
    """Build a single-dataset model with one metric expression.

    Returns the planner context and the metric name. The metric lives
    on the model (not the dataset) so it can be referenced unqualified.
    """
    yaml = textwrap.dedent(f"""\
        semantic_model:
          - name: demo
            datasets:
              - name: orders
                source: sales.orders
                primary_key: [order_id]
                fields:
                  - name: order_id
                    expression: order_id
                    role: dimension
                  - name: status
                    expression: status
                    role: dimension
                  - name: price
                    expression: price
                    role: fact
                  - name: qty
                    expression: qty
                    role: fact
                  - name: amount
                    expression: amount
                    role: fact
                  - name: discount
                    expression: discount
                    role: fact
            metrics:
              - name: target
                expression: {metric_expr}
        """)
    result = parse_semantic_model(yaml)
    return (
        PlannerContext(
            model=result.model,
            namespace=result.namespace,
            graph=result.graph,
        ),
        "target",
    )


def _orders_source_state(ctx: PlannerContext):
    """Run the source step for ``orders`` to produce a CalculationState."""
    from osi.planning.steps import PlanBuilder, fact_dataset, source_step

    builder = PlanBuilder()
    step = source_step(fact_dataset(normalize_identifier("orders"), ctx), builder, ctx)
    return step.state


# ---------------------------------------------------------------------------
# parse_metric_aggregate: column collection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr,expected_func,expected_columns",
    [
        ("SUM(price * qty)", AggregateFunction.SUM, {"price", "qty"}),
        ("SUM(amount - discount)", AggregateFunction.SUM, {"amount", "discount"}),
        (
            "SUM(price + qty + discount)",
            AggregateFunction.SUM,
            {"price", "qty", "discount"},
        ),
        ("AVG(amount - discount)", AggregateFunction.AVG, {"amount", "discount"}),
        ("MAX(price * 2 + amount)", AggregateFunction.MAX, {"price", "amount"}),
        ("MIN(price - discount)", AggregateFunction.MIN, {"price", "discount"}),
        ("SUM(amount)", AggregateFunction.SUM, {"amount"}),
        ("COUNT(*)", AggregateFunction.COUNT, set()),
        ("COUNT(DISTINCT price)", AggregateFunction.COUNT_DISTINCT, {"price"}),
    ],
)
def test_parse_metric_aggregate_collects_all_columns(
    expr: str,
    expected_func: AggregateFunction,
    expected_columns: set[str],
) -> None:
    ctx, metric_name = _build_model_with_metric(expr)
    metric = ctx.namespace.metrics[normalize_identifier(metric_name)]
    func, columns = parse_metric_aggregate(metric)
    assert func is expected_func
    seen = {c.name for c in columns}
    assert seen == expected_columns


# ---------------------------------------------------------------------------
# aggregate_argument: whole-tree fidelity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "SUM(price * qty)",
        "SUM(amount - discount)",
        "SUM(price + qty + discount)",
        "AVG(amount - discount)",
        "MAX(price * 2 + amount)",
        "MIN(price - discount)",
        "SUM(amount)",
    ],
)
def test_aggregate_argument_returns_whole_subtree(expr: str) -> None:
    """The recorded argument must structurally equal the original."""
    ctx, metric_name = _build_model_with_metric(expr)
    metric = ctx.namespace.metrics[normalize_identifier(metric_name)]
    func, columns = parse_metric_aggregate(metric)
    argument = aggregate_argument(metric, columns)
    expected = metric.expression.expr.this  # the inner expression
    assert argument.expr == expected, (
        f"aggregate_argument truncated the expression for {expr!r}: "
        f"got {argument.canonical!r}, expected {expected.sql()!r}"
    )


def test_aggregate_argument_count_star_returns_literal_one() -> None:
    """COUNT(*) should still degrade to ``1``."""
    ctx, metric_name = _build_model_with_metric("COUNT(*)")
    metric = ctx.namespace.metrics[normalize_identifier(metric_name)]
    func, columns = parse_metric_aggregate(metric)
    argument = aggregate_argument(metric, columns)
    assert argument.expr == exp.Literal.number(1)


def test_aggregate_argument_does_not_alias_metric_expression() -> None:
    """The returned ``FrozenSQL`` must hold a copy, not the original AST.

    Codegen later mutates AST nodes in place (qualifier rewriting); the
    plan-side argument must survive that without being affected.
    """
    ctx, metric_name = _build_model_with_metric("SUM(price * qty)")
    metric = ctx.namespace.metrics[normalize_identifier(metric_name)]
    func, columns = parse_metric_aggregate(metric)
    argument = aggregate_argument(metric, columns)
    # Walk to the first column inside the metric AST and mutate its name
    # in place; the FrozenSQL we already built must be untouched.
    inner_columns = list(metric.expression.expr.find_all(exp.Column))
    snapshot_canonical = argument.canonical
    inner_columns[0].set("this", exp.to_identifier("MUTATED"))
    assert argument.canonical == snapshot_canonical


# ---------------------------------------------------------------------------
# metric_to_aggregate_column: dependency tracking
# ---------------------------------------------------------------------------


def _resolve_metric(ctx: PlannerContext, metric_name: str) -> ResolvedMetric:
    name = normalize_identifier(metric_name)
    metric = ctx.namespace.metrics[name]
    # Owner dataset: the metric is model-scoped here; just attribute it
    # to the only dataset.
    return ResolvedMetric(
        metric=metric,
        dataset=normalize_identifier("orders"),
    )


@pytest.mark.parametrize(
    "expr,expected_deps",
    [
        ("SUM(price * qty)", {"price", "qty"}),
        ("SUM(amount - discount)", {"amount", "discount"}),
        ("AVG(amount - discount)", {"amount", "discount"}),
        ("MAX(price * 2 + amount)", {"price", "amount"}),
        ("SUM(amount)", {"amount"}),
    ],
)
def test_metric_to_aggregate_column_tracks_all_dependencies(
    expr: str, expected_deps: set[str]
) -> None:
    ctx, metric_name = _build_model_with_metric(expr)
    state = _orders_source_state(ctx)
    resolved = _resolve_metric(ctx, metric_name)
    column = metric_to_aggregate_column(resolved, state)
    assert column.kind is ColumnKind.AGGREGATE
    assert column.aggregate is not None
    seen_deps = {str(d) for d in column.dependencies}
    assert seen_deps == expected_deps


def test_metric_to_aggregate_column_argument_is_full_expression() -> None:
    """The aggregate column's ``argument`` must contain every referenced column."""
    ctx, metric_name = _build_model_with_metric("SUM(price * qty + discount)")
    state = _orders_source_state(ctx)
    resolved = _resolve_metric(ctx, metric_name)
    column = metric_to_aggregate_column(resolved, state)
    assert column.aggregate is not None
    arg = column.aggregate.argument.expr
    cols = {c.name for c in arg.find_all(exp.Column)}
    assert cols == {"price", "qty", "discount"}, f"argument lost columns: {cols}"


# ---------------------------------------------------------------------------
# End-to-end: planner emits an AGGREGATE step for compound metrics
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "expr",
    [
        "SUM(price * qty)",
        "AVG(amount - discount)",
        "MAX(price * 2 + amount)",
    ],
)
def test_compound_metric_plans_successfully(expr: str) -> None:
    """End-to-end: a query selecting a compound metric must plan."""
    from osi.planning import Reference, SemanticQuery, plan

    ctx, metric_name = _build_model_with_metric(expr)
    query = SemanticQuery(
        dimensions=(
            Reference(
                dataset=normalize_identifier("orders"),
                name=normalize_identifier("status"),
            ),
        ),
        measures=(
            Reference(
                dataset=None,
                name=normalize_identifier(metric_name),
            ),
        ),
    )
    p = plan(query, ctx)
    ops = [s.operation.value for s in p.steps]
    assert "aggregate" in ops
    # Find the aggregate step and confirm the metric column carries
    # every referenced source column as a dependency.
    agg_step = next(s for s in p.steps if s.operation.value == "aggregate")
    metric_col = next(
        c for c in agg_step.state.columns if c.name == normalize_identifier(metric_name)
    )
    assert metric_col.aggregate is not None
    referenced = {
        c.name for c in metric_col.aggregate.argument.expr.find_all(exp.Column)
    }
    canonical = metric_col.aggregate.argument.canonical
    assert "MUTATED" not in canonical
    assert referenced, (
        f"compound metric {expr!r} produced argument without columns: " f"{canonical!r}"
    )
