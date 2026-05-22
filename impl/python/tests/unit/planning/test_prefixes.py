"""Unit tests for :mod:`osi.planning.prefixes`.

Every synthetic name in the planner and codegen is produced here. These
tests lock in (a) *deterministic* naming — same inputs always produce the
same identifier — and (b) *valid* identifiers that pass
:func:`normalize_identifier`.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import is_valid_identifier
from osi.planning.prefixes import (
    CTE_FILTER_JOIN_RHS,
    CTE_FINAL,
    CTE_MEASURE_GROUP,
    CTE_MERGED,
    SYNTH_COLUMN_AGG_PREFIX,
    SYNTH_COLUMN_DERIVED_PREFIX,
    cte_name,
    mangle_join_key,
    stable_sorted_identifiers,
    synth_aggregate_name,
    synth_derived_name,
)


class TestConstants:
    def test_cte_constants_are_lowercase_identifiers(self) -> None:
        for const in (
            CTE_FILTER_JOIN_RHS,
            CTE_FINAL,
            CTE_MEASURE_GROUP,
            CTE_MERGED,
        ):
            assert is_valid_identifier(const), const
            assert const == const.lower()

    def test_synth_prefixes_are_distinct(self) -> None:
        assert SYNTH_COLUMN_AGG_PREFIX != SYNTH_COLUMN_DERIVED_PREFIX


class TestCteName:
    def test_deterministic(self) -> None:
        a = cte_name(CTE_MEASURE_GROUP, 0)
        b = cte_name(CTE_MEASURE_GROUP, 0)
        assert a == b

    def test_index_encoded(self) -> None:
        a = cte_name(CTE_MEASURE_GROUP, 0)
        b = cte_name(CTE_MEASURE_GROUP, 1)
        assert a != b
        assert a.endswith("_0") and b.endswith("_1")

    def test_produces_valid_identifier(self) -> None:
        name = cte_name(CTE_FILTER_JOIN_RHS, 42)
        assert is_valid_identifier(name)


class TestMangleJoinKey:
    def test_deterministic(self) -> None:
        assert mangle_join_key("orders", "customer_id") == mangle_join_key(
            "orders", "customer_id"
        )

    def test_dataset_and_column_both_encoded(self) -> None:
        a = mangle_join_key("orders", "customer_id")
        b = mangle_join_key("returns", "customer_id")
        c = mangle_join_key("orders", "order_id")
        assert len({a, b, c}) == 3

    def test_produces_valid_identifier(self) -> None:
        name = mangle_join_key("orders", "customer_id")
        assert is_valid_identifier(name)


class TestSyntheticNames:
    def test_aggregate_name_is_valid(self) -> None:
        name = synth_aggregate_name(3)
        assert is_valid_identifier(name)
        assert name.endswith("_3")

    def test_derived_name_is_valid(self) -> None:
        name = synth_derived_name(5)
        assert is_valid_identifier(name)
        assert name.endswith("_5")

    def test_aggregate_and_derived_are_distinct(self) -> None:
        assert synth_aggregate_name(0) != synth_derived_name(0)


class TestStableSort:
    def test_stable_under_permutation(self) -> None:
        xs = stable_sorted_identifiers(
            [
                is_valid_identifier_and_return("b"),
                is_valid_identifier_and_return("a"),
                is_valid_identifier_and_return("c"),
            ]
        )
        ys = stable_sorted_identifiers(
            [
                is_valid_identifier_and_return("c"),
                is_valid_identifier_and_return("a"),
                is_valid_identifier_and_return("b"),
            ]
        )
        assert xs == ys


# Helper used above — avoids re-importing normalize_identifier in several
# places. Kept module-private to prevent accidental reuse in non-test code.
def is_valid_identifier_and_return(raw: str):
    from osi.common.identifiers import normalize_identifier  # local

    return normalize_identifier(raw)


def test_prefixes_module_only_produces_ascii_names() -> None:
    for f in (synth_aggregate_name, synth_derived_name):
        for i in range(10):
            name = f(i)
            assert name.isascii(), name


def test_cte_name_rejects_invalid_prefix_via_identifier_rules() -> None:
    # Defensive: caller passing a bad prefix should surface an
    # OSI-typed parse error so the diagnostic chain stays inside the
    # OSIError hierarchy and clients can route on it. Catching the
    # generic ``Exception`` would have allowed any TypeError /
    # AttributeError regression to silently pass.
    from osi.errors import ErrorCode, OSIError

    with pytest.raises(OSIError) as excinfo:
        cte_name("1bad", 0)
    assert excinfo.value.code is ErrorCode.E1005_IDENTIFIER_INVALID
