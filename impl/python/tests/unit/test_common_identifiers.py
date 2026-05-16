"""Unit tests for ``osi.common.identifiers``.

These tests exist at the common layer because identifier normalization
is **invariant 11** (``ARCHITECTURE.md``). Every compiler layer depends
on it, so a regression here cascades everywhere.
"""

from __future__ import annotations

import pytest
from hypothesis import given

from osi.common.identifiers import (
    identifiers_equal,
    is_valid_identifier,
    normalize_identifier,
)
from osi.errors import ErrorCode, OSIError
from tests.properties.strategies import identifiers


class TestIsValidIdentifier:
    @pytest.mark.parametrize(
        "raw",
        ["a", "abc", "a1", "a_b", "_x", "CustomerId", "x_123"],
    )
    def test_accepts_valid_shapes(self, raw: str) -> None:
        assert is_valid_identifier(raw)

    @pytest.mark.parametrize(
        "raw",
        ["", "1a", "a-b", "a.b", "a b", "a!", "a\n"],
    )
    def test_rejects_invalid_shapes(self, raw: str) -> None:
        assert not is_valid_identifier(raw)


class TestNormalizeIdentifier:
    def test_lowercases(self) -> None:
        assert normalize_identifier("CustomerId") == "customerid"

    def test_returns_same_for_already_normalized(self) -> None:
        assert normalize_identifier("orders") == "orders"

    def test_empty_raises_E1005(self) -> None:
        with pytest.raises(OSIError) as exc_info:
            normalize_identifier("")
        assert exc_info.value.code == ErrorCode.E1005_IDENTIFIER_INVALID

    def test_invalid_shape_raises_E1005(self) -> None:
        with pytest.raises(OSIError) as exc_info:
            normalize_identifier("1bad")
        assert exc_info.value.code == ErrorCode.E1005_IDENTIFIER_INVALID

    def test_non_string_raises_E1005(self) -> None:
        with pytest.raises(OSIError) as exc_info:
            normalize_identifier(123)  # type: ignore[arg-type]
        assert exc_info.value.code == ErrorCode.E1005_IDENTIFIER_INVALID

    @pytest.mark.parametrize("reserved", ["__grain__", "__provenance__", "__all__"])
    def test_reserved_raises_E2008(self, reserved: str) -> None:
        with pytest.raises(OSIError) as exc_info:
            normalize_identifier(reserved)
        assert exc_info.value.code == ErrorCode.E2008_RESERVED_IDENTIFIER


class TestIdentifiersEqual:
    def test_case_insensitive(self) -> None:
        assert identifiers_equal("Orders", "orders")
        assert identifiers_equal("ORDERS", "orders")

    def test_distinct_names_not_equal(self) -> None:
        assert not identifiers_equal("orders", "customers")


class TestNormalizeProperty:
    @given(identifiers())
    def test_normalize_is_idempotent(self, ident: str) -> None:
        once = normalize_identifier(ident)
        twice = normalize_identifier(once)
        assert once == twice

    @given(identifiers())
    def test_generated_identifiers_are_valid(self, ident: str) -> None:
        assert is_valid_identifier(ident)
