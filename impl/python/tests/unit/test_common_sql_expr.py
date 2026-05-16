"""Unit tests for :mod:`osi.common.sql_expr`.

Covers the invariant 10 contract (``ARCHITECTURE.md``): SQL fragments
travel between layers only as :class:`sqlglot.exp.Expression` values,
never as raw strings, and structural comparisons go through
:func:`sql_expr_equal`.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import exp

from osi.common.sql_expr import FrozenSQL, parse_sql_expr, sql_expr_equal
from osi.errors import ErrorCode, OSIError


class TestParseSqlExpr:
    def test_parses_simple_column(self) -> None:
        expr = parse_sql_expr("orders.total_amount")
        assert isinstance(expr, exp.Expression)

    def test_parses_function_call(self) -> None:
        expr = parse_sql_expr("SUM(orders.total_amount)")
        assert isinstance(expr, exp.Sum)

    def test_invalid_raises_E5002(self) -> None:
        with pytest.raises(OSIError) as exc_info:
            parse_sql_expr("...")
        assert exc_info.value.code == ErrorCode.E1006_SQL_EXPRESSION_SYNTAX


class TestSqlExprEqual:
    def test_identical_expressions_are_equal(self) -> None:
        a = sqlglot.parse_one("a + b")
        b = sqlglot.parse_one("a + b")
        assert sql_expr_equal(a, b)

    def test_different_expressions_are_not_equal(self) -> None:
        a = sqlglot.parse_one("a + b")
        b = sqlglot.parse_one("a - b")
        assert not sql_expr_equal(a, b)


class TestFrozenSQL:
    def test_wraps_expression_with_canonical(self) -> None:
        expr = sqlglot.parse_one("COUNT(*)")
        wrapped = FrozenSQL.of(expr)
        assert wrapped.expr is expr
        assert "COUNT" in wrapped.canonical.upper()

    def test_is_hashable(self) -> None:
        expr = sqlglot.parse_one("1 + 1")
        wrapped = FrozenSQL.of(expr)
        assert hash(wrapped) == hash(FrozenSQL.of(sqlglot.parse_one("1 + 1")))

    def test_equality_uses_canonical_form(self) -> None:
        a = FrozenSQL.of(sqlglot.parse_one("a + b"))
        b = FrozenSQL.of(sqlglot.parse_one("a+b"))
        c = FrozenSQL.of(sqlglot.parse_one("b + a"))
        assert a == b
        assert a != c

    def test_equality_with_non_frozen_returns_not_implemented(self) -> None:
        wrapped = FrozenSQL.of(sqlglot.parse_one("1"))
        assert (wrapped == "1") is False
