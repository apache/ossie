"""Palantir `Ontology` -> `OsiOntology`."""

from __future__ import annotations

import warnings

from osi.common.graph import topological_sort_break_cycles
from osi.common.utils import to_pascal_case, to_verbalization_string
from osi.external.palantir.model import (
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
)
from osi.model import (
    Concept,
    ConceptMapping,
    ConceptType,
    Dataset,
    DatasetField,
    DialectExpression,
    DialectExpressionSet,
    Formula,
    LinkMapping,
    SemanticModel,
    ObjectMapping,
    OntologyComponent,
    OntologyMapping,
    ReferentMapping,
    Relationship,
    RelationshipMultiplicity,
    OsiOntology
)


_DEFAULT_DIALECT = "ANSI_SQL"


class PalantirToOsiConverter:
    """Top-level converter. Use `convert(ontology_model)` to obtain an `OsiOntology`."""

    depths_role_names = {1: "fst", 2: "snd", 3: "thd", 4: "frt"}

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    @staticmethod
    def convert(
        palantir_ontology: PalantirOntology,
        db_name: str = "palantir",
        schema_name: str = "palantir",
    ) -> OsiOntology:
        ontology = OntologyComponent()
        model = OsiOntology(name="Palantir model", ontology=ontology, version="0.1.0")

        semantic_model = SemanticModel(name="Palantir semantic model")

        ontology_mapping = OntologyMapping(name="palantir_map", ontology=ontology, semantic_model=semantic_model)
        model.add_ontology_mapping(ontology_mapping)

        # Per-(concept, dataset) ConceptMappings accumulate here as datasets
        # get created; emitted into the OntologyMapping at the end so they appear in a stable order.
        concept_mappings: list[ConceptMapping] = []

        PalantirToOsiConverter._convert_concepts(
            ontology, semantic_model, palantir_ontology, concept_mappings, db_name, schema_name
        )
        PalantirToOsiConverter._convert_relationships(
            ontology, palantir_ontology, concept_mappings, semantic_model
        )

        for cm in concept_mappings:
            ontology_mapping.add_concept_mapping(cm)

        return model

    # ------------------------------------------------------------------
    # Concepts
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_concepts(
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        palantir_ontology: PalantirOntology,
        concept_mappings: list[ConceptMapping],
        db_name: str,
        schema_name: str,
    ) -> None:
        subtype_relations = palantir_ontology.subtypes_relations()

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

        for ot_guid in order:
            ot = palantir_ontology.object_types()[ot_guid]
            if ot.active() or ot.endorsed() or ot.intermediary():
                PalantirToOsiConverter._convert_object_type(
                    ontology,
                    semantic_model,
                    ot,
                    subtype_relations,
                    ignore_subtype_relation_ids,
                    concept_mappings,
                    db_name,
                    schema_name,
                )

    @staticmethod
    def _convert_object_type(
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        ot: ObjectType,
        subtype_relations: dict[ObjectType, ManyToOneRelation],
        ignore_subtype_relation_ids: set[str],
        concept_mappings: list[ConceptMapping],
        db_name: str,
        schema_name: str,
    ) -> None:
        concept_name = PalantirToOsiConverter._concept_name(ot)
        relevant_props = [
            p for p in ot.properties().values() if p.active() or p.experimental() or p.intermediary()
        ]
        concept: Concept | None = None

        if ontology.lookup_concept(concept_name) is None:
            is_subtype = ot in subtype_relations
            subtype_relation = subtype_relations.get(ot)
            ignore_subtype = bool(
                subtype_relation and subtype_relation.guid() in ignore_subtype_relation_ids
            )

            if is_subtype and not ignore_subtype:
                parent_ot = subtype_relation.many_object_type()  # type: ignore[union-attr]
                parent_name = PalantirToOsiConverter._concept_name(parent_ot)
                parent = ontology.lookup_concept(parent_name)
                assert parent is not None, f"Parent concept '{parent_name}' not found (expected from topological order)"
                concept = Concept(name=concept_name, type=ConceptType.ENTITY_TYPE, extends=[parent])
            else:
                concept = Concept(name=concept_name, type=ConceptType.ENTITY_TYPE)
            ontology.add_concept(concept)

            for prop in relevant_props:
                PalantirToOsiConverter._convert_property(ontology, concept, prop)

            if not is_subtype or ignore_subtype:
                identifiers: dict[str, Relationship] = {}
                for prop in ot.primary_keys():
                    prop_name = PalantirToOsiConverter._attribute_name(prop)
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
                    prop_name = PalantirToOsiConverter._attribute_name(prop)
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
                prop_name = PalantirToOsiConverter._attribute_name(prop)
                if ontology.lookup_concept_relationship(concept, prop_name) is None:
                    raise ValueError(
                        f"Concept '{concept_name}' refers to multiple datasets but not all "
                        f"contain the '{prop_name}' property."
                    )

        PalantirToOsiConverter._convert_mappings(
            ontology, semantic_model, ot, subtype_relations, concept, concept_mappings, db_name, schema_name
        )

    @staticmethod
    def _convert_property(ontology: OntologyComponent, concept: Concept, prop: PalantirProperty) -> None:
        def madlib_decl(c: Concept, p: PalantirProperty) -> str:
            return (
                f"{{{c}}} {p.readable_id()} "
                f"{PalantirToOsiConverter._type_to_madlib_suffix(p.type())}"
            )

        prop_name = PalantirToOsiConverter._attribute_name(prop)
        if ontology.lookup_concept_relationship(concept, prop_name) is not None:
            return

        relates: list[tuple[Concept, str | None]] = []
        relates = PalantirToOsiConverter._convert_property_type_roles(ontology, relates, prop.type())

        ontology.add_relationship(Relationship(
            name=prop_name,
            container=concept,
            relates=relates,
            verbalizes=[madlib_decl(concept, prop)],
        ))

    # ------------------------------------------------------------------
    # Mappings: ConceptMapping per (concept, dataset)
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_mappings(
        ontology: OntologyComponent,
        semantic_model: SemanticModel,
        ot: ObjectType,
        subtype_relations: dict[ObjectType, ManyToOneRelation],
        concept: Concept,
        concept_mappings: list[ConceptMapping],
        db_name: str,
        schema_name: str,
    ) -> None:
        if not ot._syncs_from:
            return

        parent_concept: Concept | None = None
        subtype_relation = subtype_relations.get(ot)

        if subtype_relation is not None:
            parent_ot = subtype_relation.many_object_type()
            parent_concept = ontology.lookup_concept(
                PalantirToOsiConverter._concept_name(parent_ot)
            )
            property_map = subtype_relation.property_map()
            identifier_props = list(parent_ot.primary_keys())

            def resolve(p: PalantirProperty) -> PalantirProperty:
                return property_map[p]
        else:
            identifier_props = list(ot.primary_keys())

            def resolve(p: PalantirProperty) -> PalantirProperty:
                return p

        for palantir_ds in ot.syncs_from():
            dataset = PalantirToOsiConverter._convert_dataset(
                semantic_model, ontology, ot, palantir_ds, db_name, schema_name
            )

            # Build referent_mappings that locate `concept` instances by
            # walking the (effective) identifying relationships against this
            # dataset's columns.
            id_referents: list[ReferentMapping] = []
            for prop in identifier_props:
                prop_name = PalantirToOsiConverter._attribute_name(prop)
                # For subtypes, identifying relationships live on the parent
                # concept; the child reaches them via `lookup_concept_relationship`.
                rel = ontology.lookup_concept_relationship(concept, prop_name)
                if rel is None:
                    continue
                field = PalantirToOsiConverter._get_dataset_field_by_palantir_property(
                    resolve(prop), palantir_ds, dataset
                )
                if field is None:
                    continue
                id_referents.append(ReferentMapping(relationship=rel, expression=field))

            cm = ConceptMapping(concept=concept)

            # object_mappings: how to construct/identify this concept's
            # instances from this dataset. Always uses referent_mappings to
            # walk the identifying relationships (whether own or inherited).
            cm.object_mappings.append(
                ObjectMapping(
                    concept=parent_concept,
                    referent_mappings=list(id_referents) if id_referents else None,
                )
            )

            # link_mappings: the root identifies the source object (same as
            # object_mapping), children populate each property relationship.
            children: list[LinkMapping] = []
            primary_keys = set(ot.primary_keys())
            for prop in ot.properties().values():
                if not (prop.active() or prop.intermediary()):
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

                prop_name = PalantirToOsiConverter._attribute_name(prop)
                relationship = ontology.lookup_concept_relationship(concept, prop_name)
                if relationship is None:
                    continue
                field = PalantirToOsiConverter._get_dataset_field_by_palantir_property(
                    prop, palantir_ds, dataset
                )
                if field is None:
                    continue
                value_concept = relationship.last_role.player
                children.append(
                    LinkMapping(
                        object_mapping=ObjectMapping(concept=value_concept,expression=field),
                        relationship=relationship,
                    )
                )

            if id_referents or children:
                cm.link_mappings.append(
                    LinkMapping(
                        object_mapping=ObjectMapping(
                            concept=parent_concept,
                            referent_mappings=list(id_referents) if id_referents else None,
                        ),
                        children=children if children else None,
                    )
                )

            concept_mappings.append(cm)

    # ------------------------------------------------------------------
    # Relations (M:1, M:M, intermediary)
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_relationships(
        ontology: OntologyComponent,
        palantir_ontology: PalantirOntology,
        concept_mappings: list[ConceptMapping],
        semantic_model: SemanticModel,
    ) -> None:
        for rel in palantir_ontology.relations().values():
            if rel.active() or rel.intermediary():
                PalantirToOsiConverter._convert_relation(ontology, rel, concept_mappings, semantic_model)
            elif (
                isinstance(rel, ManyToOneRelation)
                and rel.experimental()
                and rel.one_object_type().active()
                and rel.many_object_type().active()
            ):
                PalantirToOsiConverter._convert_relation(ontology, rel, concept_mappings, semantic_model)

        for ir in palantir_ontology.intermediary_relations().values():
            if ir.active() or ir.intermediary():
                PalantirToOsiConverter._convert_intermediary_relation(ontology, palantir_ontology, ir)
            elif (
                ir.experimental()
                and ir.role_a_player().active()
                and ir.role_b_player().active()
                and ir.intermediary_player().active()
            ):
                PalantirToOsiConverter._convert_intermediary_relation(ontology, palantir_ontology, ir)

    @staticmethod
    def _convert_relation(
        ontology: OntologyComponent,
        relation: Relation,
        concept_mappings: list[ConceptMapping],
        semantic_model: SemanticModel,
    ) -> None:
        if isinstance(relation, ManyToOneRelation):
            PalantirToOsiConverter._convert_many_to_one(
                ontology, relation, concept_mappings, semantic_model
            )
        elif isinstance(relation, ManyToManyRelation):
            PalantirToOsiConverter._convert_many_to_many(ontology, relation)

    @staticmethod
    def _convert_many_to_one(
        ontology: OntologyComponent,
        rel: ManyToOneRelation,
        concept_mappings: list[ConceptMapping],
        semantic_model: SemanticModel,
    ) -> None:
        mot = rel.many_object_type()
        mot_name = PalantirToOsiConverter._concept_name(mot)
        mot_concept = ontology.lookup_concept(mot_name)
        oot = rel.one_object_type()
        oot_name = PalantirToOsiConverter._concept_name(oot)
        oot_concept = ontology.lookup_concept(oot_name)
        if mot_concept is None or oot_concept is None:
            return
        prop_name = PalantirToOsiConverter._attribute_name(rel)

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
        ontology.add_relationship(relationship)

        if mot._syncs_from:
            PalantirToOsiConverter._attach_link_to_concept_mappings(
                ontology, rel, relationship, mot, mot_concept, oot_concept, concept_mappings, semantic_model
            )
        else:
            # No many-side datasets: fall back to a derived_by formula that
            # equates FK columns.
            frags = [
                f"{relationship.first_role.name}.{PalantirToOsiConverter._attribute_name(mprop)}"
                f" == {relationship.last_role.name}.{PalantirToOsiConverter._attribute_name(oprop)}"
                for mprop, oprop in rel.property_map().items()
            ]
            if frags:
                formula = Formula(raw_expr=" AND ".join(frags), parent=relationship)
                relationship.add_derived_by(formula)
                ontology.add_rule(formula)

    @staticmethod
    def _attach_link_to_concept_mappings(
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
            oot_attr = PalantirToOsiConverter._attribute_name(oprop)
            id_rel = ontology.lookup_concept_relationship(oot_concept, oot_attr)
            if id_rel is None:
                return
            target_id_rels.append((id_rel, mprop))

        for palantir_ds in mot.syncs_from():
            ds_name = (
                f"{PalantirToOsiConverter._concept_name(mot)}_{palantir_ds.readable_id()}"
            )
            dataset = semantic_model.lookup_dataset(ds_name)
            if dataset is None:
                continue

            cm = PalantirToOsiConverter._find_concept_mapping(concept_mappings, mot_concept, dataset)
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
                fk_field = PalantirToOsiConverter._get_dataset_field_by_palantir_property(
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

        When multiple datasets feed the same concept we get one ConceptMapping
        per dataset; pick the one whose referent expressions reference
        `dataset`, falling back to the first candidate."""
        candidates = [cm for cm in concept_mappings if cm.concept is concept]
        if len(candidates) <= 1:
            return candidates[0] if candidates else None
        return next(
            (cm for cm in candidates if PalantirToOsiConverter._references_dataset(cm, dataset)),
            candidates[0],
        )

    @staticmethod
    def _references_dataset(cm: ConceptMapping, dataset: Dataset) -> bool:
        """True iff any referent expression in `cm` points to a field of `dataset`."""
        return any(
            isinstance(rm.expression, DatasetField) and rm.expression.dataset is dataset
            for om in cm.object_mappings
            for rm in (om.referent_mappings or [])
        )

    @staticmethod
    def _convert_many_to_many(ontology: OntologyComponent, rel: ManyToManyRelation) -> None:
        aot = rel.role_a_player()
        aot_concept = ontology.lookup_concept(PalantirToOsiConverter._concept_name(aot))
        bot = rel.role_b_player()
        bot_concept = ontology.lookup_concept(PalantirToOsiConverter._concept_name(bot))
        if aot_concept is None or bot_concept is None:
            return
        rel_name = PalantirToOsiConverter._attribute_name(rel)

        if aot_concept is bot_concept:
            verbalize = f"{{{aot_concept}}} {rel_name} {{{bot_concept}:snd}}"
            relates = [(bot_concept, "snd")]
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
        ontology.add_relationship(relationship)

    @staticmethod
    def _convert_intermediary_relation(
        ontology: OntologyComponent,
        palantir_ontology: PalantirOntology,
        rel: IntermediaryRelation,
    ) -> None:
        aot = rel.role_a_player()
        aot_name = PalantirToOsiConverter._concept_name(aot)
        aot_concept = ontology.lookup_concept(aot_name)
        bot = rel.role_b_player()
        bot_name = PalantirToOsiConverter._concept_name(bot)
        bot_concept = ontology.lookup_concept(bot_name)
        if aot_concept is None or bot_concept is None:
            return
        rel_name = PalantirToOsiConverter._attribute_name(rel)

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
        ontology.add_relationship(relationship)

        rel_a = palantir_ontology.relations()[rel.relation_a()]
        rel_a_name = PalantirToOsiConverter._attribute_name(rel_a)
        rel_b = palantir_ontology.relations()[rel.relation_b()]
        rel_b_name = PalantirToOsiConverter._attribute_name(rel_b)

        fp_a = PalantirToOsiConverter._concept_name(
            rel_a.many_object_type() if isinstance(rel_a, ManyToOneRelation) else rel_a.role_a_player()
        )
        sp_a = PalantirToOsiConverter._concept_name(
            rel_a.one_object_type() if isinstance(rel_a, ManyToOneRelation) else rel_a.role_b_player()
        )
        fp_b = PalantirToOsiConverter._concept_name(
            rel_b.many_object_type() if isinstance(rel_b, ManyToOneRelation) else rel_b.role_a_player()
        )
        sp_b = PalantirToOsiConverter._concept_name(
            rel_b.one_object_type() if isinstance(rel_b, ManyToOneRelation) else rel_b.role_b_player()
        )

        assert (aot_name == fp_a and bot_name == fp_b) or (
            aot_name == sp_a and bot_name == sp_b
        ), f"Invalid intermediary relation '{rel_name}' arguments."

        join_condition = (
            f"{fp_a}.{rel_a_name}({relationship.first_role.name}) AND "
            f"{fp_b}.{rel_b_name}({relationship.last_role.name})"
        )
        formula = Formula(raw_expr=join_condition, parent=relationship)
        relationship.add_derived_by(formula)
        ontology.add_rule(formula)

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_dataset(
        semantic_model: SemanticModel,
        ontology: OntologyComponent,
        ot: ObjectType,
        palantir_ds: PalantirDataSet,
        db_name: str,
        schema_name: str,
    ) -> Dataset:
        ds_name = f"{PalantirToOsiConverter._concept_name(ot)}_{palantir_ds.readable_id()}"
        existing = semantic_model.lookup_dataset(ds_name)
        if existing is not None:
            return existing

        fields: list[DatasetField] = []
        for column in palantir_ds.columns():
            if column.type().upper() == "ARRAY":
                continue
            field_name = PalantirToOsiConverter._normalize_field_name(column.name())
            fields.append(
                DatasetField(
                    name=field_name,
                    expression=DialectExpressionSet(
                        dialects=[
                            DialectExpression(dialect=_DEFAULT_DIALECT, expression=field_name)
                        ]
                    ),
                    type=PalantirToOsiConverter._resolve_field_type(ontology, palantir_ds, column),
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
        type_str = (
            DataType.parse_datatype(column.type()).to_type() if column.type() else "String"
        )
        concept = ontology.lookup_concept(type_str)
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
                f"{{Integer:{PalantirToOsiConverter._depth_role_name(depth)}}} maps to "
                f"{PalantirToOsiConverter._type_to_madlib_suffix(type_.base_type(), depth + 1)}"
            )
        return f"{{{type_.to_type()}}}"

    @staticmethod
    def _convert_property_type_roles(
        ontology: OntologyComponent, roles: list[tuple[Concept, str | None]], type_, arr_depth: int = 1
    ) -> list[tuple[Concept, str | None]]:
        if isinstance(type_, ArrayDataType):
            integer = ontology.lookup_concept("Integer")
            if integer is None:
                raise ValueError("Builtin 'Integer' could not be resolved for array role.")
            roles.append((integer, PalantirToOsiConverter._depth_role_name(arr_depth)))
            PalantirToOsiConverter._convert_property_type_roles(
                ontology, roles, type_.base_type(), arr_depth + 1
            )
        else:
            target = ontology.lookup_concept(type_.to_type())
            if target is None:
                raise ValueError(
                    f"Type concept '{type_.to_type()}' is not defined in the ontology."
                )
            roles.append((target, None))
        return roles

    @staticmethod
    def _depth_role_name(depth: int) -> str:
        name = PalantirToOsiConverter.depths_role_names.get(depth)
        if not name:
            raise Exception(f"Array types of depth {depth} are not supported")
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
                    f"is missing property '{PalantirToOsiConverter._attribute_name(prop)}'"
                )
            column_name = pk_mapping[ds_guid]
        if not column_name:
            return None
        field = dataset.field(PalantirToOsiConverter._normalize_field_name(column_name))
        if not field:
            warnings.warn(f"Dataset '{dataset.name}' does not contain a field named '{column_name}'")
        return field

    @staticmethod
    def _normalize_field_name(name: str) -> str:
        normalized = name.replace("-", "_")
        if normalized and normalized[0].isdigit():
            normalized = f"_{normalized}"
        return normalized
