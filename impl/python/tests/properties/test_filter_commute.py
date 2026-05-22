"""Law §4.6 — Filter Commutativity.

``filter(filter(s, p1), p2)`` yields a state structurally equivalent to
``filter(filter(s, p2), p1)``. The Foundation algebra represents filter
by a no-op on the state (predicates live on the plan step), so this
law reduces to "filter never changes the state shape, regardless of
order."

A DuckDB-executed row-set equivalence test is added alongside the
Phase 4 codegen harness.
"""

from __future__ import annotations

import sqlglot
from hypothesis import given, settings

from osi.common.sql_expr import FrozenSQL
from osi.planning.algebra import CalculationState, filter_
from tests.properties.strategies import states

_p1 = FrozenSQL.of(sqlglot.parse_one("1 = 1"))
_p2 = FrozenSQL.of(sqlglot.parse_one("2 = 2"))


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_filter_order_does_not_change_state_shape(
    state: CalculationState,
) -> None:
    left = filter_(
        filter_(state, _p1, dependencies=frozenset()), _p2, dependencies=frozenset()
    )
    right = filter_(
        filter_(state, _p2, dependencies=frozenset()), _p1, dependencies=frozenset()
    )
    assert left == right
