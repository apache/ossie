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
from typing import Any, get_args

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from ossie import models

from ossie import (
    OssieAIContextObject,
    OssieDataset,
    OssieDialect,
    OssieSemanticModel,
)


def test_to_ossie_yaml_uses_alias(document_data: dict) -> None:
    document_data["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OssieSemanticModel.model_validate(document_data)
    output = document.to_ossie_yaml()
    assert "from: orders" in output
    assert "from_dataset" not in output


def test_to_ossie_json_uses_alias(document_data: dict) -> None:
    document_data["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OssieSemanticModel.model_validate(document_data)
    output = document.to_ossie_json()
    parsed = json.loads(output)
    assert parsed["relationships"][0]["from"] == "orders"


def test_to_ossie_yaml_includes_all_dialects(document_data: dict) -> None:
    document_data["datasets"][0]["fields"][0]["expression"] = {
        "dialects": [
            {"dialect": OssieDialect.ANSI_SQL, "expression": "occurred_at"},
            {"dialect": OssieDialect.SNOWFLAKE, "expression": "OCCURRED_AT"},
        ]
    }
    document = OssieSemanticModel.model_validate(document_data)
    parsed = yaml.safe_load(document.to_ossie_yaml())
    assert parsed["datasets"][0]["fields"][0]["expression"]["dialects"] == [
        {"dialect": "ANSI_SQL", "expression": "occurred_at"},
        {"dialect": "SNOWFLAKE", "expression": "OCCURRED_AT"},
    ]


def test_to_ossie_yaml_includes_custom_extension_vendor(document_data: dict) -> None:
    document_data["custom_extensions"] = [
        {"vendor_name": "DATABRICKS", "data": '{"id":"model-1"}'}
    ]
    document = OssieSemanticModel.model_validate(document_data)
    parsed = yaml.safe_load(document.to_ossie_yaml())
    assert parsed["custom_extensions"] == [
        {"vendor_name": "DATABRICKS", "data": '{"id":"model-1"}'}
    ]


def test_to_ossie_yaml_excludes_none(document_data: dict) -> None:
    document = OssieSemanticModel.model_validate(document_data)
    output = document.to_ossie_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["name"] == "typed_model"
    assert "description" not in parsed
    assert "relationships" not in parsed
    assert "ai_context" not in parsed


def test_to_ossie_json_excludes_none(document_data: dict) -> None:
    document = OssieSemanticModel.model_validate(document_data)
    output = document.to_ossie_json()
    parsed = json.loads(output)
    assert parsed["name"] == "typed_model"
    assert "description" not in parsed
    assert "relationships" not in parsed
    assert "ai_context" not in parsed


def test_to_ossie_yaml_validates_as_yaml(document_data: dict) -> None:
    document = OssieSemanticModel.model_validate(document_data)
    output = document.to_ossie_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["name"] == "typed_model"


def test_to_ossie_json_roundtrip(document_data: dict) -> None:
    document_data["datasets"][0]["fields"][0]["expression"] = {
        "dialects": [{"dialect": OssieDialect.DATABRICKS, "expression": "order_id"}]
    }
    document = OssieSemanticModel.model_validate(document_data)
    json_str = document.to_ossie_json()
    parsed = json.loads(json_str)
    document2 = OssieSemanticModel(**parsed)
    assert document2.to_ossie_json() == json_str


def test_to_ossie_yaml_accepts_by_alias_override(document_data: dict) -> None:
    document_data["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OssieSemanticModel.model_validate(document_data)
    parsed = yaml.safe_load(document.to_ossie_yaml(by_alias=False))
    assert parsed["relationships"][0]["from_dataset"] == "orders"
    assert "from" not in parsed["relationships"][0]


def test_to_ossie_json_accepts_by_alias_override(document_data: dict) -> None:
    document_data["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OssieSemanticModel.model_validate(document_data)
    parsed = json.loads(document.to_ossie_json(by_alias=False))
    assert parsed["relationships"][0]["from_dataset"] == "orders"
    assert "from" not in parsed["relationships"][0]


def test_to_ossie_yaml_accepts_exclude_none_override(document_data: dict) -> None:
    document = OssieSemanticModel.model_validate(document_data)
    parsed = yaml.safe_load(document.to_ossie_yaml(exclude_none=False))
    assert parsed["description"] is None
    assert parsed["relationships"] is None


def test_to_ossie_json_accepts_exclude_none_override(document_data: dict) -> None:
    document = OssieSemanticModel.model_validate(document_data)
    parsed = json.loads(document.to_ossie_json(exclude_none=False))
    assert parsed["description"] is None
    assert parsed["relationships"] is None


def test_from_ossie_yaml_round_trips(document_data: dict) -> None:
    text = OssieSemanticModel.model_validate(document_data).to_ossie_yaml()

    assert OssieSemanticModel.from_ossie_yaml(text).to_ossie_yaml() == text


def test_from_ossie_json_round_trips(document_data: dict) -> None:
    text = OssieSemanticModel.model_validate(document_data).to_ossie_json()

    assert OssieSemanticModel.from_ossie_json(text).to_ossie_json() == text


@pytest.mark.parametrize(
    ("mutate", "reported"),
    [
        (lambda d: d.update(description=None), "description"),
        (lambda d: d["datasets"][0].update(description=None), "datasets[0].description"),
        (lambda d: d["datasets"][0]["fields"][0].update(label=None), "datasets[0].fields[0].label"),
        (
            lambda d: d["datasets"][0]["fields"][0].update(dimension={"is_time": None}),
            "datasets[0].fields[0].dimension.is_time",
        ),
        (lambda d: d["metrics"][0].update(datatype=None), "metrics[0].datatype"),
        (
            lambda d: d["datasets"][0].update(ai_context={"instructions": None}),
            "datasets[0].ai_context.instructions",
        ),
    ],
)
def test_loaders_reject_a_property_set_to_null(document_data: dict, mutate, reported: str) -> None:
    """No property in the schema is nullable, at any depth.

    Accepted, `exclude_none=True` would drop the key and emit a document the schema
    accepts, so the null would never be reported.
    """
    mutate(document_data)
    text = yaml.safe_dump(document_data)

    with pytest.raises(ValueError, match="set to null") as error:
        OssieSemanticModel.from_ossie_yaml(text)
    assert _reported_paths(error.value) == {reported}

    with pytest.raises(ValueError, match="set to null"):
        OssieSemanticModel.from_ossie_json(json.dumps(document_data))


def test_loaders_allow_a_null_inside_free_form_ai_context(document_data: dict) -> None:
    """`ai_context` sets `additionalProperties: true`, so an extra key may hold null."""
    document_data["datasets"][0]["ai_context"] = {"instructions": "use me", "vendor_hint": None}

    document = OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document_data))

    ai_context = document.datasets[0].ai_context
    assert isinstance(ai_context, OssieAIContextObject)
    assert ai_context.instructions == "use me"
    assert ai_context.model_extra == {"vendor_hint": None}


def test_model_validate_stays_lenient_about_null(document_data: dict) -> None:
    """Deliberate asymmetry: building a model in Python may pass None to mean "omit".

    Converters rely on it -- `label=dim.label` -- so only the document loaders, which
    read the artefact the schema governs, reject an explicit null.
    """
    document_data["description"] = None

    assert OssieSemanticModel.model_validate(document_data).description is None


def _reported_paths(error: Exception) -> set[str]:
    """The paths a loader named, as exact entries.

    A substring check would not distinguish `description` from
    `datasets[0].description`, so an implementation that dropped the root null would
    still pass.
    """
    listed = str(error).rsplit(": ", 1)[1]
    return {entry.strip() for entry in listed.split(",")}


def _model_types(annotation: object) -> list[type[BaseModel]]:
    """The model classes inside an annotation, through Optional, list and Union."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return [annotation]
    return [model for arg in get_args(annotation) for model in _model_types(arg)]


def _missing_properties(model: type[BaseModel], sample: object, where: str) -> list[str]:
    """Properties of `model`, and of every model below it, absent from `sample`."""
    if not isinstance(sample, dict):
        return []
    missing: list[str] = []
    for name, field in model.model_fields.items():
        key = field.alias or name
        if key not in sample:
            missing.append(f"{where}.{key}" if where else key)
            continue
        value = sample[key]
        items = value if isinstance(value, list) else [value]
        for nested in _model_types(field.annotation):
            for index, item in enumerate(items):
                at = f"{where}.{key}" if where else key
                if isinstance(value, list):
                    at = f"{at}[{index}]"
                missing += _missing_properties(nested, item, at)
    return missing


def _fully_populated_document() -> dict:
    """A document that sets every optional property, so each can be nulled in turn."""
    expression = {
        "sql": "amount",
        "dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}],
    }
    ai_context = {"instructions": "i", "synonyms": ["s"], "examples": ["e"]}
    extensions = [{"vendor_name": "COMMON", "data": "{}"}]
    return {
        "version": "0.2.0.dev0",
        "name": "m",
        "description": "d",
        "ai_context": dict(ai_context),
        "custom_extensions": list(extensions),
        "datasets": [
            {
                "name": "orders",
                "source": "db.s.orders",
                "description": "d",
                "primary_key": ["id"],
                "unique_keys": [["id"]],
                "ai_context": dict(ai_context),
                "custom_extensions": list(extensions),
                "fields": [
                    {
                        "name": "amount",
                        "expression": dict(expression),
                        "dimension": {"is_time": False},
                        "label": "l",
                        "description": "d",
                        "datatype": "Decimal",
                        "ai_context": dict(ai_context),
                        "custom_extensions": list(extensions),
                    }
                ],
                "metrics": [
                    {
                        "name": "total_amount",
                        "expression": dict(expression),
                        "description": "d",
                        "datatype": "Decimal",
                        "ai_context": dict(ai_context),
                        "custom_extensions": list(extensions),
                    }
                ],
            }
        ],
        "relationships": [
            {
                "name": "r",
                "from": "orders",
                "to": "orders",
                "from_columns": ["id"],
                "to_columns": ["id"],
                "ai_context": dict(ai_context),
                "custom_extensions": list(extensions),
            }
        ],
        "metrics": [
            {
                "name": "revenue",
                "expression": dict(expression),
                "description": "d",
                "datatype": "Decimal",
                "ai_context": dict(ai_context),
                "custom_extensions": list(extensions),
            }
        ],
    }


def _property_paths(node: object, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            where = f"{path}.{key}" if path else str(key)
            found.append(where)
            found += _property_paths(value, where)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found += _property_paths(item, f"{path}[{index}]")
    return found


def _set_null(document: dict, path: str) -> None:
    cursor: Any = document
    parts = path.replace("]", "").split(".")
    for part in parts[:-1]:
        if "[" in part:
            key, index = part.split("[")
            cursor = cursor[key][int(index)]
        else:
            cursor = cursor[part]
    last = parts[-1]
    if "[" in last:
        key, index = last.split("[")
        cursor[key][int(index)] = None
    else:
        cursor[last] = None


def test_the_sample_document_sets_every_declared_field() -> None:
    """Guard the test below, whose reach is only as wide as the hand-built sample.

    The walk in `models.py` is generic over keys, so a field added later is rejected
    without touching it. This sample is not: a new field is only exercised once it is
    added here, so fail loudly instead of silently covering less.

    Checked per model and position, not as a pool of names: `description` on a metric
    is a different property from `description` on a dataset, and a sample that set only
    one of them would otherwise look complete.
    """
    missing = _missing_properties(OssieSemanticModel, _fully_populated_document(), "")

    assert missing == [], "add these to _fully_populated_document()"


def test_every_property_in_a_document_rejects_null() -> None:
    """Null each property of a fully populated document in turn; all must be rejected."""
    paths = _property_paths(_fully_populated_document())
    assert len(paths) > 50, "the sample document should exercise every property"

    for path in paths:
        document = _fully_populated_document()
        _set_null(document, path)
        with pytest.raises(ValueError, match="set to null"):
            OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document))


def test_every_null_is_reported_not_just_the_first(document_data: dict) -> None:
    document_data["description"] = None
    document_data["datasets"][0]["description"] = None

    with pytest.raises(ValueError) as error:
        OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document_data))

    assert _reported_paths(error.value) == {"description", "datasets[0].description"}


def test_ai_context_may_be_a_plain_string(document_data: dict) -> None:
    """`AIContext` is a union, so the walk must tolerate the string branch."""
    document_data["ai_context"] = "free text"

    assert OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document_data)).ai_context == "free text"


@pytest.mark.parametrize("text", ["[]\n", "null\n", "42\n", ""])
def test_loaders_reject_a_document_that_is_not_a_mapping(text: str) -> None:
    with pytest.raises(ValidationError):
        OssieSemanticModel.from_ossie_yaml(text)


def test_a_null_inside_an_array_is_rejected_by_the_field_type(document_data: dict) -> None:
    """Array items are typed, so pydantic rejects a null there; the walk need not.

    Nothing is laundered: unlike an optional property, a null item is not a value the
    model would accept and then drop on serialization.
    """
    document_data["datasets"][0]["primary_key"] = [None]

    with pytest.raises(ValidationError):
        OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document_data))


def test_loaders_reject_a_cyclic_alias() -> None:
    """`yaml.safe_load` resolves a self-referencing alias into a container holding itself.

    Walking it unguarded raises RecursionError, which is worse than the
    ValidationError `model_validate` gives for the same input.
    """
    text = (
        "version: 0.2.0.dev0\n"
        "name: m\n"
        "datasets: &d\n"
        "- name: orders\n"
        "  source: db.s.orders\n"
        "  fields: *d\n"
    )

    with pytest.raises(ValueError, match="cyclic alias"):
        OssieSemanticModel.from_ossie_yaml(text)


def test_loaders_accept_an_alias_reused_without_a_cycle(document_data: dict) -> None:
    """Only containers on the current branch count, so a shared anchor still walks."""
    text = (
        "version: 0.2.0.dev0\n"
        "name: m\n"
        "datasets:\n"
        "- name: a\n"
        "  source: db.s.a\n"
        "  ai_context: &ctx {instructions: shared}\n"
        "  primary_key: &pk [id]\n"
        "  unique_keys: [*pk]\n"
        "- name: b\n"
        "  source: db.s.b\n"
        "  ai_context: *ctx\n"
    )

    document = OssieSemanticModel.from_ossie_yaml(text)

    second = document.datasets[1].ai_context
    assert isinstance(second, OssieAIContextObject)
    assert second.instructions == "shared"


def test_loaders_reject_a_cycle_inside_free_form_ai_context() -> None:
    """A null is legal among `ai_context` extras; a cycle is not.

    `extra="allow"` keeps the value as given, so a self-referential one loads and then
    fails in `to_ossie_json`. The extras are checked for cycles without their nulls
    being reported.
    """
    text = (
        "version: 0.2.0.dev0\n"
        "name: m\n"
        "datasets:\n"
        "- name: o\n"
        "  source: db.s.o\n"
        "  ai_context: {vendor: &v [*v]}\n"
    )

    with pytest.raises(ValueError, match="cyclic alias"):
        OssieSemanticModel.from_ossie_yaml(text)


def test_a_reused_alias_graph_does_not_expand_exponentially() -> None:
    """Each anchor doubles the expanded size, so touching every occurrence costs 2^n.

    The anchored subtree holds a null, which is the case that bounding the traversal
    alone does not cover: visiting each container once still produced one path per
    occurrence. With 64 anchors that is 2^64 paths, so completing at all is the
    assertion, and the single path names the anchor the author has to edit.
    """
    lines = [
        "version: 0.2.0.dev0",
        "name: m",
        "datasets:",
        "- name: o",
        "  source: db.s.o",
        "  x0: &a0 {label: null}",
    ]
    lines += [f"  x{i}: &a{i} [*a{i - 1}, *a{i - 1}]" for i in range(1, 65)]
    document = yaml.safe_load("\n".join(lines) + "\n")

    assert models._invalid_properties(document) == [("datasets[0].x0.label", models._NULL)]


def test_a_null_under_a_reused_anchor_is_reported_once() -> None:
    """An anchor is one property in the source, so one path locates it.

    Reporting each occurrence would be unbounded: an alias graph can hold 2^n copies
    of the anchored subtree. The path given is the first the walk reaches, which is
    where the anchor is defined.
    """
    text = (
        "version: 0.2.0.dev0\n"
        "name: m\n"
        "datasets:\n"
        "- name: a\n"
        "  source: db.s.a\n"
        "  fields: &f\n"
        "  - name: x\n"
        "    label: null\n"
        "    expression: {dialects: [{dialect: ANSI_SQL, expression: x}]}\n"
        "- name: b\n"
        "  source: db.s.b\n"
        "  fields: *f\n"
    )

    with pytest.raises(ValueError) as error:
        OssieSemanticModel.from_ossie_yaml(text)

    assert _reported_paths(error.value) == {"datasets[0].fields[0].label"}


def test_a_long_list_of_nulls_is_truncated_with_a_count() -> None:
    """A diagnostic a reader can use, rather than every path in a large document."""
    document_data = {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": [
            {"name": f"d{i}", "source": f"db.s.d{i}", "description": None} for i in range(30)
        ],
    }

    with pytest.raises(ValueError) as error:
        OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document_data))

    assert "and 10 more" in str(error.value)
    assert str(error.value).count("description") == models._MAX_REPORTED


@pytest.mark.parametrize(
    "text",
    [
        "  ai_context: {score: .nan}\n",
        "  ai_context: {score: .inf}\n",
        "  ai_context: {score: -.inf}\n",
        "  ai_context: {scores: [1, .nan]}\n",
    ],
)
def test_loaders_reject_a_non_finite_number_from_yaml(text: str) -> None:
    """`yaml.safe_load` reads `.nan` and `.inf`, which JSON cannot express.

    They reach an `ai_context` extra untyped, and `to_ossie_json` turns NaN into
    `null` -- a different value than was written.
    """
    document = "version: 0.2.0.dev0\nname: m\ndatasets:\n- name: o\n  source: db.s.o\n" + text

    with pytest.raises(ValueError, match="cannot round-trip"):
        OssieSemanticModel.from_ossie_yaml(document)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_loaders_reject_a_non_standard_json_constant(constant: str) -> None:
    """`json.loads` accepts these by default, though JSON itself does not define them."""
    text = (
        '{"version":"0.2.0.dev0","name":"m","datasets":[{"name":"o",'
        '"source":"db.s.o","ai_context":{"score":' + constant + "}}]}"
    )

    with pytest.raises(ValueError, match="cannot round-trip"):
        OssieSemanticModel.from_ossie_json(text)


def test_loaders_require_the_version_property() -> None:
    """The schema lists `version` as required, but the field carries a default.

    `model_validate` would fill it in, so a document without one would load and then
    serialize with a version it never declared.
    """
    text = "name: m\ndatasets:\n- name: o\n  source: db.s.o\n"

    with pytest.raises(ValueError, match="missing the required property: version"):
        OssieSemanticModel.from_ossie_yaml(text)


def test_direct_construction_still_defaults_the_version() -> None:
    """Only a document must declare it; building a model in Python need not."""
    document = OssieSemanticModel(
        name="m", datasets=[OssieDataset(name="o", source="db.s.o")]
    )

    assert document.version == "0.2.0.dev0"


def test_free_form_ai_context_content_is_passed_through() -> None:
    """The loaders do not audit the types inside `ai_context`, by design.

    Its object form sets `additionalProperties: true`, so a YAML set, a date or a
    non-string key loads and serializes as whatever it maps to -- the behaviour
    `model_validate` has today. Pinned so narrowing the contract is deliberate rather
    than accidental, and so widening it is a visible change.
    """
    text = (
        "version: 0.2.0.dev0\n"
        "name: m\n"
        "datasets:\n"
        "- name: o\n"
        "  source: db.s.o\n"
        "  ai_context:\n"
        "    tags: !!set {a: null}\n"
        "    when: 2026-01-01\n"
        "    numbered: {1: one}\n"
    )

    document = OssieSemanticModel.from_ossie_yaml(text)
    extra = document.datasets[0].ai_context

    assert isinstance(extra, OssieAIContextObject)
    assert json.loads(document.to_ossie_json())["datasets"][0]["ai_context"] == {
        "tags": ["a"],
        "when": "2026-01-01",
        "numbered": {"1": "one"},
    }


def test_the_null_diagnostic_reads_as_a_sentence(document_data: dict) -> None:
    """Assert the whole message, not a fragment of it.

    The reason and the template have to agree on who the subject is, and a substring
    match passes either way round, so only the full text covers that.
    """
    document_data["description"] = None

    with pytest.raises(ValueError) as error:
        OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document_data))

    assert str(error.value) == (
        "property set to null, which the schema does not allow; omit it instead: description"
    )


def test_the_non_finite_diagnostic_reads_as_a_sentence(document_data: dict) -> None:
    """The case the subject was missing from; see the test above."""
    document_data["datasets"][0]["ai_context"] = {"score": float("nan")}

    with pytest.raises(ValueError) as error:
        OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document_data))

    assert str(error.value) == (
        "property holds a number JSON cannot represent, so it cannot round-trip: "
        "datasets[0].ai_context.score"
    )

