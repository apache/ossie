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

"""Validate ontology mappings and mapping documents against the local schemas."""

import json
import urllib.request
from importlib.util import module_from_spec, spec_from_file_location
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


# --- Standalone mapping documents -------------------------------------------

_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate_for_ontology", _VALIDATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

_ONTOLOGY_RAW_URL = "https://raw.githubusercontent.com/apache/ossie/main/ontology/ontology.json"


@pytest.fixture
def offline(monkeypatch):
    def reject_request(*args, **kwargs):
        pytest.fail("Schema validation must not make HTTP requests")

    monkeypatch.setattr(urllib.request, "urlopen", reject_request)
    monkeypatch.setattr("jsonschema.validators.urlopen", reject_request, raising=False)


@pytest.fixture
def mapping_schema() -> dict:
    schema = json.loads((_REPO / "ontology/mapping.json").read_text())
    Draft202012Validator.check_schema(schema)
    return schema


def _schema(relative_path: str) -> dict:
    return json.loads((_REPO / relative_path).read_text())


def _mapping_document() -> dict:
    return {
        "version": "0.2.0.dev0",
        "name": "sales_mapping",
        "ontology_ref": {"name": "sales", "iri": "./sales.ontology.yaml"},
        "semantic_model_ref": {
            "name": "sales_model",
            "iri": "./sales.semantic_model.yaml",
        },
        "concept_mappings": [
            {
                "concept": "Order",
                "object_mappings": [{"expression": "orders.order_id"}],
            }
        ],
    }


@pytest.mark.parametrize(
    "uri", ["https://github.com/apache/ossie/ontology/ontology.json", _ONTOLOGY_RAW_URL]
)
def test_ontology_schema_references_resolve_offline(offline, uri):
    concept_mapping = _mapping_document()["concept_mappings"][0]

    assert _VALIDATE.validate_schema(
        concept_mapping, {"$ref": uri + "#/$defs/ConceptMapping"}
    ) == []


def test_plain_semantic_model_does_not_load_ontology_schema(offline, monkeypatch):
    core_schema = _schema("core-spec/ossie-schema.json")
    ontology_path = (_REPO / "ontology/ontology.json").resolve()
    original_read_text = Path.read_text
    _VALIDATE._retrieve_local_schema.cache_clear()

    def reject_ontology_read(path, *args, **kwargs):
        if path.resolve() == ontology_path:
            pytest.fail("Plain semantic model validation must not load ontology.json")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_ontology_read)

    assert _VALIDATE.validate_schema(_semantic_model(), core_schema) == []


@pytest.mark.parametrize("failure", ["missing", "malformed"])
def test_unavailable_referenced_ontology_is_validation_error(
    offline, mapping_schema, monkeypatch, failure
):
    ontology_path = (_REPO / "ontology/ontology.json").resolve()
    original_read_text = Path.read_text
    _VALIDATE._retrieve_local_schema.cache_clear()

    def fail_ontology_read(path, *args, **kwargs):
        if path.resolve() == ontology_path:
            if failure == "missing":
                raise FileNotFoundError(path)
            return "{"
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_ontology_read)

    errors = _VALIDATE.validate_schema(_mapping_document(), mapping_schema)
    expected_ref = f"{_ONTOLOGY_RAW_URL}#/$defs/ConceptMapping"

    assert errors == [f"[Schema] Cannot resolve schema reference: {expected_ref}"]


@pytest.mark.parametrize(
    "example, schema_path",
    [
        ("flights.ontology.yaml", "ontology/ontology.json"),
        ("flights.semantic_model.yaml", "core-spec/ossie-schema.json"),
        ("flights.mapping.yaml", "ontology/mapping.json"),
    ],
)
def test_split_flights_examples_validate_offline(offline, example, schema_path):
    document = yaml.safe_load((_REPO / "examples" / example).read_text())

    assert _VALIDATE.validate_schema(document, _schema(schema_path)) == []


def test_split_flights_examples_match_embedded_example():
    flights = yaml.safe_load((_REPO / "examples/flights.yaml").read_text())
    ontology = yaml.safe_load((_REPO / "examples/flights.ontology.yaml").read_text())
    model = yaml.safe_load((_REPO / "examples/flights.semantic_model.yaml").read_text())
    mapping = yaml.safe_load((_REPO / "examples/flights.mapping.yaml").read_text())
    (embedded,) = flights.pop("ontology_mappings")

    assert ontology == flights
    assert model == embedded["semantic_model"]
    assert mapping["concept_mappings"] == embedded["concept_mappings"]
    assert mapping["ontology_ref"]["name"] == ontology["name"]
    assert mapping["semantic_model_ref"]["name"] == model["name"]


def test_mapping_document_accepts_references(offline, mapping_schema):
    assert _VALIDATE.validate_schema(_mapping_document(), mapping_schema) == []


def test_mapping_document_reference_iri_is_optional(offline, mapping_schema):
    document = _mapping_document()
    del document["ontology_ref"]["iri"]
    del document["semantic_model_ref"]["iri"]

    assert _VALIDATE.validate_schema(document, mapping_schema) == []


@pytest.mark.parametrize(
    "required_property",
    ["version", "name", "ontology_ref", "semantic_model_ref", "concept_mappings"],
)
def test_mapping_document_requires_property(offline, mapping_schema, required_property):
    document = _mapping_document()
    del document[required_property]

    errors = _VALIDATE.validate_schema(document, mapping_schema)

    assert errors == [f"[Schema] (root): '{required_property}' is a required property"]


@pytest.mark.parametrize("reference", ["ontology_ref", "semantic_model_ref"])
def test_mapping_document_reference_requires_name(offline, mapping_schema, reference):
    document = _mapping_document()
    del document[reference]["name"]

    assert _VALIDATE.validate_schema(document, mapping_schema)


@pytest.mark.parametrize(
    "container, field",
    [
        (None, "name"),
        ("ontology_ref", "name"),
        ("ontology_ref", "iri"),
        ("semantic_model_ref", "name"),
        ("semantic_model_ref", "iri"),
    ],
)
def test_mapping_document_rejects_empty_identifiers(
    offline, mapping_schema, container, field
):
    document = _mapping_document()
    target = document if container is None else document[container]
    target[field] = ""

    assert _VALIDATE.validate_schema(document, mapping_schema)


@pytest.mark.parametrize(
    "mutate",
    [
        # Embedding a complete semantic model instead of referencing one.
        lambda d: d.update(semantic_model_ref=_semantic_model()),
        # More than one semantic model or ontology per mapping document.
        lambda d: d.update(
            semantic_model_ref=[d["semantic_model_ref"], d["semantic_model_ref"]]
        ),
        lambda d: d.update(ontology_ref=[d["ontology_ref"], d["ontology_ref"]]),
        # Unknown fields, including a document-kind discriminator.
        lambda d: d.update(kind="mapping"),
        lambda d: d["ontology_ref"].update(version="1.0"),
        # Wrong specification version.
        lambda d: d.update(version="0.1.0"),
    ],
    ids=[
        "embedded-semantic-model",
        "two-semantic-models",
        "two-ontologies",
        "kind-field",
        "unknown-reference-field",
        "wrong-version",
    ],
)
def test_mapping_document_rejects_invalid_shapes(offline, mapping_schema, mutate):
    document = _mapping_document()
    mutate(document)

    assert _VALIDATE.validate_schema(document, mapping_schema)
