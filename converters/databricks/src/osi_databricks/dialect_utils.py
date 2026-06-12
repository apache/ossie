"""Dialect selection and ANSI SQL detection utilities.

Provides shared logic for both import (Metric View → OSI) and export (OSI → Metric View)
directions, including detecting Databricks-specific SQL patterns and selecting the
best available dialect expression.
"""

from __future__ import annotations

import re

from osi.models import OSIDialect, OSIDialectExpression

# Databricks-specific SQL patterns not found in ANSI SQL
_DATABRICKS_ONLY_PATTERNS = [
    r"\bFILTER\s*\(",
    r"\bMEASURE\s*\(",
    r"\bQUALIFY\b",
    r"::",  # Type casting syntax
]


def is_standard_sql(expr: str) -> bool:
    """Determine if an expression uses only ANSI-standard SQL syntax.

    Args:
        expr: SQL expression string to check.

    Returns:
        True if the expression appears to use only standard SQL constructs.
    """
    for pattern in _DATABRICKS_ONLY_PATTERNS:
        if re.search(pattern, expr, re.IGNORECASE):
            return False
    return True


def select_dialect_expression(
    dialects: list[OSIDialectExpression],
    preferred: OSIDialect = OSIDialect.DATABRICKS,
    fallback: OSIDialect = OSIDialect.ANSI_SQL,
) -> str | None:
    """Select expression string with dialect preference chain.

    Args:
        dialects: List of dialect expressions to choose from.
        preferred: First-choice dialect (default: DATABRICKS).
        fallback: Second-choice dialect (default: ANSI_SQL).

    Returns:
        The expression string for the best available dialect, or None if
        neither preferred nor fallback dialect is available.
    """
    by_dialect = {d.dialect: d.expression for d in dialects}
    if preferred in by_dialect:
        return by_dialect[preferred]
    if fallback in by_dialect:
        return by_dialect[fallback]
    return None
