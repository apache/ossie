"""Law §4.5 — Aggregate Idempotence at same grain.

For any state whose grain already matches ``target_grain`` and whose
aggregations are identity re-aggregations at that grain, ``aggregate``
returns a state that agrees on grain and columns.

Mutation target: ``src/osi/planning/algebra/operations.py::aggregate``.
"""

from __future__ import annotations

from hypothesis import given, settings

from osi.common.identifiers import normalize_identifier
from osi.planning.algebra import CalculationState, aggregate
from tests.properties.strategies import aggregate_column, states


@given(state=states(min_facts=1, max_facts=3))
@settings(max_examples=200, deadline=None)
def test_same_grain_agg_preserves_grain(state: CalculationState) -> None:
    fact = next(c for c in state.columns if c.kind.value == "fact")
    out = aggregate(
        state,
        state.grain,
        [
            aggregate_column(
                normalize_identifier(f"total_{fact.name}"),
                over=fact.name,
            )
        ],
    )
    assert out.grain == state.grain
