"""Parse -> generate round-trip tests across the spec's construct categories.

Each case is (input, expected_output) using the Ossie dialect for both
parsing and rendering. Most cases round-trip to themselves; a few
canonicalize to a different (still spec-valid) spelling, which is called
out inline.
"""

from __future__ import annotations

import pytest
import sqlglot

import ossie_sql  # noqa: F401

ROUNDTRIP_CASES = [
    # Arithmetic / comparison / logical operators.
    ("a + b - c * d / e % f", "a + b - c * d / e % f"),
    ("a = b AND a <> b AND a != b", "a = b AND a <> b AND a <> b"),
    ("a < b OR NOT (a > b)", "a < b OR NOT (a > b)"),
    ("x BETWEEN a AND b", "x BETWEEN a AND b"),
    ("x IN (a, b, c)", "x IN (a, b, c)"),
    ("x NOT IN (a, b, c)", "x NOT IN (a, b, c)"),
    ("x LIKE 'a%'", "x LIKE 'a%'"),
    ("x ILIKE 'a%'", "x ILIKE 'a%'"),
    ("x IS NULL", "x IS NULL"),
    ("x IS NOT NULL", "x IS NOT NULL"),
    ("NOT x IS NULL", "x IS NOT NULL"),
    ("a IS DISTINCT FROM b", "a IS DISTINCT FROM b"),
    ("a IS NOT DISTINCT FROM b", "a IS NOT DISTINCT FROM b"),
    ("a || b", "a || b"),
    # || sits at the same precedence tier as binary +/- (Snowflake/Databricks
    # convention, not SQLGlot's default looser bitwise-adjacent tier).
    ("a + b || c", "a + b || c"),
    ("a || b + c", "a || b + c"),
    ("a || b * c", "a || b * c"),
    # CASE.
    ("CASE WHEN a THEN 1 WHEN b THEN 2 ELSE 3 END", "CASE WHEN a THEN 1 WHEN b THEN 2 ELSE 3 END"),
    ("CASE x WHEN 1 THEN 'a' ELSE 'b' END", "CASE x WHEN 1 THEN 'a' ELSE 'b' END"),
    # Aggregates.
    ("SUM(amount)", "SUM(amount)"),
    ("COUNT(*)", "COUNT(*)"),
    ("COUNT(DISTINCT customer_id)", "COUNT(DISTINCT customer_id)"),
    ("SUM(DISTINCT amount)", "SUM(DISTINCT amount)"),
    ("MEDIAN(x)", "MEDIAN(x)"),
    (
        "PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY x)",
        "PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY x)",
    ),
    ("APPROX_COUNT_DISTINCT(customer_id)", "APPROX_COUNT_DISTINCT(customer_id)"),
    ("APPROX_PERCENTILE(amount, 0.5)", "APPROX_PERCENTILE(amount, 0.5)"),
    ("VAR_POP(x)", "VAR_POP(x)"),
    ("VAR_SAMP(x)", "VARIANCE(x)"),  # spec declares VAR_SAMP an alias for VARIANCE
    # Date/time.
    ("YEAR(order_date)", "YEAR(order_date)"),
    ("DAYOFYEAR(order_date)", "DAYOFYEAR(order_date)"),
    ("EXTRACT(YEAR FROM order_date)", "EXTRACT(YEAR FROM order_date)"),
    ("DATE_PART('year', order_date)", "EXTRACT(YEAR FROM order_date)"),  # unified onto EXTRACT
    # DATE_TRUNC's unit literal is normalized to upper-case at AST
    # construction time (a SQLGlot-wide invariant, not dialect-specific) --
    # harmless since the part name is compared case-insensitively everywhere.
    ("DATE_TRUNC('month', order_date)", "DATE_TRUNC('MONTH', order_date)"),
    ("DATEADD(day, 7, order_date)", "DATEADD(DAY, 7, order_date)"),
    ("DATEDIFF(day, start_date, end_date)", "DATEDIFF(DAY, start_date, end_date)"),
    ("TO_CHAR(order_date, 'YYYY-MM-DD')", "TO_CHAR(order_date, 'YYYY-MM-DD')"),
    # String.
    ("CONCAT(a, b)", "CONCAT(a, b)"),
    ("POSITION('a' IN b)", "POSITION('a' IN b)"),
    ("CHARINDEX('a', b)", "POSITION('a' IN b)"),  # alias for POSITION, per spec
    ("STARTSWITH(a, 'x')", "STARTSWITH(a, 'x')"),
    ("ENDSWITH(a, 'x')", "ENDSWITH(a, 'x')"),
    ("CONTAINS(a, 'x')", "CONTAINS(a, 'x')"),
    ("REGEXP_LIKE(a, 'x.*')", "REGEXP_LIKE(a, 'x.*')"),
    # Math.
    ("ABS(x)", "ABS(x)"),
    ("CEIL(x)", "CEIL(x)"),
    ("CEILING(x)", "CEIL(x)"),  # spec-declared alias
    ("MOD(x, y)", "x % y"),  # spec also defines "%" as the operator form
    ("GREATEST(a, b, c)", "GREATEST(a, b, c)"),
    # Conditional.
    ("IFF(a > b, 1, 0)", "IFF(a > b, 1, 0)"),
    ("COALESCE(a, b, c)", "COALESCE(a, b, c)"),
    ("NVL2(a, 1, 0)", "NVL2(a, 1, 0)"),
    ("ZEROIFNULL(x)", "ZEROIFNULL(x)"),
    ("NULLIFZERO(x)", "NULLIFZERO(x)"),
    # Window.
    ("ROW_NUMBER() OVER (ORDER BY x)", "ROW_NUMBER() OVER (ORDER BY x)"),
    (
        "SUM(amount) OVER (PARTITION BY region ORDER BY order_date "
        "ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)",
        "SUM(amount) OVER (PARTITION BY region ORDER BY order_date "
        "ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)",
    ),
    ("LAG(x, 1, 0) OVER (ORDER BY d)", "LAG(x, 1, 0) OVER (ORDER BY d)"),
    # Type conversion / typed literals.
    ("CAST(a AS VARCHAR)", "CAST(a AS VARCHAR)"),
    ("TRY_CAST(a AS INTEGER)", "TRY_CAST(a AS INT)"),
    ("DATE '2024-01-15'", "DATE '2024-01-15'"),
    ("TIME '10:30:00'", "TIME '10:30:00'"),
    ("TIMESTAMP_NTZ '2024-01-15 10:30:00'", "TIMESTAMP_NTZ '2024-01-15 10:30:00'"),
]


@pytest.mark.parametrize("source,expected", ROUNDTRIP_CASES)
def test_roundtrip(source: str, expected: str) -> None:
    parsed = sqlglot.parse_one(source, read="ossie")
    assert parsed.sql(dialect="ossie") == expected


# Precedence-structure checks: `a + b || c` renders identically either way
# `||` groups relative to `+`/`-`, so the string-equality cases above alone
# wouldn't catch a regression back to SQLGlot's default (looser) precedence
# for `||`. Assert the actual top-level node type instead, matching the
# Snowflake/Databricks convention of `||` sharing +/-'s tier, left to right.
CONCAT_PRECEDENCE_CASES = [
    ("a + b || c", sqlglot.exp.DPipe),  # (a + b) || c
    ("a || b + c", sqlglot.exp.Add),  # (a || b) + c
    ("a || b * c", sqlglot.exp.DPipe),  # a || (b * c) -- * still binds tighter
]


@pytest.mark.parametrize("source,top_level_type", CONCAT_PRECEDENCE_CASES)
def test_concat_precedence_matches_term_level(
    source: str, top_level_type: type[sqlglot.exp.Expression]
) -> None:
    parsed = sqlglot.parse_one(source, read="ossie")
    assert type(parsed) is top_level_type
