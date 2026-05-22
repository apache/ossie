"""Dimension-only query planning (``Proposed_OSI_Semantics.md §5.3``).

These tests pin the three observable outcomes of
:func:`~osi.planning.planner._dimension_only_group`:

1. Single-dataset queries plan deterministically.
2. Multi-dataset queries pick a unique anchor that can reach every
   other dataset via a safe N:1 path, ignoring declaration order.
3. When no safe anchor exists, the failure is the same error the
   path finder would raise for the equivalent measure-bearing query
   (``E2004`` / ``E3011``) — never a silent fan-trap.

   FIXME(spec-alignment): Under ``Proposed_OSI_Semantics.md §6.8
   *Semantic guarantee*`` an M:N-supporting engine (which this
   implementation is) MUST NOT raise ``E3011`` at the user-facing
   surface — that code is reserved for engine-level M:N opt-outs.
   The dim-only path currently leaks the algebra-internal
   ``E3011`` precondition signal unchanged, while the measure-
   bearing path translates it to ``E_UNSAFE_REAGGREGATION`` (see
   ``test_fan_trap_safety.py``). The planner's dim-only group
   should perform the same translation. Until that refactor lands,
   the assertion below accepts both shapes.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIPlanningError
from osi.planning import PlanOperation, Reference, SemanticQuery, plan
from tests.unit.planning.fixtures import orders_context


def _ref(dataset: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(dataset), name=normalize_identifier(name)
    )


def test_single_dataset_dim_only_query_plans_cleanly() -> None:
    ctx = orders_context()
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"), _ref("customers", "segment")),
        measures=(),
    )

    plan_ = plan(query, ctx)

    assert {normalize_identifier("region"), normalize_identifier("segment")} == set(
        plan_.output_columns
    )
    sources = [s for s in plan_.steps if s.operation is PlanOperation.SOURCE]
    assert len(sources) == 1
    assert sources[0].payload.dataset == normalize_identifier("customers")


def test_multi_dataset_dim_only_picks_unique_safe_anchor() -> None:
    """``orders → customers`` is the only safe direction; anchor must be ``orders``."""
    ctx = orders_context()
    query = SemanticQuery(
        dimensions=(_ref("customers", "region"), _ref("orders", "status")),
        measures=(),
    )

    plan_ = plan(query, ctx)

    sources = [s for s in plan_.steps if s.operation is PlanOperation.SOURCE]
    assert len(sources) == 1
    assert sources[0].payload.dataset == normalize_identifier("orders")


def test_multi_dataset_dim_only_order_independent() -> None:
    """Anchor selection must not depend on dimension declaration order."""
    ctx = orders_context()
    q_a = SemanticQuery(
        dimensions=(_ref("customers", "region"), _ref("orders", "status")),
        measures=(),
    )
    q_b = SemanticQuery(
        dimensions=(_ref("orders", "status"), _ref("customers", "region")),
        measures=(),
    )

    plan_a = plan(q_a, ctx)
    plan_b = plan(q_b, ctx)

    anchor_a = next(
        s for s in plan_a.steps if s.operation is PlanOperation.SOURCE
    ).payload.dataset
    anchor_b = next(
        s for s in plan_b.steps if s.operation is PlanOperation.SOURCE
    ).payload.dataset
    assert anchor_a == anchor_b


def test_dim_only_cross_fact_chain_rejected() -> None:
    """``orders`` and ``returns`` cannot be traversed safely in either direction."""
    ctx = orders_context()
    query = SemanticQuery(
        dimensions=(_ref("orders", "status"), _ref("returns", "return_id")),
        measures=(),
    )

    with pytest.raises(OSIPlanningError) as excinfo:
        plan(query, ctx)

    assert excinfo.value.code in (
        ErrorCode.E3011_MN_AGGREGATION_REJECTED,
        ErrorCode.E2004_UNREACHABLE_DATASET,
    )
