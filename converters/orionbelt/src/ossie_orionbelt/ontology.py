"""OBML → OSI ontology derivation (the :class:`OBMLtoOSIOntology` direction).

Extracted verbatim from ``converter.py``; reuses :class:`OBMLtoOSI` for the
embedded core-spec model.
"""

from __future__ import annotations

from typing import Any

from ossie_orionbelt._common import _OSI_VERSION
from ossie_orionbelt.obml_to_osi import OBMLtoOSI


class OBMLtoOSIOntology:
    """Derive an OSI **ontology** document from an OBML semantic model.

    Produces a document conforming to ``osi-ontology-schema.json`` (OSI version
    ``0.2.0.dev0``) — a *separate* artefact from the OSI core-spec semantic
    model. The OSI ``OntologyMap`` embeds the full core-spec model, so this
    class reuses :class:`OBMLtoOSI` for that part and overlays a concept /
    relationship ontology plus the logical-to-conceptual mappings.

    See ``osi_obml_ontology_mapping_analysis.md`` for the full mapping rules
    and the documented gaps that surface as ``warnings``.
    """

    # OBML join cardinality → OSI ontology multiplicity. ``many-to-many`` has
    # no OSI equivalent (the enum is ManyToOne/OneToOne only) and is skipped.
    _MULTIPLICITY_MAP = {
        "many-to-one": "ManyToOne",
        "one-to-one": "OneToOne",
    }

    def __init__(
        self,
        obml: dict,
        model_name: str = "semantic_model",
        model_description: str = "",
        ai_instructions: str = "",
    ):
        self.obml = obml
        self.model_name = model_name
        self.model_description = model_description
        self.ai_instructions = ai_instructions
        self.warnings: list[str] = []

    @staticmethod
    def _table_ref(do_obj: dict, fallback: str) -> str:
        """Best-effort physical table identifier for SQL mapping expressions."""
        database = do_obj.get("database", "")
        schema = do_obj.get("schema", "")
        code = do_obj.get("code", "")
        source = f"{database}.{schema}.{code}" if database else code
        if source:
            return source.split(".")[-1]
        return fallback

    @staticmethod
    def _col_code(do_obj: dict, display: str) -> str:
        col = (do_obj.get("columns", {}) or {}).get(display, {})
        return col.get("code", display.lower().replace(" ", "_"))

    def _entity_key_expr(self, do_name: str, do_obj: dict, table_ref: str) -> str | None:
        """Identifying SQL expression for an entity (primary key, else first column)."""
        cols = do_obj.get("columns", {}) or {}
        pk = [c for c, col in cols.items() if col.get("primaryKey")]
        if not pk:
            if not cols:
                return None
            first = next(iter(cols))
            self.warnings.append(
                f"Entity '{do_name}' has no primary key; identified by first column '{first}'"
            )
            return f"{table_ref}.{self._col_code(do_obj, first)}"
        if len(pk) > 1:
            self.warnings.append(
                f"Entity '{do_name}' has a composite primary key; ontology object "
                f"mapping uses only the first key column '{pk[0]}'"
            )
        return f"{table_ref}.{self._col_code(do_obj, pk[0])}"

    def convert(self) -> dict:
        # 1. Core semantic model (embedded — required by OSI OntologyMap).
        core_conv = OBMLtoOSI(
            self.obml,
            model_name=self.model_name,
            model_description=self.model_description,
            ai_instructions=self.ai_instructions,
        )
        sem_model = core_conv.convert()["semantic_model"][0]
        self.warnings.extend(core_conv.warnings)

        data_objects = self.obml.get("dataObjects", {}) or {}
        table_refs = {n: self._table_ref(o, n) for n, o in data_objects.items()}

        # 2. Ontology components (one EntityType concept per dataObject) plus
        #    the outgoing relationships keyed by that concept. Collect link
        #    info so concept_mappings can bind relationships to FK columns.
        ontology: list[dict[str, Any]] = []
        rel_links: dict[str, list[tuple[str, str, str | None]]] = {}

        for do_name, do_obj in data_objects.items():
            concept: dict[str, Any] = {"name": do_name, "type": "EntityType"}
            desc = do_obj.get("description") or do_obj.get("comment")
            if desc:
                concept["description"] = desc
            component: dict[str, Any] = {"concept": concept}

            relationships: list[dict[str, Any]] = []
            used_names: set[str] = set()
            for join in do_obj.get("joins", []) or []:
                to_name = join.get("joinTo", "")
                if not to_name:
                    continue
                join_type = (join.get("joinType") or "").lower()
                if join_type == "many-to-many":
                    self.warnings.append(
                        f"Join {do_name} -> {to_name} is many-to-many; skipped "
                        f"(OSI ontology multiplicity supports only ManyToOne/OneToOne)"
                    )
                    continue
                multiplicity = self._MULTIPLICITY_MAP.get(join_type)
                if multiplicity is None:
                    multiplicity = "ManyToOne"
                    self.warnings.append(
                        f"Join {do_name} -> {to_name} has unknown joinType "
                        f"'{join.get('joinType', '')}'; defaulting multiplicity to ManyToOne"
                    )

                path_name = join.get("pathName", "")
                rel_name = f"{do_name}_to_{to_name}"
                if path_name:
                    rel_name = f"{rel_name}_{path_name}"
                    self.warnings.append(
                        f"Join {do_name} -> {to_name} is a named/secondary path "
                        f"('{path_name}'); emitted as an ordinary relationship "
                        f"(named-path semantics are not represented in OSI ontology)"
                    )
                elif join.get("secondary"):
                    self.warnings.append(
                        f"Join {do_name} -> {to_name} is a secondary path; emitted as an "
                        f"ordinary relationship (alternate-path semantics are lost)"
                    )
                base = rel_name
                dedup = 1
                while rel_name in used_names:
                    rel_name = f"{base}_{dedup}"
                    dedup += 1
                used_names.add(rel_name)

                relationships.append(
                    {
                        "name": rel_name,
                        "roles": [{"concept": to_name}],
                        "multiplicity": multiplicity,
                        "verbalizes": [f"{{{do_name}}} relates to {{{to_name}}}"],
                    }
                )

                from_cols = join.get("columnsFrom", []) or []
                fk_expr: str | None = None
                if from_cols:
                    if len(from_cols) > 1:
                        self.warnings.append(
                            f"Join {do_name} -> {to_name} has a composite key; link "
                            f"mapping uses only the first column '{from_cols[0]}'"
                        )
                    fk_expr = f"{table_refs[do_name]}.{self._col_code(do_obj, from_cols[0])}"
                rel_links.setdefault(do_name, []).append((rel_name, to_name, fk_expr))

            if relationships:
                component["relationships"] = relationships
            ontology.append(component)

        # 3. Concept mappings: object_mappings identify each entity; link_mappings
        #    bind each relationship to its foreign-key column.
        concept_mappings: list[dict[str, Any]] = []
        for do_name, do_obj in data_objects.items():
            cm: dict[str, Any] = {"concept": do_name}
            key_expr = self._entity_key_expr(do_name, do_obj, table_refs[do_name])
            if key_expr:
                cm["object_mappings"] = [{"expression": key_expr}]
            links: list[dict[str, Any]] = []
            for rel_name, to_name, fk_expr in rel_links.get(do_name, []):
                obj_map: dict[str, Any] = {"concept": to_name}
                if fk_expr:
                    obj_map["expression"] = fk_expr
                links.append({"relationship": rel_name, "object_mapping": obj_map})
            if links:
                cm["link_mappings"] = links
            if "object_mappings" in cm or "link_mappings" in cm:
                concept_mappings.append(cm)

        # 4. Assemble the ontology document.
        ontology_doc: dict[str, Any] = {"version": _OSI_VERSION, "name": self.model_name}
        model_desc = self.obml.get("description", "") or self.model_description
        if model_desc:
            ontology_doc["description"] = model_desc
        if self.ai_instructions:
            ontology_doc["ai_context"] = {"instructions": self.ai_instructions}
        ontology_doc["ontology"] = ontology
        ontology_doc["ontology_mappings"] = [
            {
                "name": f"{self.model_name}_map",
                "semantic_model": sem_model,
                "concept_mappings": concept_mappings,
            }
        ]
        return ontology_doc
