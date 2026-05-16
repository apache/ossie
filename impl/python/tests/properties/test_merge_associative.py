"""Law §4.7 — Merge Associativity.

``merge(merge(a, b), c) ≡ merge(a, merge(b, c))`` at equal grains with
disjoint non-grain columns.

Mutation target: ``src/osi/planning/algebra/operations.py::merge``.
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from osi.planning.algebra import merge, source
from tests.properties.strategies import (
    dimension_column,
    dimension_sets,
    fact_column,
    identifiers,
)


@st.composite
def _states_on_grain(draw, grain):
    """Build a CalculationState on ``grain`` with exactly one unique fact."""
    fact_name = draw(identifiers())
    # Avoid collisions with the grain identifiers.
    assume(fact_name not in grain)
    dims = [dimension_column(n) for n in sorted(grain)]
    return source(
        primary_key=grain if grain else frozenset({dims[0].name}) if dims else None,
        dimension_columns=dims,
        fact_columns=[fact_column(fact_name)],
    )


@given(grain=dimension_sets(min_size=1, max_size=3), data=st.data())
@settings(max_examples=100, deadline=None)
def test_merge_is_associative(grain, data) -> None:
    a = data.draw(_states_on_grain(grain))
    b_name = data.draw(
        identifiers().filter(lambda n: n not in {c.name for c in a.columns})
    )
    b = source(
        primary_key=grain,
        dimension_columns=[dimension_column(n) for n in sorted(grain)],
        fact_columns=[fact_column(b_name)],
    )
    c_name = data.draw(
        identifiers().filter(lambda n: n not in {c.name for c in a.columns} | {b_name})
    )
    c = source(
        primary_key=grain,
        dimension_columns=[dimension_column(n) for n in sorted(grain)],
        fact_columns=[fact_column(c_name)],
    )
    left = merge(merge(a, b), c)
    right = merge(a, merge(b, c))
    assert left.grain == right.grain
    assert {col.name for col in left.columns} == {col.name for col in right.columns}
