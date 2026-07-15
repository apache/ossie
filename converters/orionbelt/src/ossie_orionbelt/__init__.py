"""ossie-orionbelt: bidirectional OBML <-> OSI converter.

Converts between OrionBelt Markup Language (OBML) semantic models and Open
Semantic Interchange (OSI) models, in both directions, plus an OSI ontology
emitter. Validation helpers check OBML and OSI documents against their JSON
schemas.

Public API:
    OSItoOBML            - convert an OSI model dict to OBML
    OBMLtoOSI            - convert an OBML model dict to OSI core-spec
    OBMLtoOSIOntology    - emit an OSI ontology document from an OBML model
    validate_obml        - validate an OBML model dict
    validate_osi         - validate an OSI model dict
    validate_osi_ontology - validate an OSI ontology document dict
    ValidationResult     - structured validation result
"""

from __future__ import annotations

from ossie_orionbelt.converter import (
    OBMLtoOSI,
    OBMLtoOSIOntology,
    OSItoOBML,
    ValidationResult,
    validate_obml,
    validate_osi,
    validate_osi_ontology,
)

__version__ = "0.1.0"

__all__ = [
    "OBMLtoOSI",
    "OBMLtoOSIOntology",
    "OSItoOBML",
    "ValidationResult",
    "validate_obml",
    "validate_osi",
    "validate_osi_ontology",
    "__version__",
]
