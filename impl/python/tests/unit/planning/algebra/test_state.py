"""Unit tests for :class:`CalculationState` and :class:`Column`.

These cover the invariant machinery wired into ``__post_init__``. The
algebra's operator tests (``test_source.py``, ``test_aggregate.py`` …)
go one level above this; here we just make sure the value objects
themselves refuse to exist in a bad state.
"""

from __future__ import annotations

import pytest
import sqlglot

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIError
from osi.planning.algebra import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
)
from tests.properties.strategies import dimension_column, fact_column


def _ident(raw: str) -> str:
    return normalize_identifier(raw)


def test_scalar_state_has_empty_grain():
    state = CalculationState(grain=frozenset(), columns=())
    assert state.is_scalar
    assert state.column_names == frozenset()


def test_duplicate_column_names_rejected():
    col = dimension_column(_ident("a"))
    with pytest.raises(OSIError) as exc_info:
        CalculationState(grain=frozenset({_ident("a")}), columns=(col, col))
    assert exc_info.value.code == ErrorCode.E3005_COLUMN_NAME_COLLISION


def test_grain_must_reference_dimensions():
    col = fact_column(_ident("x"))
    with pytest.raises(OSIError) as exc_info:
        CalculationState(grain=frozenset({_ident("x")}), columns=(col,))
    assert exc_info.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


def test_grain_must_be_present_at_all():
    with pytest.raises(OSIError) as exc_info:
        CalculationState(grain=frozenset({_ident("missing")}), columns=())
    assert exc_info.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


def test_column_dependencies_must_exist():
    a = dimension_column(_ident("a"))
    b = Column(
        name=_ident("b"),
        expression=FrozenSQL.of(sqlglot.parse_one("a + missing")),
        dependencies=frozenset({_ident("a"), _ident("missing")}),
        kind=ColumnKind.FACT,
    )
    with pytest.raises(OSIError) as exc_info:
        CalculationState(grain=frozenset({_ident("a")}), columns=(a, b))
    assert exc_info.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


def test_aggregate_column_without_aggregate_info_rejected():
    with pytest.raises(OSIError) as exc_info:
        Column(
            name=_ident("total"),
            expression=FrozenSQL.of(sqlglot.parse_one("SUM(x)")),
            dependencies=frozenset({_ident("x")}),
            kind=ColumnKind.AGGREGATE,
            aggregate=None,
        )
    assert exc_info.value.code == ErrorCode.E4001_EXPLOSION_UNSAFE


def test_non_aggregate_column_with_aggregate_info_rejected():
    with pytest.raises(OSIError) as exc_info:
        Column(
            name=_ident("x"),
            expression=FrozenSQL.of(sqlglot.parse_one("x")),
            dependencies=frozenset(),
            kind=ColumnKind.DIMENSION,
            aggregate=AggregateInfo(
                function=AggregateFunction.SUM,
                argument=FrozenSQL.of(sqlglot.parse_one("x")),
            ),
        )
    assert exc_info.value.code == ErrorCode.E4001_EXPLOSION_UNSAFE


def test_state_is_frozen():
    col = dimension_column(_ident("a"))
    state = CalculationState(grain=frozenset({_ident("a")}), columns=(col,))
    from dataclasses import FrozenInstanceError

    with pytest.raises((FrozenInstanceError, AttributeError)):
        state.grain = frozenset()  # type: ignore[misc]


def test_column_lookup_raises_on_missing():
    state = CalculationState(grain=frozenset(), columns=())
    with pytest.raises(OSIError) as exc_info:
        state.column(_ident("nope"))
    assert exc_info.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


class TestAggregateFunction:
    def test_count_distinct_is_holistic(self):
        from osi.planning.algebra.state import Decomposability

        assert (
            AggregateFunction.COUNT_DISTINCT.decomposability is Decomposability.HOLISTIC
        )

    def test_sum_is_distributive(self):
        from osi.planning.algebra.state import Decomposability

        assert AggregateFunction.SUM.decomposability is Decomposability.DISTRIBUTIVE

    def test_avg_is_algebraic(self):
        from osi.planning.algebra.state import Decomposability

        assert AggregateFunction.AVG.decomposability is Decomposability.ALGEBRAIC


class TestUniqueKeysInvariant:
    """Invariant I-9: unique_keys must reference dimension columns and be non-empty."""

    def _state(self, **kw):
        a = dimension_column(_ident("a"))
        b = dimension_column(_ident("b"))
        defaults = dict(grain=frozenset({_ident("a")}), columns=(a, b))
        defaults.update(kw)
        return CalculationState(**defaults)

    def test_no_unique_keys_is_default(self):
        state = self._state()
        assert state.unique_keys == frozenset()

    def test_unique_keys_accepted(self):
        state = self._state(
            unique_keys=frozenset({frozenset({_ident("b")})}),
        )
        assert frozenset({_ident("b")}) in state.unique_keys

    def test_empty_unique_key_rejected(self):
        with pytest.raises(OSIError) as exc_info:
            self._state(unique_keys=frozenset({frozenset()}))
        assert exc_info.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_unique_key_referencing_unknown_column_rejected(self):
        with pytest.raises(OSIError) as exc_info:
            self._state(
                unique_keys=frozenset({frozenset({_ident("nope")})}),
            )
        assert exc_info.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY

    def test_unique_key_referencing_fact_column_rejected(self):
        a = dimension_column(_ident("a"))
        x = fact_column(_ident("x"))
        with pytest.raises(OSIError) as exc_info:
            CalculationState(
                grain=frozenset({_ident("a")}),
                columns=(a, x),
                unique_keys=frozenset({frozenset({_ident("x")})}),
            )
        assert exc_info.value.code == ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY


class TestIsUniqueOn:
    """``is_unique_on`` discharges the fan-trap rule for :func:`enrich`."""

    def _state(self, *, unique_keys=frozenset()):
        a = dimension_column(_ident("a"))
        b = dimension_column(_ident("b"))
        c = dimension_column(_ident("c"))
        return CalculationState(
            grain=frozenset({_ident("a"), _ident("b")}),
            columns=(a, b, c),
            unique_keys=unique_keys,
        )

    def test_grain_itself_is_a_key(self):
        state = self._state()
        assert state.is_unique_on(frozenset({_ident("a"), _ident("b")}))

    def test_strict_superset_of_grain_is_a_key(self):
        state = self._state()
        assert state.is_unique_on(frozenset({_ident("a"), _ident("b"), _ident("c")}))

    def test_strict_subset_of_grain_is_not_a_key(self):
        state = self._state()
        assert not state.is_unique_on(frozenset({_ident("a")}))

    def test_uk_match_proves_uniqueness(self):
        state = self._state(unique_keys=frozenset({frozenset({_ident("c")})}))
        assert state.is_unique_on(frozenset({_ident("c")}))

    def test_superset_of_uk_proves_uniqueness(self):
        state = self._state(unique_keys=frozenset({frozenset({_ident("c")})}))
        assert state.is_unique_on(frozenset({_ident("c"), _ident("a")}))

    def test_partial_uk_does_not_prove_uniqueness(self):
        state = self._state(
            unique_keys=frozenset({frozenset({_ident("a"), _ident("c")})}),
        )
        assert not state.is_unique_on(frozenset({_ident("a")}))

    def test_keys_unrelated_to_grain_or_uk_not_unique(self):
        state = self._state()
        assert not state.is_unique_on(frozenset({_ident("c")}))

    def test_empty_keys_never_unique_when_grain_non_empty(self):
        state = self._state()
        assert not state.is_unique_on(frozenset())
