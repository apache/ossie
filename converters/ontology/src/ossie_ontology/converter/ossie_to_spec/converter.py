# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Reverse converter: OssieOntology (runtime) -> OssieSpec (Pydantic DTO).

Pairs with spec_to_ossie.SpecToOssieConverter so a full round-trip
yaml -> spec -> model -> spec -> yaml is structurally stable."""

from __future__ import annotations

from ossie_ontology.model import (
    Concept,
    ConceptMapping,
    ConceptType,
    CustomExtension,
    Dataset,
    DatasetField,
    DialectExpressionSet,
    Dimension,
    JoinPath,
    LinkMapping,
    SemanticModel,
    Metric,
    ObjectMapping,
    OntologyComponent,
    OntologyMapping,
    ReferentMapping,
    Relationship,
    OssieOntology,
)
from ossie_ontology.spec import (
    ConceptComponent,
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
    OssieSpec,
    ReferentMapping as SpecReferentMapping,
    Relationship as SpecRelationship,
    Role as SpecRole,
)


class OssieToSpecConverter:
    """Top-level reverse converter."""

    @staticmethod
    def convert(model: OssieOntology) -> OssieSpec:
        ont = model.ontology
        ontology_mappings = [_convert_ontology_mapping(ontology_mapping) for ontology_mapping in model.ontology_mappings]
        return OssieSpec(
            version=model.version,
            name=model.name,
            description=model.description,
            ai_context=model.ai_context,
            requires=[f.raw_expr for f in ont.requires],
            ontology=_convert_ontology_concepts(ont),
            ontology_mappings=ontology_mappings,
        )


# ---------------------------------------------------------------------------
# Ontology
# ---------------------------------------------------------------------------

def _convert_ontology_concepts(ont: OntologyComponent) -> list[ConceptComponent]:
    components: list[ConceptComponent] = []
    # Group once: `ont.relationships` rebuilds a list on every access, so scanning
    # it per concept is O(concepts x relationships). Keyed on id() to keep the
    # identity comparison a per-concept scan would do.
    by_container: dict[int, list[Relationship]] = {}
    for rel in ont.relationships:
        by_container.setdefault(id(rel.container), []).append(rel)

    for concept in ont.concepts():
        if not concept.is_component:
            continue
        components.append(_convert_concept(concept, by_container.get(id(concept), [])))
    return components


def _convert_concept(concept: Concept, rels: list[Relationship]) -> ConceptComponent:
    type_value: str | None = None
    if isinstance(concept.type, ConceptType):
        type_value = concept.type.value  # type: ignore[union-attr]
    extends = [p.name for p in concept.extends] if concept.extends else None

    return ConceptComponent(
        concept=concept.name,
        type=type_value,  # type: ignore[arg-type]
        description=concept.description,
        extends=extends,
        identify_by=[rel.name for rel in concept.identify_by.values()],
        derived_by=[f.raw_expr for f in concept.derived_by],
        requires=[f.raw_expr for f in concept.requires],
        relationships=[_convert_relationship(rel) for rel in rels],
    )


def _convert_relationship(rel: Relationship) -> SpecRelationship:
    extra_roles = list(rel.roles)[1:]
    roles = [SpecRole(concept=role.player.name, name=role.explicit_name) for role in extra_roles]

    multiplicity = rel.multiplicity.value if rel.multiplicity is not None else None
    verbalizes = rel.verbalizes_raw if rel.verbalizes_raw is not None else []

    return SpecRelationship(
        name=rel.name,
        description=rel.description,
        roles=roles,
        verbalizes=verbalizes,
        multiplicity=multiplicity,  # type: ignore[arg-type]
        derived_by=[f.raw_expr for f in rel.derived_by],
        requires=[f.raw_expr for f in rel.requires],
    )


# ---------------------------------------------------------------------------
# Semantic model
# ---------------------------------------------------------------------------

def _convert_semantic_model(semantic_model: SemanticModel) -> SpecSemanticModel:
    return SpecSemanticModel(
        name=semantic_model.name,
        description=semantic_model.description,
        ai_context=semantic_model.ai_context,
        datasets=[_convert_dataset(ds) for ds in semantic_model.datasets],
        relationships=[_convert_join_path(jp) for jp in semantic_model.join_paths],
        metrics=[_convert_metric(metric) for metric in semantic_model.metrics],
        custom_extensions=[_convert_custom_extension(ce) for ce in semantic_model.custom_extensions],
    )


def _convert_dataset(ds: Dataset) -> SpecDataset:
    return SpecDataset(
        name=ds.name,
        source=ds.source,
        primary_key=ds.primary_key,
        unique_keys=ds.unique_keys,
        description=ds.description,
        ai_context=ds.ai_context,
        fields=[_convert_dataset_field(fl) for fl in ds.fields],
        custom_extensions=[_convert_custom_extension(ce) for ce in ds.custom_extensions],
    )


def _convert_dataset_field(fl: DatasetField) -> SpecDatasetField:
    return SpecDatasetField(
        name=fl.name,
        expression=_convert_expression(fl.expression),
        dimension=_convert_dimension(fl.dimension),
        label=fl.label,
        description=fl.description,
        ai_context=fl.ai_context,
        custom_extensions=[_convert_custom_extension(ce) for ce in fl.custom_extensions],
    )


def _convert_expression(es: DialectExpressionSet) -> SpecExpression:
    return SpecExpression(
        dialects=[SpecDialectExpression(dialect=d.dialect, expression=d.expression) for d in es.dialects]
    )


def _convert_dimension(dim: Dimension | None) -> SpecDimension | None:
    if dim is None:
        return None
    return SpecDimension(is_time=dim.is_time)


def _convert_join_path(jp: JoinPath) -> SpecJoinPath:
    return SpecJoinPath(
        name=jp.name,
        **{"from": jp.from_dataset.name},  # `from` is a reserved word in Python
        to=jp.to_dataset.name,
        from_columns=[from_col.name for from_col in jp.from_columns],
        to_columns=[to_col.name for to_col in jp.to_columns],
        ai_context=jp.ai_context,
        custom_extensions=[_convert_custom_extension(ce) for ce in jp.custom_extensions],
    )


def _convert_metric(metric: Metric) -> SpecMetric:
    return SpecMetric(
        name=metric.name,
        expression=_convert_expression(metric.expression),
        description=metric.description,
        ai_context=metric.ai_context,
        custom_extensions=[_convert_custom_extension(ce) for ce in metric.custom_extensions],
    )


def _convert_custom_extension(ce: CustomExtension) -> SpecCustomExtension:
    return SpecCustomExtension(vendor_name=ce.vendor_name, data=ce.data)


# ---------------------------------------------------------------------------
# Ontology mapping (tree)
# ---------------------------------------------------------------------------

def _convert_ontology_mapping(ontology_mapping: OntologyMapping) -> SpecOntologyMapping:
    return SpecOntologyMapping(
        name=ontology_mapping.name,
        description=ontology_mapping.description,
        semantic_model=_convert_semantic_model(ontology_mapping.semantic_model),
        concept_mappings=[_convert_concept_mapping(concept_mapping) for concept_mapping in ontology_mapping.concept_mappings],
    )


def _convert_concept_mapping(concept_mapping: ConceptMapping) -> SpecConceptMapping:
    return SpecConceptMapping(
        concept=concept_mapping.concept.name,
        object_mappings=[_convert_object_mapping(object_mapping) for object_mapping in concept_mapping.object_mappings],
        link_mappings=[_convert_link_mapping(link_mapping) for link_mapping in concept_mapping.link_mappings],
    )


def _convert_object_mapping(object_mapping: ObjectMapping) -> SpecObjectMapping:
    referent_mappings = None
    if object_mapping.referent_mappings is not None:
        referent_mappings = [_convert_referent_mapping(rm) for rm in object_mapping.referent_mappings]
    return SpecObjectMapping(
        concept=object_mapping.concept.name if object_mapping.concept is not None else None,
        expression=_render_mapping_expression(object_mapping.expression),
        referent_mappings=referent_mappings,
    )


def _convert_referent_mapping(referent_mapping: ReferentMapping) -> SpecReferentMapping:
    nested = None
    if referent_mapping.referent_mappings is not None:
        nested = [_convert_referent_mapping(child) for child in referent_mapping.referent_mappings]
    return SpecReferentMapping(
        relationship=referent_mapping.relationship.name,
        expression=_render_mapping_expression(referent_mapping.expression),
        referent_mappings=nested,
    )


def _render_mapping_expression(expr) -> str | None:
    """Reconstruct the source string for a parsed mapping expression. The
    runtime model carries either a `DatasetField` (single field reference)
    or a `Formula` (richer expression); both round-trip back to the same
    string the forward converter saw in the spec."""
    if expr is None:
        return None
    from ossie_ontology.model import DatasetField as _DF, Formula as _F
    if isinstance(expr, _DF):
        ds = expr.dataset
        return f"{ds.name}.{expr.name}" if ds is not None else expr.name
    if isinstance(expr, _F):
        return expr.raw_expr
    return str(expr)


def _convert_link_mapping(link_mapping: LinkMapping) -> SpecLinkMapping:
    children = None
    if link_mapping.children is not None:
        children = [_convert_link_mapping(child) for child in link_mapping.children]
    return SpecLinkMapping(
        object_mapping=_convert_object_mapping(link_mapping.object_mapping),
        relationship=link_mapping.relationship.name if link_mapping.relationship is not None else None,
        children=children,
    )
