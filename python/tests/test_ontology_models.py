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
from typing import get_args

import pytest
import yaml
from pydantic import ValidationError

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

_REPO = Path(__file__).parents[2]


def _schema(name: str) -> dict:
    return json.loads((_REPO / "ontology" / name).read_text())


@pytest.fixture
def ontology_data(document_data: dict) -> dict:
    return {
        "version": "0.2.0.dev0",
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
                        "object_mappings": [
                            {
                                "referent_mappings": [
                                    {"relationship": "at", "expression": "events.occurred_at"}
                                ]
                            }
                        ],
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


@pytest.mark.parametrize("name", ["flights.ontology.yaml", "flights.yaml"])
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
        ("version: 0.2.0.dev0\nname: a\nname: b\n", yaml.constructor.ConstructorError, "duplicate"),
        ("version: 0.2.0.dev0\nname: events\ndescription:\n", ValueError, "null.*: description"),
    ],
    ids=["missing-version", "duplicate-key", "null"],
)
def test_ontology_files_get_the_document_checks(text: str, error: type, message: str) -> None:
    with pytest.raises(error, match=message):
        OssieOntologyModel.from_ossie_yaml(text)


def test_embedded_mapping_carries_a_full_semantic_model_document(ontology_data: dict) -> None:
    ontology = OssieOntologyModel.model_validate(ontology_data)

    semantic_model = ontology.ontology_mappings[0].semantic_model
    assert isinstance(semantic_model, OssieSemanticModel)
    assert semantic_model.version == "0.2.0.dev0"
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


def test_models_mirror_the_schema_properties() -> None:
    """Each object node maps to a model with the same properties, required set and strictness.

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
    nodes = {"": schema} | {
        key: node for key, node in schema["$defs"].items() if node.get("type") == "object"
    }
    assert set(nodes) == set(models)

    for key, model in models.items():
        node = nodes[key]
        fields = {field.alias or name: field for name, field in model.model_fields.items()}
        assert set(fields) == set(node["properties"]), model.__name__
        # `version` carries a default for Python construction; documents still require it.
        required = {name for name, field in fields.items() if field.is_required()}
        assert required | ({"version"} & set(fields)) == set(node.get("required", [])), (
            model.__name__
        )
        assert node["additionalProperties"] is False
        assert model.model_config.get("extra") == "forbid", model.__name__


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
            "children": [
                {"object_mapping": {"concept": "Store", "expression": "ITEMS.store"}}
            ],
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
