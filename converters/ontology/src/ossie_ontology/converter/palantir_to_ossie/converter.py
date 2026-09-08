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

"""Palantir `Ontology` -> `OssieOntology`."""

from __future__ import annotations

import re
import warnings

from ossie_ontology.common.graph import topological_sort_break_cycles
from ossie_ontology.common.utils import to_pascal_case, to_verbalization_string
from ossie_ontology.external.palantir.model import (
    ArrayDataType,
    DataSet as PalantirDataSet,
    DataSetColumn,
    DataType,
    IntermediaryRelation,
    ManyToManyRelation,
    ManyToOneRelation,
    ObjectType,
    Ontology as PalantirOntology,
    Property as PalantirProperty,
    Relation,
    Resource,
    Status,
)
from ossie_ontology.model import (
    BUILTIN_CONCEPTS,
    sanitize_identifier,
    Concept,
    ConceptMapping,
    ConceptType,
    Dataset,
    DatasetField,
    DialectExpression,
    DialectExpressionSet,
    Formula,
    FormulaFactory,
    LinkMapping,
    SemanticModel,
    ObjectMapping,
    OntologyComponent,
    OntologyMapping,
    ReferentMapping,
    Relationship,
    RelationshipMultiplicity,
    OssieOntology
)


_DEFAULT_DIALECT = "ANSI_SQL"

# A column whose name is already an identifier needs no quoting in the emitted SQL.
_BARE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PalantirToOssieConverter:
    """Converts a Palantir Ontology to OssieOntology.

    Pass a *formula_factory* to control how Formula objects are created.
    The default produces plain ``Formula`` instances; downstream packages can
    inject a factory that returns enriched subclasses (e.g. with an AST).

        model = PalantirToOssieConverter().convert(palantir_ontology)
        model = PalantirToOssieConverter(formula_factory=my_parser).convert(palantir_ontology)
    """

    depths_role_names = {1: "fst", 2: "snd", 3: "thd", 4: "frt"}

    OBJECT_TYPE_STATUSES: frozenset = frozenset({Status.ACTIVE, Status.ENDORSED, Status.INTERMEDIARY})
    PROPERTY_STATUSES: frozenset = frozenset({Status.ACTIVE, Status.EXPERIMENTAL, Status.INTERMEDIARY})
    MAPPING_PROPERTY_STATUSES: frozenset = frozenset({Status.ACTIVE, Status.INTERMEDIARY})
    RELATION_STATUSES: frozenset = frozenset({Status.ACTIVE, Status.INTERMEDIARY})
    RELATION_ENDPOINT_STATUSES: frozenset = frozenset({Status.ACTIVE})
    SUBTYPE_RELATION_STATUSES: frozenset = frozenset({Status.ACTIVE})

    def allowed_object_type_statuses(self) -> frozenset:
        """Statuses an object type must have to become a concept."""
        return self.OBJECT_TYPE_STATUSES

    def allowed_property_statuses(self) -> frozenset:
        """Statuses a property must have to become a concept relationship."""
        return self.PROPERTY_STATUSES

    def allowed_mapping_property_statuses(self) -> frozenset:
        """Statuses a property must have to be wired into a concept mapping."""
        return self.MAPPING_PROPERTY_STATUSES

    def allowed_relation_statuses(self) -> frozenset:
        """Statuses a relation must have to be converted outright.

        Separate from this, an EXPERIMENTAL relation is still converted when
        every object type it connects is allowed by
        :meth:`allowed_relation_endpoint_statuses`.
        """
        return self.RELATION_STATUSES

    def allowed_relation_endpoint_statuses(self) -> frozenset:
        """Statuses required of the endpoints of an EXPERIMENTAL relation."""
        return self.RELATION_ENDPOINT_STATUSES

    def allowed_subtype_relation_statuses(self) -> frozenset:
        """Statuses a relation must have to be read as inheritance.

        Narrower than :meth:`allowed_relation_statuses` by default: an ordinary
        relation that turns out to be unusable costs one relationship, while a
        subtype relation restructures the concept graph — the subtype loses its
        own identifiers and inherits the supertype's — so only settled ones are
        honoured. As with plain relations, an EXPERIMENTAL subtype relation is
        still read when both endpoints pass
        :meth:`allowed_relation_endpoint_statuses`.
        """
        return self.SUBTYPE_RELATION_STATUSES

    def _object_type_allowed(self, ot: ObjectType) -> bool:
        return ot.status() in self.allowed_object_type_statuses()

    def _relation_endpoint_allowed(self, ot: ObjectType) -> bool:
        return ot.status() in self.allowed_relation_endpoint_statuses()

    @staticmethod
    def _allowed(resource: Resource, statuses: frozenset) -> bool:
        return resource.status() in statuses

    def __init__(self, formula_factory: FormulaFactory | None = None):
        self._formula_factory = formula_factory or FormulaFactory()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def convert(
        self,
        palantir_ontology: PalantirOntology,
        db_name: str = "palantir",
        schema_name: str = "palantir",
    ) -> OssieOntology:
        ontology = OntologyComponent()
        model = OssieOntology(name="Palantir model", ontology=ontology, version="0.1.0")

        semantic_model = SemanticModel(name="Palantir semantic model")

        ontology_mapping = OntologyMapping(name="palantir_map", ontology=ontology, semantic_model=semantic_model)
        model.add_ontology_mapping(ontology_mapping)

        # Per-(concept, dataset) ConceptMappings accumulate here as datasets
        # get created; emitted into the OntologyMapping at the end so they appear in a stable order.
        concept_mappings: list[ConceptMapping] = []

        consumed_subtype_relation_ids = self._convert_concepts(
            ontology, semantic_model, palantir_ontology, concept_mappings, db_name, schema_name
        )
        self._convert_relationships(
            ontology, palantir_ontology, concept_mappings, semantic_model, consumed_subtype_relation_ids
        )

        for cm in concept_mappings:
            ontology_mapping.add_concept_mapping(cm)

        return model

    # ------------------------------------------------------------------
    # Concepts
    # ------------------------------------------------------------------

    def _subtype_relations(self, palantir_ontology: PalantirOntology) -> dict[ObjectType, ManyToOneRelation]:
        """The relations to read as inheritance, keyed by the subtype object type.

        Palantir has no subtype construct. Inheritance shows up as a M:1
        relation whose property map pairs primary key to primary key on both
        sides: every instance of the one side is an instance of the many side,
        identified the same way — which is what an Ossie `extends` says. A
        relation that maps anything else is an ordinary reference and is
        converted as one.

        Reading it here rather than on the Palantir model keeps the status
        policy in one place: a converter that admits experimental object types
        sees the experimental inheritance between them by overriding the same
        kind of hook it already overrides for everything else.
        """
        relation_statuses = self.allowed_subtype_relation_statuses()

        result: dict[ObjectType, ManyToOneRelation] = {}
        for rel in palantir_ontology.relations().values():
            if not isinstance(rel, ManyToOneRelation):
                continue

            one_ot = rel.one_object_type()
            many_ot = rel.many_object_type()

            allowed = self._allowed(rel, relation_statuses) or (
                rel.experimental()
                and self._relation_endpoint_allowed(one_ot)
                and self._relation_endpoint_allowed(many_ot)
            )
            if not allowed:
                continue

            property_map = rel.property_map()
            if not property_map:
                continue

            # Every parent (many-side) primary key must be mapped: _convert_mappings
            # resolves each of them through property_map, so a partially mapped
            # composite key would raise a bare KeyError there. It is also the more
            # accurate reading — identity carried over in part is an ordinary FK.
            is_subtype = all(
                mprop in many_ot.primary_keys() and oprop in one_ot.primary_keys()
                for mprop, oprop in property_map.items()
            ) and all(pk in property_map for pk in many_ot.primary_keys())

            if is_subtype:
                result[one_ot] = rel

        return result

    def _convert_concepts(
        self,
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        palantir_ontology: PalantirOntology,
        concept_mappings: list[ConceptMapping],
        db_name: str,
        schema_name: str,
    ) -> set[str]:
        """Convert the object types, returning the relations read as inheritance.

        The returned guids are what `_convert_relationships` must leave alone:
        they are already carried by an `extends` and would otherwise be emitted
        a second time as ordinary references. This is narrower than
        `_subtype_relations()` — a subtype edge dropped below to break a cycle,
        or one whose concept was already converted, stays an ordinary relation —
        so only this method can say which ones were actually consumed.
        """
        subtype_relations = self._subtype_relations(palantir_ontology)

        nodes = [ot.guid() for ot in palantir_ontology.object_types().values()]
        edges: list[tuple[str, str]] = []
        edge_to_relation_guid: dict[tuple[str, str], str] = {}
        for child, rel in subtype_relations.items():
            parent = rel.many_object_type()
            if child == parent:
                continue
            edge = (parent.guid(), child.guid())
            edges.append(edge)
            edge_to_relation_guid[edge] = rel.guid()

        order, removed_edges = topological_sort_break_cycles(nodes, edges)
        # Subtype edges that would form cycles get dropped by the topo sort —
        # treat them as ignored inheritance below.
        ignore_subtype_relation_ids = {edge_to_relation_guid[e] for e in removed_edges}

        consumed_subtype_relation_ids: set[str] = set()
        for ot_guid in order:
            ot = palantir_ontology.object_types()[ot_guid]
            if self._object_type_allowed(ot):
                consumed = self._convert_object_type(
                    ontology,
                    semantic_model,
                    ot,
                    subtype_relations,
                    ignore_subtype_relation_ids,
                    concept_mappings,
                    db_name,
                    schema_name,
                )
                if consumed is not None:
                    consumed_subtype_relation_ids.add(consumed)

        return consumed_subtype_relation_ids

    def _convert_object_type(
        self,
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        ot: ObjectType,
        subtype_relations: dict[ObjectType, ManyToOneRelation],
        ignore_subtype_relation_ids: set[str],
        concept_mappings: list[ConceptMapping],
        db_name: str,
        schema_name: str,
    ) -> str | None:
        """Returns the guid of the relation converted to an `extends`, if any."""
        concept_name = PalantirToOssieConverter._concept_name(ot)
        if concept_name in BUILTIN_CONCEPTS:
            # Nothing good happens if it goes through: the entity takes the
            # builtin's name, and every column of that builtin type is then typed
            # by this object type. Reported like any other concept-name collision.
            raise ValueError(
                f"ObjectType '{ot.name()}' converts to the concept name '{concept_name}', which is "
                f"a builtin value type. Rename the object type in the export, or exclude it: a "
                f"builtin cannot also be an entity type."
            )
        relevant_props = [
            p for p in ot.properties().values()
            if self._allowed(p, self.allowed_property_statuses())
        ]
        concept: Concept | None = None
        consumed_subtype_relation_id: str | None = None

        if ontology.lookup_concept(concept_name) is None:
            is_subtype = ot in subtype_relations
            subtype_relation = subtype_relations.get(ot)
            ignore_subtype = bool(
                subtype_relation and subtype_relation.guid() in ignore_subtype_relation_ids
            )

            if is_subtype and not ignore_subtype:
                parent_ot = subtype_relation.many_object_type()  # type: ignore[union-attr]
                parent_name = PalantirToOssieConverter._concept_name(parent_ot)
                parent = ontology.lookup_concept(parent_name)
                if parent is None:
                    raise ValueError(
                        f"ObjectType '{ot.name()}' is a subtype of '{parent_ot.name()}', but the "
                        f"parent concept '{parent_name}' was not converted — the object-type status "
                        f"policy excluded it while the subtype-relation policy admitted the relation. "
                        f"Widen the allowed object-type statuses, or narrow the subtype-relation "
                        f"statuses, so the two agree."
                    )
                concept = Concept(name=concept_name, type=ConceptType.ENTITY_TYPE, extends=[parent])
                consumed_subtype_relation_id = subtype_relation.guid()  # type: ignore[union-attr]
            else:
                concept = Concept(name=concept_name, type=ConceptType.ENTITY_TYPE)
            ontology.add_concept(concept)

            for prop in relevant_props:
                self._convert_property(ontology, concept, prop)

            if not is_subtype or ignore_subtype:
                identifiers: dict[str, Relationship] = {}
                # primary_keys() is a set; sort by readable_id so identify_by
                # ordering (and the resulting YAML) is stable across runs.
                for prop in sorted(ot.primary_keys(), key=lambda p: p.readable_id()):
                    prop_name = PalantirToOssieConverter._attribute_name(prop)
                    rel = ontology.lookup_concept_relationship(concept, prop_name)
                    if rel is None:
                        raise ValueError(
                            f"Identifier relationship '{concept_name}.{prop_name}' not found "
                            f"while wiring primary keys for ObjectType '{ot.name()}'."
                        )
                    identifiers[rel.full_name] = rel
                concept.set_identify_by(identifiers)
                # Set multiplicities now that we know which relationship is the sole identifier.
                # A non-composite identifier is OneToOne; all others stay ManyToOne.
                sole = next(iter(identifiers.values())) if len(identifiers) == 1 else None
                for prop in relevant_props:
                    prop_name = PalantirToOssieConverter._attribute_name(prop)
                    prop_rel = ontology.lookup_concept_relationship(concept, prop_name)
                    if prop_rel is not None:
                        mult = RelationshipMultiplicity.ONE_TO_ONE if prop_rel is sole else RelationshipMultiplicity.MANY_TO_ONE
                        prop_rel.set_multiplicity(mult)
        else:
            concept = ontology.lookup_concept(concept_name)
            assert concept is not None
            # Re-encountered concept (multiple datasets feeding the same OT).
            # Verify every relevant property already has its relationship —
            # otherwise the second dataset is contributing fields the first
            # didn't declare, which produces an asymmetric model.
            for prop in relevant_props:
                prop_name = PalantirToOssieConverter._attribute_name(prop)
                if ontology.lookup_concept_relationship(concept, prop_name) is None:
                    raise ValueError(
                        f"Concept '{concept_name}' refers to multiple datasets but not all "
                        f"contain the '{prop_name}' property."
                    )

        self._convert_mappings(
            ontology, semantic_model, ot, subtype_relations, concept, concept_mappings, db_name, schema_name
        )

        return consumed_subtype_relation_id

    def _convert_property(self, ontology: OntologyComponent, concept: Concept, prop: PalantirProperty) -> None:
        def madlib_decl(c: Concept, p: PalantirProperty) -> str:
            return (
                f"{{{c}}} {p.readable_id()} "
                f"{PalantirToOssieConverter._type_to_madlib_suffix(p.type())}"
            )

        prop_name = PalantirToOssieConverter._attribute_name(prop)
        if ontology.lookup_concept_relationship(concept, prop_name) is not None:
            return

        relates = self._convert_property_type_roles(ontology, [], prop.type())

        ontology.add_relationship(Relationship(
            name=prop_name,
            container=concept,
            relates=relates,
            verbalizes=[madlib_decl(concept, prop)],
        ))

    # ------------------------------------------------------------------
    # Mappings: ConceptMapping per (concept, dataset)
    # ------------------------------------------------------------------

    def _convert_mappings(
        self,
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        ot: ObjectType,
        subtype_relations: dict[ObjectType, ManyToOneRelation],
        concept: Concept,
        concept_mappings: list[ConceptMapping],
        db_name: str,
        schema_name: str,
    ) -> None:
        if not ot.has_syncs_from():
            return

        parent_concept: Concept | None = None
        subtype_relation = subtype_relations.get(ot)

        if subtype_relation is not None:
            parent_ot = subtype_relation.many_object_type()
            parent_concept = ontology.lookup_concept(
                PalantirToOssieConverter._concept_name(parent_ot)
            )
            property_map = subtype_relation.property_map()
            # primary_keys() is a set; sort by readable_id — as identify_by does
            # above — so referent_mappings (and the resulting YAML) come out in
            # the same order on every run.
            identifier_props = sorted(parent_ot.primary_keys(), key=lambda p: p.readable_id())

            def resolve(p: PalantirProperty) -> PalantirProperty:
                return property_map[p]
        else:
            identifier_props = sorted(ot.primary_keys(), key=lambda p: p.readable_id())

            def resolve(p: PalantirProperty) -> PalantirProperty:
                return p

        for palantir_ds in ot.syncs_from():
            dataset = self._convert_dataset(semantic_model, ontology, ot, palantir_ds, db_name, schema_name)

            # Build referent_mappings that locate `concept` instances by
            # walking the (effective) identifying relationships against this
            # dataset's columns.
            id_referents: list[ReferentMapping] = []
            for prop in identifier_props:
                prop_name = PalantirToOssieConverter._attribute_name(prop)
                # For subtypes, identifying relationships live on the parent
                # concept; the child reaches them via `lookup_concept_relationship`.
                rel = ontology.lookup_concept_relationship(concept, prop_name)
                if rel is None:
                    continue
                field = PalantirToOssieConverter._get_dataset_field_by_palantir_property(
                    resolve(prop), palantir_ds, dataset
                )
                if field is None:
                    continue
                id_referents.append(ReferentMapping(relationship=rel, expression=field))

            if not id_referents:
                warnings.warn(
                    f"No identifying fields found for concept '{concept.name}' in dataset "
                    f"'{dataset.name}'; skipping concept mapping."
                )
                continue

            cm = ConceptMapping(concept=concept)

            # object_mappings: how to construct/identify this concept's
            # instances from this dataset. Always uses referent_mappings to
            # walk the identifying relationships (whether own or inherited).
            cm.object_mappings.append(
                ObjectMapping(
                    concept=parent_concept,
                    referent_mappings=id_referents,
                )
            )

            # link_mappings: the root identifies the source object (same as
            # object_mapping), children populate each property relationship.
            children: list[LinkMapping] = []
            primary_keys = set(ot.primary_keys())
            for prop in ot.properties().values():
                if not self._allowed(prop, self.allowed_mapping_property_statuses()):
                    continue
                if prop in primary_keys:
                    continue
                if not prop.pk_mapping() and prop.datasource_resource_id() != palantir_ds.guid():
                    continue
                if isinstance(prop.type(), ArrayDataType):
                    warnings.warn(
                        f"Skipping property '{prop.readable_id()}'. Array datatype is not supported"
                    )
                    continue

                prop_name = PalantirToOssieConverter._attribute_name(prop)
                relationship = ontology.lookup_concept_relationship(concept, prop_name)
                if relationship is None:
                    continue
                field = PalantirToOssieConverter._get_dataset_field_by_palantir_property(
                    prop, palantir_ds, dataset
                )
                if field is None:
                    continue
                value_concept = relationship.last_role.player
                children.append(
                    LinkMapping(
                        object_mapping=ObjectMapping(concept=value_concept, expression=field),
                        relationship=relationship,
                    )
                )

            if children:
                cm.link_mappings.append(
                    LinkMapping(
                        object_mapping=ObjectMapping(
                            concept=parent_concept,
                            referent_mappings=id_referents,
                        ),
                        children=children,
                    )
                )

            concept_mappings.append(cm)

    # ------------------------------------------------------------------
    # Relations (M:1, M:M, intermediary)
    # ------------------------------------------------------------------

    def _convert_relationships(
        self,
        ontology: OntologyComponent,
        palantir_ontology: PalantirOntology,
        concept_mappings: list[ConceptMapping],
        semantic_model: SemanticModel,
        consumed_subtype_relation_ids: set[str],
    ) -> None:
        for rel in palantir_ontology.relations().values():
            # Already converted as an `extends` on the subtype concept; emitting
            # it here as well would state the same fact twice.
            if rel.guid() in consumed_subtype_relation_ids:
                continue
            if self._allowed(rel, self.allowed_relation_statuses()):
                self._convert_relation(ontology, rel, concept_mappings, semantic_model)
            elif (
                isinstance(rel, ManyToOneRelation)
                and rel.experimental()
                and self._relation_endpoint_allowed(rel.one_object_type())
                and self._relation_endpoint_allowed(rel.many_object_type())
            ):
                self._convert_relation(ontology, rel, concept_mappings, semantic_model)

        for ir in palantir_ontology.intermediary_relations().values():
            if self._allowed(ir, self.allowed_relation_statuses()):
                self._convert_intermediary_relation(ontology, palantir_ontology, ir)
            elif (
                ir.experimental()
                and self._relation_endpoint_allowed(ir.role_a_player())
                and self._relation_endpoint_allowed(ir.role_b_player())
                and self._relation_endpoint_allowed(ir.intermediary_player())
            ):
                self._convert_intermediary_relation(ontology, palantir_ontology, ir)

    def _convert_relation(
        self,
        ontology: OntologyComponent,
        relation: Relation,
        concept_mappings: list[ConceptMapping],
        semantic_model: SemanticModel,
    ) -> None:
        if isinstance(relation, ManyToOneRelation):
            self._convert_many_to_one(ontology, relation, concept_mappings, semantic_model)
        elif isinstance(relation, ManyToManyRelation):
            self._convert_many_to_many(ontology, relation)

    def _add_relationship_if_name_free(
        self, ontology: OntologyComponent, concept: Concept, relationship: Relationship
    ) -> bool:
        """Add *relationship* unless its name is already taken on *concept*.

        `add_relationship` raises on a duplicate name, which would abort the
        whole conversion over one relation. An export where a property and a link
        share a name is ordinary — the FK column and the link it backs are often
        named alike — so the link is dropped and the run continues.
        `_convert_property` skips the same collision from the other side.

        Renaming instead (suffixing, as `to_verbalization_string` does for
        keywords) would keep the link, at the cost of emitted names that no
        longer match the export.
        """
        if ontology.lookup_concept_relationship(concept, relationship.name) is not None:
            warnings.warn(
                f"Relation '{relationship.full_name}' collides with an existing relationship on "
                f"the concept (a property and a link, or two links, sharing a name); "
                f"skipping the link."
            )
            return False
        ontology.add_relationship(relationship)
        return True

    def _convert_many_to_one(
        self,
        ontology: OntologyComponent,
        rel: ManyToOneRelation,
        concept_mappings: list[ConceptMapping],
        semantic_model: SemanticModel,
    ) -> None:
        mot = rel.many_object_type()
        mot_name = PalantirToOssieConverter._concept_name(mot)
        mot_concept = ontology.lookup_concept(mot_name)
        oot = rel.one_object_type()
        oot_name = PalantirToOssieConverter._concept_name(oot)
        oot_concept = ontology.lookup_concept(oot_name)
        if mot_concept is None or oot_concept is None:
            return
        prop_name = PalantirToOssieConverter._attribute_name(rel)

        if mot_concept is oot_concept:
            verbalize = f"{{{mot_concept}}} {prop_name} {{{oot_concept}:snd}}"
            relates: list[tuple[Concept, str | None]] = [(oot_concept, "snd")]
        else:
            verbalize = f"{{{mot_concept}}} {prop_name} {{{oot_concept}}}"
            relates = [(oot_concept, None)]

        relationship = Relationship(
            name=prop_name,
            container=mot_concept,
            relates=relates,
            verbalizes=[verbalize],
            multiplicity=RelationshipMultiplicity.MANY_TO_ONE,
        )
        if not self._add_relationship_if_name_free(ontology, mot_concept, relationship):
            return

        if mot.has_syncs_from():
            self._attach_link_to_concept_mappings(
                ontology, rel, relationship, mot, mot_concept, oot_concept, concept_mappings, semantic_model
            )
        else:
            # No many-side datasets: fall back to a derived_by formula that
            # equates FK columns.
            frags = [
                f"{relationship.first_role.name}.{PalantirToOssieConverter._attribute_name(mprop)}"
                f" == {relationship.last_role.name}.{PalantirToOssieConverter._attribute_name(oprop)}"
                for mprop, oprop in rel.property_map().items()
            ]
            if frags:
                formula = self._formula_factory(raw_expr=" AND ".join(frags), parent=relationship, ontology=ontology)
                relationship.add_derived_by(formula)

    def _attach_link_to_concept_mappings(
        self,
        ontology: OntologyComponent,
        rel: ManyToOneRelation,
        relationship: Relationship,
        mot: ObjectType,
        mot_concept: Concept,
        oot_concept: Concept,
        concept_mappings: list[ConceptMapping],
        semantic_model: SemanticModel,
    ) -> None:
        """For each (mot_concept, dataset) ConceptMapping, append a link_mapping
        child that walks the target concept's identifying relationships through
        the source's FK columns."""
        property_map = rel.property_map()
        if not property_map:
            return

        # Resolve target (oot) identifying relationships once.
        target_id_rels: list[tuple[Relationship, PalantirProperty]] = []
        for mprop, oprop in property_map.items():
            oot_attr = PalantirToOssieConverter._attribute_name(oprop)
            id_rel = ontology.lookup_concept_relationship(oot_concept, oot_attr)
            if id_rel is None:
                return
            target_id_rels.append((id_rel, mprop))

        for palantir_ds in mot.syncs_from():
            ds_name = (
                f"{PalantirToOssieConverter._concept_name(mot)}_{palantir_ds.readable_id()}"
            )
            dataset = semantic_model.lookup_dataset(ds_name)
            if dataset is None:
                continue

            cm = PalantirToOssieConverter._find_concept_mapping(concept_mappings, mot_concept, dataset)
            if cm is None:
                warnings.warn(
                    f"No ConceptMapping for entity '{mot_concept.name}' and dataset "
                    f"'{ds_name}'; cannot attach link '{relationship.full_name}'"
                )
                continue

            # Build referent_mappings that look up the target via FK columns.
            referents: list[ReferentMapping] = []
            resolved = True
            for id_rel, mprop in target_id_rels:
                fk_field = PalantirToOssieConverter._get_dataset_field_by_palantir_property(
                    mprop, palantir_ds, dataset
                )
                if fk_field is None:
                    resolved = False
                    break
                referents.append(ReferentMapping(relationship=id_rel, expression=fk_field))
            if not resolved:
                continue

            child = LinkMapping(
                object_mapping=ObjectMapping(concept=oot_concept, referent_mappings=referents),
                relationship=relationship,
            )
            # Attach as a child on the root link_mapping (the identifying tree).
            if cm.link_mappings:
                root = cm.link_mappings[0]
                if root.children is None:
                    root.children = []
                root.children.append(child)
            else:
                if not cm.object_mappings:
                    raise ValueError(
                        f"Cannot attach link '{relationship.full_name}': concept "
                        f"'{mot_concept.name}' has no identifying object mapping "
                        f"to use as the link root."
                    )
                root_om = cm.object_mappings[0]
                cm.link_mappings.append(LinkMapping(
                    object_mapping=ObjectMapping(
                        concept=root_om.concept,
                        referent_mappings=root_om.referent_mappings,
                    ),
                    children=[child],
                ))

    @staticmethod
    def _find_concept_mapping(
        concept_mappings: list[ConceptMapping],
        concept: Concept,
        dataset: Dataset,
    ) -> ConceptMapping | None:
        """Resolve the ConceptMapping built for this (concept, dataset).

        When several datasets feed the same concept there is one ConceptMapping
        per dataset, and only the one identified by *this* dataset's columns will
        do: the caller attaches expressions built from this dataset to whatever
        comes back, so any other mapping would join two unrelated tables. There
        is no useful second choice, and a concept whose mapping for this dataset
        was dropped (nothing in it identified the concept) has none at all — so
        None, and the caller reports the link it cannot attach.
        """
        return next(
            (
                cm
                for cm in concept_mappings
                if cm.concept is concept
                and PalantirToOssieConverter._references_dataset(cm, dataset)
            ),
            None,
        )

    @staticmethod
    def _references_dataset(cm: ConceptMapping, dataset: Dataset) -> bool:
        """True iff an identifying expression in `cm` points to a field of `dataset`.

        Covers both shapes an ObjectMapping identifies objects by — an
        expression of its own, or referent mappings walking the identifying
        relationships — since either can be the one naming the dataset.
        """
        def is_field_of(expression: DatasetField | Formula | None) -> bool:
            return isinstance(expression, DatasetField) and expression.dataset is dataset

        return any(
            is_field_of(om.expression)
            or any(is_field_of(rm.expression) for rm in (om.referent_mappings or []))
            for om in cm.object_mappings
        )

    def _convert_many_to_many(self, ontology: OntologyComponent, rel: ManyToManyRelation) -> None:
        aot = rel.role_a_player()
        aot_concept = ontology.lookup_concept(PalantirToOssieConverter._concept_name(aot))
        bot = rel.role_b_player()
        bot_concept = ontology.lookup_concept(PalantirToOssieConverter._concept_name(bot))
        if aot_concept is None or bot_concept is None:
            return
        rel_name = PalantirToOssieConverter._attribute_name(rel)

        if aot_concept is bot_concept:
            verbalize = f"{{{aot_concept}}} {rel_name} {{{bot_concept}:snd}}"
            relates: list[tuple[Concept, str | None]] = [(bot_concept, "snd")]
        else:
            verbalize = f"{{{aot_concept}}} {rel_name} {{{bot_concept}}}"
            relates = [(bot_concept, None)]

        relationship = Relationship(
            name=rel_name,
            container=aot_concept,
            relates=relates,
            verbalizes=[verbalize],
            multiplicity=None,
        )
        self._add_relationship_if_name_free(ontology, aot_concept, relationship)

    def _convert_intermediary_relation(
        self,
        ontology: OntologyComponent,
        palantir_ontology: PalantirOntology,
        rel: IntermediaryRelation,
    ) -> None:
        aot = rel.role_a_player()
        aot_name = PalantirToOssieConverter._concept_name(aot)
        aot_concept = ontology.lookup_concept(aot_name)
        bot = rel.role_b_player()
        bot_name = PalantirToOssieConverter._concept_name(bot)
        bot_concept = ontology.lookup_concept(bot_name)
        if aot_concept is None or bot_concept is None:
            return
        rel_name = PalantirToOssieConverter._attribute_name(rel)

        if aot_concept is bot_concept:
            verbalize = f"{{{aot_concept}}} {rel_name} {{{bot_concept}:snd}}"
            relates: list[tuple[Concept, str | None]] = [(bot_concept, "snd")]
        else:
            verbalize = f"{{{aot_concept}}} {rel_name} {{{bot_concept}}}"
            relates = [(bot_concept, None)]

        relationship = Relationship(
            name=rel_name,
            container=aot_concept,
            relates=relates,
            verbalizes=[verbalize],
        )
        if not self._add_relationship_if_name_free(ontology, aot_concept, relationship):
            return

        rel_a = palantir_ontology.relations()[rel.relation_a()]
        rel_a_name = PalantirToOssieConverter._attribute_name(rel_a)
        rel_b = palantir_ontology.relations()[rel.relation_b()]
        rel_b_name = PalantirToOssieConverter._attribute_name(rel_b)

        fp_a_ot, sp_a_ot = PalantirToOssieConverter._relation_players(rel_a)
        fp_a = PalantirToOssieConverter._concept_name(fp_a_ot)
        sp_a = PalantirToOssieConverter._concept_name(sp_a_ot)
        fp_b_ot, sp_b_ot = PalantirToOssieConverter._relation_players(rel_b)
        fp_b = PalantirToOssieConverter._concept_name(fp_b_ot)
        sp_b = PalantirToOssieConverter._concept_name(sp_b_ot)

        assert (aot_name == fp_a and bot_name == fp_b) or (
            aot_name == sp_a and bot_name == sp_b
        ), f"Invalid intermediary relation '{rel_name}' arguments."

        join_condition = (
            f"{fp_a}.{rel_a_name}({relationship.first_role.name}) AND "
            f"{fp_b}.{rel_b_name}({relationship.last_role.name})"
        )
        formula = self._formula_factory(raw_expr=join_condition, parent=relationship, ontology=ontology)
        relationship.add_derived_by(formula)

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    def _convert_dataset(
        self,
        semantic_model: SemanticModel,
        ontology: OntologyComponent,
        ot: ObjectType,
        palantir_ds: PalantirDataSet,
        db_name: str,
        schema_name: str,
    ) -> Dataset:
        ds_name = f"{PalantirToOssieConverter._concept_name(ot)}_{palantir_ds.readable_id()}"
        existing = semantic_model.lookup_dataset(ds_name)
        if existing is not None:
            return existing

        fields: list[DatasetField] = []
        for column in palantir_ds.columns():
            # type() may be None for a partially-specified schema; only ARRAY
            # columns are skipped, and a missing type falls back to String below.
            col_type = column.type()
            if col_type is not None and col_type.upper() == "ARRAY":
                continue
            field_name = PalantirToOssieConverter._normalize_field_name(column.name())
            fields.append(
                DatasetField(
                    name=field_name,
                    expression=DialectExpressionSet(
                        dialects=[
                            DialectExpression(
                                dialect=_DEFAULT_DIALECT,
                                expression=PalantirToOssieConverter._column_expression(column.name()),
                            )
                        ]
                    ),
                    type=PalantirToOssieConverter._resolve_field_type(ontology, palantir_ds, column),
                )
            )

        dataset = Dataset(
            name=ds_name,
            source=f"{db_name}.{schema_name}.{palantir_ds.readable_id()}",
            fields=fields,
            description=palantir_ds.description(),
        )
        semantic_model.add_dataset(dataset)
        return dataset

    @staticmethod
    def _resolve_field_type(
        ontology: OntologyComponent, palantir_ds: PalantirDataSet, column: DataSetColumn
    ) -> Concept:
        try:
            type_str = (
                DataType.parse_datatype(column.type()).to_type() if column.type() else "String"
            )
        except ValueError:
            # A type this export spells differently, or one Palantir has added:
            # the same fallback a column with no type at all already takes.
            warnings.warn(
                f"Unrecognized column type '{column.type()}' on "
                f"'{palantir_ds.readable_id()}.{column.name()}'; falling back to String."
            )
            type_str = "String"

        concept = ontology.ensure_builtin_concept(type_str)
        if not concept:
            raise ValueError(
                f"Concept '{type_str}' is not defined in the ontology but used in the "
                f"DatasetField '{palantir_ds.readable_id()}.{column.name()}'."
            )
        return concept

    # ------------------------------------------------------------------
    # Naming / typing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _attribute_name(prop: PalantirProperty | Relation) -> str:
        return to_verbalization_string(prop.readable_id())

    @staticmethod
    def _concept_name(ot: ObjectType) -> str:
        return to_pascal_case(ot.name())

    @staticmethod
    def _type_to_madlib_suffix(type_, arr_depth: int = 1) -> str:
        if isinstance(type_, ArrayDataType):
            depth = arr_depth
            return (
                f"{{Integer:{PalantirToOssieConverter._depth_role_name(depth)}}} maps to "
                f"{PalantirToOssieConverter._type_to_madlib_suffix(type_.base_type(), depth + 1)}"
            )
        return f"{{{type_.to_type()}}}"

    def _convert_property_type_roles(
        self, ontology: OntologyComponent, roles: list[tuple[Concept, str | None]], type_, arr_depth: int = 1
    ) -> list[tuple[Concept, str | None]]:
        if isinstance(type_, ArrayDataType):
            integer = ontology.ensure_builtin_concept("Integer")
            if integer is None:
                raise ValueError("Builtin 'Integer' could not be resolved for array role.")
            roles.append((integer, PalantirToOssieConverter._depth_role_name(arr_depth)))
            self._convert_property_type_roles(ontology, roles, type_.base_type(), arr_depth + 1)
        else:
            target = ontology.ensure_builtin_concept(type_.to_type())
            if target is None:
                raise ValueError(
                    f"Type concept '{type_.to_type()}' is not defined in the ontology."
                )
            roles.append((target, None))
        return roles

    @staticmethod
    def _relation_players(rel: Relation) -> tuple[ObjectType, ObjectType]:
        """Return the (first, second) role-player object types of a binary
        relation, regardless of its concrete relation type."""
        if isinstance(rel, ManyToOneRelation):
            return rel.many_object_type(), rel.one_object_type()
        if isinstance(rel, (ManyToManyRelation, IntermediaryRelation)):
            return rel.role_a_player(), rel.role_b_player()
        raise ValueError(
            f"Unsupported relation type '{type(rel).__name__}' for relation {rel.readable_id()}"
        )

    @staticmethod
    def _depth_role_name(depth: int) -> str:
        name = PalantirToOssieConverter.depths_role_names.get(depth)
        if not name:
            raise ValueError(f"Array types of depth {depth} are not supported")
        return name

    @staticmethod
    def _get_dataset_field_by_palantir_property(
        prop: PalantirProperty, palantir_ds: PalantirDataSet, dataset: Dataset
    ) -> DatasetField | None:
        column_name = prop.column_name()
        pk_mapping = prop.pk_mapping()
        ds_guid = palantir_ds.guid()
        if pk_mapping:
            if ds_guid not in pk_mapping:
                raise ValueError(
                    f"Primary key mapping for Palantir DataSet '{palantir_ds.readable_id()}' "
                    f"is missing property '{PalantirToOssieConverter._attribute_name(prop)}'"
                )
            column_name = pk_mapping[ds_guid]
        if not column_name:
            return None
        normalized = PalantirToOssieConverter._normalize_field_name(column_name)
        field = dataset.field(normalized)
        if not field:
            # Fall back to a case-insensitive match. The two halves of an export
            # routinely disagree on case — `primaryKeyMapping` names the physical
            # warehouse column (`LOCNO`) while the property carries the API
            # spelling (`locno`) — and an unquoted Snowflake identifier is
            # case-insensitive anyway, so treating them as distinct only loses
            # the mapping. Exact match still wins, so a source that genuinely
            # relies on quoted, case-sensitive columns is unaffected.
            lowered = normalized.lower()
            field = next((f for f in dataset.fields if f.name.lower() == lowered), None)
        if not field:
            warnings.warn(f"Dataset '{dataset.name}' does not contain a field named '{column_name}'")
        return field

    @staticmethod
    def _normalize_field_name(name: str) -> str:
        """A dataset field name that is a usable identifier.

        Everything a bare identifier cannot hold — spaces, dots, dashes, quotes —
        becomes `_`, because a mapping expression naming this field is matched
        against an identifier pattern when the spec is read back, and a field
        name that fails it is silently taken for a formula instead of resolving
        to the field.
        """
        normalized = sanitize_identifier(name)
        if normalized and normalized[0].isdigit():
            normalized = f"_{normalized}"
        return normalized

    @staticmethod
    def _column_expression(column_name: str) -> str:
        """The SQL reading this physical column.

        Separate from the field name above: that one is sanitized and so may no
        longer spell the column, while this has to go on naming it exactly —
        quoted when the name is not a bare identifier.
        """
        if _BARE_IDENTIFIER_RE.match(column_name):
            return column_name
        escaped = column_name.replace('"', '""')
        return f'"{escaped}"'
