"""Shared primitives used by all layers.

Contains:

- :mod:`osi.common.identifiers` — ``Identifier`` NewType, normalization,
  validation.
- :mod:`osi.common.sql_expr` — thin wrappers over SQLGlot for frozen,
  comparable ASTs.
- :mod:`osi.common.types` — cross-layer NewTypes (``DimensionSet``,
  ``CTEName``, ``ExpressionId``, ``SourceLocation``).
"""

from osi.common.identifiers import (
    Identifier,
    identifiers_equal,
    is_valid_identifier,
    normalize_identifier,
)
from osi.common.sql_expr import FrozenSQL, parse_sql_expr, sql_expr_equal
from osi.common.types import CTEName, DimensionSet, ExpressionId, SourceLocation

__all__ = [
    "CTEName",
    "DimensionSet",
    "ExpressionId",
    "FrozenSQL",
    "Identifier",
    "SourceLocation",
    "identifiers_equal",
    "is_valid_identifier",
    "normalize_identifier",
    "parse_sql_expr",
    "sql_expr_equal",
]
