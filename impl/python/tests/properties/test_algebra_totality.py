"""Law §4.1 — Totality.

Every operator either returns a valid :class:`CalculationState` or raises
:class:`AlgebraError` / :class:`OSIError` with an ``E4xxx`` / ``E3xxx``
code. No ``None``, no silent fallback.

Mutation target: ``src/osi/planning/algebra/operations.py``.
"""

from __future__ import annotations

from hypothesis import given, settings

from osi.errors import ErrorCode, OSIError
from osi.planning.algebra import CalculationState, aggregate, project
from tests.properties.strategies import aggregate_column, dimension_sets, states


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_source_states_are_valid(state: CalculationState) -> None:
    assert isinstance(state, CalculationState)
    assert state.grain.issubset(state.column_names)


@given(state=states(), target=dimension_sets(max_size=4))
@settings(max_examples=200, deadline=None)
def test_aggregate_is_total(state: CalculationState, target: frozenset) -> None:
    # Pick an aggregation that is always valid: SUM over some fact if
    # one exists; else skip the aggregation entirely by passing [].
    fact_names = [
        c.name
        for c in state.columns
        if c.kind.value == "fact"  # stringly to avoid importing enum here
    ]
    aggs = (
        [aggregate_column(_unused_name(state), over=fact_names[0])]
        if fact_names
        else []
    )
    try:
        out = aggregate(state, target, aggs)
    except OSIError as err:
        assert err.code.value.startswith(("E3", "E4")), err.code
        return
    assert isinstance(out, CalculationState)
    assert out.grain == target


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_project_is_total(state: CalculationState) -> None:
    # Always project exactly onto the current columns: happy path.
    names = [c.name for c in state.columns]
    out = project(state, names)
    assert isinstance(out, CalculationState)
    assert out.column_names == state.column_names


@given(state=states())
@settings(max_examples=50, deadline=None)
def test_project_unknown_raises_osi_error(state: CalculationState) -> None:
    try:
        project(state, ["definitely_not_a_real_column_name_xyzzy"])
    except OSIError as err:
        assert err.code is ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY
        return
    raise AssertionError("project on unknown column should raise E3006")


def _unused_name(state: CalculationState) -> str:
    base = "agg"
    i = 0
    while True:
        candidate = f"{base}_{i}"
        if candidate not in state.column_names:
            return candidate
        i += 1
