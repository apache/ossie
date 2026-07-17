"""Tests for ossie_sql.identifiers, matching the spec's comparison table.

You type this | Equivalent to | Matches a column created as `id`?
id            | ID            | Yes (standard behavior)
Id            | ID            | Yes (standard behavior)
"ID"          | ID            | Yes (force-matched to normalized case)
"id"          | id            | No (quotes force exact lower-case match)
"""

from __future__ import annotations

import pytest
import sqlglot

import ossie_sql  # noqa: F401
from ossie_sql.identifiers import (
    InvalidIdentifierError,
    identifiers_equal,
    is_valid_identifier,
    normalize_identifier,
    normalize_identifier_text,
)


def _identifier(sql: str) -> sqlglot.exp.Identifier:
    column = sqlglot.parse_one(sql, read="ossie")
    assert isinstance(column, sqlglot.exp.Column)
    ident = column.this
    assert isinstance(ident, sqlglot.exp.Identifier)
    return ident


@pytest.mark.parametrize(
    "source,expected_normalized",
    [
        ("id", "ID"),
        ("Id", "ID"),
        ('"ID"', "ID"),
        ('"id"', "id"),
    ],
)
def test_normalize_identifier_matches_spec_table(source: str, expected_normalized: str) -> None:
    assert normalize_identifier(_identifier(source)) == expected_normalized


@pytest.mark.parametrize(
    "a,b,expected_equal",
    [
        ("id", "ID", True),
        ("Id", "ID", True),
        ('"ID"', "ID", True),
        ('"id"', "ID", False),  # quoted lower-case does NOT match column ID
    ],
)
def test_identifiers_equal_matches_spec_table(a: str, b: str, expected_equal: bool) -> None:
    assert identifiers_equal(_identifier(a), _identifier(b)) is expected_equal


def test_regular_identifier_must_start_with_a_letter() -> None:
    assert is_valid_identifier("abc123", quoted=False)
    assert not is_valid_identifier("123abc", quoted=False)


def test_regular_identifier_rejects_special_characters() -> None:
    assert not is_valid_identifier("a-b", quoted=False)
    assert not is_valid_identifier("a b", quoted=False)


def test_quoted_identifier_allows_arbitrary_text() -> None:
    assert is_valid_identifier("a-b c!", quoted=True)


def test_identifier_length_limit_is_128() -> None:
    ok = "a" * 128
    too_long = "a" * 129
    assert is_valid_identifier(ok, quoted=False)
    assert not is_valid_identifier(too_long, quoted=False)
    assert not is_valid_identifier(too_long, quoted=True)


def test_normalize_invalid_identifier_raises() -> None:
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier_text("123abc", quoted=False)
    with pytest.raises(InvalidIdentifierError):
        normalize_identifier_text("", quoted=True)
