"""Law §4.11 — Chasm-Trap Safety.

Two facts sharing a dimension must be computed in separate states and
:func:`merge`-d on the shared dimension — never joined through a single
multi-branch state. This activates at plan level as soon as the planner
exists (Phase 3). Full end-to-end row-count verification lands in Phase
4 when the reference interpreter ships.

Property under test: whenever a :class:`~osi.planning.SemanticQuery`
requests measures from ``n`` distinct fact datasets, the resulting
:class:`~osi.planning.QueryPlan` contains exactly ``n`` ``AGGREGATE``
steps and ``n - 1`` ``MERGE`` steps. The aggregates occur before the
merges (topological order), and the merge grain equals the shared
dimension grain.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.planning import PlanOperation, Reference, SemanticQuery, plan
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


@pytest.mark.parametrize(
    "measures",
    [
        (
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    ],
)
def test_two_facts_route_through_merge(measures: tuple[Reference, ...]) -> None:
    ctx = orders_context()
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=measures,
    )
    p = plan(query, ctx)
    agg_count = sum(1 for s in p.steps if s.operation is PlanOperation.AGGREGATE)
    merge_count = sum(1 for s in p.steps if s.operation is PlanOperation.MERGE)
    assert agg_count == len(measures)
    assert merge_count == len(measures) - 1


def test_merge_grain_equals_query_dimension_grain() -> None:
    ctx = orders_context()
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    )
    p = plan(query, ctx)
    merge_step = next(s for s in p.steps if s.operation is PlanOperation.MERGE)
    assert merge_step.state.grain == frozenset({normalize_identifier("region")})


def test_aggregates_precede_merge_in_topo_order() -> None:
    ctx = orders_context()
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    )
    p = plan(query, ctx)
    first_merge = next(
        i for i, s in enumerate(p.steps) if s.operation is PlanOperation.MERGE
    )
    aggregates = [
        i for i, s in enumerate(p.steps) if s.operation is PlanOperation.AGGREGATE
    ]
    assert all(i < first_merge for i in aggregates)
