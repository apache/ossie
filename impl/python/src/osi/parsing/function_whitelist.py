"""OSI_SQL_2026 function whitelist (D-021 / Phase 3 review I7).

The Foundation's expression dialect is
``../../../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md``.
That document is normative: it lists every aggregate, window,
date/time, string, math, conditional, type-conversion, and CAST
function the Foundation accepts. Anything not in that subset is *not*
part of OSI_SQL_2026 and must be rejected at parse time with
:attr:`ErrorCode.E_UNKNOWN_FUNCTION` so a model author sees the error
immediately rather than at SQL-execution time (where the message
would point at the engine, not at their model).

The whitelist below is the union of every function name in the spec's
REQUIRED and RECOMMENDED tables, normalised to upper-case. Aliases
listed by the spec (e.g. ``CEIL`` / ``CEILING``, ``TRUNC`` /
``TRUNCATE``) are both included so users can write either spelling.
The deferred-function names (``EXISTS_IN``, ``ATTR``, ``UNSAFE``,
``AGG``, ``GRAIN_AGG``) are deliberately *not* in this whitelist —
they raise :attr:`ErrorCode.E_DEFERRED_KEY_REJECTED` instead, via
:mod:`osi.parsing.deferred`, so the user sees the more specific
"deferred for a later spec tier" message rather than the generic
"unknown function" one.
"""

from __future__ import annotations

from typing import Final

from sqlglot import expressions as exp

from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIParseError

# ---------------------------------------------------------------------------
# The whitelist
# ---------------------------------------------------------------------------

# Aggregation §170.
_AGGREGATE_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # Core (REQUIRED)
        "SUM",
        "COUNT",
        "AVG",
        "MIN",
        "MAX",
        # Statistical (REQUIRED)
        "STDDEV",
        "STDDEV_POP",
        "STDDEV_SAMP",
        "VARIANCE",
        "VAR_POP",
        "VAR_SAMP",
        # Percentile (REQUIRED)
        "MEDIAN",
        "PERCENTILE_CONT",
        "PERCENTILE_DISC",
        # Approximate (RECOMMENDED)
        "APPROX_COUNT_DISTINCT",
        "APPROX_PERCENTILE",
    }
)

# Date/Time §274.
_DATETIME_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # Current
        "CURRENT_DATE",
        "CURRENT_TIMESTAMP",
        "CURRENT_TIME",
        # Extraction (component getters)
        "YEAR",
        "QUARTER",
        "MONTH",
        "WEEK",
        "DAY",
        "DAYOFWEEK",
        "DAYOFYEAR",
        "HOUR",
        "MINUTE",
        "SECOND",
        # Alternative-syntax (operator-like, but parse as functions)
        "EXTRACT",
        "DATE_PART",
        # Truncation / arithmetic
        "DATE_TRUNC",
        "DATEADD",
        "DATEDIFF",
        # Construction / parsing / formatting
        "DATE",
        "TIMESTAMP",
        "TO_DATE",
        "TO_TIMESTAMP",
        "TO_CHAR",
    }
)

# String §385.
_STRING_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # Manipulation
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
        # Search
        "POSITION",
        "CHARINDEX",
        "CONTAINS",
        "STARTSWITH",
        "ENDSWITH",
        # Pattern matching as function call (LIKE / ILIKE are operators)
        "REGEXP_LIKE",
        # Regex (RECOMMENDED)
        "REGEXP_EXTRACT",
        "REGEXP_REPLACE",
        "REGEXP_COUNT",
    }
)

# Math §439.
_MATH_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # Basic
        "ABS",
        "ROUND",
        "FLOOR",
        "CEIL",
        "CEILING",
        "TRUNC",
        "TRUNCATE",
        "MOD",
        "SIGN",
        # Advanced
        "POWER",
        "SQRT",
        "EXP",
        "LN",
        "LOG",
        "LOG10",
        # Trigonometric (RECOMMENDED)
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
        # Comparison
        "GREATEST",
        "LEAST",
    }
)

# Conditional §488.
_CONDITIONAL_FUNCTIONS: Final[frozenset[str]] = frozenset(
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

# Window §533. Aggregations as window functions reuse the aggregate list.
_WINDOW_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        # Ranking
        "ROW_NUMBER",
        "RANK",
        "DENSE_RANK",
        "NTILE",
        "PERCENT_RANK",
        "CUME_DIST",
        # Offset / position
        "LAG",
        "LEAD",
        "FIRST_VALUE",
        "LAST_VALUE",
        "NTH_VALUE",
    }
)

# Type conversion §594. CAST is structural (handled by sqlglot's
# ``exp.Cast``) but TRY_CAST appears as a function call.
_TYPE_CONVERSION_FUNCTIONS: Final[frozenset[str]] = frozenset(
    {
        "CAST",
        "TRY_CAST",
        "TO_VARCHAR",
        "TO_NUMBER",
        # TO_DATE / TO_TIMESTAMP already in datetime
        "TO_BOOLEAN",
    }
)

# sqlglot canonicalises some Foundation-listed names to different
# spellings during parse (e.g. ``APPROX_COUNT_DISTINCT`` collapses to
# ``APPROX_DISTINCT``). Both the spec name and the sqlglot-canonical
# name must be in the whitelist so a user can author the spec name
# and the validator still accepts the parsed AST.
_SQLGLOT_ALIASES: Final[frozenset[str]] = frozenset(
    {
        "APPROX_DISTINCT",  # parsed form of APPROX_COUNT_DISTINCT
        "VARIANCE_POP",  # parsed form of VAR_POP
        "DAY_OF_WEEK",  # parsed form of DAYOFWEEK
        "DAY_OF_YEAR",  # parsed form of DAYOFYEAR
    }
)

OSI_SQL_2026_FUNCTIONS: Final[frozenset[str]] = (
    _AGGREGATE_FUNCTIONS
    | _DATETIME_FUNCTIONS
    | _STRING_FUNCTIONS
    | _MATH_FUNCTIONS
    | _CONDITIONAL_FUNCTIONS
    | _WINDOW_FUNCTIONS
    | _TYPE_CONVERSION_FUNCTIONS
    | _SQLGLOT_ALIASES
)
"""Every function name accepted by the OSI_SQL_2026 expression subset.

Names are upper-case. The set is the union of every REQUIRED or
RECOMMENDED entry in
``../../../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md``,
plus the spec-listed aliases (``CEIL`` / ``CEILING``, ``TRUNC`` /
``TRUNCATE``).
"""

# Function-shaped AST nodes whose semantics are operators or
# language constructs rather than user-callable functions. We never
# raise ``E_UNKNOWN_FUNCTION`` for these — they are accepted by the
# parser via the operator surface (``CASE``, ``LIKE``, ``IS NULL`` …)
# even though sqlglot models them as ``exp.Func`` subclasses.
_ALLOWED_FUNC_CLASSES: Final[tuple[type[exp.Expression], ...]] = (
    exp.Case,
    exp.Cast,
    exp.TryCast,
    exp.Coalesce,  # also in the function list, but defensively allowed
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_expression_functions(expression: FrozenSQL, *, where: str) -> None:
    """Reject function calls outside the OSI_SQL_2026 subset (D-021 / I7).

    Walks the AST and raises :attr:`ErrorCode.E_UNKNOWN_FUNCTION` for any
    function call whose name is not in
    :data:`OSI_SQL_2026_FUNCTIONS`. Deferred functions
    (``EXISTS_IN`` etc.) are reported separately by
    :mod:`osi.parsing.deferred`, which runs before this check, so a
    deferred function never reaches this code path.
    """
    for node in expression.expr.walk():
        ast = _unwrap_walk_item(node)
        if not isinstance(ast, exp.Func):
            continue
        if isinstance(ast, _ALLOWED_FUNC_CLASSES):
            continue
        name = _canonical_function_name(ast)
        if name is None:
            continue
        if name in OSI_SQL_2026_FUNCTIONS:
            continue
        raise OSIParseError(
            ErrorCode.E_UNKNOWN_FUNCTION,
            (
                f"{where} calls function {name!r}, which is not in the "
                f"OSI_SQL_2026 expression subset. The accepted function "
                f"list is fixed by ../../../../proposals/foundation-v0.1/"
                f"SQL_EXPRESSION_SUBSET.md; see Appendix C for the error "
                f"code (D-021)."
            ),
            context={
                "where": where,
                "function": name,
                "expression": expression.canonical,
            },
        )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _canonical_function_name(node: exp.Func) -> str | None:
    """Return the function name to compare against the whitelist.

    For :class:`exp.Anonymous` (sqlglot's catch-all for unrecognised
    function names), use the raw ``this`` attribute. For every other
    :class:`exp.Func` subclass, use :meth:`sqlglot.exp.Func.sql_name`,
    which returns the canonical SQL spelling for that class.
    """
    if isinstance(node, exp.Anonymous):
        raw = node.this
        if not isinstance(raw, str):
            return None
        return raw.upper()
    # sqlglot's ``Func.sql_name`` is annotated as returning ``Any``;
    # cast the result before normalising.
    sql_name: object = node.sql_name()  # type: ignore[no-untyped-call]
    if not isinstance(sql_name, str) or not sql_name:
        return None
    return sql_name.upper()


def _unwrap_walk_item(item: object) -> exp.Expression | None:
    """``walk()`` yields ``(node, parent, key)`` in newer sqlglot."""
    if isinstance(item, exp.Expression):
        return item
    if isinstance(item, tuple) and item and isinstance(item[0], exp.Expression):
        return item[0]
    return None


__all__ = [
    "OSI_SQL_2026_FUNCTIONS",
    "check_expression_functions",
]
