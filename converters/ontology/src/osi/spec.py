from __future__ import annotations

from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class OsiObject(BaseModel):
    """Base for all OSI DTOs. Strict (`extra=forbid`) to surface spec drift early."""
    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        extra="forbid",
    )


# Free-form AI context: either a bare string or a structured object with keys
# like `instructions`, `synonyms`, `examples` (per core.md).
AiContext = str | dict[str, Any]


class CustomExtension(OsiObject):
    """Vendor-specific metadata attached to any logical-model element (core.md).

    `data` is a JSON-encoded string so vendors can carry arbitrary payloads
    without extending the core schema.
    """
    vendor_name: str
    data: str


# ---------- Ontology ----------

class Role(OsiObject):
    """An additional role in a Relationship (the first role is implicit — the
    container concept). `name` is only required to disambiguate when the same
    concept plays multiple roles in the same relationship."""
    concept: str
    name: str | None = None


class Relationship(OsiObject):
    """A relationship grouped under its first-role concept.

    `roles` enumerates the *additional* roles (the first is the container
    concept). `multiplicity` constrains the last role; `OneToOne` is only
    valid for binary relationships. `verbalizes` is a list of natural-language
    patterns with `{Concept}` or `{Concept:role_name}` placeholders.
    `derived_by` and `requires` are raw expression strings (parsed elsewhere).
    """
    name: str
    description: str | None = None
    roles: list[Role] = Field(default_factory=list)
    verbalizes: list[str] = Field(default_factory=list)
    multiplicity: Literal["OneToOne", "ManyToOne"] | None = None
    derived_by: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)


class Concept(OsiObject):
    """A type-like node in the ontology — either an `EntityType` (real-world
    object referenced via other relationships) or a `ValueType` (a data type
    with extra semantics, must transitively extend a built-in value type).

    `identify_by` lists the names of relationships (declared under this
    concept) whose values uniquely reference its objects.
    """
    name: str
    type: Literal["EntityType", "ValueType"] | None = None
    description: str | None = None
    extends: list[str] | None = None
    identify_by: list[str] = Field(default_factory=list)
    derived_by: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)


class ConceptComponent(OsiObject):
    """Envelope for a concept and the relationships nested under it.

    Mirrors the YAML shape `{ concept: {...}, relationships: [...] }` where
    every relationship in the list takes the enclosing concept as its
    implicit first role.
    """
    concept: Concept
    relationships: list[Relationship] = Field(default_factory=list)


# ---------- Logical model (per osi/core.md) ----------

class DialectExpression(OsiObject):
    """A scalar (non-aggregating) SQL/expression in a specific dialect."""
    dialect: str
    expression: str


class Expression(OsiObject):
    """Multi-dialect expression carrier — same logical expression rendered in
    one or more dialects (e.g. ANSI_SQL + SNOWFLAKE)."""
    dialects: list[DialectExpression] = Field(default_factory=list)


class Dimension(OsiObject):
    """Dimensional metadata on a DatasetField."""
    is_time: bool | None = None


class DatasetField(OsiObject):
    """A row-level attribute of a Dataset. `expression` is scalar (no
    aggregations); use Metric for aggregates."""
    name: str
    expression: Expression
    dimension: Dimension | None = None
    label: str | None = None
    description: str | None = None
    ai_context: AiContext | None = None
    custom_extensions: list[CustomExtension] = Field(default_factory=list)


class Dataset(OsiObject):
    """A logical dataset (fact or dimension table) backed by `source` — a
    physical table/view reference or a query."""
    name: str
    source: str
    primary_key: list[str] | None = None
    unique_keys: list[list[str]] | None = None
    description: str | None = None
    ai_context: AiContext | None = None
    fields: list[DatasetField] = Field(default_factory=list)
    custom_extensions: list[CustomExtension] = Field(default_factory=list)


class JoinPath(OsiObject):
    """A foreign-key style join between two Datasets: rows in `from` reference
    rows in `to` by matching `from_columns` against `to_columns` in order.
    Same arity required on both sides."""
    name: str
    from_: str = Field(alias="from")
    to: str
    from_columns: list[str]
    to_columns: list[str]
    ai_context: AiContext | None = None
    custom_extensions: list[CustomExtension] = Field(default_factory=list)


class Metric(OsiObject):
    """A model-level quantitative measure defined as an aggregate expression.
    Can reference fields across multiple Datasets."""
    name: str
    expression: Expression
    description: str | None = None
    ai_context: AiContext | None = None
    custom_extensions: list[CustomExtension] = Field(default_factory=list)


class SemanticModel(OsiObject):
    """A complete logical/semantic model (the body that the core spec calls
    `semantic_model`): datasets plus the join paths and metrics defined over
    them. One or more SemanticModels can feed a single OntologyMapping."""
    name: str
    description: str | None = None
    ai_context: AiContext | None = None
    datasets: list[Dataset] = Field(default_factory=list)
    relationships: list[JoinPath] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    custom_extensions: list[CustomExtension] = Field(default_factory=list)


# ---------- Ontology mapping ----------

class ReferentMapping(OsiObject):
    """Locates an entity object by walking one of its identifying
    relationships. Carries either a leaf `expression` (SQL over dataset
    fields) or a nested `referent_mappings` list when the referenced concept
    is itself an entity with a compound/recursive identifier."""
    relationship: str
    expression: str | None = None
    referent_mappings: list[ReferentMapping] | None = None


class ObjectMapping(OsiObject):
    """Maps to objects of some concept. Either a direct scalar `expression`
    (for value types or simple-id entities) or `referent_mappings` (for
    entities with compound identifiers). XOR — never both."""
    concept: str | None = None
    expression: str | None = None
    referent_mappings: list[ReferentMapping] | None = None


class LinkMapping(OsiObject):
    """A node in the link-mapping tree. The arity of `relationship` equals
    the node's depth (top-level = unary, depth 2 = binary, etc.). `children`
    extend the mapped tuple by one role each, sharing this node's
    `object_mapping` as their prefix to avoid duplication."""
    object_mapping: ObjectMapping
    relationship: str | None = None
    children: list[LinkMapping] | None = None


class ConceptMapping(OsiObject):
    """Mappings that populate one concept and the relationships grouped under
    it. `object_mappings` populate the concept's objects; `link_mappings` is
    a forest of trees populating its relationships."""
    concept: str
    object_mappings: list[ObjectMapping] = Field(default_factory=list)
    link_mappings: list[LinkMapping] = Field(default_factory=list)


class OntologyMapping(OsiObject):
    """Binds a semantic model to the document ontology, then declares how its
    fields populate the ontology's concepts and relationships."""
    name: str
    description: str | None = None
    semantic_model: SemanticModel
    concept_mappings: list[ConceptMapping] = Field(default_factory=list)


# ---------- Root ----------

class OsiSpec(OsiObject):
    """Root OSI document: a single ontology definition and the ontology
    mappings that wire semantic models into it."""
    version: str | None = None
    name: str
    description: str | None = None
    ai_context: AiContext | None = None
    ontology: list[ConceptComponent] = Field(default_factory=list)
    ontology_mappings: list[OntologyMapping] = Field(default_factory=list)

    @classmethod
    def load_yaml(cls, text: str) -> OsiSpec:
        return cls.model_validate(yaml.safe_load(text))

    def dump_dict(self) -> dict:
        return self.model_dump(exclude_none=True, exclude_defaults=True, by_alias=True)

    def dump_yaml(self) -> str:
        return yaml.safe_dump(self.dump_dict(), sort_keys=False)


# `ReferentMapping` and `LinkMapping` are self-referential (each can contain a
# list of itself). Combined with `from __future__ import annotations`, every
# annotation is a string at class-definition time, so the self-reference is an
# unresolved forward ref. `model_rebuild()` re-walks the schema once the class
# is fully defined and pins the forward ref to the real type — without it,
# validating a payload with nested children raises PydanticUndefinedAnnotation.
ReferentMapping.model_rebuild()
LinkMapping.model_rebuild()