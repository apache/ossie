"""Law §4.3 — Determinism.

Same inputs ⇒ same output, including column order.

At Phase 1 we only have states and algebra ops — byte-identical SQL
rendering (the ultimate determinism test) lands in
``test_sql_determinism.py`` during Phase 4.
"""

from __future__ import annotations

from hypothesis import given, settings

from osi.planning.algebra import CalculationState, project
from tests.properties.strategies import states


@given(state=states())
@settings(max_examples=300, deadline=None)
def test_project_preserves_column_order(state: CalculationState) -> None:
    names = [c.name for c in state.columns]
    out = project(state, names)
    assert [c.name for c in out.columns] == names


@given(state=states(min_dims=2, max_dims=4))
@settings(max_examples=200, deadline=None)
def test_same_projection_twice_is_identical(state: CalculationState) -> None:
    names = [c.name for c in state.columns]
    a = project(state, names)
    b = project(state, names)
    assert a == b
    assert tuple(c.name for c in a.columns) == tuple(c.name for c in b.columns)
