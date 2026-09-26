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

"""Validate ontology mappings against the local core document schema."""

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

_REPO = Path(__file__).parents[2]


@pytest.fixture
def ontology_validator() -> Draft202012Validator:
    core = json.loads((_REPO / "core-spec/ossie-schema.json").read_text())
    ontology = json.loads((_REPO / "ontology/ontology.json").read_text())
    Draft202012Validator.check_schema(core)
    Draft202012Validator.check_schema(ontology)
    resource = Resource.from_contents(core)
    # Resolve both the core schema's canonical ID and the retrieval URL used by
    # ontology references locally. Registry never fetches over HTTP.
    registry = Registry().with_resources(
        [
            (core["$id"], resource),
            (
                "https://raw.githubusercontent.com/apache/ossie/main/core-spec/ossie-schema.json",
                resource,
            ),
        ]
    )
    return Draft202012Validator(ontology, registry=registry)


def _ontology(semantic_model: object) -> dict:
    return {
        "version": "0.2.0.dev0",
        "name": "sales",
        "ai_context": {"instructions": "Use for sales analysis"},
        "ontology": [{"concept": "Order", "type": "EntityType"}],
        "ontology_mappings": [
            {
                "name": "sales_mapping",
                "semantic_model": semantic_model,
                "concept_mappings": [],
            }
        ],
    }


def _semantic_model() -> dict:
    return {
        "version": "0.2.0.dev0",
        "name": "sales_model",
        "datasets": [{"name": "orders", "source": "sales.public.orders"}],
    }


def test_ontology_accepts_complete_core_document(ontology_validator):
    ontology_validator.validate(_ontology(_semantic_model()))


@pytest.mark.parametrize("required_property", ["version", "name", "datasets"])
def test_embedded_model_requires_core_document_properties(
    ontology_validator, required_property
):
    model = _semantic_model()
    del model[required_property]

    errors = list(ontology_validator.iter_errors(_ontology(model)))

    assert len(errors) == 1
    assert errors[0].validator == "required"
    assert required_property in errors[0].message
    assert list(errors[0].absolute_path) == ["ontology_mappings", 0, "semantic_model"]


@pytest.mark.parametrize("version", ["0.1.0", "0.2.0", "", None, 2])
def test_embedded_model_rejects_invalid_version(ontology_validator, version):
    model = _semantic_model()
    model["version"] = version

    errors = list(ontology_validator.iter_errors(_ontology(model)))

    assert errors
    assert all(
        list(error.absolute_path) == ["ontology_mappings", 0, "semantic_model", "version"]
        for error in errors
    )


@pytest.mark.parametrize(
    "model",
    [
        {**_semantic_model(), "datasets": []},
        {**_semantic_model(), "unexpected": True},
        {"semantic_model": _semantic_model()},
        [_semantic_model()],
    ],
)
def test_embedded_model_rejects_invalid_document_shapes(ontology_validator, model):
    assert not ontology_validator.is_valid(_ontology(model))


def test_flights_embedded_models_validate_as_core_documents(ontology_validator):
    flights = yaml.safe_load((_REPO / "examples/flights.yaml").read_text())

    assert flights["ontology_mappings"]
    for mapping in flights["ontology_mappings"]:
        # Validate the embedded core documents without expanding this test to
        # unrelated ontology concept and relationship constraints.
        ontology_validator.validate(_ontology(mapping["semantic_model"]))
