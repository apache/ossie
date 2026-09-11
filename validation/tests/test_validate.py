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
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# validate.py exits at import time when its dependencies are missing, which
# would abort the whole pytest session during collection — skip instead.
pytest.importorskip("yaml")
pytest.importorskip("jsonschema")

_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate", _VALIDATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

validate_references = _VALIDATE.validate_references


@pytest.fixture
def core_schema() -> dict:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    return json.loads(schema_path.read_text())


def _document(datasets: list[dict], relationships: list[dict]) -> dict:
    return {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": datasets,
        "relationships": relationships,
    }


_CUSTOMERS = {
    "name": "customers",
    "source": "db.s.customers",
    "primary_key": ["id"],
    "unique_keys": [["email"]],
}

_ORDERS = {"name": "orders", "source": "db.s.orders"}


def test_accepts_a_single_root_model(core_schema: dict) -> None:
    document = _document([_ORDERS, _CUSTOMERS], [])

    assert _VALIDATE.validate_schema(document, core_schema) == []


@pytest.mark.parametrize("required_property", ["version", "name", "datasets"])
def test_requires_model_and_document_properties(core_schema: dict, required_property: str) -> None:
    document = _document([_ORDERS], [])
    del document[required_property]

    errors = _VALIDATE.validate_schema(document, core_schema)

    assert any(f"'{required_property}' is a required property" in error for error in errors)


@pytest.mark.parametrize("property_name", ["name", "datasets"])
def test_rejects_null_model_properties(core_schema: dict, property_name: str) -> None:
    document = _document([_ORDERS], [])
    document[property_name] = None

    errors = _VALIDATE.validate_schema(document, core_schema)

    assert any(f"[Schema] {property_name}:" in error for error in errors)


@pytest.mark.parametrize(
    "wrapped",
    [
        None,
        [],
        {"name": "one", "datasets": []},
        [{"name": "one", "datasets": []}],
        [{"name": "one", "datasets": []}, {"name": "two", "datasets": []}],
    ],
)
def test_rejects_legacy_or_object_wrappers(core_schema: dict, wrapped: object) -> None:
    document = {"version": "0.2.0.dev0", "semantic_model": wrapped}

    assert _VALIDATE.validate_schema(document, core_schema)

    # A wrapper must also be rejected when a valid root model is present.
    document.update(_document([_ORDERS], []))
    errors = _VALIDATE.validate_schema(document, core_schema)

    assert any(
        "Additional properties are not allowed" in error and "semantic_model" in error
        for error in errors
    )


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        42,
        {"version": "0.2.0.dev0"},
        {"version": "0.2.0.dev0", "semantic_model": [{"name": "old", "datasets": []}]},
    ],
)
def test_semantic_checks_skip_non_model_payloads(data: object) -> None:
    assert _VALIDATE.validate_unique_names(data) == []
    assert validate_references(data) == []
    assert _VALIDATE.validate_sql(data) == []


def test_unique_names_are_checked_in_the_root_model() -> None:
    errors = _VALIDATE.validate_unique_names(_document([_ORDERS, _ORDERS], []))

    assert errors == ["[Unique] Duplicate dataset name 'orders' in model 'm'"]


def test_sql_checks_traverse_root_fields_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def record_expression(expression: str, dialect: str, context: str) -> None:
        seen.append((expression, dialect, context))

    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", True)
    monkeypatch.setattr(_VALIDATE, "validate_sql_expression", record_expression)
    expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": "value"}]}
    dataset = {**_ORDERS, "fields": [{"name": "value", "expression": expression}]}
    document = _document([dataset], [])
    document["metrics"] = [{"name": "total", "expression": expression}]

    assert _VALIDATE.validate_sql(document) == []
    assert seen == [
        ("value", "ANSI_SQL", "Field 'orders.value' in model 'm' (ANSI_SQL)"),
        ("value", "ANSI_SQL", "Metric 'total' in model 'm' (ANSI_SQL)"),
    ]


def _relationship(to_columns: list[str], to: str = "customers") -> dict:
    return {
        "name": "orders_to_customers",
        "from": "orders",
        "to": to,
        "from_columns": ["customer_id"],
        "to_columns": to_columns,
    }


def test_warns_when_to_columns_does_not_cover_a_declared_key() -> None:
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["region"])])
    )

    assert errors == [
        "[Reference] Warning: Relationship 'orders_to_customers' in model 'm': "
        "to_columns ['region'] does not cover the primary key or a unique key of dataset 'customers'"
    ]


def test_accepts_to_columns_matching_the_primary_key() -> None:
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["id"])])
    )

    assert errors == []


def test_accepts_to_columns_matching_a_unique_key() -> None:
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["email"])])
    )

    assert errors == []


def test_accepts_to_columns_that_is_a_superset_of_a_key() -> None:
    # e.g. tenant-sharded joins carry extra columns on top of the key;
    # coverage still guarantees the many-to-one semantics.
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["tenant_id", "id"])])
    )

    assert errors == []


def test_accepts_composite_key_regardless_of_column_order() -> None:
    composite = {
        "name": "order_lines",
        "source": "db.s.order_lines",
        "primary_key": ["order_id", "line_number"],
    }
    rel = _relationship(to_columns=["line_number", "order_id"], to="order_lines")

    assert validate_references(_document([_ORDERS, composite], [rel])) == []


def test_skips_datasets_that_declare_no_keys() -> None:
    no_keys = {"name": "raw_table", "source": "db.s.raw_table"}
    rel = _relationship(to_columns=["anything"], to="raw_table")

    assert validate_references(_document([_ORDERS, no_keys], [rel])) == []


def test_still_reports_unknown_datasets() -> None:
    errors = validate_references(
        _document([_ORDERS], [_relationship(to_columns=["id"], to="nope")])
    )

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm' references unknown dataset 'nope'"
    ]


def test_tolerates_null_unique_keys() -> None:
    # `unique_keys:` present but empty parses to None; the check must not crash.
    dataset = {"name": "customers", "source": "db.s.customers",
               "primary_key": ["id"], "unique_keys": None}
    errors = validate_references(
        _document([_ORDERS, dataset], [_relationship(to_columns=["id"])])
    )

    assert errors == []


def test_skips_non_list_to_columns() -> None:
    # Schema validation reports the shape error; the semantic check must
    # neither crash nor emit a misleading character-set comparison.
    rel = _relationship(to_columns=["id"])
    rel["to_columns"] = "id"

    assert validate_references(_document([_ORDERS, _CUSTOMERS], [rel])) == []


def test_skips_malformed_flat_unique_keys() -> None:
    # unique_keys mistakenly written flat like primary_key: strings are not
    # keys, so with no well-formed key declared the check does not fire.
    dataset = {"name": "customers", "source": "db.s.customers", "unique_keys": ["email"]}
    errors = validate_references(
        _document([_ORDERS, dataset], [_relationship(to_columns=["email"])])
    )

    assert errors == []
