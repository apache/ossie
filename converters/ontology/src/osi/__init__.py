"""
Public API surface for osi.

Consumers should import from here rather than from deep sub-paths.
"""

from osi.model import (
    Concept,
    ConceptMapping,
    ConceptType,
    CustomExtension,
    Dataset,
    DatasetField,
    DialectExpression,
    DialectExpressionSet,
    Formula,
    JoinPath,
    LinkMapping,
    Metric,
    ObjectMapping,
    OntologyComponent,
    OntologyMapping,
    OsiOntology,
    ReferentMapping,
    Relationship,
    RelationshipMultiplicity,
    Role,
    SemanticModel,
)
from osi.spec import OsiSpec
from osi.parser import OsiParser
from osi.external.palantir.parser import PalantirParser
from osi.converter.spec_to_osi.converter import SpecToOsiConverter
from osi.converter.osi_to_spec.converter import OsiToSpecConverter
from osi.converter.palantir_to_osi.converter import PalantirToOsiConverter

__all__ = [
    # Model — ontology layer
    "Concept",
    "ConceptType",
    "Relationship",
    "RelationshipMultiplicity",
    "Role",
    "Formula",
    # Model — semantic layer
    "Dataset",
    "DatasetField",
    "DialectExpression",
    "DialectExpressionSet",
    "JoinPath",
    "Metric",
    "SemanticModel",
    # Model — mapping layer
    "ObjectMapping",
    "ReferentMapping",
    "LinkMapping",
    "ConceptMapping",
    "OntologyMapping",
    "OntologyComponent",
    "OsiOntology",
    # Supporting types
    "CustomExtension",
    # Spec DTO
    "OsiSpec",
    # Parsers
    "OsiParser",
    "PalantirParser",
    # Converters
    "SpecToOsiConverter",
    "OsiToSpecConverter",
    "PalantirToOsiConverter",
]