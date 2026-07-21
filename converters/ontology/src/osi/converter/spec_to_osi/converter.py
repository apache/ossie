"""Converter from OsiSpec (Pydantic DTOs) to OsiOntology (runtime semantic model)."""

from __future__ import annotations

import re

from osi.common.graph import topological_sort
from osi.model import (
    Concept,
    ConceptMapping,
    ConceptType,
    CustomExtension,
    Dataset,
    DatasetField,
    DialectExpression,
    DialectExpressionSet,
    Dimension,
    Formula,
    FormulaFactory,
    MappingFormulaFactory,
    JoinPath,
    LinkMapping,
    SemanticModel,
    Metric,
    ObjectMapping,
    OntologyComponent,
    OntologyMapping,
    ReferentMapping,
    Relationship,
    RelationshipMultiplicity,
    OsiOntology,
    BUILTIN_CONCEPTS
)
from osi.spec import (
    Concept as SpecConcept,
    ConceptMapping as SpecConceptMapping,
    CustomExtension as SpecCustomExtension,
    Dataset as SpecDataset,
    DatasetField as SpecDatasetField,
    DialectExpression as SpecDialectExpression,
    Dimension as SpecDimension,
    Expression as SpecExpression,
    JoinPath as SpecJoinPath,
    LinkMapping as SpecLinkMapping,
    SemanticModel as SpecSemanticModel,
    Metric as SpecMetric,
    ObjectMapping as SpecObjectMapping,
    OntologyMapping as SpecOntologyMapping,
    OsiSpec,
    ReferentMapping as SpecReferentMapping,
    Relationship as SpecRelationship,
)
Container = Concept | Relationship

# A mapping expression is treated as a single field reference when it matches
# `DATASET.field` or a bare `field` identifier — no parsing, just a pattern check.
_QUALIFIED_FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")
_BARE_FIELD_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*$")


class SpecToOsiConverter:
    """Converts OsiSpec (Pydantic DTOs) to OsiOntology (runtime model).

    Pass a *formula_factory* to control how Formula objects are created.
    The default produces plain ``Formula`` instances; downstream packages can
    inject a factory that returns enriched subclasses (e.g. with an AST).

        model = SpecToOsiConverter().convert(spec)
        model = SpecToOsiConverter(formula_factory=my_parser).convert(spec)
    """

    def __init__(self, formula_factory: FormulaFactory | None = None,
                 mapping_formula_factory: MappingFormulaFactory | None = None):
        self._formula_factory = formula_factory or FormulaFactory()
        self._mapping_formula_factory = mapping_formula_factory or MappingFormulaFactory()

    def convert(self, spec: OsiSpec) -> OsiOntology:
        ontology = OntologyComponent()
        model = OsiOntology(
            name=spec.name,
            ontology=ontology,
            description=spec.description,
            ai_context=spec.ai_context,
            version=spec.version,
        )

        self._populate_ontology(ontology, spec)

        for om_spec in spec.ontology_mappings:
            self._convert_ontology_mapping(model, om_spec)

        return model

    # ----- Ontology ------------------------------------------------------

    def _populate_ontology(self, ontology: OntologyComponent, spec: OsiSpec) -> None:

        concept_specs = {concept_component.concept.name: concept_component.concept for concept_component in spec.ontology}
        sorted_names = self._sort_spec_dependency_graph(list(concept_specs.values()))
        for name in sorted_names:
            concept_spec = concept_specs[name]
            extends: list[Concept] = []
            if concept_spec.extends:
                for ext in concept_spec.extends:
                    parent = ontology.lookup_concept(ext)
                    if not parent:
                        raise ValueError(
                            f"Subtype '{ext}' is not declared in ontology '{spec.name}'."
                        )
                    extends.append(parent)
            ontology.add_concept(
                Concept(
                    name=concept_spec.name,
                    type=ConceptType.from_value(concept_spec.type),
                    description=concept_spec.description,
                    extends=extends,
                )
            )

        for concept_component in spec.ontology:
            container = ontology.lookup_concept(concept_component.concept.name)
            if container is None:
                raise ValueError(f"Internal: container concept '{concept_component.concept.name}' not found")
            for rel_spec in concept_component.relationships:
                self._convert_relationship(ontology, container, rel_spec)

        # Identifiers: now that all relationships exist, resolve identify_by.
        for concept_component in spec.ontology:
            concept_spec = concept_component.concept
            concept = ontology.lookup_concept(concept_spec.name)
            if concept is None:
                continue
            identifiers: dict[str, Relationship] = {}
            for ref_name in concept_spec.identify_by:
                rel = ontology.lookup_concept_relationship(concept, ref_name)
                if rel is None:
                    raise ValueError(
                        f"identify_by '{ref_name}' on concept '{concept.name}' refers to an "
                        f"unknown relationship in ontology '{spec.name}'."
                    )
                identifiers[rel.full_name] = rel
            concept.set_identify_by(identifiers)

        # Formulas: derived_by + requires (after concepts/relationships exist).
        for concept_component in spec.ontology:
            concept_spec = concept_component.concept
            concept = ontology.lookup_concept(concept_spec.name)
            if concept is None:
                continue
            for raw in concept_spec.requires:
                req = self._build_rule(raw, concept, ontology)
                if req:
                    concept.add_require(req)
            for raw in concept_spec.derived_by:
                rule = self._build_rule(raw, concept, ontology)
                if rule:
                    concept.add_derived_by(rule)
            for rel_spec in concept_component.relationships:
                rel = ontology.lookup_concept_relationship(concept, rel_spec.name)
                if rel is None:
                    continue
                for raw in rel_spec.requires:
                    req = self._build_rule(raw, rel, ontology)
                    if req:
                        rel.add_require(req)
                for raw in rel_spec.derived_by:
                    rule = self._build_rule(raw, rel, ontology)
                    if rule:
                        rel.add_derived_by(rule)

        # Ontology-level requires are not scoped to any concept/relationship;
        # they attach directly to the ontology component.
        for raw in spec.requires:
            req = self._build_rule(raw, None, ontology)
            if req:
                ontology.add_require(req)

    def _convert_relationship(
        self, ontology: OntologyComponent, container: Concept, rel_spec: SpecRelationship
    ) -> None:
        relates: list[tuple[Concept, str | None]] = []
        for role_spec in rel_spec.roles:
            role_concept = ontology.lookup_concept(role_spec.concept)
            if role_concept is None:
                raise ValueError(
                    f"Role concept '{role_spec.concept}' in relationship '{container.name}.{rel_spec.name}' "
                    f"is not declared in the ontology."
                )
            relates.append((role_concept, role_spec.name))

        multiplicity = RelationshipMultiplicity.from_value(rel_spec.multiplicity)
        relationship = Relationship(
            name=rel_spec.name,
            container=container,
            relates=relates,
            description=rel_spec.description,
            verbalizes=list(rel_spec.verbalizes) if rel_spec.verbalizes else None,
            multiplicity=multiplicity,
        )
        ontology.add_relationship(relationship)

    # ----- Logical model -------------------------------------------------

    def _convert_semantic_model(self, lm_spec: SpecSemanticModel) -> SemanticModel:
        semantic_model = SemanticModel(
            name=lm_spec.name,
            description=lm_spec.description,
            ai_context=lm_spec.ai_context,
            custom_extensions=[
                _convert_custom_extension(ce) for ce in lm_spec.custom_extensions
            ],
        )
        for ds_spec in lm_spec.datasets:
            semantic_model.add_dataset(_convert_dataset(ds_spec))
        for jp_spec in lm_spec.relationships:
            semantic_model.add_join_path(_convert_join_path(jp_spec, semantic_model))
        for m_spec in lm_spec.metrics:
            semantic_model.add_metric(_convert_metric(m_spec))
        return semantic_model

    # ----- Ontology mapping ---------------------------------------------

    def _convert_ontology_mapping(self, model: OsiOntology, om_spec: SpecOntologyMapping) -> None:
        ontology = model.ontology

        semantic_model = self._convert_semantic_model(om_spec.semantic_model)

        mapping = OntologyMapping(
            name=om_spec.name,
            ontology=ontology,
            semantic_model=semantic_model,
            description=om_spec.description,
        )
        model.add_ontology_mapping(mapping)

        for cm_spec in om_spec.concept_mappings:
            mapping.add_concept_mapping(self._convert_concept_mapping(model, ontology, semantic_model, cm_spec))

    def _convert_concept_mapping(
        self,
        model: OsiOntology,
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        cm_spec: SpecConceptMapping,
    ) -> ConceptMapping:
        concept = ontology.lookup_concept(cm_spec.concept)
        if concept is None:
            raise ValueError(
                f"ConceptMapping references unknown concept '{cm_spec.concept}' in ontology '{model.name}'."
            )
        cm = ConceptMapping(concept=concept)
        for object_mapping_spec in cm_spec.object_mappings:
            cm.object_mappings.append(
                self._convert_object_mapping(model, ontology, semantic_model, concept, object_mapping_spec)
            )
        for link_mapping_spec in cm_spec.link_mappings:
            cm.link_mappings.append(
                self._convert_link_mapping(model, ontology, semantic_model, concept, link_mapping_spec)
            )
        return cm

    def _convert_object_mapping(
        self,
        model: OsiOntology,
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        container: Concept,
        om_spec: SpecObjectMapping,
    ) -> ObjectMapping:
        concept: Concept | None = None
        if om_spec.concept:
            concept = ontology.lookup_concept(om_spec.concept)
            if concept is None:
                raise ValueError(
                    f"ObjectMapping references unknown concept '{om_spec.concept}' in ontology "
                    f"'{model.name}'."
                )
        expression: DatasetField | Formula | None = None
        if om_spec.expression is not None:
            expression = self._resolve_mapping_expression(om_spec.expression, semantic_model, concept, ontology)
        referent_mappings = None
        if om_spec.referent_mappings is not None:
            rm_container = concept if concept is not None else container
            referent_mappings = [
                self._convert_referent_mapping(model, ontology, semantic_model, rm_container, rm)
                for rm in om_spec.referent_mappings
            ]
        return ObjectMapping(concept=concept, expression=expression, referent_mappings=referent_mappings)

    def _convert_referent_mapping(
        self,
        model: OsiOntology,
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        container: Concept,
        rm_spec: SpecReferentMapping,
    ) -> ReferentMapping:
        rel = ontology.lookup_concept_relationship(container, rm_spec.relationship)
        if rel is None:
            raise ValueError(
                f"ReferentMapping references unknown relationship "
                f"'{container.name}.{rm_spec.relationship}' in ontology '{model.name}'."
            )
        sibling_player = rel.last_role.player
        expression: DatasetField | Formula | None = None
        if rm_spec.expression is not None:
            expression = self._resolve_mapping_expression(rm_spec.expression, semantic_model, sibling_player, ontology)
        nested = None
        if rm_spec.referent_mappings is not None:
            nested = [
                self._convert_referent_mapping(model, ontology, semantic_model, sibling_player, child)
                for child in rm_spec.referent_mappings
            ]
        return ReferentMapping(relationship=rel, expression=expression, referent_mappings=nested)

    def _convert_link_mapping(
        self,
        model: OsiOntology,
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        container: Concept,
        lm_spec: SpecLinkMapping,
    ) -> LinkMapping:
        object_mapping = self._convert_object_mapping(model, ontology, semantic_model, container, lm_spec.object_mapping)
        relationship: Relationship | None = None
        if lm_spec.relationship is not None:
            relationship = ontology.lookup_concept_relationship(container, lm_spec.relationship)
            if relationship is None:
                raise ValueError(
                    f"LinkMapping references unknown relationship "
                    f"'{container.name}.{lm_spec.relationship}' in ontology '{model.name}'."
                )
        children: list[LinkMapping] | None = None
        if lm_spec.children is not None:
            child_container = relationship.last_role.player if relationship is not None else container
            children = [
                self._convert_link_mapping(model, ontology, semantic_model, child_container, child)
                for child in lm_spec.children
            ]
        return LinkMapping(object_mapping=object_mapping, relationship=relationship, children=children)

    # ----- Formula helpers -----------------------------------------------

    def _build_rule(self, raw: str | None, parent: Container | None, ontology: OntologyComponent) -> Formula | None:
        if not raw:
            return None
        return self._formula_factory(raw_expr=raw, parent=parent, ontology=ontology)

    def _resolve_mapping_expression(self, expression: str, semantic_model: SemanticModel, expected_type: Concept | None,
                                    ontology: OntologyComponent) -> DatasetField | Formula:
        """Map a raw spec expression onto either a DatasetField (single
        `DATASET.field` or bare `field` reference) or a Formula (anything else).
        """
        qualified = _QUALIFIED_FIELD_RE.match(expression)
        if qualified:
            ds_name, field_name = qualified.group(1), qualified.group(2)
            dataset = semantic_model.lookup_dataset(ds_name)
            if dataset is not None:
                field = dataset.field(field_name)
                if field is not None:
                    _pin_field_type(field, expected_type)
                    return field
            return self._mapping_formula_factory(raw_expr=expression, ontology=ontology, semantic_model=semantic_model)

        bare = _BARE_FIELD_RE.match(expression)
        if bare:
            field_name = bare.group(1)
            matches = [
                (dataset, field)
                for dataset in semantic_model.datasets
                if (field := dataset.field(field_name)) is not None
            ]
            if len(matches) > 1:
                owners = ", ".join(dataset.name for dataset, _ in matches)
                raise ValueError(
                    f"Bare field reference '{field_name}' is ambiguous: it exists in multiple "
                    f"datasets ({owners}). Qualify it as 'DATASET.{field_name}'."
                )
            if matches:
                _, field = matches[0]
                _pin_field_type(field, expected_type)
                return field
            return self._mapping_formula_factory(raw_expr=expression, ontology=ontology, semantic_model=semantic_model)

        return self._mapping_formula_factory(raw_expr=expression, ontology=ontology, semantic_model=semantic_model)

    # ----- Structural helpers --------------------------

    @staticmethod
    def _sort_spec_dependency_graph(concepts: list[SpecConcept]) -> list[str]:
        nodes: list[str] = []
        edges: list[tuple[str, str]] = []
        for concept in concepts:
            name = concept.name
            nodes.append(name)
            if concept.extends:
                for ext in concept.extends:
                    if ext not in BUILTIN_CONCEPTS:
                        edges.append((ext, name))
        return topological_sort(nodes, edges)


def _pin_field_type(field: DatasetField, expected_type: Concept | None) -> None:
    if expected_type is None:
        return
    if field.type is None:
        field.type = expected_type
        return
    if field.type is not expected_type:
        raise ValueError(
            f"Field '{field.name}' is already mapped as concept "
            f"'{field.type.name}' but this mapping expects "
            f"'{expected_type.name}'. A dataset field can only be "
            f"bound to one ontology concept type."
        )


def _convert_custom_extension(ce: SpecCustomExtension) -> CustomExtension:
    return CustomExtension(vendor_name=ce.vendor_name, data=ce.data)


def _convert_expression(expr: SpecExpression) -> DialectExpressionSet:
    return DialectExpressionSet(
        dialects=[_convert_dialect_expression(d) for d in expr.dialects]
    )


def _convert_dialect_expression(dialect_expr: SpecDialectExpression) -> DialectExpression:
    return DialectExpression(dialect=dialect_expr.dialect, expression=dialect_expr.expression)


def _convert_dimension(dim: SpecDimension | None) -> Dimension | None:
    if dim is None:
        return None
    return Dimension(is_time=dim.is_time)


def _convert_dataset_field(fl: SpecDatasetField) -> DatasetField:
    return DatasetField(
        name=fl.name,
        expression=_convert_expression(fl.expression),
        dimension=_convert_dimension(fl.dimension),
        label=fl.label,
        description=fl.description,
        ai_context=fl.ai_context,
        custom_extensions=[_convert_custom_extension(ce) for ce in fl.custom_extensions],
    )


def _convert_dataset(ds: SpecDataset) -> Dataset:
    fields = [_convert_dataset_field(fl) for fl in ds.fields]
    return Dataset(
        name=ds.name,
        source=ds.source,
        fields=fields,
        primary_key=ds.primary_key,
        unique_keys=ds.unique_keys,
        description=ds.description,
        ai_context=ds.ai_context,
        custom_extensions=[_convert_custom_extension(ce) for ce in ds.custom_extensions],
    )


def _convert_join_path(jp: SpecJoinPath, lm: SemanticModel) -> JoinPath:
    from_dataset = lm.lookup_dataset(jp.from_)
    to_dataset = lm.lookup_dataset(jp.to)
    if from_dataset is None:
        raise ValueError(f"JoinPath '{jp.name}': unknown 'from' dataset '{jp.from_}'.")
    if to_dataset is None:
        raise ValueError(f"JoinPath '{jp.name}': unknown 'to' dataset '{jp.to}'.")
    from_columns: list[DatasetField] = []
    for col in jp.from_columns:
        field = from_dataset.field(col)
        if field is None:
            raise ValueError(
                f"JoinPath '{jp.name}': column '{col}' not found in dataset '{from_dataset.name}'."
            )
        from_columns.append(field)
    to_columns: list[DatasetField] = []
    for col in jp.to_columns:
        field = to_dataset.field(col)
        if field is None:
            raise ValueError(
                f"JoinPath '{jp.name}': column '{col}' not found in dataset '{to_dataset.name}'."
            )
        to_columns.append(field)
    return JoinPath(
        name=jp.name,
        from_dataset=from_dataset,
        to_dataset=to_dataset,
        from_columns=from_columns,
        to_columns=to_columns,
        ai_context=jp.ai_context,
        custom_extensions=[_convert_custom_extension(ce) for ce in jp.custom_extensions],
    )


def _convert_metric(m: SpecMetric) -> Metric:
    return Metric(
        name=m.name,
        expression=_convert_expression(m.expression),
        description=m.description,
        ai_context=m.ai_context,
        custom_extensions=[_convert_custom_extension(ce) for ce in m.custom_extensions],
    )