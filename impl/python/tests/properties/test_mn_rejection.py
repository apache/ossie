"""Law §4.12 — Fan-Trap Rejection.

The closed-algebra ``enrich`` operator is meant to preserve the parent's
grain. That is true only when the child is **unique on the join keys**
— equivalently, ``child.grain ⊆ frozenset(child_keys)``. Any traversal
that violates this rule (1→N relationships traversed in reverse,
many-to-many fact-to-fact joins, or a key set narrower than the child's
grain) is a *fan trap* and must be rejected with
:attr:`ErrorCode.E3011_MN_AGGREGATION_REJECTED`.

``filtering_join`` is excused: it only filters left rows, so the right
side's cardinality cannot fan out the result.

Mutation target: ``src/osi/planning/algebra/operations.py::enrich``.
"""

from __future__ import annotations

from hypothesis import given, settings

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIError
from osi.planning.algebra import (
    CalculationState,
    FilterMode,
    JoinType,
    enrich,
    filtering_join,
    source,
)
from tests.properties.strategies import dimension_column, states


@given(state=states())
@settings(max_examples=100, deadline=None)
def test_enrich_rejects_fan_trap_when_child_grain_exceeds_keys(
    state: CalculationState,
) -> None:
    """A child whose grain is wider than the join keys is a fan trap.

    The Foundation ``enrich`` requires ``child.grain ⊆ child_keys``; any
    counter-example must be rejected with ``E3011``.
    """
    a = normalize_identifier("a")
    b = normalize_identifier("b")
    if a not in state.column_names or b in state.column_names:
        return  # parent must contain ``a`` for the parent_keys check
    child = source(
        primary_key=frozenset({a, b}),
        dimension_columns=[dimension_column(a), dimension_column(b)],
    )
    try:
        enrich(
            state,
            child,
            parent_keys=(a,),
            child_keys=(a,),
            join_type=JoinType.INNER,
        )
    except OSIError as err:
        assert err.code is ErrorCode.E3011_MN_AGGREGATION_REJECTED
        return
    raise AssertionError("enrich should have rejected fan-trap traversal")


def test_filtering_join_accepts_nn_style_relationship() -> None:
    # filtering_join does not care about cardinality — it's a
    # set-membership test.
    left = source(
        primary_key=frozenset({normalize_identifier("a")}),
        dimension_columns=[dimension_column(normalize_identifier("a"))],
    )
    right = source(
        primary_key=frozenset({normalize_identifier("a")}),
        dimension_columns=[dimension_column(normalize_identifier("a"))],
    )
    out = filtering_join(
        left,
        right,
        lhs_keys=frozenset({normalize_identifier("a")}),
        rhs_keys=frozenset({normalize_identifier("a")}),
        mode=FilterMode.SEMI,
    )
    assert out.column_names == left.column_names
