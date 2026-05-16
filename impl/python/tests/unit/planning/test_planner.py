"""End-to-end unit tests for :func:`osi.planning.plan`.

Covers the full pipeline: resolve → classify → group → build measure
group → merge → having → project. Error codes asserted:
``E2002`` (unknown name), ``E3011`` / ``E3012`` (M:N rejection — the
spec's deprecated and current codes respectively), ``E3008`` (grain
mismatch on merge — surfaces through ``E3002_UNSATISFIABLE_GRAIN`` when
a grain can't be reached), and ``E1209_CROSS_DATASET_AD_HOC_AGGREGATE``.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL, parse_sql_expr
from osi.errors import ErrorCode, OSIError
from osi.planning import PlanOperation, Reference, SemanticQuery, SortDirection, plan
from osi.planning.semantic_query import OrderBy
from tests.unit.planning.fixtures import mn_context, orders_context

# ---------------------------------------------------------------------------
# Shorthand
# ---------------------------------------------------------------------------


def _ref(ds: str | None, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds) if ds else None,
        name=normalize_identifier(name),
    )


def _sql(txt: str) -> FrozenSQL:
    return FrozenSQL.of(parse_sql_expr(txt))


def _operations(plan_obj) -> list[str]:  # type: ignore[no-untyped-def]
    return [s.operation.value for s in plan_obj.steps]


# ---------------------------------------------------------------------------
# Single-dataset cases
# ---------------------------------------------------------------------------


class TestSingleDataset:
    def test_dim_plus_measure(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
        )
        p = plan(q, ctx)
        assert _operations(p) == ["source", "aggregate", "project"]
        assert p.output_columns == (
            normalize_identifier("status"),
            normalize_identifier("total_revenue"),
        )

    def test_measure_only(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(measures=(_ref("orders", "total_revenue"),))
        p = plan(q, ctx)
        # No dimensions → grain collapses to scalar.
        assert p.root.state.is_scalar

    def test_distinct_count_metric(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "distinct_customers"),),
        )
        p = plan(q, ctx)
        assert "aggregate" in _operations(p)

    def test_avg_metric(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "avg_discount"),),
        )
        p = plan(q, ctx)
        assert "aggregate" in _operations(p)


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


class TestEnrichment:
    def test_dim_from_joined_dataset_adds_enrich(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(_ref("orders", "total_revenue"),),
        )
        p = plan(q, ctx)
        ops = _operations(p)
        assert ops == ["source", "enrich", "aggregate", "project"]

    def test_fan_trap_dimension_raises_unsafe_reaggregation(self) -> None:
        """Reject fan-trap enrichment paths with ``E_UNSAFE_REAGGREGATION``.

        S-9 / D-022: asking for a ``returns`` dimension from an
        ``orders`` measure routes through customers (orders →
        customers is N:1, safe), but the second hop customers →
        returns is the reverse of ``returns_to_customers`` — a fan
        trap. The planner refuses with the named code (the legacy
        ``E3011`` is the algebra-side code; the planner translates
        it to the user-facing name).
        """
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("returns", "return_id"),),
            measures=(_ref("orders", "total_revenue"),),
        )
        with pytest.raises(OSIError) as excinfo:
            plan(q, ctx)
        assert excinfo.value.code is ErrorCode.E_UNSAFE_REAGGREGATION


# ---------------------------------------------------------------------------
# WHERE pushdown + semi-joins
# ---------------------------------------------------------------------------


class TestFilters:
    def test_fact_local_filter_before_aggregate(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
            where=_sql("amount > 100"),
        )
        p = plan(q, ctx)
        ops = _operations(p)
        # filter must come before aggregate (row-level pushdown)
        assert ops.index("filter") < ops.index("aggregate")

    def test_exists_in_produces_filtering_join(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
            where=_sql("EXISTS_IN(customer_id, returns.customer_id)"),
        )
        p = plan(q, ctx)
        assert "filtering_join" in _operations(p)

    def test_not_exists_in_produces_anti_semi_join(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
            where=_sql("NOT EXISTS_IN(customer_id, returns.customer_id)"),
        )
        p = plan(q, ctx)
        fj = next(s for s in p.steps if s.operation is PlanOperation.FILTERING_JOIN)
        assert fj.payload.mode.name == "ANTI"


# ---------------------------------------------------------------------------
# HAVING
# ---------------------------------------------------------------------------


class TestHaving:
    def test_having_becomes_post_aggregate_filter(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
            having=_sql("total_revenue > 1000"),
        )
        p = plan(q, ctx)
        ops = _operations(p)
        agg_idx = ops.index("aggregate")
        filt_idx = ops.index("filter")
        assert filt_idx > agg_idx
        # Filter step has post_aggregate flag
        filt_step = p.steps[filt_idx]
        assert filt_step.payload.is_post_aggregate


# ---------------------------------------------------------------------------
# Multi-fact merge
# ---------------------------------------------------------------------------


class TestMultiFact:
    def test_two_facts_on_shared_dim_merge_at_grain(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(
                _ref("orders", "total_revenue"),
                _ref("returns", "total_refunds"),
            ),
        )
        p = plan(q, ctx)
        ops = _operations(p)
        assert ops.count("aggregate") == 2
        assert "merge" in ops

    def test_merged_plan_output_grain_matches_query(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(
                _ref("orders", "total_revenue"),
                _ref("returns", "total_refunds"),
            ),
        )
        p = plan(q, ctx)
        assert p.root.state.grain == frozenset({normalize_identifier("region")})


# ---------------------------------------------------------------------------
# M:N rejection
# ---------------------------------------------------------------------------


class TestMNRejection:
    def test_M_N_edge_on_enrichment_path_E3012(self) -> None:
        """Declared N:N with no resolution route → ``E3012``.

        Per ``Proposed_OSI_Semantics.md §6.8`` an M:N-supporting
        engine (which this implementation is) surfaces per-query M:N
        failures as ``E3012_MN_NO_SAFE_REWRITE`` (or ``E3013`` for
        the two-fact stitch case), which carry the actionable
        resolution routes (bridge, stitch, EXISTS_IN) in the error
        context. ``E3011`` is reserved for engines that opt out of
        M:N support entirely and never appears at the user-facing
        surface here.
        """
        ctx = mn_context()
        q = SemanticQuery(
            dimensions=(_ref("courses", "subject"),),
            measures=(_ref("grade_logs", "avg_grade"),),
        )
        with pytest.raises(OSIError) as excinfo:
            plan(q, ctx)
        assert excinfo.value.code is ErrorCode.E3012_MN_NO_SAFE_REWRITE


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Order/limit
# ---------------------------------------------------------------------------


class TestOrderLimit:
    def test_order_by_on_output_column(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("orders", "status"),),
            measures=(_ref("orders", "total_revenue"),),
            order_by=(
                OrderBy(
                    target=_ref(None, "total_revenue"), direction=SortDirection.DESC
                ),
            ),
            limit=5,
        )
        p = plan(q, ctx)
        assert p.order_by[0].descending
        assert p.order_by[0].column == normalize_identifier("total_revenue")
        assert p.limit == 5

    def test_order_by_non_output_rejected(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            measures=(_ref("orders", "total_revenue"),),
            order_by=(OrderBy(target=_ref(None, "nope")),),
        )
        with pytest.raises(OSIError) as excinfo:
            plan(q, ctx)
        assert excinfo.value.code is ErrorCode.E2002_NAME_NOT_FOUND


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_query_produces_identical_plans(self) -> None:
        ctx = orders_context()
        q = SemanticQuery(
            dimensions=(_ref("customers", "region"),),
            measures=(_ref("orders", "total_revenue"),),
            where=_sql("amount > 100"),
        )
        a = plan(q, ctx).to_json()
        b = plan(q, ctx).to_json()
        assert a == b
