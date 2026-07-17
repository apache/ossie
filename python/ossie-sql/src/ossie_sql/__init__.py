"""SQLGlot dialect and validation helpers for the Ossie expression language.

See ``core-spec/expression_language.md`` for the language spec this package
implements, and this package's README for scope and usage.
"""

from ossie_sql.dialect import Ossie
from ossie_sql.functions import ComplianceLevel, compliance_level
from ossie_sql.identifiers import (
    InvalidIdentifierError,
    identifiers_equal,
    normalize_identifier,
    normalize_identifier_text,
)
from ossie_sql.validate import UnsupportedConstructError, validate_expression

__all__ = [
    "ComplianceLevel",
    "InvalidIdentifierError",
    "Ossie",
    "UnsupportedConstructError",
    "compliance_level",
    "identifiers_equal",
    "normalize_identifier",
    "normalize_identifier_text",
    "validate_expression",
]
