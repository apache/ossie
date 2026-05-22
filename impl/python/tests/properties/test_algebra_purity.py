"""Law §4.2 — Purity.

Every operator is pure: no I/O, no clocks, no randomness, no mutation of
inputs. Calling the same operator twice with the same arguments returns
equal results, and the input state is unchanged.

Mutation target: whole ``src/osi/planning/algebra/`` package.
"""

from __future__ import annotations

from copy import deepcopy

from hypothesis import given, settings

from osi.common.identifiers import normalize_identifier
from osi.planning.algebra import CalculationState, aggregate, project, source
from tests.properties.strategies import (
    aggregate_column,
    dimension_column,
    fact_column,
    states,
)


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_project_does_not_mutate_state(state: CalculationState) -> None:
    before = deepcopy(state)
    _ = project(state, [c.name for c in state.columns])
    assert state == before


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_project_is_deterministic(state: CalculationState) -> None:
    names = [c.name for c in state.columns]
    a = project(state, names)
    b = project(state, names)
    assert a == b
    assert a is not b or a == b


@given(state=states(min_facts=1, max_facts=3))
@settings(max_examples=100, deadline=None)
def test_aggregate_is_deterministic(state: CalculationState) -> None:
    target = state.grain
    fact = next(c for c in state.columns if c.kind.value == "fact")
    agg = aggregate_column(
        normalize_identifier("total_repeat"),
        over=fact.name,
    )
    a = aggregate(state, target, [agg])
    b = aggregate(state, target, [agg])
    assert a == b


def test_source_with_equal_args_is_equal() -> None:
    # Concrete case — property generator cannot compare because it
    # already returns a built state, but we can double-build here.
    pk = frozenset({normalize_identifier("a")})
    a = source(
        primary_key=pk,
        dimension_columns=[dimension_column(normalize_identifier("a"))],
        fact_columns=[fact_column(normalize_identifier("x"))],
    )
    b = source(
        primary_key=pk,
        dimension_columns=[dimension_column(normalize_identifier("a"))],
        fact_columns=[fact_column(normalize_identifier("x"))],
    )
    assert a == b
    # Strong structural equality — frozen dataclasses hash/compare by value.
    assert hash(a.grain) == hash(b.grain)
