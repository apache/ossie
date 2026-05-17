"""Layer 1 of the compiler pipeline.

Takes a YAML file (or string) and produces a frozen, validated
:class:`SemanticModel` plus a :class:`Namespace` and
:class:`RelationshipGraph`. Rejects any use of deferred features
(``specs/deferred/``) with ``E_DEFERRED_KEY_REJECTED``.

See ``../../../ARCHITECTURE.md`` §2 for the full contract.
"""

from osi.config import FoundationFlags
from osi.parsing.graph import (
    Cardinality,
    RelationshipEdge,
    RelationshipGraph,
    build_graph,
)
from osi.parsing.models import (
    Dataset,
    Dialect,
    Field,
    FieldRole,
    Metric,
    NamedFilter,
    Parameter,
    Relationship,
    SemanticModel,
)
from osi.parsing.namespace import DatasetNamespace, Namespace, build_namespace
from osi.parsing.parser import ParseResult, parse_semantic_model
from osi.parsing.validation import validate_model

__all__ = [
    "Cardinality",
    "Dataset",
    "DatasetNamespace",
    "Dialect",
    "Field",
    "FieldRole",
    "FoundationFlags",
    "Metric",
    "NamedFilter",
    "Namespace",
    "Parameter",
    "ParseResult",
    "Relationship",
    "RelationshipEdge",
    "RelationshipGraph",
    "SemanticModel",
    "build_graph",
    "build_namespace",
    "parse_semantic_model",
    "validate_model",
]
