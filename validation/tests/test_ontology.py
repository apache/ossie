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


@pytest.mark.parametrize(
    "mutate",
    [
        # A concept mapping populating neither objects nor links.
        lambda d: d["concept_mappings"].append({"concept": "Customer"}),
        # An object mapping that computes nothing and references nothing.
        lambda d: d["concept_mappings"][0]["object_mappings"].append({}),
        lambda d: d["concept_mappings"][0]["object_mappings"].append({"concept": "Order"}),
        # A referent mapping that computes nothing and references nothing.
        lambda d: d["concept_mappings"][0]["object_mappings"].append(
            {"referent_mappings": [{"relationship": "placed_by"}]}
        ),
        # Present but empty: an empty array satisfies `required` on its own.
        lambda d: d["concept_mappings"].append({"concept": "Customer", "object_mappings": []}),
        lambda d: d["concept_mappings"].append({"concept": "Customer", "link_mappings": []}),
        lambda d: d["concept_mappings"][0]["object_mappings"].append(
            {"referent_mappings": []}
        ),
        lambda d: d["concept_mappings"][0]["object_mappings"].append(
            {"referent_mappings": [{"relationship": "placed_by", "referent_mappings": []}]}
        ),
    ],
    ids=[
        "concept-mapping-populates-nothing",
        "empty-object-mapping",
        "object-mapping-names-only-a-concept",
        "referent-mapping-resolves-nothing",
        "empty-object-mappings-array",
        "empty-link-mappings-array",
        "empty-referent-mappings-array",
        "empty-nested-referent-mappings-array",
    ],
)
def test_mapping_document_rejects_a_mapping_that_maps_nothing(
    offline, mapping_schema, mutate
):
    """Each pair is required-if-the-other-is-absent in `ontology.md`.

    The rule is stated in the Concept mappings, Object mappings and Referent mappings
    sections, but was not expressed in the schema, so `{}` was a legal object mapping
    and a document could declare a concept while saying nothing about how to populate
    it. An empty array satisfies `required`, so each array is also `minItems: 1`.
    """
    document = _mapping_document()
    mutate(document)

    assert _VALIDATE.validate_schema(document, mapping_schema)


def test_mapping_document_accepts_either_side_of_each_pair(offline, mapping_schema):
    """The alternative to the one the base document uses is equally valid."""
    document = _mapping_document()
    document["concept_mappings"][0]["object_mappings"].append(
        {"referent_mappings": [{"relationship": "placed_by", "expression": "orders.cust"}]}
    )
    document["concept_mappings"].append(
        {
            "concept": "Customer",
            "link_mappings": [{"object_mapping": {"expression": "customers.id"}}],
        }
    )

    assert not _VALIDATE.validate_schema(document, mapping_schema)


def test_mapping_document_accepts_both_sides_of_each_pair(offline, mapping_schema):
    """The rule is at least one, not exactly one, which is why it is `anyOf`.

    `ontology.md` makes each property required only when the other is absent, so a
    mapping carrying both is not forbidden. `oneOf` would reject this document.
    """
    document = _mapping_document()
    document["concept_mappings"][0].update(
        object_mappings=[
            {
                "expression": "orders.order_id",
                "referent_mappings": [
                    {
                        "relationship": "placed_by",
                        "expression": "orders.cust",
                        "referent_mappings": [
                            {"relationship": "code", "expression": "customers.id"}
                        ],
                    }
                ],
            }
        ],
        link_mappings=[{"object_mapping": {"expression": "customers.id"}}],
    )

    assert not _VALIDATE.validate_schema(document, mapping_schema)


# ---------------------------------------------------------------------------
# Mapping documents: cross-checks against the referenced ontology and model.
# ---------------------------------------------------------------------------


def _sales_ontology() -> dict:
    def relationship(name: str, concept: str) -> dict:
        return {
            "name": name,
            "roles": [{"concept": concept}],
            "verbalizes": [f"{{_}} has {{{concept}}}"],
        }

    return {
        "version": "0.2.0.dev0",
        "name": "sales",
        "ontology": [
            {
                "concept": "Party",
                "type": "EntityType",
                "relationships": [relationship("nr", "Integer")],
            },
            # Customer inherits nr from Party.
            {
                "concept": "Customer",
                "type": "EntityType",
                "extends": ["Party"],
                "identify_by": ["nr"],
            },
            {
                "concept": "Order",
                "type": "EntityType",
                "identify_by": ["nr"],
                "relationships": [
                    relationship("nr", "Integer"),
                    relationship("customer", "Customer"),
                    # Named at a link mapping root in the undeclared-concept test, so unary.
                    {"name": "placed_by", "verbalizes": ["{Order} is placed"]},
                    relationship("total", "Decimal"),
                    # Unary: a link at level 1, and nothing for a referent to resolve on.
                    {"name": "active", "verbalizes": ["{Order} is active"]},
                ],
            },
        ],
    }


def _sales_model() -> dict:
    expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_id"}]}
    return {
        "version": "0.2.0.dev0",
        "name": "sales_model",
        "datasets": [
            {
                "name": "orders",
                "source": "sales.public.orders",
                "fields": [
                    {"name": "order_id", "expression": expression},
                    {"name": "customer_id", "expression": expression},
                ],
            }
        ],
    }


def _write_mapping_set(tmp_path, mapping=None, ontology=None, model=None) -> dict:
    """Write the three documents next to each other and return the mapping."""
    mapping = _mapping_document() if mapping is None else mapping
    (tmp_path / "sales.ontology.yaml").write_text(
        yaml.safe_dump(_sales_ontology() if ontology is None else ontology)
    )
    (tmp_path / "sales.semantic_model.yaml").write_text(
        yaml.safe_dump(_sales_model() if model is None else model)
    )
    return mapping


def _cross_check(tmp_path, mapping_schema, mapping) -> list[str]:
    return _VALIDATE.validate_document(mapping, mapping_schema, tmp_path)


def test_split_flights_mapping_cross_checks_against_its_documents(offline, mapping_schema):
    mapping = yaml.safe_load((_REPO / "examples/flights.mapping.yaml").read_text())

    diagnostics = _VALIDATE.validate_document(mapping, mapping_schema, _REPO / "examples")

    # Two Flight link mappings filter with "FLIGHT.id WHERE ( ... == TRUE )", which is
    # not a SQL expression, so their fields are skipped with a warning and nothing fails.
    assert [type(d) for d in diagnostics] == [_VALIDATE.ValidationWarning] * 2
    assert all("expression does not parse as a SQL expression" in d for d in diagnostics)
    assert all("concept_mappings -> 8 -> link_mappings -> " in d for d in diagnostics)


def test_mapping_cross_checks_accept_resolving_references(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)

    assert _cross_check(tmp_path, mapping_schema, mapping) == []


@pytest.mark.parametrize("reference", ["ontology_ref", "semantic_model_ref"])
def test_mapping_reference_must_name_the_document_it_resolves_to(
    offline, mapping_schema, tmp_path, reference
):
    mapping = _write_mapping_set(tmp_path)
    mapping[reference]["name"] = "something_else"
    mapping["concept_mappings"][0]["concept"] = "Ordr"
    iri = mapping[reference]["iri"]
    actual = {"ontology_ref": "sales", "semantic_model_ref": "sales_model"}[reference]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    # The misnamed document is still the one the mapping points at, so the
    # checks against its content run as well.
    assert errors == [
        f"[Reference] {reference} names 'something_else' but the document at iri '{iri}' "
        f"is named '{actual}'",
        "[Reference] concept_mappings -> 0: concept 'Ordr' is not declared in the ontology",
    ]


def test_mapping_concepts_must_be_declared_in_the_ontology(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"] = [
        {"concept": "Ordr", "object_mappings": [{"expression": "orders.order_id"}]},
        {
            "concept": "Order",
            "link_mappings": [
                {
                    "object_mapping": {"expression": "orders.order_id"},
                    "relationship": "placed_by",
                    "children": [
                        {
                            "object_mapping": {
                                "concept": "Custmer",
                                "expression": "orders.customer_id",
                            }
                        },
                        {
                            "object_mapping": {
                                "concept": "Integer",
                                "expression": "orders.customer_id",
                            }
                        },
                    ],
                }
            ],
        },
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0: concept 'Ordr' is not declared in the ontology",
        "[Reference] concept_mappings -> 1 -> link_mappings -> 0 -> children -> 0 -> object_mapping: "
        "concept 'Custmer' is not declared in the ontology",
    ]


def test_mapping_expressions_must_name_declared_fields(offline, mapping_schema, tmp_path):
    model = _sales_model()
    # A dataset that declares no fields list exposes no fields (spec.md, Name Resolution).
    model["datasets"].append({"name": "customers", "source": "sales.public.customers"})
    mapping = _write_mapping_set(tmp_path, model=model)
    mapping["concept_mappings"][0]["object_mappings"] = [
        {"expression": "orders.order_id"},
        {"expression": "orders.order_nr + orders.order_nr"},
        # Regular identifiers match without regard to case (expression_language.md).
        {"expression": "ORDERS.Order_Id"},
        {"expression": "ORDERS.ORDER_NR"},
        # A quoted identifier matches as written, against the model's regular name upper-cased.
        {"expression": '"ORDERS"."ORDER_ID"'},
        {"expression": '"orders".order_id'},
        {"expression": 'orders."order_id"'},
        {"expression": "CONCAT(orders.order_id, '.', orders.customer_id)"},
        {"expression": "CASE WHEN orders.order_id > 0 THEN 'a.b' END"},
        {"expression": "CAST(orders.order_id AS pg_catalog.int4)"},
        {"expression": "orders.order_id::pg_catalog.int4"},
        {"expression": "transform(orders.order_id, x -> x.y)"},
        {"expression": "customers.anything"},
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 1: expression names "
        "'orders.order_nr' but dataset 'orders' declares no field 'order_nr'",
        "[Reference] concept_mappings -> 0 -> object_mappings -> 3: expression names "
        "'ORDERS.ORDER_NR' but dataset 'orders' declares no field 'ORDER_NR'",
        "[Reference] concept_mappings -> 0 -> object_mappings -> 5: expression names "
        "'\"orders\".order_id' but the semantic model declares no dataset '\"orders\"'",
        "[Reference] concept_mappings -> 0 -> object_mappings -> 6: expression names "
        "'orders.\"order_id\"' but dataset 'orders' declares no field '\"order_id\"'",
        "[Reference] concept_mappings -> 0 -> object_mappings -> 12: expression names "
        "'customers.anything' but dataset 'customers' declares no field 'anything'",
    ]


def test_mapping_correlated_subquery_reference_is_checked(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["object_mappings"] = [
        {"expression": "EXISTS(SELECT 1 FROM t WHERE t.a = orders.order_id)"},
        {"expression": "EXISTS(SELECT 1 FROM t WHERE t.a = orders.nonexistent)"},
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 1: expression names "
        "'orders.nonexistent' but dataset 'orders' declares no field 'nonexistent'",
    ]


def test_warning_before_the_mapping_checks_does_not_skip_them(
    offline, mapping_schema, tmp_path, monkeypatch
):
    # No check that runs before the mapping checks warns on a mapping document
    # today, so one is made to: the gate must count errors, not diagnostics.
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["concept"] = "Ordr"
    validate_schema = _VALIDATE.validate_schema

    def warn_on_the_mapping(data, schema):
        warning = [_VALIDATE.ValidationWarning("[Schema] Warning: probe")] if data is mapping else []
        return warning + validate_schema(data, schema)

    monkeypatch.setattr(_VALIDATE, "validate_schema", warn_on_the_mapping)

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Schema] Warning: probe",
        "[Reference] concept_mappings -> 0: concept 'Ordr' is not declared in the ontology",
    ]


def test_mapping_datasets_that_differ_only_in_case_pool_their_fields(
    offline, mapping_schema, tmp_path
):
    model = _sales_model()
    model["datasets"].append(
        {
            "name": "ORDERS",
            "source": "sales.public.orders_v2",
            "fields": [{"name": "status", "expression": {"dialects": [
                {"dialect": "ANSI_SQL", "expression": "status"}
            ]}}],
        }
    )
    mapping = _write_mapping_set(tmp_path, model=model)
    mapping["concept_mappings"][0]["object_mappings"] = [
        {"expression": "orders.order_id"},
        {"expression": "orders.status"},
        {"expression": "Orders.order_nr"},
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 2: expression names "
        "'Orders.order_nr' but dataset 'orders' declares no field 'order_nr'"
    ]


def test_mapping_declared_names_normalize_like_references(offline, mapping_schema, tmp_path):
    model = _sales_model()
    expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": "1"}]}
    model["datasets"] += [
        {
            "name": "order items",
            "source": "sales.public.order_items",
            "fields": [{"name": "Order Id", "expression": expression}],
        },
        {
            "name": "été",
            "source": "sales.public.ete",
            "fields": [
                {"name": "jour", "expression": expression},
                {"name": "from", "expression": expression},
            ],
        },
    ]
    mapping = _write_mapping_set(tmp_path, model=model)
    mapping["concept_mappings"][0]["object_mappings"] = [
        # A name that is not a regular identifier can only be written quoted,
        # so it matches as written.
        {"expression": '"order items"."Order Id"'},
        {"expression": '"ORDER ITEMS"."ORDER ID"'},
        # A name the parser reads unquoted is regular, whatever its first
        # letter, and so is a keyword it reads as a field.
        {"expression": "été.jour"},
        {"expression": "ÉTÉ.Jour"},
        {"expression": "été.from"},
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 1: expression names "
        "'\"ORDER ITEMS\".\"ORDER ID\"' but the semantic model declares no dataset "
        "'\"ORDER ITEMS\"'"
    ]


def test_mapping_dataset_without_a_fields_list_adds_no_fields_to_its_name(
    offline, mapping_schema, tmp_path
):
    model = _sales_model()
    model["datasets"].append({"name": "ORDERS", "source": "sales.public.orders_v2"})
    mapping = _write_mapping_set(tmp_path, model=model)
    mapping["concept_mappings"][0]["object_mappings"] = [
        {"expression": "orders.order_id"},
        {"expression": "orders.order_nr"},
    ]

    assert _cross_check(tmp_path, mapping_schema, mapping) == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 1: expression names "
        "'orders.order_nr' but dataset 'orders' declares no field 'order_nr'",
    ]


@pytest.mark.parametrize("expression", ["", "   ", "-- nothing", ";"])
def test_mapping_expression_that_holds_nothing_is_an_error(
    offline, mapping_schema, tmp_path, expression
):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["object_mappings"] = [{"expression": expression}]

    assert _cross_check(tmp_path, mapping_schema, mapping) == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0: expression is empty",
    ]


def test_mapping_expression_that_holds_nothing_is_an_error_without_the_model(
    offline, mapping_schema, tmp_path
):
    mapping = _write_mapping_set(tmp_path)
    (tmp_path / "sales.semantic_model.yaml").unlink()
    mapping["concept_mappings"][0]["object_mappings"] = [{"expression": "  "}]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert len(errors) == 2
    assert "does not resolve" in errors[0]
    assert errors[1] == (
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0: expression is empty"
    )


def test_mapping_expression_that_is_not_sql_is_skipped_with_a_warning(
    offline, mapping_schema, tmp_path
):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["object_mappings"] = [
        {"expression": "orders.payload:customer.id"},
        {"expression": "orders.order_nr"},
    ]

    diagnostics = _cross_check(tmp_path, mapping_schema, mapping)

    assert [type(d) for d in diagnostics] == [_VALIDATE.ValidationWarning, str]
    assert diagnostics[0] == (
        "[Reference] Warning: concept_mappings -> 0 -> object_mappings -> 0: expression does "
        "not parse as a SQL expression, so the fields it names are not checked"
    )
    assert "declares no field 'order_nr'" in diagnostics[1]


def test_mapping_expressions_are_not_checked_without_sqlglot(
    offline, mapping_schema, tmp_path, monkeypatch
):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["object_mappings"] = [{"expression": "orders.order_nr"}]
    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", False)

    diagnostics = _cross_check(tmp_path, mapping_schema, mapping)

    assert diagnostics == [
        "[Reference] Warning: sqlglot is not installed, so the fields the mapping "
        "expressions name are not checked. Install with: pip install sqlglot"
    ]
    assert isinstance(diagnostics[0], _VALIDATE.ValidationWarning)


def _reference(dataset: str, field: str, dataset_quoted: bool = False, field_quoted: bool = False):
    """A FieldReference for a dataset.field written with the given quoting."""
    written = (
        f'"{dataset}"' if dataset_quoted else dataset,
        f'"{field}"' if field_quoted else field,
    )
    normalized = (
        dataset if dataset_quoted else dataset.upper(),
        field if field_quoted else field.upper(),
    )
    return _VALIDATE.FieldReference(written, normalized)


_ORDER_ID = _reference("orders", "order_id")


@pytest.mark.parametrize(
    "expression, expected",
    [
        ("orders.order_id -- see orders.nope", [_ORDER_ID]),
        ("/* orders.nope */ orders.order_id", [_ORDER_ID]),
        ("'it''s a.b' || orders.order_id", [_ORDER_ID]),
        ("'orders.nope' || orders.order_id", [_ORDER_ID]),
        ("order_id", []),
        ("cat.sch.orders.order_id", []),
        ("orders.*", []),
        ("COUNT(*)", []),
        ("sch.fn(orders.order_id)", [_ORDER_ID]),
        ("CAST(orders.order_id AS pg_catalog.int4)", [_ORDER_ID]),
        ("orders.order_id::pg_catalog.int4", [_ORDER_ID]),
        ("transform(orders.order_id, x -> x.y)", [_ORDER_ID]),
        ("orders.order_id + orders.order_id", [_ORDER_ID]),
        ("1.5 * orders.order_id", [_ORDER_ID]),
        (
            "orders.order_id > 0 AND orders.amount > 1.5",
            [_ORDER_ID, _reference("orders", "amount")],
        ),
        ('"Orders".order_id', [_reference("Orders", "order_id", True, False)]),
        ('orders."Order Id"', [_reference("orders", "Order Id", False, True)]),
        # The quoted form is kept as written and the normalized one unescaped.
        ('"it""s".x', [_VALIDATE.FieldReference(('"it""s"', "x"), ('it"s', "X"))]),
        # A column inside a subquery that names a table or alias bound in that
        # query, or in a query around it, resolves there, not in the model. A
        # correlated reference to a dataset is still a reference.
        ("orders.order_id IN (SELECT o.id FROM other o)", [_ORDER_ID]),
        ("(SELECT MAX(o.amount) FROM other o WHERE o.id = orders.order_id)", [_ORDER_ID]),
        ("EXISTS(SELECT 1 FROM t WHERE t.a = orders.order_id)", [_ORDER_ID]),
        ("EXISTS(SELECT 1 FROM t AS u WHERE u.a = orders.order_id)", [_ORDER_ID]),
        ("EXISTS(SELECT 1 FROM sch.t WHERE t.a = orders.order_id)", [_ORDER_ID]),
        (
            "EXISTS(SELECT 1 FROM t JOIN u ON t.id = u.id WHERE u.v = orders.order_id)",
            [_ORDER_ID],
        ),
        (
            "EXISTS(SELECT 1 FROM (SELECT 1 AS k) AS s WHERE s.k = orders.order_id)",
            [_ORDER_ID],
        ),
        (
            "EXISTS(WITH c AS (SELECT 1 AS k FROM z) SELECT 1 FROM c WHERE c.k = orders.order_id)",
            [_ORDER_ID],
        ),
        (
            "EXISTS(SELECT 1 FROM UNNEST(orders.tags) AS u WHERE u.v = orders.order_id)",
            [_reference("orders", "tags"), _ORDER_ID],
        ),
        ("EXISTS(SELECT 1 FROM (VALUES (1)) AS t(k) WHERE t.k = orders.order_id)", [_ORDER_ID]),
        (
            "EXISTS(SELECT 1 FROM a PIVOT (SUM(x) FOR y IN ('p')) AS p WHERE p.x = orders.order_id)",
            [_ORDER_ID],
        ),
        ("EXISTS(SELECT 1 FROM a, b WHERE a.k = b.k AND b.z = orders.order_id)", [_ORDER_ID]),
        ("EXISTS(SELECT a.k FROM a GROUP BY a.k HAVING MAX(a.v) > orders.order_id)", [_ORDER_ID]),
        # Inside the query, a dataset's own name is bound like any table.
        ("EXISTS(SELECT 1 FROM orders AS o WHERE o.k = orders.order_id)", [_ORDER_ID]),
        ("EXISTS(SELECT 1 FROM orders WHERE orders.order_id > 0)", []),
        (
            "EXISTS(SELECT 1 FROM a WHERE EXISTS(SELECT 1 FROM b WHERE b.k = a.k AND a.z = orders.order_id))",
            [_ORDER_ID],
        ),
        # A name bound in a nested query, or in the other branch of a set
        # operation, is not visible outside it.
        (
            "EXISTS(SELECT 1 FROM a UNION ALL SELECT 1 FROM b WHERE a.k = orders.order_id)",
            [_reference("a", "k"), _ORDER_ID],
        ),
        (
            "EXISTS(SELECT 1 FROM a WHERE EXISTS(SELECT 1 FROM b) AND b.k = orders.order_id)",
            [_reference("b", "k"), _ORDER_ID],
        ),
        # Bindings follow identifier normalization: a quoted alias binds as written.
        (
            'EXISTS(SELECT 1 FROM t AS "Orders" WHERE "Orders".x = orders.order_id)',
            [_ORDER_ID],
        ),
        ('EXISTS(SELECT 1 FROM t AS "ORDERS" WHERE orders.order_id > 0)', []),
        # Not SQL expressions: a Snowflake path, a backslash escape, a backtick
        # identifier, nothing at all, token soup, and nesting past the recursion limit.
        ("orders.payload:customer.id", None),
        ("'it\\'s a.b' || orders.order_id", None),
        ("`a.b` + orders.order_id", None),
        ("", None),
        ("orders.order_id = {:}", None),
        ("(" * 5000 + "orders.order_id" + ")" * 5000, None),
        # Parsed as an expression, a dataset named like a keyword reads as a
        # column. A statement, or two expressions, does not parse.
        ("use.no_such_field", [_reference("use", "no_such_field")]),
        ("cache.x", [_reference("cache", "x")]),
        ("analyze.x", [_reference("analyze", "x")]),
        ("truncate.x", [_reference("truncate", "x")]),
        ("update.x", [_reference("update", "x")]),
        ("set.x", [_reference("set", "x")]),
        ("pivot.x", [_reference("pivot", "x")]),
        ("from.x", None),
        ("VALUES (1)", None),
        ("orders.order_id; DROP TABLE t", None),
        ("orders.order_id; orders.customer_id", None),
    ],
)
def test_field_references_are_the_two_part_columns_of_the_parsed_expression(expression, expected):
    assert _VALIDATE.field_references(expression) == expected


@pytest.mark.parametrize(
    "name, expected",
    [
        ("order_id", "ORDER_ID"),
        ("Order_Id", "ORDER_ID"),
        ("été", "ÉTÉ"),
        ("order", "ORDER"),
        ("from", "FROM"),
        ("case", "CASE"),
        ("order items", "order items"),
        ("order_id ", "order_id "),
        ("1x", "1x"),
        ("select", "select"),
    ],
)
def test_declared_name_is_upper_cased_only_when_regular(name, expected):
    assert _VALIDATE.declared_name(name) == expected


def test_mapping_expressions_in_referent_mappings_are_checked(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["object_mappings"] = [
        {
            "referent_mappings": [
                {
                    "relationship": "customer",
                    "referent_mappings": [
                        {"relationship": "nr", "expression": "orders.customer_nr"}
                    ],
                }
            ]
        }
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0 -> referent_mappings -> 0 "
        "-> referent_mappings -> 0: expression names 'orders.customer_nr' but dataset 'orders' "
        "declares no field 'customer_nr'"
    ]


def test_sales_ontology_fixture_is_a_valid_ontology():
    assert _VALIDATE.validate_ontology(_sales_ontology()) == []


def test_mapping_relationships_resolve_on_their_concept(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"] = [
        {
            "concept": "Order",
            "object_mappings": [
                {
                    "referent_mappings": [
                        {"relationship": "nr", "expression": "orders.order_id"},
                        {
                            # Resolves on Customer, which inherits nr from Party.
                            "relationship": "customer",
                            "referent_mappings": [
                                {"relationship": "Party.nr", "expression": "orders.customer_id"}
                            ],
                        },
                    ]
                }
            ],
            "link_mappings": [
                {
                    # The root maps Order itself, at level 1 for a unary relationship.
                    "object_mapping": {
                        "referent_mappings": [
                            {"relationship": "nr", "expression": "orders.order_id"}
                        ]
                    },
                    "relationship": "active",
                    "children": [
                        {
                            "object_mapping": {
                                "concept": "Decimal",
                                "expression": "orders.order_id",
                            },
                            "relationship": "total",
                        },
                        {
                            # Maps the Customer in the last position of customer links.
                            "object_mapping": {
                                "referent_mappings": [
                                    {"relationship": "nr", "expression": "orders.customer_id"}
                                ]
                            },
                            "relationship": "customer",
                        },
                    ],
                }
            ],
        }
    ]

    assert _cross_check(tmp_path, mapping_schema, mapping) == []


def test_mapping_relationships_must_be_declared_on_their_concept(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"] = [
        {
            "concept": "Order",
            "object_mappings": [
                {
                    "referent_mappings": [
                        {"relationship": "nmbr", "expression": "orders.order_id"},
                        {
                            "relationship": "customer",
                            "referent_mappings": [
                                {"relationship": "code", "expression": "orders.customer_id"}
                            ],
                        },
                    ]
                }
            ],
            "link_mappings": [
                {
                    # The root maps Order even though its own relationship does not resolve.
                    "object_mapping": {
                        "referent_mappings": [
                            {"relationship": "nmbr", "expression": "orders.order_id"}
                        ]
                    },
                    "relationship": "placed",
                    "children": [
                        {
                            # Maps a Customer through the customer relationship's role.
                            "object_mapping": {
                                "referent_mappings": [
                                    {"relationship": "code", "expression": "orders.customer_id"}
                                ],
                            },
                            "relationship": "customer",
                        },
                        {
                            # An undeclared concept is reported once, by the concept check.
                            "object_mapping": {
                                "concept": "Custmer",
                                "referent_mappings": [
                                    {"relationship": "nr", "expression": "orders.customer_id"}
                                ],
                            },
                            "relationship": "customer",
                        },
                    ],
                }
            ],
        },
        {
            "concept": "Ordr",
            "object_mappings": [
                {"referent_mappings": [{"relationship": "nr", "expression": "orders.order_id"}]}
            ],
        },
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> link_mappings -> 0 -> children -> 1 "
        "-> object_mapping: concept 'Custmer' is not declared in the ontology",
        "[Reference] concept_mappings -> 1: concept 'Ordr' is not declared in the ontology",
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0 -> referent_mappings -> 0: "
        "relationship 'nmbr' is not declared on concept 'Order' or a concept it extends",
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0 -> referent_mappings -> 1 "
        "-> referent_mappings -> 0: relationship 'code' is not declared on concept 'Customer' "
        "or a concept it extends",
        "[Reference] concept_mappings -> 0 -> link_mappings -> 0: relationship 'placed' is not "
        "declared on concept 'Order' or a concept it extends",
        "[Reference] concept_mappings -> 0 -> link_mappings -> 0 -> object_mapping "
        "-> referent_mappings -> 0: relationship 'nmbr' is not declared on concept 'Order' or a "
        "concept it extends",
        "[Reference] concept_mappings -> 0 -> link_mappings -> 0 -> children -> 0 "
        "-> object_mapping -> referent_mappings -> 0: relationship 'code' is not declared on "
        "concept 'Customer' or a concept it extends",
    ]


def test_mapping_relationship_prefix_names_the_mapped_concept(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["object_mappings"] = [
        {
            "referent_mappings": [
                {"relationship": "Custmer.nr", "expression": "orders.order_id"},
                {
                    "relationship": "customer",
                    "referent_mappings": [
                        # Order is neither Customer nor a concept Customer extends.
                        {"relationship": "Order.nr", "expression": "orders.customer_id"}
                    ],
                },
                {"relationship": "Order.nmbr", "expression": "orders.order_id"},
            ]
        }
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0 -> referent_mappings -> 0: "
        "relationship 'Custmer.nr' names concept 'Custmer', which is not declared in the ontology",
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0 -> referent_mappings -> 1 "
        "-> referent_mappings -> 0: relationship 'Order.nr' names concept 'Order' but this "
        "mapping maps 'Customer'",
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0 -> referent_mappings -> 2: "
        "relationship 'Order.nmbr' is not declared on concept 'Order' or a concept it extends",
    ]


def test_mapping_nested_referents_need_a_role_to_resolve_on(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["object_mappings"] = [
        {
            "referent_mappings": [
                {
                    "relationship": "active",
                    "referent_mappings": [{"relationship": "nr", "expression": "orders.order_id"}],
                }
            ]
        }
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> object_mappings -> 0 -> referent_mappings -> 0: "
        "relationship 'active' has no role for its referent_mappings to resolve on",
    ]


def test_mapping_link_level_matches_relationship_arity(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["link_mappings"] = [
        {
            "object_mapping": {"expression": "orders.order_id"},
            "relationship": "customer",
            "children": [
                {"object_mapping": {"expression": "orders.order_id"}, "relationship": "active"}
            ],
        }
    ]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [
        "[Reference] concept_mappings -> 0 -> link_mappings -> 0: relationship 'customer' has "
        "arity 2 but is named at level 1",
        "[Reference] concept_mappings -> 0 -> link_mappings -> 0 -> children -> 0: relationship "
        "'active' has arity 1 but is named at level 2",
    ]


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (
            lambda ref: ref.pop("iri"),
            "ontology_ref 'sales' has no iri, so only a catalog can resolve it",
        ),
        (
            lambda ref: ref.update(iri="https://example.org/sales.ontology.yaml"),
            "ontology_ref iri 'https://example.org/sales.ontology.yaml' is not a path reference",
        ),
    ],
)
def test_mapping_reference_this_validator_does_not_follow_is_a_warning(
    offline, mapping_schema, tmp_path, mutate, expected
):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["concept"] = "Ordr"
    mapping["concept_mappings"][0]["object_mappings"] = [{"expression": "orders.order_nr"}]
    mutate(mapping["ontology_ref"])

    diagnostics = _cross_check(tmp_path, mapping_schema, mapping)

    # The ontology half is skipped with a warning, the model half still runs.
    assert [type(d) for d in diagnostics] == [_VALIDATE.ValidationWarning, str]
    assert diagnostics[0].startswith(f"[Reference] Warning: {expected}")
    assert diagnostics[0].endswith("cross-document checks for ontology_ref skipped")
    assert "declares no field 'order_nr'" in diagnostics[1]


def test_mapping_without_a_location_skips_cross_checks_with_a_warning(offline, mapping_schema):
    diagnostics = _VALIDATE.validate_document(_mapping_document(), mapping_schema)

    assert [type(d) for d in diagnostics] == [_VALIDATE.ValidationWarning] * 2
    assert all(d.startswith("[Reference] Warning: ") for d in diagnostics)
    assert all("has no location to resolve it against" in d for d in diagnostics)


def test_mapping_iri_is_read_as_a_uri_reference(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    (tmp_path / "sales ontology.yaml").write_text(yaml.safe_dump(_sales_ontology()))
    mapping["ontology_ref"]["iri"] = "./sales%20ontology.yaml#concepts"
    mapping["semantic_model_ref"]["iri"] = "sales.semantic_model.yaml?v=1"

    assert _cross_check(tmp_path, mapping_schema, mapping) == []


def test_mapping_absolute_path_iri_is_followed_without_a_location(offline, mapping_schema, tmp_path):
    mapping = _write_mapping_set(tmp_path)
    mapping["ontology_ref"]["iri"] = str(tmp_path / "sales.ontology.yaml")
    mapping["semantic_model_ref"]["iri"] = str(tmp_path / "sales.semantic_model.yaml")
    mapping["concept_mappings"][0]["concept"] = "Ordr"

    errors = _VALIDATE.validate_document(mapping, mapping_schema)

    assert errors == [
        "[Reference] concept_mappings -> 0: concept 'Ordr' is not declared in the ontology"
    ]


def test_mapping_reference_to_the_wrong_kind_of_document_is_reported_once(
    offline, mapping_schema, tmp_path
):
    mapping = _write_mapping_set(tmp_path)
    mapping["ontology_ref"] = {"name": "sales_model", "iri": "./sales.semantic_model.yaml"}
    mapping["concept_mappings"].append(
        {"concept": "Customer", "object_mappings": [{"expression": "orders.customer_id"}]}
    )

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    # One line for the document, not one per concept the mapping names.
    assert errors == [
        "[Reference] ontology_ref iri './sales.semantic_model.yaml' does not validate against "
        "ontology/ontology.json (2 error(s), the first at (root): 'ontology' is a required "
        "property), so its concepts are not checked"
    ]


@pytest.mark.parametrize(
    "mutate, label, expected",
    [
        (
            lambda o, m: o["ontology"][2].update(concept=["Order"]),
            "ontology_ref",
            "ontology -> 2 -> concept: ['Order'] is not of type 'string'",
        ),
        (
            lambda o, m: o["ontology"][2]["relationships"][1].update(roles={"concept": "Customer"}),
            "ontology_ref",
            "ontology -> 2 -> relationships -> 1 -> roles: {'concept': 'Customer'} is not of "
            "type 'array'",
        ),
        (
            lambda o, m: m["datasets"][0].update(name=["orders"]),
            "semantic_model_ref",
            "datasets -> 0 -> name: ['orders'] is not of type 'string'",
        ),
        (
            lambda o, m: m["datasets"][0]["fields"][0].update(name={"a": 1}),
            "semantic_model_ref",
            "datasets -> 0 -> fields -> 0 -> name: {'a': 1} is not of type 'string'",
        ),
    ],
)
def test_mapping_referenced_document_that_fails_its_schema_is_one_error(
    offline, mapping_schema, tmp_path, mutate, label, expected
):
    ontology, model = _sales_ontology(), _sales_model()
    mutate(ontology, model)
    mapping = _write_mapping_set(tmp_path, ontology=ontology, model=model)
    # Both halves have something to report. Only the intact half does.
    mapping["concept_mappings"][0]["concept"] = "Custmer"
    mapping["concept_mappings"][0]["object_mappings"] = [{"expression": "orders.order_nr"}]

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert len(errors) == 2
    iri = "./sales.ontology.yaml" if label == "ontology_ref" else "./sales.semantic_model.yaml"
    half = "concepts" if label == "ontology_ref" else "fields"
    assert errors[0].startswith(f"[Reference] {label} iri '{iri}' does not validate against ")
    assert errors[0].endswith(
        f"(1 error(s), the first at {expected}), so its {half} are not checked"
    )
    other = "declares no field 'order_nr'" if label == "ontology_ref" else "concept 'Custmer'"
    assert other in errors[1]


@pytest.mark.parametrize(
    "content, expected",
    [
        (None, "does not resolve: No such file or directory"),
        ("a: [", "is not valid YAML"),
        ("- just\n- a list\n", "does not hold a document"),
        # SafeLoader raises ValueError, AttributeError or KeyError, not YAMLError,
        # for a scalar it resolves to a tag but cannot build.
        ("description: 2024-02-30\n", "holds a value YAML cannot build: "),
        ("description: !!int abc\n", "holds a value YAML cannot build: "),
        ("description: !!timestamp abc\n", "holds a value YAML cannot build: "),
        ("description: !!bool abc\n", "holds a value YAML cannot build: "),
        ("description: !!int ''\n", "holds a value YAML cannot build: "),
        (b"name: caf\xe9\n", "is not valid UTF-8 text"),
        ("[" * 5000, "is too deeply nested to parse"),
    ],
)
def test_mapping_reference_that_does_not_read_as_a_document_is_an_error(
    offline, mapping_schema, tmp_path, content, expected
):
    mapping = _write_mapping_set(tmp_path)
    target = tmp_path / "sales.semantic_model.yaml"
    if content is None:
        target.unlink()
    elif isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content)

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert len(errors) == 1
    assert not isinstance(errors[0], _VALIDATE.ValidationWarning)
    assert errors[0].startswith("[Reference] semantic_model_ref iri './sales.semantic_model.yaml' ")
    assert expected in errors[0]


def test_schema_at_is_none_for_a_file_that_is_not_there_or_not_json(tmp_path):
    (tmp_path / "broken.json").write_text("{")

    assert _VALIDATE._schema_at(tmp_path / "missing.json") is None
    assert _VALIDATE._schema_at(tmp_path / "broken.json") is None
    assert _VALIDATE._schema_at(_REPO / "ontology/ontology.json")["$id"].endswith("ontology.json")


def test_mapping_referenced_document_is_not_checked_without_its_schema(
    offline, mapping_schema, tmp_path, monkeypatch
):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["concept"] = "Custmer"
    mapping["concept_mappings"][0]["object_mappings"] = [{"expression": "orders.order_nr"}]
    monkeypatch.setattr(_VALIDATE, "_schema_at", lambda path: None)

    diagnostics = _cross_check(tmp_path, mapping_schema, mapping)

    assert [type(d) for d in diagnostics] == [_VALIDATE.ValidationWarning] * 2
    assert diagnostics[0] == (
        "[Reference] Warning: ontology/ontology.json is not next to this script, so the "
        "concepts of ontology_ref are not checked"
    )
    assert diagnostics[1].startswith("[Reference] Warning: core-spec/ossie-schema.json is not")


@pytest.mark.parametrize(
    "iri, expected",
    [
        ("http://[oops/sales.ontology.yaml", "is not a valid URI reference: Invalid IPv6 URL"),
        ("//[::1/sales.ontology.yaml", "is not a valid URI reference: Invalid IPv6 URL"),
        ("./sales%00.ontology.yaml", "does not resolve: embedded null byte"),
        ("./sales\ud800.ontology.yaml", "is not a valid file name"),
        # Not a regular file: the mapping document's own directory, and a device.
        (".", "does not name a regular file"),
        pytest.param(
            "/dev/null",
            "does not name a regular file",
            marks=pytest.mark.skipif(not Path("/dev/null").exists(), reason="no /dev/null"),
        ),
    ],
)
def test_mapping_iri_that_cannot_be_followed_is_an_error(
    offline, mapping_schema, tmp_path, iri, expected
):
    mapping = _write_mapping_set(tmp_path)
    mapping["ontology_ref"]["iri"] = iri

    errors = _cross_check(tmp_path, mapping_schema, mapping)

    assert errors == [f"[Reference] ontology_ref iri '{iri}' {expected}"]


def test_validate_file_resolves_mapping_references_from_the_file_location(
    offline, mapping_schema, tmp_path, capsys
):
    mapping = _write_mapping_set(tmp_path)
    mapping["concept_mappings"][0]["object_mappings"] = [{"expression": "orders.order_nr"}]
    mapping_path = tmp_path / "sales.mapping.yaml"
    mapping_path.write_text(yaml.safe_dump(mapping))

    assert _VALIDATE.validate_file(mapping_path, mapping_schema) is False
    assert "declares no field 'order_nr'" in capsys.readouterr().out
