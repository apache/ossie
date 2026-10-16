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

"""Read an Apache Ossie ontology and mapping off an Auto Ontology export.

Auto Ontology's terms are business concepts, their attributes are properties
of those concepts, and its foreign keys link one concept to another. The
semantic model export carries the three as datasets, fields and relationships,
one dataset per term, so the ontology, and the mapping that populates it from
the semantic model, follow from that model alone.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import yaml

from .native_converter import (
    OSSIE_VERSION,
    _dump_yaml,
    _field_columns,
    _is_plain_name,
    _keys_as_columns,
    _relationship_as_columns,
    _simple_source_column,
    convert_auto_ontology_to_ossie,
)

# Built-in value type for each Ossie datatype. A field whose type has none, or
# whose type is unknown, is left out of the ontology rather than guessed.
_VALUE_TYPE_BY_DATATYPE = {
    "String": "String",
    "Integer": "Integer",
    "Decimal": "Decimal",
    "Float": "Float",
    "Boolean": "Boolean",
    "Date": "Date",
    "DateTime": "DateTime",
    "DateTimeTz": "DateTime",
}
_BUILT_IN_CONCEPTS = frozenset({"Any", *_VALUE_TYPE_BY_DATATYPE.values()})
_WORD = re.compile(r"[A-Za-z0-9]+")


@dataclass
class _Concept:
    """One dataset read as a concept: its relationships and how it is mapped."""

    name: str
    dataset: dict[str, Any]
    field_by_column: dict[str, dict[str, Any]]
    # Relationship name of each field kept as an attribute, by field name.
    attributes: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Relationship name of each Ossie relationship leaving this dataset.
    links: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    # Identifying relationships, each ("attribute", field) or ("link", rel).
    identity: list[tuple[str, Any]] | None = None
    relationships: list[dict[str, Any]] = field(default_factory=list)


def convert_auto_ontology_to_ossie_ontology(
    auto_ontology_yaml: str,
    *,
    model_name: str | None = None,
    semantic_model_iri: str | None = None,
    ontology_iri: str | None = None,
) -> tuple[str, str]:
    """Return the Ossie ontology and mapping YAML for a native document.

    The mapping populates the ontology from the semantic model that
    ``convert_auto_ontology_to_ossie`` returns for the same document and name.
    The IRIs, when given, tell the mapping where to find the two documents it
    references, relative to the mapping's own location.
    """
    semantic_model = yaml.safe_load(
        convert_auto_ontology_to_ossie(auto_ontology_yaml, model_name=model_name)
    )
    ontology, mapping = build_ontology_documents(
        semantic_model,
        semantic_model_iri=semantic_model_iri,
        ontology_iri=ontology_iri,
    )
    return _dump_yaml(ontology), _dump_yaml(mapping)


def convert_auto_ontology_to_embedded_ossie_ontology(
    auto_ontology_yaml: str,
    *,
    model_name: str | None = None,
) -> str:
    """Return the Ossie ontology for a native document, its mapping embedded.

    The mapping carries the semantic model ``convert_auto_ontology_to_ossie``
    returns for the same document and name, so the one document is complete.
    See ``embed_mapping`` for when this form is wanted.
    """
    semantic_model = yaml.safe_load(
        convert_auto_ontology_to_ossie(auto_ontology_yaml, model_name=model_name)
    )
    ontology, mapping = build_ontology_documents(semantic_model)
    return _dump_yaml(embed_mapping(ontology, mapping, semantic_model))


def embed_mapping(
    ontology: dict[str, Any],
    mapping: dict[str, Any],
    semantic_model: dict[str, Any],
) -> dict[str, Any]:
    """Return *ontology* with *mapping* embedded in it, as ``ontology_mappings``.

    The specification has replaced embedded mappings with mapping documents
    but still accepts them, and asks for them where a tool requires them:
    until tools read mapping documents, some read only this form. An embedded
    mapping carries its semantic model whole rather than referencing it.
    """
    embedded: dict[str, Any] = {"name": mapping["name"]}
    if mapping.get("description"):
        embedded["description"] = mapping["description"]
    embedded["semantic_model"] = semantic_model
    embedded["concept_mappings"] = mapping["concept_mappings"]
    return {**ontology, "ontology_mappings": [embedded]}


def build_ontology_documents(
    semantic_model: dict[str, Any],
    *,
    semantic_model_iri: str | None = None,
    ontology_iri: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the ontology and the mapping documents for *semantic_model*."""
    model_name = str(semantic_model["name"])
    # Keys and relationships are read by source column, whether the model
    # names their columns by field, as this converter exports them, or not.
    field_columns = {
        str(dataset["name"]): _field_columns(dataset, str(dataset["name"]), "")
        for dataset in semantic_model.get("datasets") or []
    }
    datasets = [
        _keys_as_columns(dataset, field_columns[str(dataset["name"])])
        for dataset in semantic_model.get("datasets") or []
    ]
    relationships = [
        _relationship_as_columns(relationship, field_columns)
        for relationship in semantic_model.get("relationships") or []
    ]
    concepts = _read_concepts(datasets, relationships)

    ontology: dict[str, Any] = {
        "version": OSSIE_VERSION,
        "name": f"{model_name}_ontology",
        "description": f"Business concepts of the {model_name} semantic model",
        "ontology": [_ontology_component(concept) for concept in concepts.values()],
    }
    concept_mappings = [
        mapping
        for concept in concepts.values()
        if (mapping := _concept_mapping(concept, concepts))
    ]
    ontology_ref: dict[str, str] = {"name": ontology["name"]}
    if ontology_iri:
        ontology_ref["iri"] = ontology_iri
    semantic_model_ref: dict[str, str] = {"name": model_name}
    if semantic_model_iri:
        semantic_model_ref["iri"] = semantic_model_iri
    mapping = {
        "version": OSSIE_VERSION,
        "name": f"{model_name}_mapping",
        "description": f"Maps the {ontology['name']} onto the {model_name} semantic model",
        "ontology_ref": ontology_ref,
        "semantic_model_ref": semantic_model_ref,
        "concept_mappings": concept_mappings,
    }
    return ontology, mapping


def _read_concepts(
    datasets: list[dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> dict[str, _Concept]:
    concepts: dict[str, _Concept] = {}
    taken = set(_BUILT_IN_CONCEPTS)
    for dataset in datasets:
        name = _unique(_pascal_case(str(dataset["name"])), taken)
        concepts[str(dataset["name"])] = _Concept(
            name=name,
            dataset=dataset,
            field_by_column={
                column: item
                for item in dataset.get("fields") or []
                if (column := _field_column(dataset, item))
            },
        )

    for concept in concepts.values():
        names: set[str] = set()
        outgoing = [
            relationship
            for relationship in relationships
            if relationship["from"] == concept.dataset["name"]
            and relationship["to"] in concepts
        ]
        targets = [relationship["to"] for relationship in outgoing]
        for relationship in outgoing:
            # A dataset linked to another only once names the link after it;
            # several links to the same dataset are told apart by column.
            basis = (
                str(relationship["to"])
                if targets.count(relationship["to"]) == 1
                else " ".join(relationship["from_columns"])
            )
            concept.links.append((_unique(_snake_case(basis), names), relationship))
        linked_columns = {
            column
            for relationship in outgoing
            for column in relationship["from_columns"]
        }
        for item in concept.dataset.get("fields") or []:
            value_type = _VALUE_TYPE_BY_DATATYPE.get(str(item.get("datatype")))
            # A foreign-key column is the link to another concept, not a value.
            if not value_type or _field_column(concept.dataset, item) in linked_columns:
                continue
            concept.attributes[str(item["name"])] = (
                _unique(_snake_case(str(item["name"])), names),
                value_type,
            )
        concept.identity = _identity(concept)

    for concept in concepts.values():
        concept.relationships = _relationships(concept, concepts)
    return concepts


def _identity(concept: _Concept) -> list[tuple[str, Any]] | None:
    """Return the relationships that identify *concept*, or ``None``.

    The dataset's primary key identifies it, or failing that its first unique
    key. Each key column must be a field kept as an attribute or a column of a
    link to another concept; otherwise the concept cannot be referenced.
    """
    dataset = concept.dataset
    key = dataset.get("primary_key") or next(iter(dataset.get("unique_keys") or []), [])
    identity: list[tuple[str, Any]] = []
    for column in key:
        link = next(
            (
                relationship
                for _, relationship in concept.links
                if column in relationship["from_columns"]
            ),
            None,
        )
        item = concept.field_by_column.get(column)
        if link is not None:
            if ("link", link) not in identity:
                identity.append(("link", link))
        elif item is not None and str(item["name"]) in concept.attributes:
            identity.append(("attribute", item))
        else:
            return None
    return identity or None


def _relationships(
    concept: _Concept, concepts: dict[str, _Concept]
) -> list[dict[str, Any]]:
    simple_id = _simple_identifier(concept)
    relationships: list[dict[str, Any]] = []
    for field_name, (name, value_type) in concept.attributes.items():
        item = _field(concept, field_name)
        identifies = simple_id is item
        relationship: dict[str, Any] = {"name": name}
        if item.get("description"):
            relationship["description"] = str(item["description"])
        verb = "is identified by" if identifies else "has"
        reading = field_name.replace("_", " ")
        relationship |= {
            "roles": [{"concept": value_type}],
            "multiplicity": "OneToOne" if identifies else "ManyToOne",
            "verbalizes": [f"{{{concept.name}}} {verb} {reading} {{{value_type}}}"],
        }
        relationships.append(relationship)
    for name, link in concept.links:
        target = concepts[str(link["to"])].name
        role: dict[str, str] = {"concept": target}
        reference = target
        # A concept linked to itself needs a role name to tell its roles apart.
        if target == concept.name:
            role["name"] = name
            reference = f"{target}:{name}"
        relationships.append(
            {
                "name": name,
                "roles": [role],
                "multiplicity": "ManyToOne",
                "verbalizes": [f"{{{concept.name}}} has {{{reference}}}"],
            }
        )
    return relationships


def _ontology_component(concept: _Concept) -> dict[str, Any]:
    component: dict[str, Any] = {"concept": concept.name, "type": "EntityType"}
    if concept.dataset.get("description"):
        component["description"] = str(concept.dataset["description"])
    if concept.identity:
        component["identify_by"] = [
            _identifying_name(concept, kind, value) for kind, value in concept.identity
        ]
    if concept.relationships:
        component["relationships"] = concept.relationships
    return component


def _concept_mapping(
    concept: _Concept, concepts: dict[str, _Concept]
) -> dict[str, Any] | None:
    """Map *concept* and its relationships from its own dataset's fields.

    A concept without an identity has nothing to name its objects by, so it
    is declared in the ontology but not mapped.
    """
    own = {
        column: _field_expression(concept.dataset, item)
        for column, item in concept.field_by_column.items()
    }
    objects = _object_mapping(concept, own, concepts)
    if objects is None:
        return None
    # Identifying relationships are populated by the object mapping itself.
    identifying = [value for _, value in concept.identity or []]
    children: list[dict[str, Any]] = []
    for field_name, (name, value_type) in concept.attributes.items():
        item = _field(concept, field_name)
        if any(item is value for value in identifying):
            continue
        children.append(
            {
                "object_mapping": {
                    "concept": value_type,
                    "expression": _field_expression(concept.dataset, item),
                },
                "relationship": name,
            }
        )
    for name, link in concept.links:
        if any(link is value for value in identifying):
            continue
        target = concepts[str(link["to"])]
        target_objects = _object_mapping(target, _link_expressions(link, own), concepts)
        if target_objects is None:
            continue
        children.append(
            {
                "object_mapping": {"concept": target.name, **target_objects},
                "relationship": name,
            }
        )
    mapping: dict[str, Any] = {"concept": concept.name, "object_mappings": [objects]}
    if children:
        # A copy, so the YAML repeats the mapping rather than aliasing it.
        mapping["link_mappings"] = [
            {"object_mapping": deepcopy(objects), "children": children}
        ]
    return mapping


def _object_mapping(
    concept: _Concept,
    expressions: dict[str, str],
    concepts: dict[str, _Concept],
) -> dict[str, Any] | None:
    """Map to objects of *concept* from *expressions* for its key columns."""
    simple_id = _simple_identifier(concept)
    if simple_id is not None:
        column = _field_column(concept.dataset, simple_id)
        expression = expressions.get(column or "")
        return {"expression": expression} if expression else None
    referents = _referent_mappings(concept, expressions, concepts)
    return {"referent_mappings": referents} if referents else None


def _referent_mappings(
    concept: _Concept,
    expressions: dict[str, str],
    concepts: dict[str, _Concept],
) -> list[dict[str, Any]] | None:
    if not concept.identity:
        return None
    referents: list[dict[str, Any]] = []
    for kind, value in concept.identity:
        name = _identifying_name(concept, kind, value)
        if kind == "attribute":
            expression = expressions.get(_field_column(concept.dataset, value) or "")
            if not expression:
                return None
            referents.append({"relationship": name, "expression": expression})
            continue
        # As in the specification's examples, an identifying link names the
        # referenced concept's own identifying relationships.
        target = concepts[str(value["to"])]
        nested = _referent_mappings(
            target, _link_expressions(value, expressions), concepts
        )
        if nested is None:
            return None
        referents.append({"relationship": name, "referent_mappings": nested})
    return referents


def _link_expressions(
    link: dict[str, Any], expressions: dict[str, str]
) -> dict[str, str]:
    """Carry the expressions of a link's columns over to the columns it references."""
    return {
        to_column: expressions[from_column]
        for from_column, to_column in zip(link["from_columns"], link["to_columns"])
        if from_column in expressions
    }


def _simple_identifier(concept: _Concept) -> dict[str, Any] | None:
    """Return the one field that identifies *concept* on its own, if any."""
    identity = concept.identity or []
    if len(identity) == 1 and identity[0][0] == "attribute":
        return identity[0][1]
    return None


def _identifying_name(concept: _Concept, kind: str, value: Any) -> str:
    if kind == "attribute":
        return concept.attributes[str(value["name"])][0]
    return next(name for name, link in concept.links if link is value)


def _field(concept: _Concept, field_name: str) -> dict[str, Any]:
    return next(
        item
        for item in concept.dataset.get("fields") or []
        if str(item["name"]) == field_name
    )


def _field_column(dataset: dict[str, Any], item: dict[str, Any]) -> str | None:
    """Return the source column a field passes through, if it is one."""
    dialects = (item.get("expression") or {}).get("dialects") or []
    if not dialects:
        return None
    return _simple_source_column(
        str(dialects[0].get("expression") or ""), str(dataset["name"]), ""
    )


def _field_expression(dataset: dict[str, Any], item: dict[str, Any]) -> str:
    return f"{_sql_name(str(dataset['name']))}.{_sql_name(str(item['name']))}"


def _sql_name(name: str) -> str:
    """Write *name* as SQL reads it, quoted only where it has to be.

    Not every tool that reads a mapping expression parses quoted names, while
    every one reads an identifier, which is what this converter names
    datasets and fields with.
    """
    return name if _is_plain_name(name) else '"' + name.replace('"', '""') + '"'


def _pascal_case(text: str) -> str:
    name = "".join(word[0].upper() + word[1:] for word in _WORD.findall(text))
    return name if name and not name[0].isdigit() else f"C{name}"


def _snake_case(text: str) -> str:
    name = "_".join(word.lower() for word in _WORD.findall(text))
    return name if name and not name[0].isdigit() else f"r_{name}"


def _unique(name: str, taken: set[str]) -> str:
    candidate, count = name, 1
    while candidate in taken:
        count += 1
        candidate = f"{name}_{count}"
    taken.add(candidate)
    return candidate
