"""Unit tests for :mod:`osi.parsing.function_whitelist` (D-021 / I7).

The OSI_SQL_2026 dialect accepts the function set listed in
``../../../../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md``.
Anything outside that set must raise
:class:`ErrorCode.E_UNKNOWN_FUNCTION` at parse time so model authors
see the error immediately rather than at SQL execution time.
"""

from __future__ import annotations

import pytest

from osi.common.sql_expr import FrozenSQL, parse_sql_expr
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.function_whitelist import (
    OSI_SQL_2026_FUNCTIONS,
    check_expression_functions,
)


def _expr(sql: str) -> FrozenSQL:
    return FrozenSQL.of(parse_sql_expr(sql))


# ---------------------------------------------------------------------------
# In-subset functions are accepted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        # Aggregate (§170)
        "SUM(amount)",
        "COUNT(DISTINCT customer_id)",
        "AVG(amount)",
        "MIN(amount)",
        "MAX(amount)",
        "STDDEV_POP(amount)",
        "VAR_SAMP(amount)",
        "MEDIAN(amount)",
        "APPROX_COUNT_DISTINCT(customer_id)",
        # Date/Time (§274)
        "CURRENT_DATE",
        "YEAR(order_date)",
        "DATE_TRUNC('month', order_date)",
        "DATEADD('day', 7, order_date)",
        "DATEDIFF('day', start_date, end_date)",
        "EXTRACT(YEAR FROM order_date)",
        # String (§385)
        "CONCAT(first, last)",
        "UPPER(name)",
        "LOWER(name)",
        "LENGTH(name)",
        "SUBSTRING(name, 1, 3)",
        "TRIM(name)",
        "REGEXP_LIKE(name, '^A')",
        # Math (§439)
        "ABS(amount)",
        "ROUND(amount, 2)",
        "CEILING(amount)",
        "CEIL(amount)",  # alias
        "POWER(x, 2)",
        "SQRT(x)",
        "GREATEST(a, b, c)",
        # Conditional (§488)
        "COALESCE(a, b)",
        "IFNULL(a, 0)",
        "NULLIF(a, 0)",
        "IF(a > 0, 1, 0)",
        # CAST is a structural construct in sqlglot; allowed regardless of name.
        "CAST(amount AS DECIMAL)",
        "CAST(name AS VARCHAR)",
        # Window (§533) — names alone; the FrozenSQL parser accepts the
        # ``OVER (...)`` shape as long as the function call name is in
        # the whitelist.
        "ROW_NUMBER() OVER (ORDER BY id)",
        "RANK() OVER (PARTITION BY region ORDER BY amount)",
        "LAG(amount, 1, 0) OVER (ORDER BY order_date)",
        # CASE is a language construct, not a function.
        "CASE WHEN x > 0 THEN 'pos' ELSE 'neg' END",
    ],
)
def test_in_subset_functions_accepted(sql: str) -> None:
    # No exception should be raised. ``where`` is mandatory because the
    # error message embeds it.
    check_expression_functions(_expr(sql), where="test")


# ---------------------------------------------------------------------------
# Out-of-subset functions are rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql, function_name",
    [
        ("FOOBAR(x)", "FOOBAR"),
        ("BIT_AND(flags)", "BIT_AND"),  # not in the OSI subset
        ("MY_CUSTOM_FUNCTION(amount)", "MY_CUSTOM_FUNCTION"),
        ("OBJECT_CONSTRUCT('a', 1)", "OBJECT_CONSTRUCT"),  # Snowflake-only
        ("ARRAY_AGG(x)", "ARRAY_AGG"),  # outside Foundation Tier 1
    ],
)
def test_out_of_subset_function_rejected(sql: str, function_name: str) -> None:
    with pytest.raises(OSIParseError) as exc:
        check_expression_functions(_expr(sql), where="metric m")
    assert exc.value.code is ErrorCode.E_UNKNOWN_FUNCTION
    # Error context must name the offending function so users can fix it.
    assert exc.value.context["function"] == function_name
    assert exc.value.context["where"] == "metric m"


def test_error_message_cites_d021_and_spec() -> None:
    with pytest.raises(OSIParseError) as exc:
        check_expression_functions(_expr("FOOBAR(x)"), where="metric m")
    msg = str(exc.value)
    assert "FOOBAR" in msg
    assert "OSI_SQL_2026" in msg
    assert "D-021" in msg
    assert "SQL_EXPRESSION_SUBSET" in msg


def test_unknown_function_nested_inside_aggregate_rejected() -> None:
    # The walk must descend into aggregate arguments — a forbidden
    # function call hidden under SUM() should still be caught.
    with pytest.raises(OSIParseError) as exc:
        check_expression_functions(_expr("SUM(BOGUS_TRANSFORM(amount))"), where="m")
    assert exc.value.code is ErrorCode.E_UNKNOWN_FUNCTION
    assert exc.value.context["function"] == "BOGUS_TRANSFORM"


def test_unknown_function_inside_case_branch_rejected() -> None:
    # CASE itself is allowed; the unknown function under its WHEN/THEN
    # must still be rejected.
    sql = "CASE WHEN amount > 0 THEN FOOBAR(amount) ELSE 0 END"
    with pytest.raises(OSIParseError) as exc:
        check_expression_functions(_expr(sql), where="m")
    assert exc.value.context["function"] == "FOOBAR"


# ---------------------------------------------------------------------------
# Whitelist surface invariants
# ---------------------------------------------------------------------------


class TestWhitelistInvariants:
    def test_whitelist_is_all_uppercase(self) -> None:
        # Function names are case-insensitive in SQL; we canonicalise to
        # upper-case at compare time. The whitelist itself must follow
        # the same convention so a typo (mixed case) cannot creep in.
        for name in OSI_SQL_2026_FUNCTIONS:
            assert name == name.upper(), name

    def test_core_aggregates_are_in_subset(self) -> None:
        # The five-function distributive set named in
        # SQL_EXPRESSION_SUBSET.md §259 must be present — these are the
        # functions that every OSI engine must accept.
        for name in ("SUM", "COUNT", "MIN", "MAX", "AVG"):
            assert name in OSI_SQL_2026_FUNCTIONS

    def test_window_offset_set_is_complete(self) -> None:
        # SQL_EXPRESSION_SUBSET.md §564 ranks LAG/LEAD/FIRST_VALUE/
        # LAST_VALUE/NTH_VALUE as REQUIRED. Lock the set so a partial
        # delete cannot quietly slip through.
        offset = {"LAG", "LEAD", "FIRST_VALUE", "LAST_VALUE", "NTH_VALUE"}
        assert offset <= OSI_SQL_2026_FUNCTIONS

    def test_aliases_both_spellings_present(self) -> None:
        # Each alias pair listed in the spec must round-trip — users
        # should be able to write either spelling.
        for a, b in [("CEIL", "CEILING"), ("TRUNC", "TRUNCATE")]:
            assert a in OSI_SQL_2026_FUNCTIONS
            assert b in OSI_SQL_2026_FUNCTIONS

    def test_deferred_functions_are_not_in_whitelist(self) -> None:
        # Deferred names land via E_DEFERRED_KEY_REJECTED, not via
        # E_UNKNOWN_FUNCTION. Allowing a deferred name into the
        # whitelist would silently re-enable a feature the Foundation
        # has not standardised.
        for name in ("EXISTS_IN", "NOT_EXISTS_IN", "ATTR", "UNSAFE", "AGG"):
            assert name not in OSI_SQL_2026_FUNCTIONS
