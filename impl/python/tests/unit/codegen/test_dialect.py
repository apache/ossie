"""Unit tests for :mod:`osi.codegen.dialect`."""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import expressions as exp

from osi.codegen import Dialect
from osi.codegen.dialect import render_sql


@pytest.mark.parametrize(
    "dialect,expected",
    [
        (Dialect.ANSI, "SELECT 1"),
        (Dialect.DUCKDB, "SELECT 1"),
        (Dialect.SNOWFLAKE, "SELECT 1"),
    ],
)
def test_render_sql__trivial_select(dialect: Dialect, expected: str) -> None:
    rendered = render_sql(exp.select(exp.Literal.number(1)), dialect=dialect)
    # Whitespace-insensitive check; syrupy locks exact formatting in goldens.
    assert " ".join(rendered.split()) == expected


def test_render_sql__preserves_cte_structure() -> None:
    # ``render_sql`` always quotes identifiers (``identify=True``) so a
    # user-defined name that collides with a SQL reserved word renders
    # to valid SQL on every supported dialect. The assertions below
    # mirror that quoted form rather than the bare ANSI shape.
    ast = sqlglot.parse_one("WITH t AS (SELECT 1 AS x) SELECT x FROM t")
    rendered = render_sql(ast, dialect=Dialect.DUCKDB)
    compact = " ".join(rendered.split())
    assert 'WITH "t" AS' in compact and 'SELECT "x" FROM "t"' in compact


def test_dialect_enum_is_closed() -> None:
    """Dialect must stay a finite enum — new values are deliberate changes."""
    assert {d.name for d in Dialect} == {
        "OSI_SQL_2026",
        "ANSI",
        "DUCKDB",
        "SNOWFLAKE",
    }
