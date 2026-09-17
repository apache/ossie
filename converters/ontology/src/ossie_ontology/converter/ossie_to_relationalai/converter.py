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

"""Ossie -> RelationalAI: an `OssieOntology` becomes a PyRel `OntologyModel`.

Together with `ossie_ontology.expr.formula.visitor.converter` — the formula
emitter, which stays with its sibling visitors — this package is the only part
of `ossie_ontology` that imports `relationalai`. Neither is reachable from
`ossie_ontology/__init__.py`, which is what lets the SDK be an optional extra
rather than a hard dependency. Install it with:

    pip install "apache-ossie-ontology[relationalai]"
"""
from __future__ import annotations

import warnings
from typing import Any, Callable

from relationalai.util.naming import NameCache
from ossie_ontology.vendor.relationalai.mappings import EntityMapping
from ossie_ontology.vendor.relationalai.ontology import OntologyModel
from ossie_ontology.expr.formula.model import Formula
from ossie_ontology.expr.formula.visitor.converter import FormulaConverter, MappingFormulaConverter
from ossie_ontology.reasoner import OntologyReasoner
from ossie_ontology.model import (
    Concept,
    Dataset,
    DatasetField,
    LinkMapping,
    ObjectMapping,
    SemanticModel,
    OntologyComponent,
    OntologyMapping,
    ReferentMapping,
    Relationship,
    OssieOntology,
)
import relationalai.semantics as pyrel
from relationalai.semantics import Concept as RAIConcept

# How a dataset becomes something the model can read. Takes the model being
# built, the dataset as the Ossie spec declares it, and the column types derived
# from that spec; returns whatever pyrel construct should stand in for it.
#
# The default resolves the dataset's `source` to a warehouse table. Substituting
# a provider is how a caller reads from somewhere else — a downstream project
# supplies one backed by inline CSV so its tests need no warehouse — without
# this converter having to know that any such alternative exists.
TableProvider = Callable[["OntologyModel", Dataset, dict[str, RAIConcept]], Any]


def warehouse_table(ontology: "OntologyModel", dataset: Dataset,
                    schema: dict[str, RAIConcept]) -> Any:
    """Resolve a dataset to the warehouse table its `source` names.

    The schema is deliberately *not* passed on: pyrel reads the real columns
    from the table instead. That costs a connection when the model is compiled,
    but it is the only way to get the column identifiers right — Snowflake folds
    an unquoted name to upper case, so declaring the spec's `airport_code`
    yields `AIRPORT_CODE`, which does not address a column created as
    `"airport_code"`. Only the table itself knows which it is.

    Use :func:`declared_table` where the spec's field names are known to match
    and a connection is unwanted.
    """
    return ontology.base_model().Table(dataset.source)


def declared_table(ontology: "OntologyModel", dataset: Dataset,
                   schema: dict[str, RAIConcept]) -> Any:
    """Resolve a dataset to its table, declaring the columns from the spec.

    Nothing is read from the warehouse, so conversion and compilation work with
    no connection — which is what makes an offline test suite possible. The
    tradeoff is the one :func:`warehouse_table` avoids: the declared identifiers
    have to match how the table was actually created.
    """
    return ontology.base_model().Table(dataset.source, schema=schema)


class OssieToRelationalAIConverter:

    @staticmethod
    def convert(
        model: OssieOntology,
        reasoner: OntologyReasoner | None = None,
        model_cls: type[OntologyModel] = OntologyModel,
        table_provider: TableProvider = warehouse_table,
    ) -> OntologyModel:
        """Convert an OssieOntology into a PyRel model.

        `model_cls` decides what the result is an instance of. The conversion
        itself only ever touches the plain `OntologyModel` surface, so the
        default is that class; pass `MeasureOntologyModel` when the caller goes
        on to ask dimensional measure queries of the result.
        """
        ontology = model_cls(model.name)
        properties_index: dict[str, pyrel.Relationship] = {}

        oc = model.ontology
        if reasoner is None:
            reasoner = OntologyReasoner()
            oc.register(reasoner)

        OssieToRelationalAIConverter._convert_concepts(oc, ontology)
        OssieToRelationalAIConverter._convert_relationships(oc, ontology, properties_index, reasoner)
        OssieToRelationalAIConverter._identify_concepts(oc, ontology, properties_index)

        for om in model.ontology_mappings:
            OssieToRelationalAIConverter._convert_datasets(
                om.semantic_model, ontology, table_provider
            )

        # Shared across every ontology_mapping, so a binding declared in two of
        # them collapses to one creation rule. Insurance rather than a live
        # saving: every scenario in the corpus has a single ontology_mapping.
        entity_bindings: dict = {}
        for om in model.ontology_mappings:
            OssieToRelationalAIConverter._convert_mappings(
                om, ontology, properties_index, reasoner, entity_bindings
            )

        OssieToRelationalAIConverter._convert_formulas(oc, ontology, reasoner)

        return ontology

    @staticmethod
    def _concept(ontology: OntologyModel, name: str) -> RAIConcept:
        """The pyrel concept for an Ossie concept name.

        Every concept is created by `_convert_concepts` before any mapping or
        reading is converted, so a miss means those passes disagree about the
        ontology. Failing here names the concept; letting the None through
        surfaces later as an opaque `NoneType has no attribute` from inside
        pyrel, or binds a null silently.
        """
        concept = ontology.lookup_concept(name)
        assert concept is not None, f"Concept '{name}' not found in the converted ontology"
        return concept

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_datasets(sm: SemanticModel, ontology: OntologyModel, table_provider: TableProvider) -> None:
        for dataset in sm.datasets:
            schema = OssieToRelationalAIConverter._pyrel_schema(dataset, ontology)
            ontology.add_table(dataset.name, table_provider(ontology, dataset, schema))

    @staticmethod
    def _pyrel_schema(dataset: Dataset, ontology: OntologyModel) -> dict:
        """Column types for *dataset*, taken from the Ossie spec that declares it.

        Handed to the table provider so it can declare the columns up front,
        which is what keeps the conversion from needing a live warehouse.
        """
        schema = {}
        for fl in dataset.fields:
            if fl.type is not None:
                concept = OssieToRelationalAIConverter._concept(ontology, fl.type.name)
                schema[fl.name] = ontology.get_topmost_parent(concept)
            else:
                schema[fl.name] = ontology.lookup_concept("String")
        return schema

    # ------------------------------------------------------------------
    # Concepts
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_concepts(oc: OntologyComponent, ontology: OntologyModel) -> None:
        sorted_names = OssieToRelationalAIConverter._sort_dependency_graph(oc.concepts(exclude_builtin=True))
        for name in sorted_names:
            concept = oc.lookup_concept(name)
            if concept and concept.name not in ontology.base_model().concept_index:
                subtypes = []
                for parent in concept.extends:
                    pyrel_parent = ontology.lookup_concept(parent.name)
                    if pyrel_parent is None:
                        raise ValueError(f"'{parent.name}' is not declared in the ontology.")
                    subtypes.append(pyrel_parent)
                ontology.Concept(concept.name, extends=subtypes)

    # ------------------------------------------------------------------
    # Relationships
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_relationships(oc: OntologyComponent, ontology: OntologyModel, properties_index: dict, reasoner: OntologyReasoner) -> None:
        for rel in oc.relationships:
            container = rel.first_role.player
            if rel.name in pyrel.Concept.RESERVED_NAMES:
                warnings.warn(
                    f"Relationship '{rel.full_name}' clashes with Concept reserved name '{rel.name}'",
                    UserWarning,
                    stacklevel=2,
                )
                continue
            reading = OssieToRelationalAIConverter._generate_reading(rel, ontology)
            if reasoner.is_property(rel):
                pyrel_rel = ontology.Property(reading, short_name=rel.name)
            else:
                pyrel_rel = ontology.Relationship(reading, short_name=rel.name)
            properties_index[rel.full_name] = pyrel_rel
            if not container.is_builtin:
                c = ontology.lookup_concept(container.name)
                c.__setattr__(rel.name, pyrel_rel)

    # ------------------------------------------------------------------
    # Identifiers
    # ------------------------------------------------------------------

    @staticmethod
    def _identify_concepts(oc: OntologyComponent, ontology: OntologyModel, properties_index: dict) -> None:
        for concept in oc.concepts(exclude_builtin=True):
            identify_by = concept.identify_by
            if identify_by:
                pyrel_concept = OssieToRelationalAIConverter._concept(ontology, concept.name)
                props = [properties_index[key] for key in identify_by]
                pyrel_concept.identify_by(*props)

    # ------------------------------------------------------------------
    # Mappings
    # ------------------------------------------------------------------

    @staticmethod
    def _expression_key(expression) -> tuple:
        """Dataset-qualified identity of a mapping expression.

        A ``DatasetField`` stringifies to the bare column name (``runway_id``),
        which is *not* unique: att_latest maps ``OFT_Runway`` twice from two
        different datasets that both have a ``runway_id`` column. Keying on the
        rendered string alone merges those two bindings and rebinds the second
        concept mapping's properties onto the first one's dataset. The owning
        dataset therefore has to be part of the key — by identity, since two
        distinct datasets may also share a name.
        """
        if isinstance(expression, DatasetField):
            dataset = expression.dataset
            return ("field", getattr(dataset, "name", None), id(dataset), expression.name)
        # Formulas render their dataset references inline, so the text is
        # already qualified.
        return ("other", str(expression))

    @staticmethod
    def _object_mapping_key(osi_concept: Concept, om: ObjectMapping) -> tuple:
        """Identity of the entity binding an ObjectMapping would produce.

        Two object mappings that agree on concept *and* on how the identifier
        is sourced describe the same set of entities, so they only need one
        creation rule between them. Everything the identifier is derived from
        goes into the key: the referent mappings (relationship + expression),
        or — for the bare-expression form — the expression and the concept it
        casts to. `_build_entity_binding` also reads ``om.concept`` (the
        supertype that supplies the ref scheme), so that is part of the key too.
        """
        supertype = om.concept.name if om.concept is not None else None
        if om.referent_mappings:
            identifier = (
                "referents",
                tuple(sorted(
                    (str(getattr(rm.relationship, "name", rm.relationship)),
                     OssieToRelationalAIConverter._expression_key(rm.expression))
                    for rm in om.referent_mappings
                )),
            )
        elif om.expression is not None:
            identifier = ("expression", OssieToRelationalAIConverter._expression_key(om.expression))
        else:
            identifier = ("none",)
        return (osi_concept.name, supertype, identifier)

    @staticmethod
    def _convert_mappings(om: OntologyMapping, ontology: OntologyModel, properties_index: dict,
                          reasoner: OntologyReasoner, entity_bindings: dict | None = None) -> None:
        # A concept is routinely mapped twice: once standalone under
        # `object_mappings` and again as the `object_mapping` of a
        # `link_mappings` root that hangs properties off it. Both used to build
        # their own EBinding, and every EBinding emits `define(C.new(...))`
        # (see `mappings.EBinding.__init__`), so the model ended up with two
        # identical creation rules — idempotent, but duplicated work in every
        # compiled model. Bindings are now cached by
        # `_object_mapping_key`, so the second reference reuses the first
        # binding instead of re-declaring it. `Eref` is immutable once built and
        # a binding already drives many `binds_to` calls, so sharing is safe.
        #
        # Note this cannot be simplified to "never create from link roots":
        # some concepts (Flight in the flights scenarios, CardPayment /
        # CashPayment / Item in retail_refactored) have no `object_mappings`
        # entry at all and are created solely by their link root.
        if entity_bindings is None:
            entity_bindings = {}
        for cm in om.concept_mappings:
            entity_concept = ontology.lookup_concept(cm.concept.name)
            for obj_m in cm.object_mappings:
                OssieToRelationalAIConverter._entity_binding_for(
                    obj_m, cm.concept, entity_concept, ontology, reasoner, entity_bindings
                )
            for root_lm in cm.link_mappings:
                OssieToRelationalAIConverter._convert_root_link_mapping(
                    root_lm, cm.concept, entity_concept, ontology, properties_index, reasoner,
                    entity_bindings
                )

    @staticmethod
    def _entity_binding_for(om: ObjectMapping, osi_concept: Concept, entity_concept,
                            ontology: OntologyModel, reasoner: OntologyReasoner,
                            entity_bindings: dict):
        """Return the binding for *om*, building it only on first sight."""
        key = OssieToRelationalAIConverter._object_mapping_key(osi_concept, om)
        binding = entity_bindings.get(key)
        if binding is None:
            binding = OssieToRelationalAIConverter._build_entity_binding(
                om, osi_concept, entity_concept, ontology, reasoner
            )
            entity_bindings[key] = binding
        return binding

    @staticmethod
    def _build_entity_binding(om: ObjectMapping, osi_concept: Concept, entity_concept: RAIConcept,
                              ontology: OntologyModel, reasoner: OntologyReasoner):
        if om.referent_mappings:
            kwargs = OssieToRelationalAIConverter._build_entity_kwargs(om.referent_mappings, ontology)
        elif om.expression is not None:
            scheme = reasoner.ref_scheme(osi_concept)
            if not scheme:
                raise ValueError(
                    f"Concept '{osi_concept.name}' has no reference scheme; "
                    "cannot map a bare expression without referent_mappings."
                )
            if len(scheme) != 1:
                raise ValueError(
                    f"Concept '{osi_concept.name}' has a composite reference scheme; "
                    "use referent_mappings instead of a bare expression."
                )
            id_rel = scheme[0]
            kwargs = {id_rel.name: OssieToRelationalAIConverter._render_expression(om.expression, ontology)}
        else:
            kwargs = {}
        if om.concept is not None:
            parent_concept = OssieToRelationalAIConverter._concept(ontology, om.concept.name)
            return ontology.EntityBinding(entity_concept, stype=parent_concept, **kwargs)
        return ontology.EntityBinding(entity_concept, **kwargs)

    @staticmethod
    def _render_expression(expression, ontology: OntologyModel, concept=None):
        if isinstance(expression, DatasetField):
            # `dataset` is a back-pointer set when the field is attached to its
            # dataset; a field that never was cannot be resolved to a table.
            dataset = expression.dataset
            assert dataset is not None, (
                f"Mapping expression references column '{expression.name}', which is "
                f"not attached to any dataset"
            )
            table = ontology.lookup_table(dataset.name)
            assert table is not None, (
                f"Dataset '{dataset.name}' has no table in the converted ontology; "
                f"it is referenced by the mapping for column '{expression.name}'"
            )
            result = table.__getattr__(expression.name)
            return concept(result) if concept is not None else result
        if isinstance(expression, Formula):
            return MappingFormulaConverter(ontology, expression, concept=concept).result
        raise ValueError(f"Unsupported expression type in mapping: {type(expression)}")

    @staticmethod
    def _convert_root_link_mapping(root_lm: LinkMapping, osi_concept: Concept, entity_concept, ontology: OntologyModel,
                                   properties_index: dict, reasoner: OntologyReasoner,
                                   entity_bindings: dict | None = None) -> None:
        entity_binding = OssieToRelationalAIConverter._entity_binding_for(
            root_lm.object_mapping, osi_concept, entity_concept, ontology, reasoner,
            entity_bindings if entity_bindings is not None else {},
        )
        if root_lm.relationship is not None:
            # Unary relationship: the object_mapping identifies the entity and
            # the relationship is asserted on it with no additional role.
            pyrel_rel = properties_index[root_lm.relationship.full_name]
            entity_binding.binds_to(pyrel_rel)
        for child_lm in (root_lm.children or []):
            OssieToRelationalAIConverter._apply_link_binding(child_lm, entity_binding, ontology, properties_index, reasoner)

    @staticmethod
    def _apply_link_binding(lm: LinkMapping, entity_binding, ontology: OntologyModel, properties_index: dict,
                             reasoner: OntologyReasoner, intermediate_roles: list | None = None) -> None:
        om = lm.object_mapping

        if lm.relationship is None:
            # Intermediate role node for ternary (or higher arity) relationships.
            # Build the role reference without emitting any binding, then forward
            # it as accumulated context to each child.
            role_val = OssieToRelationalAIConverter._build_role_value(om, ontology)
            accumulated = (intermediate_roles or []) + ([role_val] if role_val is not None else [])
            for child_lm in (lm.children or []):
                OssieToRelationalAIConverter._apply_link_binding(
                    child_lm, entity_binding, ontology, properties_index, reasoner, accumulated
                )
            return

        pyrel_rel = properties_index[lm.relationship.full_name]

        if intermediate_roles:
            # Ternary (or higher): emit a single binding with all accumulated roles + this role.
            final_val = OssieToRelationalAIConverter._build_role_value(om, ontology)
            entity_binding.binds_to(pyrel_rel, *intermediate_roles, final_val)
            # Continue into any children with a fresh intermediate context.
            child_binding = entity_binding
        elif om.referent_mappings:
            # Binary entity→entity binding.
            # binds_to_entity uses EntityMapping (filter_by), so no ghost entity is created here.
            # Only create a child EntityBinding (creator) when there are grandchildren that need it.
            kwargs = OssieToRelationalAIConverter._build_entity_kwargs(om.referent_mappings, ontology)
            entity_binding.binds_to_entity(pyrel_rel, **kwargs)
            if lm.children:
                target_concept = OssieToRelationalAIConverter._concept(ontology, lm.relationship.last_role.player.name)
                stype = OssieToRelationalAIConverter._concept(ontology, om.concept.name) if om.concept is not None else None
                child_binding = ontology.EntityBinding(target_concept, stype=stype, **kwargs)
            else:
                child_binding = entity_binding
        elif om.expression is not None:
            pyrel_concept = ontology.lookup_concept(om.concept.name) if (om.concept is not None and not om.concept.is_builtin) else None
            value = OssieToRelationalAIConverter._render_expression(om.expression, ontology, concept=pyrel_concept)
            entity_binding.binds_to(pyrel_rel, value)
            if lm.children:
                # For entity targets with a single-element ref scheme, build an
                # explicit child binding so grandchildren are scoped to that entity.
                target_osi = lm.relationship.last_role.player
                scheme = reasoner.ref_scheme(target_osi)
                if scheme and len(scheme) == 1:
                    target_pyrel = OssieToRelationalAIConverter._concept(ontology, target_osi.name)
                    stype = OssieToRelationalAIConverter._concept(ontology, om.concept.name) if om.concept is not None else None
                    key_value = OssieToRelationalAIConverter._render_expression(om.expression, ontology)
                    child_binding = ontology.EntityBinding(target_pyrel, stype=stype, **{scheme[0].name: key_value})
                else:
                    child_binding = entity_binding
            else:
                child_binding = entity_binding
        else:
            child_binding = entity_binding

        for child_lm in (lm.children or []):
            OssieToRelationalAIConverter._apply_link_binding(child_lm, child_binding, ontology, properties_index, reasoner)

    @staticmethod
    def _build_role_value(om: ObjectMapping, ontology: OntologyModel):
        """Build a PyRel ref or value from an object_mapping without emitting any binding rule.

        Used for intermediate (no-relationship) nodes in ternary link_mapping trees, where the
        role value must be accumulated before the final relationship binding is emitted."""
        if om.referent_mappings:
            kwargs = OssieToRelationalAIConverter._build_entity_kwargs(om.referent_mappings, ontology)
            assert om.concept is not None, "a referent mapping must name its concept"
            target = OssieToRelationalAIConverter._concept(ontology, om.concept.name)
            return EntityMapping(target, **kwargs)
        if om.expression is not None:
            pyrel_concept = ontology.lookup_concept(om.concept.name) if (om.concept is not None and not om.concept.is_builtin) else None
            return OssieToRelationalAIConverter._render_expression(om.expression, ontology, concept=pyrel_concept)
        return None

    @staticmethod
    def _build_entity_kwargs(referent_mappings: list[ReferentMapping], ontology: OntologyModel) -> dict[str, Any]:
        """Identifier kwargs for an entity binding: relationship name -> value.

        The values are whatever the mapping renders to — a nested EntityMapping,
        a pyrel Chain over a dataset column, or a ValueMapping — so `Any` is the
        honest element type rather than a union that would have to be restated
        every time the renderer learns a new shape.
        """
        kwargs: dict[str, Any] = {}
        for rm in referent_mappings:
            rel_name = rm.relationship.name
            if rm.referent_mappings:
                target_concept = OssieToRelationalAIConverter._concept(ontology, rm.relationship.last_role.player.name)
                sub_kwargs = OssieToRelationalAIConverter._build_entity_kwargs(rm.referent_mappings, ontology)
                kwargs[rel_name] = EntityMapping(target_concept, **sub_kwargs)
            elif rm.expression is not None:
                kwargs[rel_name] = OssieToRelationalAIConverter._render_expression(rm.expression, ontology)
        return kwargs

    # ------------------------------------------------------------------
    # Formulas / rules
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_formulas(oc: OntologyComponent, ontology: OntologyModel, reasoner: OntologyReasoner) -> None:
        # `derived_by` does not guarantee a *parsed* formula. `FormulaFactory`
        # hands back a plain `ossie_ontology.model.Formula` — raw text, no AST —
        # and owl2osi builds them that way on purpose: OWL's formula vocabulary
        # is not what the formula validator models. Wiring a FormulaParserFactory
        # into owl2osi was tried and fails every OWL shape, not just one:
        #
        #   IRI.names("<iri>", CodeListElement)  -> Unsupported argument for the
        #                                           fact reference (derivations)
        #   EXISTS( IRI.names(...) )             -> Undefined relationship 'IRI.names'
        #   CatalogRecord.primary_topic          -> Undefined relationship
        #
        # Only the enriched `Formula` imported above carries what FormulaConverter
        # needs, so anything else is skipped: the rule goes undeclared, as it
        # already did, rather than aborting the whole conversion. IATA alone
        # reaches here with 81 such formulas, all OWL code-list enumerations.
        var_name_cache = NameCache()
        for concept in oc.concepts(exclude_builtin=True):
            if concept.derived_by:
                for formula in concept.derived_by:
                    if isinstance(formula, Formula):
                        FormulaConverter(oc, ontology, reasoner, var_name_cache, formula)
        for rel in oc.relationships:
            if rel.derived_by:
                for formula in rel.derived_by:
                    if isinstance(formula, Formula):
                        FormulaConverter(oc, ontology, reasoner, var_name_cache, formula)

    # ------------------------------------------------------------------
    # Reading string generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_reading(rel: Relationship, ontology: OntologyModel) -> str:
        verbalizations = rel.verbalizations
        if not verbalizations:
            return rel.name
        parts = []
        for role in verbalizations[0].roles:
            if role.preceding_text:
                parts.append(role.preceding_text)
            if role.prefix:
                parts.append(role.prefix)
            pyrel_concept = OssieToRelationalAIConverter._concept(ontology, role.concept.name)
            if role.name:
                parts.append(format(pyrel_concept, role.name))
            else:
                parts.append(f"{pyrel_concept}")
            if role.postfix:
                parts.append(role.postfix)
            if role.following_text:
                parts.append(role.following_text)
        return " ".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sort_dependency_graph(concepts: list[Concept]) -> list[str]:
        from ossie_ontology.common.graph import topological_sort
        from relationalai.semantics.frontend.base import CoreConcepts
        nodes = []
        edges = []
        for concept in concepts:
            name = concept.name
            nodes.append(name)
            for parent in concept.extends:
                if parent.name not in CoreConcepts:
                    edges.append((parent.name, name))
        return topological_sort(nodes, edges)
