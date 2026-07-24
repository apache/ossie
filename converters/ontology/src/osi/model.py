from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Builtin concept names.
# ---------------------------------------------------------------------------

BUILTIN_CONCEPTS: frozenset[str] = frozenset({
    "Any", "AnyEntity", "Boolean", "Date", "DateTime", "Decimal", "Float", "Integer", "String"
})

# ---------------------------------------------------------------------------
# Free-form metadata mirroring spec
# ---------------------------------------------------------------------------

AiContext = str | dict[str, Any]


@dataclass
class CustomExtension:
    vendor_name: str
    data: str


# ---------------------------------------------------------------------------
# Ontology (concepts + relationships grouped by container)
# ---------------------------------------------------------------------------

class ConceptType(str, Enum):
    ENTITY_TYPE = "EntityType"
    VALUE_TYPE = "ValueType"

    @classmethod
    def from_value(cls, value: str | None) -> ConceptType | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        for member in cls:
            if member.value == value:
                return member
        raise ValueError(f"Unknown concept type: {value}")


class RelationshipMultiplicity(str, Enum):
    """Spec-level multiplicity declared on a relationship.

    Allows OneToOne or ManyToOne (ManyToMany is no longer expressible
    at the spec level — it becomes the default 'unconstrained' case).
    """
    ONE_TO_ONE = "OneToOne"
    MANY_TO_ONE = "ManyToOne"

    @classmethod
    def from_value(cls, value: str | None) -> RelationshipMultiplicity | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        normalized = value.strip().lower()
        for member in cls:
            if member.value.lower() == normalized:
                return member
        raise ValueError(f"Unknown relationship multiplicity value: {value}")


class Concept:
    """Type-like ontology node. May be an EntityType (real-world object,
    referenced via identifying relationships) or a ValueType (primitive-ish,
    transitively extending a built-in value type)."""
    _name: str
    _type: ConceptType | None
    _description: str | None
    _builtin: bool
    _extends: list[Concept]
    _identify_by: dict[str, Relationship]
    _derived_by: list[Formula]
    _requires: list[Formula]
    _is_component: bool

    def __init__(
        self,
        name: str,
        type: ConceptType | None = None,
        description: str | None = None,
        builtin: bool = False,
        extends: list[Concept] | None = None,
        identify_by: dict[str, Relationship] | None = None,
        derived_by: list[Formula] | None = None,
        requires: list[Formula] | None = None,
        is_component: bool = True
    ):
        self._name = name
        self._type = type
        self._description = description
        self._builtin = builtin
        self._extends = extends if extends else []
        self._identify_by = identify_by if identify_by else {}
        self._derived_by = derived_by if derived_by else []
        self._requires = requires if requires else []
        self._is_component = is_component

    def add_require(self, require: Formula) -> None:
        self._requires.append(require)

    def add_derived_by(self, rule: Formula) -> None:
        self._derived_by.append(rule)

    def set_identify_by(self, identifiers: dict[str, Relationship]) -> None:
        self._identify_by = identifiers

    def extend(self, parent: Concept) -> None:
        self._extends.append(parent)

    @property
    def name(self) -> str:
        return self._name

    @property
    def type(self) -> ConceptType | None:
        return self._type

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def is_builtin(self) -> bool:
        return self._builtin

    # True if this concept identifies a concept component in an OSI ontology
    @property
    def is_component(self) -> bool:
        return self._is_component

    @property
    def is_value_type(self) -> bool:
        return self._type == ConceptType.VALUE_TYPE

    @property
    def is_entity_type(self) -> bool:
        return self._type == ConceptType.ENTITY_TYPE

    @property
    def is_primitive(self) -> bool:
        if self.is_builtin:
            return True
        if self._extends and len(self._extends) == 1:
            return self._extends[0].is_primitive
        return False

    @property
    def is_derived(self) -> bool:
        return bool(self._derived_by)

    @property
    def extends(self) -> list[Concept]:
        return list(self._extends)

    @property
    def identify_by(self) -> dict[str, Relationship]:
        return dict(self._identify_by)

    @property
    def derived_by(self) -> list[Formula]:
        return list(self._derived_by)

    @property
    def requires(self) -> list[Formula]:
        return list(self._requires)

    def __str__(self) -> str:
        return self._name


class Relationship:
    """A relationship grouped under its first-role concept (the container).
    In this model class we choose to store all the roles explicitly, including the first implicit role from the OSI spec.
    """
    _name: str
    _container: Concept
    _roles: tuple[Role, ...]
    _description: str | None
    _verbalizes_raw: list[str] | None
    _verbalizations: list[RelationshipVerbalization]
    _multiplicity: RelationshipMultiplicity | None
    _derived_by: list[Formula]
    _requires: list[Formula]

    def __init__(
        self,
        name: str,
        container: Concept,
        relates: list[tuple[Concept, str | None]],
        description: str | None = None,
        verbalizes: list[str] | None = None,
        multiplicity: RelationshipMultiplicity | None = None,
    ):
        self._name = name
        self._container = container
        container_role = Role(self, container, 0, None)
        additional = [Role(self, concept, idx + 1, role_name) for idx, (concept, role_name) in enumerate(relates)]
        self._roles = tuple([container_role] + additional)
        self._description = description
        self._multiplicity = multiplicity
        self._verbalizes_raw = list(verbalizes) if verbalizes else None
        self._verbalizations = parse_verbalizations(self, verbalizes)
        self._derived_by = []
        self._requires = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def full_name(self) -> str:
        return f"{self._container.name}.{self._name}"

    @property
    def container(self) -> Concept:
        return self._container

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def signature(self) -> list[Concept]:
        return [role.player for role in self._roles]

    @property
    def arity(self) -> int:
        return len(self._roles)

    @property
    def binary(self) -> bool:
        return self.arity == 2

    @property
    def unary(self) -> bool:
        return self.arity == 1

    def role(self, pos: int | Concept | str) -> Role:
        if isinstance(pos, int):
            return self._roles[pos]
        if isinstance(pos, Concept):
            for role in self._roles:
                if role.player == pos:
                    return role
        elif isinstance(pos, str):
            for role in self._roles:
                if role.name == pos:
                    return role
        raise ValueError(f"Role '{pos}' not found in relationship '{self.full_name}'")

    @property
    def roles(self) -> tuple[Role, ...]:
        return self._roles

    def set_multiplicity(self, mult: RelationshipMultiplicity) -> None:
        if self._multiplicity is not None and self._multiplicity != mult:
            raise ValueError(
                f"Conflicting multiplicity settings for relationship {self}: "
                f"{self._multiplicity} and {mult}"
            )
        self._multiplicity = mult

    @property
    def first_role(self) -> Role:
        return self._roles[0]

    @property
    def last_role(self) -> Role:
        return self._roles[-1]

    @property
    def verbalizations(self) -> list[RelationshipVerbalization]:
        return self._verbalizations

    @property
    def verbalizes_raw(self) -> list[str] | None:
        return self._verbalizes_raw

    @property
    def multiplicity(self) -> RelationshipMultiplicity | None:
        return self._multiplicity

    @property
    def derived_by(self) -> list[Formula]:
        return list(self._derived_by)

    @property
    def requires(self) -> list[Formula]:
        return list(self._requires)

    def add_derived_by(self, rule: Formula) -> None:
        self._derived_by.append(rule)

    def add_require(self, rule: Formula) -> None:
        self._requires.append(rule)

    def __str__(self) -> str:
        return self._name


class Role:
    _part_of: Relationship
    _player: Concept
    _name: str | None
    _sibling: Role | None
    _idx: int

    def __init__(self, part_of: Relationship, player: Concept, idx: int, name: str | None = None):
        self._part_of = part_of
        self._player = player
        self._idx = idx
        self._name = name
        self._sibling = None

    @property
    def player(self) -> Concept:
        return self._player

    @property
    def idx(self) -> int:
        return self._idx

    @property
    def name(self) -> str:
        return self._name or self._player.name

    @property
    def explicit_name(self) -> str | None:
        return self._name

    @property
    def part_of(self) -> Relationship:
        return self._part_of

    @property
    def sibling(self) -> Role | None:
        if self._part_of.binary and not self._sibling:
            first_role, second_role = self._part_of.roles
            sibling = second_role if self == first_role else first_role
            self._sibling = sibling
        return self._sibling

    @property
    def madlib(self) -> str:
        return f"{self._player.name}:{self._name}" if self._name else self._player.name

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Role):
            return False
        return (
            self._part_of == other._part_of
            and self._player == other._player
            and self._name == other._name
        )

    def __hash__(self) -> int:
        return hash((self._part_of, self._player, self._name))


# ---------------------------------------------------------------------------
# Formula — raw expression string only.
# ---------------------------------------------------------------------------

FormulaParent = Concept | Relationship | tuple[Concept, Relationship] | None


class Formula:
    _raw_expr: str
    _parent: FormulaParent

    def __init__(self, raw_expr: str, parent: FormulaParent = None):
        self._raw_expr = raw_expr
        self._parent = parent

    @property
    def raw_expr(self) -> str:
        return self._raw_expr

    @property
    def parent(self) -> FormulaParent:
        return self._parent

    def __str__(self) -> str:
        return self._raw_expr


class FormulaFactory:
    """Base factory for ontology-level formulas (derived_by / requires on concepts and relationships).

    Subclass and override __call__ to return an enriched Formula subclass,
    e.g. one carrying an AST produced by a FormulaParser.
    """

    def __call__(self, raw_expr: str, parent: FormulaParent = None, ontology: OntologyComponent | None = None) -> Formula:
        return Formula(raw_expr=raw_expr, parent=parent)


class MappingFormulaFactory:
    """Base factory for mapping-level formulas (object / referent mapping expressions).

    Receives both the ontology and the semantic model so that name resolution
    and field-level validation can be performed against the full mapping context.

    Subclass and override __call__ to return an enriched Formula subclass.
    """

    def __call__(self, raw_expr: str, parent: FormulaParent = None,
                 ontology: OntologyComponent | None = None, semantic_model: SemanticModel | None = None) -> Formula:
        return Formula(raw_expr=raw_expr, parent=parent)


# ---------------------------------------------------------------------------
# Semantic model (datasets, join paths, metrics)
# ---------------------------------------------------------------------------

@dataclass
class DialectExpression:
    dialect: str
    expression: str


@dataclass
class DialectExpressionSet:
    """Runtime equivalent of spec.Expression — same logical expression rendered
    in one or more dialects."""
    dialects: list[DialectExpression] = field(default_factory=list)

    def by_dialect(self, dialect: str) -> DialectExpression | None:
        for d in self.dialects:
            if d.dialect == dialect:
                return d
        return None

    @property
    def primary(self) -> DialectExpression | None:
        return self.dialects[0] if self.dialects else None


@dataclass
class Dimension:
    is_time: bool | None = None


@dataclass
class DatasetField:
    name: str
    expression: DialectExpressionSet
    type: Concept | None = None
    dimension: Dimension | None = None
    label: str | None = None
    description: str | None = None
    ai_context: AiContext | None = None
    custom_extensions: list[CustomExtension] = field(default_factory=list)
    # Back-reference to the owning Dataset, wired by Dataset.__init__. Used by
    # mapping-expression rendering to reconstruct `<dataset>.<field>` strings
    # for round-trip output. Not in the spec — purely runtime metadata.
    dataset: "Dataset | None" = field(default=None, repr=False, compare=False)

    def __str__(self) -> str:
        return self.name


def sanitize_identifier(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", ref)


class Dataset:
    _name: str
    _source: str
    _primary_key: list[str] | None
    _unique_keys: list[list[str]] | None
    _description: str | None
    _ai_context: AiContext | None
    _fields: list[DatasetField]
    _custom_extensions: list[CustomExtension]
    _field_name_map: dict[str, DatasetField]

    def __init__(
        self,
        name: str,
        source: str,
        fields: list[DatasetField],
        primary_key: list[str] | None = None,
        unique_keys: list[list[str]] | None = None,
        description: str | None = None,
        ai_context: AiContext | None = None,
        custom_extensions: list[CustomExtension] | None = None,
    ):
        self._name = name
        self._source = source
        self._fields = fields
        self._primary_key = primary_key
        self._unique_keys = unique_keys
        self._description = description
        self._ai_context = ai_context
        self._custom_extensions = custom_extensions or []
        self._field_name_map = {fl.name: fl for fl in fields}
        # Wire the back-reference so each field knows its owning Dataset —
        # the mapping-expression renderer needs it to reconstruct
        # `<dataset>.<field>` strings on reverse conversion.
        for fl in fields:
            fl.dataset = self

    @property
    def name(self) -> str:
        return self._name

    @property
    def source(self) -> str:
        return self._source

    @property
    def primary_key(self) -> list[str] | None:
        return self._primary_key

    @property
    def unique_keys(self) -> list[list[str]] | None:
        return self._unique_keys

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def ai_context(self) -> AiContext | None:
        return self._ai_context

    def field(self, name: str) -> DatasetField | None:
        return self._field_name_map.get(name)

    @property
    def fields(self) -> list[DatasetField]:
        return list(self._fields)

    @property
    def custom_extensions(self) -> list[CustomExtension]:
        return list(self._custom_extensions)

    @property
    def schema(self) -> dict[str, Concept | None]:
        return {fl.name: fl.type for fl in self._fields}

    def __str__(self) -> str:
        return self._name


class JoinPath:
    """Runtime equivalent of spec.JoinPath — a foreign-key style join
    between two Datasets, matching `from_columns` against `to_columns`."""
    _name: str
    _from_dataset: Dataset
    _to_dataset: Dataset
    _from_columns: list[DatasetField]
    _to_columns: list[DatasetField]
    _ai_context: AiContext | None
    _custom_extensions: list[CustomExtension]

    def __init__(
        self,
        name: str,
        from_dataset: Dataset,
        to_dataset: Dataset,
        from_columns: list[DatasetField],
        to_columns: list[DatasetField],
        ai_context: AiContext | None = None,
        custom_extensions: list[CustomExtension] | None = None,
    ):
        if len(from_columns) != len(to_columns):
            raise ValueError(
                f"JoinPath '{name}': from_columns/to_columns arity mismatch "
                f"({len(from_columns)} vs {len(to_columns)})"
            )
        self._name = name
        self._from_dataset = from_dataset
        self._to_dataset = to_dataset
        self._from_columns = from_columns
        self._to_columns = to_columns
        self._ai_context = ai_context
        self._custom_extensions = custom_extensions or []

    @property
    def name(self) -> str:
        return self._name

    @property
    def from_dataset(self) -> Dataset:
        return self._from_dataset

    @property
    def to_dataset(self) -> Dataset:
        return self._to_dataset

    @property
    def from_columns(self) -> list[DatasetField]:
        return list(self._from_columns)

    @property
    def to_columns(self) -> list[DatasetField]:
        return list(self._to_columns)

    @property
    def ai_context(self) -> AiContext | None:
        return self._ai_context

    @property
    def custom_extensions(self) -> list[CustomExtension]:
        return list(self._custom_extensions)

    def __str__(self) -> str:
        return self._name


class Metric:
    """Logical-model-level metric defined as a multi-dialect aggregate expression."""
    _name: str
    _expression: DialectExpressionSet
    _description: str | None
    _ai_context: AiContext | None
    _custom_extensions: list[CustomExtension]

    def __init__(
        self,
        name: str,
        expression: DialectExpressionSet,
        description: str | None = None,
        ai_context: AiContext | None = None,
        custom_extensions: list[CustomExtension] | None = None,
    ):
        self._name = name
        self._expression = expression
        self._description = description
        self._ai_context = ai_context
        self._custom_extensions = custom_extensions or []

    @property
    def name(self) -> str:
        return self._name

    @property
    def expression(self) -> DialectExpressionSet:
        return self._expression

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def ai_context(self) -> AiContext | None:
        return self._ai_context

    @property
    def custom_extensions(self) -> list[CustomExtension]:
        return list(self._custom_extensions)


class SemanticModel:
    """Bundle of datasets, join paths and metrics. One or more SemanticModels
    can feed a single OntologyMapping (see spec)."""
    _name: str
    _description: str | None
    _ai_context: AiContext | None
    _datasets: list[Dataset]
    _join_paths: list[JoinPath]
    _metrics: list[Metric]
    _custom_extensions: list[CustomExtension]
    _dataset_name_map: dict[str, Dataset]
    _join_path_name_map: dict[str, JoinPath]
    _metric_name_map: dict[str, Metric]

    def __init__(
        self,
        name: str,
        description: str | None = None,
        ai_context: AiContext | None = None,
        custom_extensions: list[CustomExtension] | None = None,
    ):
        self._name = name
        self._description = description
        self._ai_context = ai_context
        self._datasets = []
        self._join_paths = []
        self._metrics = []
        self._custom_extensions = custom_extensions or []
        self._dataset_name_map = {}
        self._join_path_name_map = {}
        self._metric_name_map = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def ai_context(self) -> AiContext | None:
        return self._ai_context

    @property
    def datasets(self) -> list[Dataset]:
        return list(self._datasets)

    @property
    def join_paths(self) -> list[JoinPath]:
        return list(self._join_paths)

    @property
    def metrics(self) -> list[Metric]:
        return list(self._metrics)

    @property
    def custom_extensions(self) -> list[CustomExtension]:
        return list(self._custom_extensions)

    def add_dataset(self, dataset: Dataset) -> None:
        if dataset.name in self._dataset_name_map:
            raise ValueError(f"Dataset '{dataset.name}' already exists in logical model '{self._name}'")
        self._datasets.append(dataset)
        self._dataset_name_map[dataset.name] = dataset

    def add_join_path(self, join_path: JoinPath) -> None:
        if join_path.name in self._join_path_name_map:
            raise ValueError(f"JoinPath '{join_path.name}' already exists in logical model '{self._name}'")
        self._join_paths.append(join_path)
        self._join_path_name_map[join_path.name] = join_path

    def add_metric(self, metric: Metric) -> None:
        if metric.name in self._metric_name_map:
            raise ValueError(f"Metric '{metric.name}' already exists in logical model '{self._name}'")
        self._metrics.append(metric)
        self._metric_name_map[metric.name] = metric

    def lookup_dataset(self, name: str) -> Dataset | None:
        return self._dataset_name_map.get(name)

    def lookup_join_path(self, name: str) -> JoinPath | None:
        return self._join_path_name_map.get(name)

    def lookup_metric(self, name: str) -> Metric | None:
        return self._metric_name_map.get(name)


# ---------------------------------------------------------------------------
# Ontology mapping (tree-shaped)
# ---------------------------------------------------------------------------

@dataclass
class ObjectMapping:
    """Maps to objects of some concept — either a direct `expression` (value
    types / simple-id entities) or `referent_mappings` (compound id). XOR —
    never both.

    `expression` carries the *parsed* mapping expression: a `DatasetField`
    when it resolves to a single field reference, or a `Formula` for richer
    expressions. The forward converter parses the spec's raw string and the
    reverse converter reconstructs it — storing the parsed form rather than the
    raw string lets callers introspect the mapping target."""
    concept: Concept | None = None
    expression: DatasetField | Formula | None = None
    referent_mappings: list[ReferentMapping] | None = None

    def __post_init__(self) -> None:
        has_expr = self.expression is not None
        has_refs = self.referent_mappings is not None
        if has_expr and has_refs:
            raise ValueError("ObjectMapping must not have both expression and referent_mappings")
        if not has_expr and not has_refs:
            raise ValueError("ObjectMapping must have either expression or referent_mappings")


@dataclass
class ReferentMapping:
    """Locates an entity object by walking one of its identifying relationships.

    `expression`, like ObjectMapping's, is the parsed result — a `DatasetField`
    for simple references or a `Formula` for richer expressions. Nested
    `referent_mappings` descend into compound identifiers."""
    relationship: Relationship
    expression: DatasetField | Formula | None = None
    referent_mappings: list[ReferentMapping] | None = None

    def __post_init__(self) -> None:
        has_expr = self.expression is not None
        has_refs = self.referent_mappings is not None
        if has_expr and has_refs:
            raise ValueError("ReferentMapping must not have both expression and referent_mappings")
        if not has_expr and not has_refs:
            raise ValueError("ReferentMapping must have either expression or referent_mappings")


@dataclass
class LinkMapping:
    """A node in the link-mapping tree. The arity of `relationship` equals the
    node's depth (top-level = unary, depth 2 = binary, ...). `children` extend
    the mapped tuple by one role each, sharing this node's `object_mapping`."""
    object_mapping: ObjectMapping
    relationship: Relationship | None = None
    children: list[LinkMapping] | None = None


@dataclass
class ConceptMapping:
    """Mappings that populate one concept and the relationships under it."""
    concept: Concept
    object_mappings: list[ObjectMapping] = field(default_factory=list)
    link_mappings: list[LinkMapping] = field(default_factory=list)


class OntologyMapping:
    """Binds a logical model to an ontology and declares how its fields
    populate the ontology's concepts and relationships."""
    _name: str
    _description: str | None
    _ontology: OntologyComponent
    _semantic_model: SemanticModel
    _concept_mappings: list[ConceptMapping]

    def __init__(
        self,
        name: str,
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        description: str | None = None,
    ):
        self._name = name
        self._description = description
        self._ontology = ontology
        self._semantic_model = semantic_model
        self._concept_mappings = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def ontology(self) -> OntologyComponent:
        return self._ontology

    @property
    def semantic_model(self) -> SemanticModel:
        return self._semantic_model

    @property
    def concept_mappings(self) -> list[ConceptMapping]:
        return list(self._concept_mappings)

    def add_concept_mapping(self, cm: ConceptMapping) -> None:
        self._concept_mappings.append(cm)


# ---------------------------------------------------------------------------
# Observer protocol + Ontology component (mirrors OntologyComponent in spec)
# ---------------------------------------------------------------------------

class OntologyObserver(Protocol):
    """Structural interface for objects that want to be notified when concepts
    or requires are added to an OntologyComponent.  Implement both methods
    and pass an instance to OntologyComponent.register()."""

    def on_concept_added(self, concept: Concept) -> None: ...

    def on_require_added(self, require: Formula) -> None: ...


class OntologyComponent:
    """Structural container for concepts, relationships, constraints, and rules.
    Document-level metadata (name, description, ai_context) lives on OsiOntology."""
    _concepts: list[Concept]
    _relationships: list[Relationship]
    _requires: list[Formula]
    _concept_name_map: dict[str, Concept]
    _relationship_name_map: dict[str, Relationship]
    _observers: list[OntologyObserver]

    def __init__(self):
        self._concepts = []
        self._relationships = []
        self._requires = []
        self._concept_name_map = {}
        self._relationship_name_map = {}
        self._observers = []

    def register(self, observer: OntologyObserver) -> None:
        self._observers.append(observer)
        for concept in self._concepts:
            observer.on_concept_added(concept)
        for require in self._requires:
            observer.on_require_added(require)

    def add_concept(self, concept: Concept) -> None:
        if concept.name in self._concept_name_map:
            raise ValueError(f"Concept '{concept.name}' already exists in the ontology")
        self._concepts.append(concept)
        self._concept_name_map[concept.name] = concept
        for obs in self._observers:
            obs.on_concept_added(concept)

    def add_relationship(self, relationship: Relationship) -> None:
        full_name = relationship.full_name
        if full_name in self._relationship_name_map:
            raise ValueError(f"Relationship '{full_name}' already exists in the ontology")
        self._relationships.append(relationship)
        self._relationship_name_map[full_name] = relationship

    def add_require(self, require: Formula) -> None:
        self._requires.append(require)
        for obs in self._observers:
            obs.on_require_added(require)

    def concepts(self, exclude_builtin: bool = False) -> list[Concept]:
        if exclude_builtin:
            return [c for c in self._concepts if not c.is_builtin]
        return list(self._concepts)

    @property
    def relationships(self) -> list[Relationship]:
        return list(self._relationships)

    @property
    def requires(self) -> list[Formula]:
        return list(self._requires)

    def lookup_concept(self, name: str | None) -> Concept | None:
        if not name:
            return None
        if name in self._concept_name_map:
            return self._concept_name_map[name]
        if name in BUILTIN_CONCEPTS:
            concept = Concept(name=name, builtin=True, is_component=False)
            self.add_concept(concept)
            return concept
        return None

    def lookup_concept_relationship(self, concept: Concept, name: str) -> Relationship | None:
        rel = self._relationship_name_map.get(f"{concept.name}.{name}")
        if rel:
            return rel
        for ext in concept.extends:
            rel = self.lookup_concept_relationship(ext, name)
            if rel:
                return rel
        return None


# ---------------------------------------------------------------------------
# Root semantic model (per OsiSpec)
# ---------------------------------------------------------------------------

class OsiOntology:
    _name: str
    _description: str | None
    _ai_context: AiContext | None
    _version: str | None
    _ontology: OntologyComponent
    _ontology_mappings: list[OntologyMapping]
    _ontology_mapping_index: dict[str, OntologyMapping]

    def __init__(
        self,
        name: str,
        ontology: OntologyComponent,
        description: str | None = None,
        ai_context: AiContext | None = None,
        version: str | None = None,
    ):
        self._name = name
        self._description = description
        self._ai_context = ai_context
        self._version = version
        self._ontology = ontology
        self._ontology_mappings = []
        self._ontology_mapping_index = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str | None:
        return self._description

    @property
    def ai_context(self) -> AiContext | None:
        return self._ai_context

    @property
    def version(self) -> str | None:
        return self._version

    @property
    def ontology(self) -> OntologyComponent:
        return self._ontology

    def add_ontology_mapping(self, mapping: OntologyMapping) -> None:
        if mapping.name in self._ontology_mapping_index:
            raise ValueError(f"OntologyMapping '{mapping.name}' already exists in model")
        self._ontology_mappings.append(mapping)
        self._ontology_mapping_index[mapping.name] = mapping

    @property
    def ontology_mappings(self) -> list[OntologyMapping]:
        return list(self._ontology_mappings)


# ---------------------------------------------------------------------------
# Verbalization parser (handles a list of verbalization patterns)
# ---------------------------------------------------------------------------

@dataclass
class Verbalization:
    text: str


@dataclass
class RelationshipVerbalization(Verbalization):
    roles: list[VerbalizationRole]


@dataclass
class VerbalizationRole:
    concept: Concept
    name: str | None = None
    preceding_text: str | None = None
    prefix: str | None = None
    following_text: str | None = None
    postfix: str | None = None

    def verbalization_name(self) -> str:
        return f"{{{self.concept.name}:{self.name}}}" if self.name else f"{{{self.concept.name}}}"


_CONCEPT_TOKEN_RE = re.compile(r"\{([^:}]+?)(?::([^}]+))?\}")


def parse_verbalizations(
    relationship: Relationship, verbalizations: list[str] | None
) -> list[RelationshipVerbalization]:
    if not verbalizations:
        return [_build_verbalization(relationship)]
    return [_parse_verbalization(relationship, v) for v in verbalizations]


def _build_verbalization(relationship: Relationship) -> RelationshipVerbalization:
    roles: list[VerbalizationRole] = []
    parts: list[str] = []
    for role in relationship.roles:
        vr = VerbalizationRole(concept=role.player, name=role.explicit_name)
        roles.append(vr)
        parts.append(vr.verbalization_name())
    if relationship.unary:
        return RelationshipVerbalization(text=f"{relationship.name} {parts[0]}", roles=roles)
    return RelationshipVerbalization(text=" has ".join(parts), roles=roles)


def _parse_verbalization(relationship: Relationship, verbalization: str) -> RelationshipVerbalization:
    """
        Parse a verbalization string into an ordered list of :class:`VerbalizationRole` objects.

        Format example:

            'every chain- super {Store} reports returns of {Item} big -box for average- {Amount:amt}'

        The string may contain any number of ``{Concept}`` / ``{Concept:roleName}`` tokens.
        Text between tokens is split uniformly by :func:`_split_segment` into
        ``(postfix, middle, prefix)`` and assigned to the adjacent roles:

        +-----------+------------------------+------------------------------+----------------------+
        | position  | postfix                | middle                       | prefix               |
        +===========+========================+==============================+======================+
        | segment 0 | *ignored*              | → roles[0].preceding_text    | → roles[0].prefix    |
        +-----------+------------------------+------------------------------+----------------------+
        | segment i | → roles[i-1].postfix   | → roles[i-1].following_text  | → roles[i].prefix    |
        +-----------+------------------------+------------------------------+----------------------+
        | last seg  | → roles[-1].postfix    | → roles[-1].following_text   | *ignored*            |
        +-----------+------------------------+------------------------------+----------------------+
        """
    tokens = list(_CONCEPT_TOKEN_RE.finditer(verbalization))
    if len(tokens) != relationship.arity:
        raise ValueError(
            f"Number of roles in verbalization '{verbalization}' for relationship "
            f"{relationship.full_name} doesn't match its arity ({relationship.arity})"
        )
    segments: list[str] = []
    roles: list[VerbalizationRole] = []
    prev_end = 0
    for idx, m in enumerate(tokens):
        role = relationship.role(idx)
        segments.append(verbalization[prev_end:m.start()].strip())
        verb_concept_name = m.group(1).strip()
        rel_role_name = role.explicit_name
        verb_role_name = m.group(2).strip() if m.group(2) else None
        if rel_role_name != verb_role_name or role.player.name != verb_concept_name:
            raise ValueError(
                f"Role {idx}: '{role.player.name}:{role.name}' "
                f"does not match verbalization role '{verb_concept_name}:{verb_role_name}'"
            )
        roles.append(VerbalizationRole(concept=role.player, name=verb_role_name))
        prev_end = m.end()
    segments.append(verbalization[prev_end:].strip())
    for i, seg in enumerate(segments):
        if not seg:
            continue
        postfix, middle, prefix = _split_segment(seg)
        if i == 0:
            roles[0].preceding_text = middle
            roles[0].prefix = prefix
        elif i == len(tokens):
            roles[-1].postfix = postfix
            roles[-1].following_text = middle
        else:
            roles[i - 1].postfix = postfix
            roles[i - 1].following_text = middle
            roles[i].prefix = prefix
    return RelationshipVerbalization(text=verbalization, roles=roles)


def _split_segment(segment: str) -> tuple[str | None, str | None, str | None]:
    """Split a text segment into ``(postfix, middle, prefix)`` using the dash
    conventions (dashes are stripped from the returned text):

    - **postfix** — text glued to the *preceding* concept. A leading-dash word
      (e.g. ``-box``) marks the segment's opening run as a postfix: the first
      word plus any following dash-prefixed words (so ``"big -box"`` -> ``"big box"``).
    - **prefix** — text glued to the *following* concept. A trailing-dash word
      (e.g. ``chain-``) marks the start of the closing run (so ``"chain- super"``
      -> ``"chain super"``).
    - **middle** — the free text left between the postfix and prefix runs.

    See tests/test_verbalization_parsing.py for the full set of expected splits.
    """
    words = segment.split()
    if not words:
        return None, None, None

    # Postfix run: first word plus any following dash-prefixed words, but only
    # when the segment actually contains a leading-dash marker somewhere.
    postfix_end = 0
    if any(w.startswith("-") for w in words):
        postfix_end = 1
        while postfix_end < len(words) and words[postfix_end].startswith("-"):
            postfix_end += 1

    # Prefix run: begins at the first trailing-dash word (after the postfix run)
    # and extends to the end of the segment.
    prefix_start = len(words)
    for i in range(postfix_end, len(words)):
        if words[i].endswith("-"):
            prefix_start = i
            break

    postfix = " ".join(w.lstrip("-") for w in words[:postfix_end]) if postfix_end > 0 else None
    prefix = " ".join(w.rstrip("-") for w in words[prefix_start:]) if prefix_start < len(words) else None
    middle = " ".join(words[postfix_end:prefix_start]) if postfix_end < prefix_start else None

    return postfix, middle, prefix
