"""Function-name compliance levels from ``core-spec/expression_language.md``.

This module is purely informational (see the package README / project plan
for the rationale): the spec's own "Dialect Extensions" section says an
unrecognized function name should pass through by default, so
:mod:`ossie_sql.validate` does *not* use this module to reject expressions.
Instead, :func:`compliance_level` lets callers (tests, tooling, documentation
generators, ...) ask "is this function name part of the portable core the
spec defines, and at what tier?"

This may be used in the future for strict modes and generating warnings.

* Only function *names* are listed here -- operators (``||``, ``%``,
  ``AND``/``OR``/``NOT``, ``LIKE``/``ILIKE``) and typed literals
  (``DATE '...'``) are syntax, not callable names, and don't belong in a
  function-name registry.
* The spec's date-part vocabulary for ``EXTRACT``/``DATE_PART`` (``WEEK``,
  ``DAYOFWEEK``, ``MILLISECOND``, ...) is a separate, closed list of
  *argument* values, not standalone function names, and is intentionally
  excluded from :data:`DATETIME_FUNCTIONS`.
* Names like ``TO_VARCHAR``/``TO_NUMBER``/``TO_BOOLEAN`` appear in some
  draft implementations but are not defined anywhere in the committed
  spec, so they are not included here.
"""

from __future__ import annotations

from typing import Final, Literal

ComplianceLevel = Literal["REQUIRED", "RECOMMENDED", "EXPERIMENTAL"]

# Aggregation -- Core (REQUIRED), Statistical (REQUIRED), Percentile (REQUIRED),
# Approximate (RECOMMENDED).
_AGGREGATE_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "SUM",
        "COUNT",
        "AVG",
        "MIN",
        "MAX",
        "STDDEV",
        "STDDEV_POP",
        "STDDEV_SAMP",
        "VARIANCE",
        "VAR_POP",
        "VAR_SAMP",
        "MEDIAN",
        "PERCENTILE_CONT",
        "PERCENTILE_DISC",
    }
)
_AGGREGATE_RECOMMENDED: Final[frozenset[str]] = frozenset(
    {
        "APPROX_COUNT_DISTINCT",
        "APPROX_PERCENTILE",
    }
)

# Date/Time -- Current, Extraction, Alternative Extraction Syntax, Truncation,
# Arithmetic, and Construction are all REQUIRED. Formatting (TO_CHAR) and
# format-string construction are EXPERIMENTAL per the spec's own section
# headers.
_DATETIME_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "CURRENT_DATE",
        "CURRENT_TIMESTAMP",
        "CURRENT_TIME",
        "YEAR",
        "QUARTER",
        "MONTH",
        "DAY",
        "DAYOFYEAR",
        "HOUR",
        "MINUTE",
        "SECOND",
        "EXTRACT",
        "DATE_PART",
        "DATE_TRUNC",
        "DATEADD",
        "DATEDIFF",
        "TO_DATE",
        "TO_TIMESTAMP",
    }
)
_DATETIME_EXPERIMENTAL: Final[frozenset[str]] = frozenset({"TO_CHAR"})

# String -- Manipulation and Search are REQUIRED (including REGEXP_LIKE, listed
# under Pattern Matching); the RECOMMENDED regex functions are separate.
_STRING_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "CONCAT",
        "LENGTH",
        "LOWER",
        "UPPER",
        "TRIM",
        "LTRIM",
        "RTRIM",
        "LEFT",
        "RIGHT",
        "SUBSTRING",
        "REPLACE",
        "SPLIT_PART",
        "POSITION",
        "CHARINDEX",
        "CONTAINS",
        "STARTSWITH",
        "ENDSWITH",
        "REGEXP_LIKE",
    }
)
_STRING_RECOMMENDED: Final[frozenset[str]] = frozenset(
    {
        "REGEXP_EXTRACT",
        "REGEXP_REPLACE",
        "REGEXP_COUNT",
    }
)

# Math -- Basic and Advanced are REQUIRED; Trigonometric is RECOMMENDED.
_MATH_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "ABS",
        "ROUND",
        "FLOOR",
        "CEIL",
        "CEILING",
        "TRUNC",
        "TRUNCATE",
        "MOD",
        "SIGN",
        "POWER",
        "SQRT",
        "EXP",
        "LN",
        "LOG",
        "LOG10",
        "GREATEST",
        "LEAST",
    }
)
_MATH_RECOMMENDED: Final[frozenset[str]] = frozenset(
    {
        "SIN",
        "COS",
        "TAN",
        "ASIN",
        "ACOS",
        "ATAN",
        "ATAN2",
        "RADIANS",
        "DEGREES",
        "PI",
    }
)

# Conditional (REQUIRED).
_CONDITIONAL_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "IF",
        "IFF",
        "NULLIF",
        "COALESCE",
        "IFNULL",
        "NVL",
        "NVL2",
        "ZEROIFNULL",
        "NULLIFZERO",
    }
)

# Window -- Ranking and Offset (REQUIRED). Window aggregations reuse the
# aggregate functions above.
_WINDOW_REQUIRED: Final[frozenset[str]] = frozenset(
    {
        "ROW_NUMBER",
        "RANK",
        "DENSE_RANK",
        "NTILE",
        "PERCENT_RANK",
        "CUME_DIST",
        "LAG",
        "LEAD",
        "FIRST_VALUE",
        "LAST_VALUE",
        "NTH_VALUE",
    }
)

# Type conversion -- CAST is REQUIRED, TRY_CAST is RECOMMENDED.
_TYPE_CONVERSION_REQUIRED: Final[frozenset[str]] = frozenset({"CAST"})
_TYPE_CONVERSION_RECOMMENDED: Final[frozenset[str]] = frozenset({"TRY_CAST"})

REQUIRED_FUNCTIONS: Final[frozenset[str]] = (
    _AGGREGATE_REQUIRED
    | _DATETIME_REQUIRED
    | _STRING_REQUIRED
    | _MATH_REQUIRED
    | _CONDITIONAL_REQUIRED
    | _WINDOW_REQUIRED
    | _TYPE_CONVERSION_REQUIRED
)
"""Every function name the spec marks REQUIRED (MUST support)."""

RECOMMENDED_FUNCTIONS: Final[frozenset[str]] = (
    _AGGREGATE_RECOMMENDED | _STRING_RECOMMENDED | _MATH_RECOMMENDED | _TYPE_CONVERSION_RECOMMENDED
)
"""Every function name the spec marks RECOMMENDED (SHOULD support)."""

EXPERIMENTAL_FUNCTIONS: Final[frozenset[str]] = _DATETIME_EXPERIMENTAL
"""Function names the spec marks EXPERIMENTAL (format-string-driven date/time)."""


def compliance_level(name: str) -> ComplianceLevel | None:
    """Return the spec compliance tier for a function name, or ``None``.

    ``None`` means ``name`` is not defined anywhere in
    ``core-spec/expression_language.md`` -- per the spec's "Dialect
    Extensions" section, that makes it a vendor/dialect extension, which
    :mod:`ossie_sql.validate` passes through rather than rejects.
    """
    upper = name.upper()
    if upper in REQUIRED_FUNCTIONS:
        return "REQUIRED"
    if upper in RECOMMENDED_FUNCTIONS:
        return "RECOMMENDED"
    if upper in EXPERIMENTAL_FUNCTIONS:
        return "EXPERIMENTAL"
    return None


__all__ = [
    "REQUIRED_FUNCTIONS",
    "RECOMMENDED_FUNCTIONS",
    "EXPERIMENTAL_FUNCTIONS",
    "ComplianceLevel",
    "compliance_level",
]
