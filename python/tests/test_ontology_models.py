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

import json
from pathlib import Path
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin

import pytest
import yaml
from pydantic import AfterValidator, ValidationError

from ossie import (
    OssieConceptComponent,
    OssieConceptMapping,
    OssieConceptType,
    OssieDocument,
    OssieLinkMapping,
    OssieMultiplicity,
    OssieObjectMapping,
    OssieOntologyMapping,
    OssieOntologyModel,
    OssieOntologyRelationship,
    OssieReferentMapping,
    OssieRole,
    OssieSemanticModel,
    OssieVersion,
)
from ossie.models import _distinct

_REPO = Path(__file__).parents[2]


def _schema(name: str) -> dict:
    return json.loads((_REPO / "ontology" / name).read_text())


@pytest.fixture
def ontology_data(document_data: dict) -> dict:
    return {
        "version": "0.3.0.dev0",
        "name": "events",
        "ontology": [
            {
                "concept": "Event",
                "type": "EntityType",
                "identify_by": ["at"],
                "relationships": [
                    {
                        "name": "at",
                        "roles": [{"concept": "DateTime"}],
                        "multiplicity": "OneToOne",
                        "verbalizes": ["{Event} occurred at {DateTime}"],
                    }
                ],
            }
        ],
        "ontology_mappings": [
            {
                "name": "events_mapping",
                "semantic_model": document_data,
                "concept_mappings": [
                    {
                        "concept": "Event",
                        "object_mappings": [{"referent_mappings": [{"relationship": "at", "expression": "events.occurred_at"}]}],
                    }
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Examples
# ---------------------------------------------------------------------------


def _dump(document: OssieOntologyModel) -> dict:
    return document.model_dump(mode="json", by_alias=True, exclude_none=True)


@pytest.mark.parametrize(
    "name",
    [
        "flights.ontology.yaml",
        "flights.yaml",
        "sap_business_partner.ontology.yaml",
        "foaf_owl_import.yaml",
    ],
)
def test_example_round_trips(name: str) -> None:
    text = (_REPO / "examples" / name).read_text()

    ontology = OssieOntologyModel.from_ossie_yaml(text)

    assert yaml.safe_load(ontology.to_ossie_yaml()) == yaml.safe_load(text)
    assert OssieOntologyModel.from_ossie_json(ontology.to_ossie_json()) == ontology


def test_documents_share_the_base_document() -> None:
    assert issubclass(OssieSemanticModel, OssieDocument)
    assert issubclass(OssieOntologyModel, OssieDocument)


@pytest.mark.parametrize(
    ("text", "error", "message"),
    [
        (
            "name: events\nontology:\n- {concept: Event, type: EntityType}\n",
            ValueError,
            "missing the required property: version",
        ),
        ("version: 0.3.0.dev0\nname: a\nname: b\n", yaml.constructor.ConstructorError, "duplicate"),
        ("version: 0.3.0.dev0\nname: events\ndescription:\n", ValueError, "null.*: description"),
    ],
    ids=["missing-version", "duplicate-key", "null"],
)
def test_ontology_files_get_the_document_checks(text: str, error: type, message: str) -> None:
    with pytest.raises(error, match=message):
        OssieOntologyModel.from_ossie_yaml(text)


def test_embedded_semantic_model_requires_a_version() -> None:
    """The schema requires `version` on the embedded core document too.

    Without the check the loader fell through to the field default and wrote the
    document back with `version` filled in.
    """
    data = yaml.safe_load((_REPO / "examples" / "flights.yaml").read_text())
    del data["ontology_mappings"][0]["semantic_model"]["version"]
    message = r"missing the required property: ontology_mappings\[0\]\.semantic_model\.version$"

    with pytest.raises(ValueError, match=message):
        OssieOntologyModel.from_ossie_yaml(yaml.safe_dump(data))
    with pytest.raises(ValueError, match=message):
        OssieOntologyModel.from_ossie_json(json.dumps(data))


def test_every_missing_version_is_reported(ontology_data: dict) -> None:
    mapping = ontology_data["ontology_mappings"][0]
    del mapping["semantic_model"]["version"]
    ontology_data["ontology_mappings"].append(mapping | {"name": "again"})
    del ontology_data["version"]

    with pytest.raises(ValueError) as error:
        OssieOntologyModel.from_ossie_yaml(yaml.safe_dump(ontology_data))

    assert str(error.value) == (
        "document is missing the required property: version, "
        "ontology_mappings[0].semantic_model.version, "
        "ontology_mappings[1].semantic_model.version"
    )


def test_embedded_mapping_carries_a_full_semantic_model_document(ontology_data: dict) -> None:
    ontology = OssieOntologyModel.model_validate(ontology_data)

    semantic_model = ontology.ontology_mappings[0].semantic_model
    assert isinstance(semantic_model, OssieSemanticModel)
    assert semantic_model.version == "0.3.0.dev0"
    assert OssieOntologyModel.model_validate(_dump(ontology)) == ontology


def test_serialization_omits_absent_fields_and_puts_version_first(ontology_data: dict) -> None:
    del ontology_data["ontology_mappings"]
    ontology = OssieOntologyModel.model_validate(ontology_data)

    assert _dump(ontology) == ontology_data
    assert list(_dump(ontology))[0] == "version"


# ---------------------------------------------------------------------------
# Schema parity
# ---------------------------------------------------------------------------


def test_enums_match_the_ontology_schema() -> None:
    defs = _schema("ontology.json")["$defs"]

    assert [member.value for member in OssieConceptType] == defs["ConceptType"]["enum"]
    assert [member.value for member in OssieMultiplicity] == defs["Multiplicity"]["enum"]


def test_version_matches_the_schema_const() -> None:
    const = _schema("ontology.json")["properties"]["version"]["const"]

    assert get_args(OssieVersion) == (const,)


# An optional field is `X | None` (types.UnionType) or `Optional[X]` (typing.Union);
# the two are distinct origins before Python 3.14.
_UNIONS = (Union, UnionType)


def _schema_min_length(node: dict, defs: dict) -> int | None:
    """Return the `minLength` of the strings a schema property holds: itself, its items, or its values."""
    if "$ref" in node:
        core_prefix = "https://raw.githubusercontent.com/apache/ossie/main/core-spec/ossie-schema.json#/$defs/"
        if node["$ref"].startswith(core_prefix):
            core_defs = json.loads((_REPO / "core-spec/ossie-schema.json").read_text())["$defs"]
            return _schema_min_length(core_defs[node["$ref"].removeprefix(core_prefix)], core_defs)
        return _schema_min_length(defs[node["$ref"].removeprefix("#/$defs/")], defs)
    if node.get("type") == "string":
        return node.get("minLength")
    if node.get("type") == "array":
        return _schema_min_length(node["items"], defs)
    if isinstance(node.get("additionalProperties"), dict):
        return _schema_min_length(node["additionalProperties"], defs)
    return None


def _model_min_length(annotation: Any, metadata: list) -> int | None:
    """Return the `min_length` of the strings a model field holds: itself, its items, or its values."""
    if get_origin(annotation) is Annotated:
        base, *extra = get_args(annotation)
        inner = [item for info in extra for item in getattr(info, "metadata", [info])]
        return _model_min_length(base, inner)
    args = get_args(annotation)
    if get_origin(annotation) in _UNIONS:
        (branch,) = [arg for arg in args if arg is not type(None)]
        return _model_min_length(branch, metadata)
    if annotation is str:
        return next((item.min_length for item in metadata if hasattr(item, "min_length")), None)
    if get_origin(annotation) is list:
        return _model_min_length(args[0], [])
    if get_origin(annotation) is dict:
        return _model_min_length(args[1], [])
    return None


def _model_distinct(annotation: Any, metadata: list) -> bool:
    """Whether a model field rejects repeated items, mirroring `uniqueItems`.

    Pydantic moves a required field's outer `Annotated` extras into its `metadata`.
    """
    if any(isinstance(info, AfterValidator) and info.func is _distinct for info in metadata):
        return True
    if get_origin(annotation) is Annotated:
        base, *extra = get_args(annotation)
        return _model_distinct(base, extra)
    if get_origin(annotation) in _UNIONS:
        (branch,) = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _model_distinct(branch, [])
    return False


def test_models_mirror_the_schema_properties() -> None:
    """Each object node maps to a model with the same properties, required set, string lengths and strictness.

    The root node is keyed by "". Comparing the property names catches a field added to
    the schema but not the model, and vice versa.
    """
    models = {
        "": OssieOntologyModel,
        "OntologyComponent": OssieConceptComponent,
        "Relationship": OssieOntologyRelationship,
        "Role": OssieRole,
        "OntologyMap": OssieOntologyMapping,
        "ConceptMapping": OssieConceptMapping,
        "ObjectMapping": OssieObjectMapping,
        "ReferentMapping": OssieReferentMapping,
        "LinkMapping": OssieLinkMapping,
    }
    schema = _schema("ontology.json")
    nodes = {"": schema} | {key: node for key, node in schema["$defs"].items() if node.get("type") == "object"}
    # An open object of arbitrary JSON has no properties to mirror; its fields are
    # typed `dict[str, Any]` and checked below.
    open_objects = {key for key, node in nodes.items() if "properties" not in node}
    assert open_objects == {"Annotations"}
    assert nodes["Annotations"]["additionalProperties"] is True
    assert set(nodes) - open_objects == set(models)

    for key, model in models.items():
        node = nodes[key]
        fields = {field.alias or name: field for name, field in model.model_fields.items()}
        assert set(fields) == set(node["properties"]), model.__name__
        # `version` carries a default for Python construction; documents still require it.
        required = {name for name, field in fields.items() if field.is_required()}
        assert required | ({"version"} & set(fields)) == set(node.get("required", [])), model.__name__
        assert node["additionalProperties"] is False
        assert model.model_config.get("extra") == "forbid", model.__name__

        for name, prop in node["properties"].items():
            if prop.get("$ref", "#").startswith("http"):
                # Defined by the core schema (`ai_context`, `semantic_model`) and
                # mirrored by the semantic models, whose own tests cover them.
                continue
            field = fields[name]
            if prop.get("$ref") == "#/$defs/Annotations":
                assert field.annotation == dict[str, Any] | None, model.__name__
                continue
            assert _model_min_length(field.annotation, field.metadata) == _schema_min_length(prop, schema["$defs"]), (
                f"{model.__name__}.{name}"
            )
            assert _model_distinct(field.annotation, field.metadata) == prop.get("uniqueItems", False), f"{model.__name__}.{name}"


# ---------------------------------------------------------------------------
# Constraints the field types cannot express
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (OssieReferentMapping, {"relationship": "at"}),
        (OssieObjectMapping, {"concept": "Event"}),
        (OssieConceptMapping, {"concept": "Event"}),
    ],
)
def test_any_of_requires_one_alternative(model: type, data: dict) -> None:
    with pytest.raises(ValidationError, match="requires at least one of"):
        model.model_validate(data)


@pytest.mark.parametrize(
    "data",
    [
        {"concept": "Event", "object_mappings": []},
        {"concept": "Event", "link_mappings": []},
    ],
)
def test_concept_mapping_lists_cannot_be_empty(data: dict) -> None:
    with pytest.raises(ValidationError):
        OssieConceptMapping.model_validate(data)


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (OssieRole, {"concept": ""}),
        (OssieOntologyRelationship, {"name": "at", "verbalizes": [""]}),
        (OssieConceptComponent, {"concept": "Event", "type": "EntityType", "iri": ""}),
        (OssieConceptComponent, {"concept": "Event", "type": "EntityType", "requires": [""]}),
        (OssieReferentMapping, {"relationship": "at", "expression": ""}),
        (OssieObjectMapping, {"expression": ""}),
        (OssieConceptMapping, {"concept": "", "object_mappings": [{"expression": "e.id"}]}),
    ],
)
def test_empty_strings_are_rejected(model: type, data: dict) -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        model.model_validate(data)


def test_empty_ontology_name_and_prefix_are_rejected(ontology_data: dict) -> None:
    for key, value in [("name", ""), ("prefixes", {"ex": ""})]:
        data = ontology_data | {key: value}
        with pytest.raises(ValidationError, match="at least 1 character"):
            OssieOntologyModel.model_validate(data)


def test_referent_mappings_nest() -> None:
    nested = OssieReferentMapping.model_validate(
        {
            "relationship": "order",
            "referent_mappings": [{"relationship": "nr", "expression": "LINES.order_nr"}],
        }
    )

    assert nested.referent_mappings[0].expression == "LINES.order_nr"


def test_link_mappings_nest() -> None:
    mapping = OssieLinkMapping.model_validate(
        {
            "object_mapping": {"expression": "ITEMS.sku"},
            "relationship": "active",
            "children": [{"object_mapping": {"concept": "Store", "expression": "ITEMS.store"}}],
        }
    )

    assert mapping.children[0].object_mapping.concept == "Store"


@pytest.mark.parametrize(
    ("path", "key"),
    [
        (("ontology", 0), "type"),
        (("ontology", 0, "relationships", 0), "verbalizes"),
        (("ontology", 0, "relationships", 0, "roles", 0), "concept"),
        (("ontology_mappings", 0), "semantic_model"),
    ],
)
def test_required_properties(ontology_data: dict, path: tuple, key: str) -> None:
    container = ontology_data
    for part in path:
        container = container[part]
    del container[key]

    with pytest.raises(ValidationError) as error:
        OssieOntologyModel.model_validate(ontology_data)

    assert any(item["loc"] == (*path, key) for item in error.value.errors())


def test_ontology_requires_a_concept(ontology_data: dict) -> None:
    ontology_data["ontology"] = []

    with pytest.raises(ValidationError):
        OssieOntologyModel.model_validate(ontology_data)


def test_invalid_multiplicity_is_rejected(ontology_data: dict) -> None:
    ontology_data["ontology"][0]["relationships"][0]["multiplicity"] = "ManyToMany"

    with pytest.raises(ValidationError):
        OssieOntologyModel.model_validate(ontology_data)


@pytest.mark.parametrize(
    ("model", "data"),
    [
        (OssieConceptComponent, {"concept": "E", "type": "EntityType", "extends": ["A", "A"]}),
        (OssieConceptComponent, {"concept": "E", "type": "EntityType", "identify_by": ["a", "a"]}),
        (OssieConceptComponent, {"concept": "E", "type": "EntityType", "derived_by": ["x", "x"]}),
        (OssieConceptComponent, {"concept": "E", "type": "EntityType", "requires": ["x", "x"]}),
        (OssieOntologyRelationship, {"name": "at", "verbalizes": ["{E}", "{E}"]}),
        (OssieOntologyRelationship, {"name": "at", "verbalizes": ["{E}"], "requires": ["x", "x"]}),
        (OssieOntologyRelationship, {"name": "at", "verbalizes": ["{E}"], "derived_by": ["x", "x"]}),
    ],
)
def test_repeated_names_are_rejected(model: type, data: dict) -> None:
    with pytest.raises(ValidationError, match="repeats"):
        model.model_validate(data)


def test_repeated_ontology_requires_is_rejected(ontology_data: dict) -> None:
    ontology_data["requires"] = ["x", "x"]

    with pytest.raises(ValidationError, match="repeats"):
        OssieOntologyModel.model_validate(ontology_data)


_ANNOTATED = """\
version: 0.3.0.dev0
name: events
annotations:
  owl:imports: [http://example.com/base]
  missing: null
ontology:
- concept: Event
  type: EntityType
  annotations:
    rdfs:label: {en: Event, de: null}
  relationships:
  - name: at
    roles:
    - concept: DateTime
      annotations: {order: 1}
    verbalizes: ['{Event} occurred at {DateTime}']
    annotations:
      owl:inverseOf: when
"""


def test_annotations_hold_any_json_and_round_trip() -> None:
    """Keys are free-form and values any JSON, a null included.

    The spec does not interpret them.
    """
    ontology = OssieOntologyModel.from_ossie_yaml(_ANNOTATED)

    assert ontology.annotations == {
        "owl:imports": ["http://example.com/base"],
        "missing": None,
    }
    assert ontology.ontology[0].relationships[0].roles[0].annotations == {"order": 1}
    assert yaml.safe_load(ontology.to_ossie_yaml()) == yaml.safe_load(_ANNOTATED)
    assert OssieOntologyModel.from_ossie_json(ontology.to_ossie_json()) == ontology


@pytest.mark.parametrize(
    ("annotations", "message"),
    [
        ("annotations:\n  released: 2024-01-01\n", "JSON cannot represent.*: annotations.released"),
        ("annotations:\n  1: one\n", r"not a string.*: annotations\[1\]"),
        ("annotations:\n  score: .nan\n", "number JSON cannot.*: annotations.score"),
        ("annotations:\n", "null.*: annotations"),
    ],
    ids=["date", "integer-key", "non-finite", "null-object"],
)
def test_annotations_must_be_json(annotations: str, message: str) -> None:
    text = "version: 0.3.0.dev0\nname: events\nontology:\n- {concept: Event, type: EntityType}\n" + annotations

    with pytest.raises(ValueError, match=message):
        OssieOntologyModel.from_ossie_yaml(text)


def test_concept_knowledge_references_survive_document_round_trip() -> None:
    example = _REPO / "examples/knowledge-references/payments.ontology.yaml"
    document = OssieOntologyModel.from_ossie_yaml(example.read_text())
    restored = OssieOntologyModel.from_ossie_json(document.to_ossie_json())
    refs = restored.ontology[0].knowledge_refs
    assert refs is not None
    assert [ref.model_dump(exclude_none=True) for ref in refs] == (
        yaml.safe_load(example.read_text())["ontology"][0]["knowledge_refs"]
    )
