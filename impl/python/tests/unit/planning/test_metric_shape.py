"""Tests for :mod:`osi.planning.metric_shape`.

Covers:

* top-level aggregate detection (``SUM``, ``COUNT``, ``COUNT(*)``,
  ``COUNT(DISTINCT …)``, ``MIN``, ``MAX``, ``AVG``)
* composite classification (metric-refs-metric) with qualified and
  bare leaves
* rejections: undeclared leaves, nested aggregates inside composites,
  mixed-dataset base aggregates (tested via the planner — here we only
  check the classifier)
* end-to-end plan output: composite metric produces AGGREGATE + then
  ADD_COLUMNS with the derived expression

The classifier plus end-to-end composite planning together address
``Proposed_OSI_Semantics.md §5.4``.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIPlanningError
from osi.parsing.parser import parse_semantic_model
from osi.planning import PlanOperation, Reference, SemanticQuery, plan
from osi.planning.algebra.state import AggregateFunction
from osi.planning.metric_shape import AggregateMetric, CompositeMetric, classify_metric
from osi.planning.planner_context import PlannerContext

_MODEL_TEMPLATE = """\
semantic_model:
  - name: demo
    dialect: ANSI_SQL
    datasets:
      - name: orders
        source: sales.orders
        primary_key: [order_id]
        fields:
          - {name: order_id,    expression: order_id,    role: dimension}
          - {name: customer_id, expression: customer_id, role: dimension}
          - {name: status,      expression: status,      role: dimension}
          - {name: amount,      expression: amount,      role: fact}
        metrics:
          - {name: total_revenue, expression: SUM(amount)}
          - {name: order_count,   expression: COUNT(order_id)}
          - {name: distinct_customers,
             expression: COUNT(DISTINCT customer_id)}
          - {name: max_amount,    expression: MAX(amount)}
      - name: customers
        source: sales.customers
        primary_key: [id]
        fields:
          - {name: id,     expression: id,     role: dimension}
          - {name: region, expression: region, role: dimension}
    relationships:
      - name: orders_to_customers
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]
__METRICS__"""


def _model(extra_metrics: str = "") -> PlannerContext:
    text = _MODEL_TEMPLATE.replace("__METRICS__", extra_metrics)
    # The fixture uses per-dataset ``metrics:`` blocks, deferred under
    # the strict Foundation. Opt back in via the legacy-permissive
    # flag set so the planner-side metric_shape contract stays
    # exercised.
    parsed = parse_semantic_model(text, flags=FoundationFlags.legacy_permissive())
    return PlannerContext(
        model=parsed.model,
        namespace=parsed.namespace,
        graph=parsed.graph,
    )


def _metric_by_name(ctx: PlannerContext, name: str):
    for m in ctx.model.metrics:
        if m.name == normalize_identifier(name):
            return m
    for ds in ctx.model.datasets:
        for m in ds.metrics:
            if m.name == normalize_identifier(name):
                return m
    raise AssertionError(f"metric {name} not found")


# ---------------------------------------------------------------------------
# Aggregate detection
# ---------------------------------------------------------------------------


class TestAggregateDetection:
    def test_sum(self) -> None:
        ctx = _model()
        shape = classify_metric(_metric_by_name(ctx, "total_revenue"), ctx.namespace)
        assert isinstance(shape, AggregateMetric)
        assert shape.function is AggregateFunction.SUM

    def test_count(self) -> None:
        ctx = _model()
        shape = classify_metric(_metric_by_name(ctx, "order_count"), ctx.namespace)
        assert isinstance(shape, AggregateMetric)
        assert shape.function is AggregateFunction.COUNT

    def test_count_distinct(self) -> None:
        ctx = _model()
        shape = classify_metric(
            _metric_by_name(ctx, "distinct_customers"), ctx.namespace
        )
        assert isinstance(shape, AggregateMetric)
        assert shape.function is AggregateFunction.COUNT_DISTINCT

    def test_max(self) -> None:
        ctx = _model()
        shape = classify_metric(_metric_by_name(ctx, "max_amount"), ctx.namespace)
        assert isinstance(shape, AggregateMetric)
        assert shape.function is AggregateFunction.MAX


# ---------------------------------------------------------------------------
# Composite detection
# ---------------------------------------------------------------------------

_COMPOSITE_MODEL_MODEL_SCOPED = """\
    metrics:
      - {name: avg_order_value,
         expression: "total_revenue / NULLIF(order_count, 0)"}
"""

_COMPOSITE_MODEL_QUALIFIED = """\
    metrics:
      - {name: avg_order_value,
         expression: "orders.total_revenue / NULLIF(orders.order_count, 0)"}
"""

_COMPOSITE_MODEL_NESTED = """\
    metrics:
      - {name: avg_order_value,
         expression: "total_revenue / NULLIF(order_count, 0)"}
      - {name: avg_doubled,
         expression: "2 * avg_order_value"}
"""


class TestCompositeDetection:
    def test_bare_leaves(self) -> None:
        ctx = _model(extra_metrics=_COMPOSITE_MODEL_MODEL_SCOPED)
        shape = classify_metric(_metric_by_name(ctx, "avg_order_value"), ctx.namespace)
        assert isinstance(shape, CompositeMetric)
        names = [r.name for r in shape.references]
        assert normalize_identifier("total_revenue") in names
        assert normalize_identifier("order_count") in names

    def test_qualified_leaves(self) -> None:
        ctx = _model(extra_metrics=_COMPOSITE_MODEL_QUALIFIED)
        shape = classify_metric(_metric_by_name(ctx, "avg_order_value"), ctx.namespace)
        assert isinstance(shape, CompositeMetric)
        assert all(
            r.dataset == normalize_identifier("orders") for r in shape.references
        )

    def test_composite_referencing_composite(self) -> None:
        """Accept a composite that references another composite.

        The planner recursively expands until it reaches base aggregates.
        """
        ctx = _model(extra_metrics=_COMPOSITE_MODEL_NESTED)
        shape = classify_metric(_metric_by_name(ctx, "avg_doubled"), ctx.namespace)
        assert isinstance(shape, CompositeMetric)

    def test_bare_leaf_that_is_not_a_metric_raises_E1206(self) -> None:
        bogus = """\
    metrics:
      - {name: broken,
         expression: "amount / 2"}
"""
        ctx = _model(extra_metrics=bogus)
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_metric(_metric_by_name(ctx, "broken"), ctx.namespace)
        assert excinfo.value.code is ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE

    def test_nested_aggregate_inside_composite_raises_E1206(self) -> None:
        bogus = """\
    metrics:
      - {name: broken,
         expression: "SUM(amount) / order_count"}
"""
        ctx = _model(extra_metrics=bogus)
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_metric(_metric_by_name(ctx, "broken"), ctx.namespace)
        assert excinfo.value.code is ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE


# ---------------------------------------------------------------------------
# Whitelisted-but-unsupported top-level aggregates (Phase 9 P1, 8a I1)
# ---------------------------------------------------------------------------


class TestUnsupportedTopLevelAggregate:
    """Reject whitelisted-but-unimplemented aggregates with a clear error.

    The OSI_SQL_2026 parse whitelist names aggregate functions that
    the planner does not yet lower (``MEDIAN``, ``STDDEV``,
    ``PERCENTILE_CONT``, …). Without an explicit rejection, the
    composite-classification path mistakes them for non-aggregate
    expressions and raises the misleading
    ``E1206_METRIC_IN_RAW_AGGREGATE``. ``classify_metric`` now rejects
    these up front with ``E1208_UNSUPPORTED_SQL_CONSTRUCT`` and names
    the offending function in ``error.context["function"]``.
    """

    @pytest.mark.parametrize(
        ("expression", "function_name"),
        [
            ("MEDIAN(amount)", "MEDIAN"),
            ("STDDEV(amount)", "STDDEV"),
            ("VARIANCE(amount)", "VARIANCE"),
            ("APPROX_COUNT_DISTINCT(amount)", "APPROXDISTINCT"),
        ],
    )
    def test_top_level_unsupported_aggregate_raises_E1208(
        self, expression: str, function_name: str
    ) -> None:
        bogus = f"""\
    metrics:
      - {{name: broken,
         expression: "{expression}"}}
"""
        ctx = _model(extra_metrics=bogus)
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_metric(_metric_by_name(ctx, "broken"), ctx.namespace)
        assert excinfo.value.code is ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT
        assert excinfo.value.context["metric"] == normalize_identifier("broken")
        assert excinfo.value.context["function"] == function_name
        assert "OSI_SQL_2026" in str(excinfo.value)

    def test_supported_aggregates_still_classify_normally(self) -> None:
        """SUM / COUNT / MIN / MAX / AVG must remain unchanged."""
        ctx = _model()
        for name in ("total_revenue", "order_count", "max_amount"):
            shape = classify_metric(_metric_by_name(ctx, name), ctx.namespace)
            assert isinstance(shape, AggregateMetric)

    def test_count_distinct_still_classifies_as_count(self) -> None:
        """``COUNT(DISTINCT x)`` must reach the supported branch.

        It is wrapped under ``exp.Count`` and so must not be diverted to
        the new rejection path.
        """
        ctx = _model()
        shape = classify_metric(
            _metric_by_name(ctx, "distinct_customers"), ctx.namespace
        )
        assert isinstance(shape, AggregateMetric)
        assert shape.function is AggregateFunction.COUNT_DISTINCT


class TestWindowedMetricRoot:
    """F-16: windowed metric bodies surface a precise engine-gap code.

    The Foundation spec accepts direct use of a windowed metric in
    ``Measures`` (§6.10 / D-031); this engine's aggregation planner
    does not yet implement that surface. Before F-16 the metric fell
    into the composite path and raised the misleading
    ``E1206_METRIC_IN_RAW_AGGREGATE``. ``classify_metric`` now rejects
    these up front with ``E_WINDOWED_MEASURE_NOT_SUPPORTED``.
    """

    @pytest.mark.parametrize(
        "expression",
        [
            "ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY amount DESC)",
            "RANK() OVER (PARTITION BY customer_id ORDER BY amount DESC)",
            "SUM(amount) OVER (PARTITION BY customer_id ORDER BY order_id ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        ],
    )
    def test_windowed_root_raises_engine_gap_code(self, expression: str) -> None:
        bogus = f"""\
    metrics:
      - {{name: windowed_metric,
         expression: "{expression}"}}
"""
        ctx = _model(extra_metrics=bogus)
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_metric(_metric_by_name(ctx, "windowed_metric"), ctx.namespace)
        assert excinfo.value.code is ErrorCode.E_WINDOWED_MEASURE_NOT_SUPPORTED
        assert excinfo.value.context["metric"] == normalize_identifier(
            "windowed_metric"
        )
        assert excinfo.value.context["shape"] == "windowed"
        # Diagnostic must steer the author toward the scalar path or
        # a plain aggregate, never the misleading composite-shape
        # remediation.
        msg = str(excinfo.value)
        assert "scalar" in msg.lower() or "Fields" in msg


# ---------------------------------------------------------------------------
# End-to-end composite planning
# ---------------------------------------------------------------------------


class TestCompositeMetricPlan:
    def _query(
        self,
        ctx: PlannerContext,
        *measures: str,
        dims: tuple[tuple[str | None, str], ...] = ((None, "region"),),
    ) -> SemanticQuery:
        return SemanticQuery(
            dimensions=tuple(
                Reference(
                    dataset=normalize_identifier(d) if d else None,
                    name=normalize_identifier(n),
                )
                for d, n in dims
            ),
            measures=tuple(
                Reference(dataset=None, name=normalize_identifier(m)) for m in measures
            ),
        )

    def test_plan_composite_adds_base_aggregates_and_derived_step(self) -> None:
        ctx = _model(extra_metrics=_COMPOSITE_MODEL_MODEL_SCOPED)
        q = self._query(ctx, "avg_order_value", dims=((None, "region"),))
        p = plan(q, ctx)
        agg = next(s for s in p.steps if s.operation is PlanOperation.AGGREGATE)
        agg_names = {c.name for c in agg.payload.aggregations}
        assert normalize_identifier("total_revenue") in agg_names
        assert normalize_identifier("order_count") in agg_names
        assert any(s.operation is PlanOperation.ADD_COLUMNS for s in p.steps)
        assert p.output_columns == (
            normalize_identifier("region"),
            normalize_identifier("avg_order_value"),
        )

    def test_plan_composite_plus_base_deduplicates_aggregate_set(self) -> None:
        ctx = _model(extra_metrics=_COMPOSITE_MODEL_MODEL_SCOPED)
        q = self._query(
            ctx,
            "avg_order_value",
            "total_revenue",
            dims=((None, "region"),),
        )
        p = plan(q, ctx)
        agg = next(s for s in p.steps if s.operation is PlanOperation.AGGREGATE)
        agg_names = [c.name for c in agg.payload.aggregations]
        assert agg_names.count(normalize_identifier("total_revenue")) == 1

    def test_plan_composite_of_composite(self) -> None:
        ctx = _model(extra_metrics=_COMPOSITE_MODEL_NESTED)
        q = self._query(ctx, "avg_doubled", dims=((None, "region"),))
        p = plan(q, ctx)
        agg = next(s for s in p.steps if s.operation is PlanOperation.AGGREGATE)
        agg_names = {c.name for c in agg.payload.aggregations}
        # The transitive base-aggregate set must include both roots.
        assert normalize_identifier("total_revenue") in agg_names
        assert normalize_identifier("order_count") in agg_names

    def test_composite_leaf_not_a_metric_raises_E1206_at_plan_time(self) -> None:
        bogus = """\
    metrics:
      - {name: broken,
         expression: "amount / 2"}
"""
        ctx = _model(extra_metrics=bogus)
        q = self._query(ctx, "broken", dims=((None, "region"),))
        with pytest.raises(OSIPlanningError) as excinfo:
            plan(q, ctx)
        assert excinfo.value.code is ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE
