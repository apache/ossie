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

import contextlib
import copy
import json
import logging
import os
import re
import sys
import threading
import urllib.request
from datetime import UTC, date, datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

# validate.py exits at import time when its dependencies are missing, which
# would abort the whole pytest session during collection — skip instead.
yaml = pytest.importorskip("yaml")
pytest.importorskip("jsonschema")

_VALIDATE_PATH = Path(__file__).parents[1] / "validate.py"
_SPEC = spec_from_file_location("ossie_validate", _VALIDATE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_VALIDATE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_VALIDATE)

validate_references = _VALIDATE.validate_references
validate_key_declarations = _VALIDATE.validate_key_declarations
validate_relationship_column_arity = _VALIDATE.validate_relationship_column_arity


@pytest.fixture
def core_schema() -> dict:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    return json.loads(schema_path.read_text())


@pytest.mark.parametrize(
    ("content", "error_path"),
    [
        ("", "(root)"),
        ("[]\n", "(root)"),
        ("scalar\n", "(root)"),
        ("semantic_model: invalid\n", "(root)"),
    ],
)
def test_schema_invalid_documents_report_errors_without_traceback(
    content: str,
    error_path: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_path = tmp_path / "model.yaml"
    model_path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["validate.py", str(model_path)])

    with pytest.raises(SystemExit) as raised:
        _VALIDATE.main()

    output = capsys.readouterr().out
    assert raised.value.code == 1
    assert f"[Schema] {error_path}:" in output
    assert "Semantic checks skipped until schema errors are fixed." in output


def test_schema_errors_defer_semantic_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_path = tmp_path / "model.yaml"
    model_path.write_text(
        "version: 0.3.0.dev0\n"
        "name: sales\n"
        "unknown: true\n"
        "datasets:\n"
        "  - name: orders\n"
        "    source: db.orders\n"
        "  - name: orders\n"
        "    source: db.orders_archive\n"
        "relationships:\n"
        "  - name: orders_to_customers\n"
        "    from: orders\n"
        "    to: customers\n"
        "    from_columns: [customer_id]\n"
        "    to_columns: [id]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "argv", ["validate.py", str(model_path)])

    with pytest.raises(SystemExit) as raised:
        _VALIDATE.main()

    output = capsys.readouterr().out
    assert raised.value.code == 1
    assert "[Schema] (root):" in output
    assert "Semantic checks skipped until schema errors are fixed." in output
    assert "[Unique]" not in output
    assert "[Reference]" not in output


def _document(datasets: list[dict], relationships: list[dict]) -> dict:
    # Deep-copied so a test can edit the document it gets back without
    # changing the module-level fixtures below for every test after it.
    return {
        "version": "0.3.0.dev0",
        "name": "m",
        "datasets": copy.deepcopy(datasets),
        "relationships": copy.deepcopy(relationships),
    }


def _field(name: str, expression: str | None = None, dialect: str = "ANSI_SQL") -> dict:
    """Build a field defined by one expression; by default the same-named source column."""
    sql = name if expression is None else expression
    return {"name": name, "expression": {"dialects": [{"dialect": dialect, "expression": sql}]}}


_CUSTOMERS = {
    "name": "customers",
    "source": "db.s.customers",
    "primary_key": ["id"],
    "unique_keys": [["email"]],
    "fields": [_field("id"), _field("email"), _field("region"), _field("tenant_id")],
}

_ORDERS = {
    "name": "orders",
    "source": "db.s.orders",
    "fields": [_field("customer_id"), _field("product_id"), _field("variant_id")],
}


def test_accepts_a_single_root_model(core_schema: dict) -> None:
    document = _document([_ORDERS, _CUSTOMERS], [])

    assert _VALIDATE.validate_schema(document, core_schema) == []


@pytest.fixture
def offline(monkeypatch):
    def reject_request(*args, **kwargs):
        pytest.fail("Schema validation must not make HTTP requests")

    monkeypatch.setattr(urllib.request, "urlopen", reject_request)
    monkeypatch.setattr("jsonschema.validators.urlopen", reject_request, raising=False)


@pytest.mark.parametrize(
    "uri",
    [
        "https://github.com/apache/ossie/core-spec/ossie-schema.json",
        "https://raw.githubusercontent.com/apache/ossie/main/core-spec/ossie-schema.json",
    ],
)
@pytest.mark.parametrize(
    "fragment, document",
    [
        ("", _document([_ORDERS], [])),
        ("#/$defs/AIContext", {"instructions": "Use for sales analysis"}),
    ],
)
def test_core_schema_references_resolve_offline(offline, uri, fragment, document):
    assert _VALIDATE.validate_schema(document, {"$ref": uri + fragment}) == []


def test_unresolvable_reference_is_a_validation_error(offline):
    uri = "https://example.invalid/unknown-schema.json"

    errors = _VALIDATE.validate_schema({}, {"$ref": uri})

    assert errors == [f"[Schema] Cannot resolve schema reference: {uri}"]


def test_rejects_empty_root_datasets(core_schema: dict) -> None:
    errors = _VALIDATE.validate_schema(_document([], []), core_schema)

    assert errors == ["[Schema] datasets: [] should be non-empty"]


@pytest.mark.parametrize(
    "keys, expected",
    [
        ({"primary_key": ["id", "id"]}, "primary_key: ['id', 'id']"),
        ({"unique_keys": [["id", "id"]]}, "unique_keys -> 0: ['id', 'id']"),
    ],
    ids=["primary_key", "one key twice over a field"],
)
def test_rejects_a_key_that_names_a_field_twice(core_schema: dict, keys, expected) -> None:
    """A key is a set of fields: repeating one declares fewer columns than it looks like."""
    customers = {**_CUSTOMERS, **keys}

    errors = _VALIDATE.validate_schema(_document([_ORDERS, customers], []), core_schema)

    assert errors == [f"[Schema] datasets -> 1 -> {expected} has non-unique elements"]


def test_accepts_a_unique_key_definition_written_twice(core_schema: dict) -> None:
    """The list of definitions carries no `uniqueItems`, deliberately.

    A definition written twice states nothing untrue, and a schema error stops
    the semantic checks, so the author would lose every reference and SQL
    diagnostic over a redundancy. validate_key_declarations warns instead; this
    asserts that hand-off rather than assuming it.
    """
    customers = {**_CUSTOMERS, "unique_keys": [["email"], ["email"]]}
    document = _document([_ORDERS, customers], [])

    assert _VALIDATE.validate_schema(document, core_schema) == []

    errors = validate_key_declarations(document)
    assert errors == [
        "[Unique] Warning: Dataset 'customers' in model 'm': unique_keys ['email'] declares the same key as unique_keys ['email']"
    ]
    assert all(isinstance(error, _VALIDATE.ValidationWarning) for error in errors)


def test_accepts_a_relationship_column_named_twice(core_schema: dict) -> None:
    """from_columns and to_columns correspond positionally, not as sets.

    One column of the from dataset may be compared with two columns of the to
    dataset, so neither array is constrained the way a key is.
    """
    relationship = {
        "name": "orders_to_customers",
        "from": "orders",
        "to": "customers",
        "from_columns": ["customer_id", "customer_id"],
        "to_columns": ["id", "tenant_id"],
    }

    document = _document([_ORDERS, _CUSTOMERS], [relationship])

    assert _VALIDATE.validate_schema(document, core_schema) == []


def _keyed(**keys) -> dict:
    """Return the customers dataset with its key declarations replaced."""
    return {**_CUSTOMERS, **keys}


def _declaring(*names: str) -> dict:
    """Return a customers dataset declaring exactly the fields named, in that order."""
    return {**_CUSTOMERS, "fields": [_field(name) for name in names]}


@pytest.mark.parametrize(
    "keys, expected",
    [
        ({"primary_key": ["id", "ID"]}, "primary_key names field 'id' more than once"),
        ({"unique_keys": [["email", "EMAIL"]]}, "unique_keys ['email', 'EMAIL'] names field 'email' more than once"),
    ],
    ids=["primary_key", "unique key"],
)
def test_rejects_a_key_that_names_one_field_under_two_spellings(keys, expected) -> None:
    """Key field names resolve case-insensitively, so these claim a column they lack.

    The schema's `uniqueItems` compares the strings, so only this check sees it.
    """
    errors = validate_key_declarations(_document([_ORDERS, _keyed(**keys)], []))

    assert errors == [f"[Unique] Dataset 'customers' in model 'm': {expected}"]


@pytest.mark.parametrize(
    "declared, named",
    [
        (["ID"], "ID"),
        (["Id"], "Id"),
    ],
    ids=["upper", "mixed"],
)
def test_a_repeated_key_entry_is_reported_as_the_field_it_resolves_to(declared, named):
    """Either spelling in the key folds onto the one the dataset declares."""
    dataset = {**_declaring(*declared), "primary_key": ["id", "ID"], "unique_keys": None}

    errors = validate_key_declarations(_document([_ORDERS, dataset], []))

    assert errors == [f"[Unique] Dataset 'customers' in model 'm': primary_key names field '{named}' more than once"]


@pytest.mark.parametrize(
    "keys, expected",
    [
        ({"primary_key": ["id", "ID"]}, "primary_key names field 'id' more than once"),
        ({"primary_key": ["ID", "Id"]}, "primary_key names field 'id' more than once"),
        (
            {"primary_key": ["id"], "unique_keys": [["ID"], ["Id"]]},
            "unique_keys ['Id'] declares the same key as unique_keys ['ID']",
        ),
    ],
    ids=["both spellings", "neither spelling declared first", "a key for each"],
)
def test_a_dataset_declaring_a_name_twice_in_two_cases_has_one_field(keys, expected):
    """Treat identifiers differing only in case as one field.

    Regular identifiers are case insensitive (expression_language.md), so
    `id`, `ID` and `Id` are one field however the dataset spells them.

    validate_unique_names compares field names as written and so does not report
    the duplicate declaration, but that gap does not make these two fields: the
    first declared is the one everything resolves to, per validate_field_shadowing.
    """
    dataset = {**_declaring("id", "ID"), "primary_key": None, "unique_keys": None, **keys}

    errors = validate_key_declarations(_document([_ORDERS, dataset], []))

    assert [error.removeprefix("[Unique] ").removeprefix("Warning: ") for error in errors] == [
        f"Dataset 'customers' in model 'm': {expected}"
    ]


@pytest.mark.parametrize(
    "keys",
    [
        {"primary_key": ["ghost", "GHOST"]},
        {"unique_keys": [["ghost"], ["GHOST"]]},
    ],
    ids=["one key", "a key for each"],
)
def test_says_nothing_about_a_key_entry_that_names_no_declared_field(keys) -> None:
    """Ignore a key entry that names no declared field.

    An undeclared entry names no field, so calling it a repeat of another
    entry would name a field the dataset does not have.

    validate_references is what reports it, which the assertion below checks
    rather than assumes.
    """
    dataset = {**_declaring("id"), "primary_key": None, "unique_keys": None, **keys}
    document = _document([_ORDERS, dataset], [])

    assert validate_key_declarations(document) == []
    assert any("undeclared field 'ghost'" in error for error in validate_references(document))


def test_reports_a_repeated_entry_alongside_an_empty_one() -> None:
    """An empty entry is validate_references' to report, and is dropped here.

    Dropping it rather than skipping the declaration keeps the entries around it
    compared, so one malformed key does not hide the other problem in it.
    """
    dataset = {**_declaring("id"), "primary_key": ["id", "ID", ""], "unique_keys": None}
    document = _document([_ORDERS, dataset], [])

    assert validate_key_declarations(document) == [
        "[Unique] Dataset 'customers' in model 'm': primary_key names field 'id' more than once"
    ]
    assert any("undeclared field ''" in error for error in validate_references(document))


@pytest.mark.parametrize(
    "unique_keys, repeat, first",
    [
        ([["id", "email"], ["email", "id"]], "['email', 'id']", "['id', 'email']"),
        ([["email"], ["EMAIL"]], "['EMAIL']", "['email']"),
    ],
    ids=["reordered", "recased"],
)
def test_warns_when_two_unique_keys_hold_the_same_fields(unique_keys, repeat, first) -> None:
    """A key is a set, so neither order nor case makes a second declaration a new key."""
    errors = validate_key_declarations(_document([_ORDERS, _keyed(unique_keys=unique_keys)], []))

    assert errors == [
        f"[Unique] Warning: Dataset 'customers' in model 'm': unique_keys {repeat} declares the same key as unique_keys {first}"
    ]
    assert all(isinstance(error, _VALIDATE.ValidationWarning) for error in errors)


def test_warns_when_a_yaml_alias_declares_a_unique_key_twice() -> None:
    """An alias makes one list object the value of two definitions.

    Definitions are compared by position rather than by identity, so the second
    is reported even though it is the first one over again.
    """
    document = _document([_ORDERS, _keyed(unique_keys=yaml.safe_load("[&k [email], *k]"))], [])
    declared = document["datasets"][1]["unique_keys"]
    assert declared[0] is declared[1]  # the alias survives _document's deep copy

    assert validate_key_declarations(document) == [
        "[Unique] Warning: Dataset 'customers' in model 'm': unique_keys ['email'] declares the same key as unique_keys ['email']"
    ]


@pytest.mark.parametrize(
    "keys",
    [
        {"unique_keys": [["id"]]},
        {"primary_key": ["id", "email"], "unique_keys": [["email", "id"]]},
    ],
    ids=["single column", "composite reordered"],
)
def test_accepts_a_unique_key_that_restates_the_primary_key(keys) -> None:
    """Accept a unique key that restates the primary key.

    DDL commonly declares both, and examples/tpcds_semantic_model.yaml does so
    in five datasets, so this is not reported.
    """
    assert validate_key_declarations(_document([_ORDERS, _keyed(**keys)], [])) == []


@pytest.mark.parametrize(
    "unique_keys",
    [
        [["email"], ["region"]],
        [["email"], ["email", "region"]],
    ],
    ids=["disjoint", "one a superset of the other"],
)
def test_accepts_unique_keys_that_hold_different_fields(unique_keys) -> None:
    """Only set equality is a repeat; a key that merely contains another is its own."""
    errors = validate_key_declarations(_document([_ORDERS, _keyed(unique_keys=unique_keys)], []))

    assert errors == []


def test_semantic_model_definition_does_not_require_document_version(core_schema: dict) -> None:
    # The reusable contents definition remains independent of document metadata.
    embedded_schema = {
        "$ref": "#/$defs/SemanticModel",
        "$defs": core_schema["$defs"],
    }
    model = _document([_ORDERS, _CUSTOMERS], [])
    del model["version"]

    assert _VALIDATE.validate_schema(model, embedded_schema) == []


@pytest.mark.parametrize("unknown_property", ["dataset", "owner", "dialects", "vendors"])
def test_rejects_unknown_root_properties(core_schema: dict, unknown_property: str) -> None:
    document = _document([_ORDERS], [])
    document[unknown_property] = "unexpected"

    errors = _VALIDATE.validate_schema(document, core_schema)

    assert any("Additional properties are not allowed" in error and unknown_property in error for error in errors)


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
    document = {"version": "0.3.0.dev0", "semantic_model": wrapped}

    assert _VALIDATE.validate_schema(document, core_schema)

    # A wrapper must also be rejected when a valid root model is present.
    document.update(_document([_ORDERS], []))
    errors = _VALIDATE.validate_schema(document, core_schema)

    assert any("Additional properties are not allowed" in error and "semantic_model" in error for error in errors)


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        42,
        {"version": "0.3.0.dev0"},
        {"version": "0.3.0.dev0", "semantic_model": [{"name": "old", "datasets": []}]},
    ],
)
def test_semantic_checks_skip_non_model_payloads(data: object) -> None:
    assert _VALIDATE.validate_unique_names(data) == []
    assert validate_references(data) == []
    assert validate_relationship_column_arity(data) == []
    assert _VALIDATE.validate_sql(data) == []
    assert _VALIDATE.validate_metric_agreement(data) == []


@pytest.mark.parametrize(
    ("items", "expected"),
    [
        (["a", "a"], ["a"]),
        (["a", "a", "a"], ["a"]),
        (["a", "b", "a", "b", "a"], ["a", "b"]),
        (["a", "b", "b", "a"], ["a", "b"]),
        (["a", "b", "c"], []),
        ([], []),
    ],
    ids=[
        "twice",
        "three-times",
        "two-names-interleaved",
        "repeats-in-reverse-order",
        "all-unique",
        "empty",
    ],
)
def test_find_duplicates_reports_each_name_once(items: list[str], expected: list[str]) -> None:
    assert _VALIDATE.find_duplicates(items) == expected


def _expression(expr: str = "x") -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": expr}]}


def _with_duplicate_fields(name: str) -> dict:
    dataset = {
        "name": "orders",
        "source": "db.s.orders",
        "fields": [
            {"name": name, "expression": _expression()},
            {"name": name, "expression": _expression()},
            {"name": name, "expression": _expression()},
        ],
    }
    return _document([dataset], [])


def _with_duplicate_metrics(name: str) -> dict:
    document = _document([_ORDERS], [])
    document["metrics"] = [
        {"name": name, "expression": _expression("SUM(orders.amount)")},
        {"name": name, "expression": _expression("SUM(orders.amount)")},
        {"name": name, "expression": _expression("SUM(orders.amount)")},
    ]
    return document


def _with_duplicate_relationships(name: str) -> dict:
    rels = [dict(_relationship(to_columns=["id"]), name=name) for _ in range(3)]
    return _document([_ORDERS, _CUSTOMERS], rels)


@pytest.mark.parametrize(
    ("build", "expected"),
    [
        (
            lambda: _document([_ORDERS, _ORDERS, _ORDERS], []),
            "[Unique] Duplicate dataset name 'orders' in model 'm'",
        ),
        (
            lambda: _with_duplicate_fields("amount"),
            "[Unique] Duplicate field name 'amount' in dataset 'orders'",
        ),
        (
            lambda: _with_duplicate_metrics("revenue"),
            "[Unique] Duplicate metric name 'revenue' in model 'm'",
        ),
        (
            lambda: _with_duplicate_relationships("orders_to_customers"),
            "[Unique] Duplicate relationship name 'orders_to_customers' in model 'm'",
        ),
    ],
    ids=["dataset", "field", "metric", "relationship"],
)
def test_a_name_repeated_three_times_is_one_error(build, expected: str) -> None:
    """Three copies of a name are one problem, for every kind of name checked."""
    assert _VALIDATE.validate_unique_names(build()) == [expected]


def test_unique_names_are_checked_in_the_root_model() -> None:
    errors = _VALIDATE.validate_unique_names(_document([_ORDERS, _ORDERS], []))

    assert errors == ["[Unique] Duplicate dataset name 'orders' in model 'm'"]


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda doc: doc.update(name=""), "name"),
        (lambda doc: doc["datasets"][0].update(name=""), "datasets -> 0 -> name"),
        (lambda doc: doc["datasets"][0].update(source=""), "datasets -> 0 -> source"),
    ],
)
def test_schema_rejects_empty_identifiers(core_schema: dict, mutate, path: str) -> None:
    # Empty identifiers are invalid data. The schema (minLength: 1) rejects them
    # up front, so no downstream check has to special-case "" -- and a single
    # empty name is caught even though it never trips duplicate detection.
    document = _document([_ORDERS], [])
    mutate(document)

    assert _VALIDATE.validate_schema(document, core_schema) == [f"[Schema] {path}: '' should be non-empty"]


def test_schema_rejects_empty_relationship_endpoints(core_schema: dict) -> None:
    document = _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["id"])])
    document["relationships"][0]["to"] = ""

    assert _VALIDATE.validate_schema(document, core_schema) == ["[Schema] relationships -> 0 -> to: '' should be non-empty"]


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"warehouse": "ANALYTICS_WH", "some_unmapped_flag": True},
        {"text": '{"legacy": true}', "nested": {"tags": ["a", None], "n": 1.5}},
    ],
)
def test_nonportable_extension_data_accepts_objects(core_schema: dict, data: dict) -> None:
    document = _document([dict(_ORDERS)], [])
    document["nonportable_extensions"] = [{"vendor_name": "SNOWFLAKE", "data": data}]
    document["datasets"][0]["nonportable_extensions"] = [{"vendor_name": "DBT", "data": data}]

    assert _VALIDATE.validate_schema(document, core_schema) == []


_EXTENSION_LOCATIONS = {
    "model": lambda d: d,
    "dataset": lambda d: d["datasets"][0],
    "field": lambda d: d["datasets"][0]["fields"][0],
    "dataset_metric": lambda d: d["datasets"][0]["metrics"][0],
    "metric": lambda d: d["metrics"][0],
    "relationship": lambda d: d["relationships"][0],
}


@pytest.mark.parametrize("location", list(_EXTENSION_LOCATIONS))
@pytest.mark.parametrize("data", ['{"warehouse": "ANALYTICS_WH"}', ["warehouse"], 1, True, None])
def test_nonportable_extension_data_rejects_non_objects(core_schema: dict, location: str, data: object) -> None:
    document = _document(
        [dict(_ORDERS, metrics=[_metric("order_count", "COUNT(customer_id)")]), _CUSTOMERS],
        [_relationship(["id"])],
    )
    document["metrics"] = [_metric("customer_count", "COUNT(customers.id)")]
    _EXTENSION_LOCATIONS[location](document)["nonportable_extensions"] = [{"vendor_name": "SNOWFLAKE", "data": data}]

    errors = _VALIDATE.validate_schema(document, core_schema)

    assert len(errors) == 1
    assert "nonportable_extensions -> 0 -> data:" in errors[0]
    assert "is not of type 'object'" in errors[0]


def _extension_document() -> dict:
    document = _document(
        [dict(_ORDERS, metrics=[_metric("order_count", "COUNT(customer_id)")]), _CUSTOMERS],
        [_relationship(["id"])],
    )
    document["metrics"] = [_metric("customer_count", "COUNT(customers.id)")]
    return document


_EXTENSION_ERROR_PREFIX = {
    "model": "",
    "dataset": "datasets -> 0 -> ",
    "field": "datasets -> 0 -> fields -> 0 -> ",
    "dataset_metric": "datasets -> 0 -> metrics -> 0 -> ",
    "metric": "metrics -> 0 -> ",
    "relationship": "relationships -> 0 -> ",
}


def _blocking(errors: list) -> list:
    return [error for error in errors if not isinstance(error, _VALIDATE.ValidationWarning)]


@pytest.mark.parametrize("location", list(_EXTENSION_LOCATIONS))
def test_extension_data_rejects_dates_and_non_finite_floats(core_schema: dict, location: str) -> None:
    # JSON Schema types data as object and does not look inside. PyYAML has
    # already turned an unquoted date into a date, so validate_schema accepts
    # it. validate_document rejects it at schema severity.
    document = _extension_document()
    _EXTENSION_LOCATIONS[location](document)["nonportable_extensions"] = [
        {
            "vendor_name": "DB_VENDOR",
            "data": {
                "released": date(2024, 1, 1),
                "ratio": float("inf"),
                "nested": {"when": datetime(2024, 1, 1, tzinfo=UTC)},
                "missing": float("nan"),
            },
        }
    ]

    assert _VALIDATE.validate_schema(document, core_schema) == []
    prefix = f"[Schema] {_EXTENSION_ERROR_PREFIX[location]}nonportable_extensions -> 0 -> data"
    assert _blocking(_VALIDATE.validate_document(document, core_schema)) == [
        f"{prefix} -> released: date is not a JSON value",
        f"{prefix} -> ratio: non-finite float is not a JSON value",
        f"{prefix} -> nested -> when: timestamp is not a JSON value",
        f"{prefix} -> missing: non-finite float is not a JSON value",
    ]


def test_yaml_date_under_data_fails_before_it_can_be_dumped(core_schema: dict) -> None:
    # The case from the dev@ discussion: an unquoted date loads, the schema
    # object check does not see it, and json.dumps cannot write it back.
    document = _VALIDATE.load_yaml_named(
        """
version: 0.3.0.dev0
name: m
datasets:
  - name: orders
    source: db.s.orders
    nonportable_extensions:
      - vendor_name: DB_VENDOR
        data:
          released: 2024-01-01
          ratio: .inf
""",
        "model.yaml",
    )
    payload = document["datasets"][0]["nonportable_extensions"][0]["data"]
    with pytest.raises(TypeError, match="not JSON serializable"):
        json.dumps(payload)

    errors = _blocking(_VALIDATE.validate_document(document, core_schema))
    assert errors == [
        "[Schema] datasets -> 0 -> nonportable_extensions -> 0 -> data -> released: date is not a JSON value",
        "[Schema] datasets -> 0 -> nonportable_extensions -> 0 -> data -> ratio: non-finite float is not a JSON value",
    ]


def test_extension_data_accepts_yaml_scalars_that_retype_to_json(core_schema: dict) -> None:
    # Residual cost: these are valid JSON and are not what was written.
    # yes/NO/true/false/010 are not rejected. 1e3 stays a string under PyYAML.
    document = _VALIDATE.load_yaml_named(
        """
version: 0.3.0.dev0
name: m
datasets:
  - name: orders
    source: db.s.orders
    nonportable_extensions:
      - vendor_name: DB_VENDOR
        data:
          yes_flag: yes
          no_flag: NO
          truth: true
          falsehood: false
          octal: 010
          scientific: 1e3
          quoted_date: "2024-01-01"
""",
        "model.yaml",
    )
    payload = document["datasets"][0]["nonportable_extensions"][0]["data"]
    assert payload == {
        "yes_flag": True,
        "no_flag": False,
        "truth": True,
        "falsehood": False,
        "octal": 8,
        "scientific": "1e3",
        "quoted_date": "2024-01-01",
    }
    assert _blocking(_VALIDATE.validate_document(document, core_schema)) == []


def test_extension_data_schema_does_not_describe_keys(core_schema: dict) -> None:
    extension = core_schema["$defs"]["NonportableExtension"]
    data_schema = extension["properties"]["data"]
    assert data_schema["type"] == "object"
    assert "properties" not in data_schema
    assert "patternProperties" not in data_schema
    assert "required" not in data_schema
    assert "additionalProperties" not in data_schema
    assert "if" not in extension
    assert "dependentSchemas" not in extension
    assert set(extension["properties"]) == {"vendor_name", "data"}


def test_old_custom_extensions_name_is_rejected(core_schema: dict) -> None:
    document = _document([_ORDERS], [])
    document["custom_extensions"] = [{"vendor_name": "DB_VENDOR", "data": {"kept": True}}]

    errors = _VALIDATE.validate_schema(document, core_schema)

    assert len(errors) == 1
    assert "custom_extensions" in errors[0]
    assert "Additional properties are not allowed" in errors[0]


def test_portable_checks_ignore_extension_payload(core_schema: dict) -> None:
    # Core diagnostics must not change when a vendor payload copies core names.
    plain = _document([_ORDERS], [_relationship(["id"], to="missing")])
    carrying = copy.deepcopy(plain)
    carrying["nonportable_extensions"] = [
        {
            "vendor_name": "DB_VENDOR",
            "data": {
                "expression": "SUM(orders.amount)",
                "sql": "1",
                "name": "hijack",
                "from": "nowhere",
                "datasets": [{"name": "ghost"}],
            },
        }
    ]
    carrying["datasets"][0]["nonportable_extensions"] = [{"vendor_name": "DB_VENDOR", "data": {"primary_key": ["not_a_field"]}}]

    assert _VALIDATE.validate_document(carrying, core_schema) == _VALIDATE.validate_document(plain, core_schema)
    assert _blocking(_VALIDATE.validate_document(plain, core_schema))


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


def test_too_old_sqlglot_warns_and_skips_instead_of_crashing(core_schema: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    # sqlglot < 30.19.0 does not ship exp.Agg, which the AGG checks reference.
    # The validator must report that as a warning and skip the SQL checks, not
    # raise AttributeError mid-validation.
    monkeypatch.setattr(_VALIDATE, "SQLGLOT_TOO_OLD", True)
    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", False)
    document = _document([_ORDERS, _CUSTOMERS], [])

    errors = _VALIDATE.validate_document(document, core_schema)

    assert _blocking(errors) == []
    assert any(
        isinstance(error, _VALIDATE.ValidationWarning)
        and _VALIDATE.SQLGLOT_MIN_VERSION in error
        and "SQL validation requires" in error
        for error in errors
    )
    # The too-old case must not claim an installed sqlglot is "not installed".
    assert not any("not installed" in error for error in errors)


@pytest.mark.parametrize("script", ["validate.py", "test_validate.py"])
def test_script_header_declares_the_sqlglot_minimum(script: str) -> None:
    # `uv run` installs from the PEP 723 header, and the too-old warning tells
    # a user to upgrade to SQLGLOT_MIN_VERSION. The two must name one version.
    header = (_VALIDATE_PATH.parent / script).read_text(encoding="utf-8")
    declared = re.search(r'^#\s+"sqlglot>=([^"]+)",$', header, re.MULTILINE)

    assert declared is not None
    assert declared.group(1) == _VALIDATE.SQLGLOT_MIN_VERSION


def test_ossie_sql_2026_maps_to_the_ansi_default() -> None:
    # OSSIE_SQL_2026 is ANSI-SQL-compatible, so it resolves to the same sqlglot
    # default (None) as ANSI_SQL, and is parseable rather than a non-SQL dialect.
    assert _VALIDATE.SQLGLOT_DIALECTS["OSSIE_SQL_2026"] is _VALIDATE.SQLGLOT_DIALECTS["ANSI_SQL"]
    assert "OSSIE_SQL_2026" not in _VALIDATE.NON_SQL_DIALECTS


def test_holistics_aql_is_skipped_rather_than_parsed_as_sql() -> None:
    # AQL is relationship-aware and is not SQL, so sqlglot cannot parse it.
    # Skipping it is what lets a Holistics field carry its body at all, the way
    # MDX, MAQL, THOUGHTSPOT and DAX already do.
    assert "HOLISTICS_AQL" in _VALIDATE.NON_SQL_DIALECTS
    assert "HOLISTICS_AQL" not in _VALIDATE.SQLGLOT_DIALECTS


def test_pyiceberg_is_skipped_rather_than_parsed_as_sql() -> None:
    # PyIceberg predicate strings are SQL-like but are not SQL: a `.` reaches
    # into a nested field and dates are plain strings, so they are carried
    # without being handed to sqlglot.
    assert "PYICEBERG" in _VALIDATE.NON_SQL_DIALECTS
    assert "PYICEBERG" not in _VALIDATE.SQLGLOT_DIALECTS


def _registered_dialects() -> set[str]:
    """Return the tokens in the table of extensions/dialects.md."""
    registry = Path(__file__).parents[2] / "extensions" / "dialects.md"
    return {
        match.group(1)
        for line in registry.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^\|\s*`([^`]+)`\s*\|", line))
    }


def _registry_mismatch(kind: str, code: str, in_code: set[str], in_registry: set[str]) -> str:
    """Return the message for a pin that failed: which side each token is missing from."""
    registry = f"extensions/{kind}.md"
    parts = []
    if only_registry := sorted(in_registry - in_code):
        parts.append(f"in {registry} but not in {code}: {', '.join(only_registry)}")
    if only_code := sorted(in_code - in_registry):
        parts.append(f"in {code} but not in {registry}: {', '.join(only_code)}")
    return f"{code} and {registry} do not list the same {kind}: " + "; ".join(parts)


def test_registry_mismatch_names_each_side() -> None:
    assert _registry_mismatch("dialects", "code", {"A", "B"}, {"B", "C"}) == (
        "code and extensions/dialects.md do not list the same dialects: "
        "in extensions/dialects.md but not in code: C; in code but not in extensions/dialects.md: A"
    )
    assert _registry_mismatch("vendors", "code", {"a"}, {"a", "b"}) == (
        "code and extensions/vendors.md do not list the same vendors: in extensions/vendors.md but not in code: b"
    )


def test_the_dialect_tables_match_the_registry() -> None:
    # A dialect registered in extensions/dialects.md has to be listed here as
    # well, as the sqlglot reader that parses it or as one sqlglot cannot read,
    # or the validator warns about a token the registry says is fine. A dialect
    # listed here but not in the registry is a registration someone skipped.
    known = set(_VALIDATE.SQLGLOT_DIALECTS) | _VALIDATE.NON_SQL_DIALECTS
    registered = _registered_dialects()

    assert known == registered, _registry_mismatch(
        "dialects", "validate.py (SQLGLOT_DIALECTS or NON_SQL_DIALECTS)", known, registered
    )
    assert set(_VALIDATE.SQLGLOT_DIALECTS) & _VALIDATE.NON_SQL_DIALECTS == set()


def test_only_the_portable_dialects_use_the_default_reader() -> None:
    # A None reader is sqlglot's default, which only ANSI_SQL and the portable
    # language are meant to be parsed with.
    default_reader = {d for d, reader in _VALIDATE.SQLGLOT_DIALECTS.items() if reader is None}

    assert default_reader == {"ANSI_SQL", _VALIDATE.PORTABLE_DIALECT}


def test_every_sqlglot_reader_exists() -> None:
    # Separate from the registry checks so that those still run without
    # sqlglot. A misspelled reader passes them and then makes sqlglot raise
    # ValueError, which _parse_sql_expression does not catch.
    sqlglot = pytest.importorskip("sqlglot")

    for reader in _VALIDATE.SQLGLOT_DIALECTS.values():
        if reader is not None:
            sqlglot.Dialect.get_or_raise(reader)


def test_a_holistics_aql_expression_is_not_reported_as_invalid_sql() -> None:
    # `|` is AQL's pipe operator. Parsed as SQL it is a bitwise OR over two
    # names, which sqlglot accepts, so the skip is asserted through a body that
    # SQL cannot read at all.
    body = "count(orders.id) | where(orders.status == 'delivered')"
    expression = {"dialects": [{"dialect": "HOLISTICS_AQL", "expression": body}]}
    dataset = {**_ORDERS, "fields": [{"name": "delivered", "expression": expression}]}

    assert _VALIDATE.validate_sql(_document([dataset], [])) == []


def test_a_pyiceberg_expression_is_carried_without_a_warning() -> None:
    # sqlglot's default reader accepts most PyIceberg strings, and also the
    # function calls PyIceberg rejects, so parsing them as SQL would pass bodies
    # that are not valid PYICEBERG. A registered non-SQL dialect is carried
    # silently; an unregistered one would get the unchecked-dialect warning.
    body = "ratio IS NOT NAN AND region = 'EMEA'"
    expression = {"dialects": [{"dialect": "PYICEBERG", "expression": body}]}
    dataset = {**_ORDERS, "fields": [{"name": "is_emea", "expression": expression}]}

    assert _VALIDATE.validate_sql(_document([dataset], [])) == []


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_validates_a_valid_ossie_sql_2026_expression() -> None:
    error = _VALIDATE.validate_sql_expression("SUM(amount)", "OSSIE_SQL_2026", "ctx")

    assert error is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_flags_an_invalid_ossie_sql_2026_expression() -> None:
    error = _VALIDATE.validate_sql_expression("SUM(", "OSSIE_SQL_2026", "ctx")

    assert error is not None
    assert error.startswith("[SQL] ctx:")


_COMPLIANCE_EXPRESSIONS = Path(__file__).parents[2] / "compliance" / "expressions"
# Query-shaped fixture line. expression_language.md lists SELECT as unsupported
# in expressions; rejecting it is a separate change from these construct checks.
_INVALID_QUERY_EXPRESSION = "SELECT a FROM t"


def _compliance_expressions(name: str) -> list[str]:
    """One expression per line; blank lines and '#' comments are skipped."""
    text = (_COMPLIANCE_EXPRESSIONS / name).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize("expression", _compliance_expressions("valid.txt"))
def test_valid_compliance_expressions_are_accepted(expression: str) -> None:
    assert _VALIDATE.validate_sql_expression(expression, "OSSIE_SQL_2026", "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    [line for line in _compliance_expressions("invalid.txt") if line != _INVALID_QUERY_EXPRESSION],
)
def test_invalid_compliance_expressions_are_rejected(expression: str) -> None:
    error = _VALIDATE.validate_sql_expression(expression, "OSSIE_SQL_2026", "ctx")

    assert error is not None
    assert error.startswith("[SQL] ctx:")


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    ("expression", "detail"),
    [
        ("order_date + INTERVAL '7 days'", "INTERVAL is not part of OSSIE_SQL_2026"),
        ("DATE_ADD(d, INTERVAL 7 DAY)", "INTERVAL is not part of OSSIE_SQL_2026"),
        ("INTERVAL '7 days'", "INTERVAL is not part of OSSIE_SQL_2026"),
        ("x IN ()", "IN list must contain at least one value"),
        ("x NOT IN ()", "IN list must contain at least one value"),
        ("a b", "expected an expression, found an alias"),
        ("a IS", "expected an expression, found an alias"),
        ("a AS b", "expected an expression, found an alias"),
        ("SUM(DISTINCT)", "DISTINCT requires an argument"),
        ("COUNT(DISTINCT)", "DISTINCT requires an argument"),
        ("AVG(DISTINCT)", "DISTINCT requires an argument"),
    ],
    ids=[
        "interval-add",
        "date-add-interval",
        "bare-interval",
        "empty-in",
        "empty-not-in",
        "juxtaposition",
        "dangling-is",
        "explicit-alias",
        "sum-distinct",
        "count-distinct",
        "avg-distinct",
    ],
)
def test_rejects_portable_sql_constructs_sqlglot_still_parses(expression: str, detail: str) -> None:
    error = _VALIDATE.validate_sql_expression(expression, "OSSIE_SQL_2026", "ctx")

    assert error == f"[SQL] ctx: {detail}"


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    ("expression", "dialect"),
    [
        ("order_date + INTERVAL '7 days'", "POSTGRES"),
        ("DATE_ADD(d, INTERVAL 7 DAY)", "BIGQUERY"),
    ],
    ids=["postgres-interval", "bigquery-date-add-interval"],
)
def test_interval_stays_valid_in_vendor_dialects(expression: str, dialect: str) -> None:
    assert _VALIDATE.validate_sql_expression(expression, dialect, "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_validate_sql_rejects_portable_interval_on_sql() -> None:
    dataset = {
        **_ORDERS,
        "fields": [
            {
                "name": "week_later",
                "expression": {"sql": "order_date + INTERVAL '7 days'"},
            }
        ],
        "metrics": [{"name": "distinct_none", "expression": {"sql": "SUM(DISTINCT)"}}],
    }

    assert _VALIDATE.validate_sql(_document([dataset], [])) == [
        "[SQL] Field 'orders.week_later' in model 'm' (OSSIE_SQL_2026): INTERVAL is not part of OSSIE_SQL_2026",
        "[SQL] Metric 'orders.distinct_none' in model 'm' (OSSIE_SQL_2026): DISTINCT requires an argument",
    ]


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    ("expression", "name"),
    [
        # expression_language.md, Window Functions: ranking and offset functions.
        ("ROW_NUMBER()", "ROW_NUMBER"),
        ("RANK()", "RANK"),
        ("DENSE_RANK()", "DENSE_RANK"),
        ("NTILE(4)", "NTILE"),
        ("PERCENT_RANK()", "PERCENT_RANK"),
        ("CUME_DIST()", "CUME_DIST"),
        ("LAG(x, 1, 0)", "LAG"),
        ("LEAD(x)", "LEAD"),
        ("FIRST_VALUE(x)", "FIRST_VALUE"),
        ("LAST_VALUE(x)", "LAST_VALUE"),
        ("NTH_VALUE(x, 2)", "NTH_VALUE"),
        ("x - lag(x)", "LAG"),
        # Only the outer call is the window's function.
        ("RANK() OVER (ORDER BY LAG(x))", "LAG"),
        ("FIRST_VALUE(LAG(x)) OVER (ORDER BY d)", "LAG"),
    ],
)
def test_rejects_a_portable_window_function_without_over(expression: str, name: str) -> None:
    error = _VALIDATE.validate_sql_expression(expression, "OSSIE_SQL_2026", "ctx")

    assert error == f"[SQL] ctx: {name} is a window function and needs an OVER clause"


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    [
        "LAG(x, 1, 0) OVER (ORDER BY d)",
        "x - LEAD(x) OVER (PARTITION BY k ORDER BY d)",
        "FIRST_VALUE(x) IGNORE NULLS OVER (ORDER BY d)",
        "LAST_VALUE(x) RESPECT NULLS OVER (ORDER BY d)",
        "NTH_VALUE(x, 2) OVER (ORDER BY d ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        "CUME_DIST() OVER (ORDER BY x)",
        "PERCENT_RANK() OVER (PARTITION BY k ORDER BY x)",
        "DENSE_RANK() OVER (ORDER BY x DESC NULLS LAST)",
    ],
)
def test_accepts_a_portable_window_function_with_over(expression: str) -> None:
    assert _VALIDATE.validate_sql_expression(expression, "OSSIE_SQL_2026", "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_window_function_without_over_is_left_to_other_dialects() -> None:
    # The rule is the portable language's; a vendor dialect keeps its own.
    assert _VALIDATE.validate_sql_expression("RANK()", "SNOWFLAKE", "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_select_distinct_stays_deferred_without_bare_distinct_error() -> None:
    # SELECT DISTINCT stores an empty Distinct on Select.args["distinct"]; it is
    # a query modifier rather than a bare DISTINCT inside a function call.
    assert _VALIDATE.validate_sql_expression("SELECT DISTINCT a FROM t", "OSSIE_SQL_2026", "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    [
        "order_date + INTERVAL '7 days'",
        "x IN ()",
        "a b",
        "SUM(DISTINCT)",
    ],
    ids=["interval", "empty-in", "alias", "bare-distinct"],
)
def test_parse_tree_returns_none_for_rejected_portable_constructs(expression: str) -> None:
    assert _VALIDATE.parse_tree(expression, "OSSIE_SQL_2026", "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_parse_tree_keeps_interval_in_vendor_dialects() -> None:
    assert _VALIDATE.parse_tree("order_date + INTERVAL '7 days'", "POSTGRES", "ctx") is not None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_rejected_portable_constructs_do_not_emit_secondary_scope_or_drift_diagnostics() -> None:
    # A portable expression that validate_sql rejects must be treated as unparsed
    # by downstream checks so they do not add [Scope] or [Drift] noise.
    dataset = {
        **_ORDERS,
        "metrics": [
            {"name": "alias_metric", "expression": {"sql": "unknown_col b"}},
            {
                "name": "distinct_metric",
                "expression": {
                    "sql": "SUM(DISTINCT)",
                    "dialects": [{"dialect": "POSTGRES", "expression": "SUM(DISTINCT customer_id)"}],
                },
            },
        ],
    }
    document = _document([dataset], [])

    assert _VALIDATE.validate_metric_scoping(document) == []
    assert _VALIDATE.validate_metric_agreement(document) == []


def _parse_ossie_sql(expression: str):
    """Parse *expression* the way the validator parses an OSSIE_SQL_2026 body."""
    return _VALIDATE.sqlglot.parse_one(expression, dialect=_VALIDATE.SQLGLOT_DIALECTS[_VALIDATE.PORTABLE_DIALECT])


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_not_binds_looser_than_comparison() -> None:
    """`NOT a = b` is `NOT (a = b)`, the rank expression_language.md gives NOT.

    Listing NOT among the unary operators would instead read it as `(NOT a) = b`,
    which negates a before comparing it and is a different expression. Pinned so
    the precedence table and the parser behind it cannot drift apart.
    """
    exp = _VALIDATE.sqlglot.exp
    tree = _parse_ossie_sql("NOT a = b")

    assert isinstance(tree, exp.Not)
    assert isinstance(tree.this, exp.EQ)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_unary_minus_binds_tighter_than_comparison() -> None:
    """`- a = b` is `(-a) = b`: the operators NOT was listed beside do bind first."""
    exp = _VALIDATE.sqlglot.exp
    tree = _parse_ossie_sql("- a = b")

    assert isinstance(tree, exp.EQ)
    assert isinstance(tree.this, exp.Neg)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_not_binds_tighter_than_and() -> None:
    """`NOT a AND b` is `(NOT a) AND b`, placing NOT above AND rather than below."""
    exp = _VALIDATE.sqlglot.exp
    tree = _parse_ossie_sql("NOT a AND b")

    assert isinstance(tree, exp.And)
    assert isinstance(tree.this, exp.Not)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_unsupported_dialect_is_skipped() -> None:
    # A dialect this validator cannot parse is skipped, not parsed as ANSI.
    assert "DB_DIALECT" not in _VALIDATE.SQLGLOT_DIALECTS
    error = _VALIDATE.validate_sql_expression("this is not sql @ all", "DB_DIALECT", "ctx")

    assert error is None


def test_portable_expression_is_validated_as_ossie_sql_2026(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The portable `sql` is validated as OSSIE_SQL_2026, alongside `dialects`.
    seen = []

    def record_expression(expression: str, dialect: str, context: str) -> None:
        seen.append((expression, dialect, context))

    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", True)
    monkeypatch.setattr(_VALIDATE, "validate_sql_expression", record_expression)
    expression = {
        "sql": "amount",
        "dialects": [{"dialect": "ANSI_SQL", "expression": "amount"}],
    }
    dataset = {**_ORDERS, "fields": [{"name": "value", "expression": expression}]}
    document = _document([dataset], [])

    assert _VALIDATE.validate_sql(document) == []
    assert seen == [
        ("amount", "OSSIE_SQL_2026", "Field 'orders.value' in model 'm' (OSSIE_SQL_2026)"),
        ("amount", "ANSI_SQL", "Field 'orders.value' in model 'm' (ANSI_SQL)"),
    ]


def test_schema_accepts_portable_only_expression(core_schema: dict) -> None:
    # The portable `sql` may stand alone, with no `dialects` (schema anyOf).
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": {"sql": "amount"}}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == []


def test_schema_rejects_expression_with_neither_form(core_schema: dict) -> None:
    # An expression object carrying neither `sql` nor `dialects` is invalid.
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": {}}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == [
        "[Schema] datasets -> 0 -> fields -> 0 -> expression: {} is not valid under any of the given schemas"
    ]


def test_schema_rejects_empty_dialects_without_portable_form(core_schema: dict) -> None:
    # An explicitly empty `dialects` list still fails min_length.
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": {"dialects": []}}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == [
        "[Schema] datasets -> 0 -> fields -> 0 -> expression -> dialects: [] should be non-empty"
    ]


def test_schema_rejects_empty_portable_sql(core_schema: dict) -> None:
    # `sql` requires minLength 1, so an empty portable form fails the schema
    # rather than slipping through to the SQL validator.
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": {"sql": ""}}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == [
        "[Schema] datasets -> 0 -> fields -> 0 -> expression -> sql: '' should be non-empty"
    ]


def test_schema_rejects_empty_dialect(core_schema: dict) -> None:
    # `dialect` requires minLength 1, so an empty dialect fails the schema rather
    # than producing an "is not supported by this validator" warning.
    expression = {"dialects": [{"dialect": "", "expression": "x"}]}
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": expression}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == [
        "[Schema] datasets -> 0 -> fields -> 0 -> expression -> dialects -> 0 -> dialect: '' should be non-empty"
    ]


def test_schema_rejects_empty_dialect_expression(core_schema: dict) -> None:
    # The per-dialect `expression` requires minLength 1, like `sql`.
    expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": ""}]}
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": expression}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == [
        "[Schema] datasets -> 0 -> fields -> 0 -> expression -> dialects -> 0 -> expression: '' should be non-empty"
    ]


def test_schema_rejects_empty_vendor_name(core_schema: dict) -> None:
    # `vendor_name` requires minLength 1.
    dataset = {**_ORDERS, "nonportable_extensions": [{"vendor_name": "", "data": {}}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == [
        "[Schema] datasets -> 0 -> nonportable_extensions -> 0 -> vendor_name: '' should be non-empty"
    ]


def test_unsupported_dialect_emits_one_deduped_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # A dialect this validator cannot parse is nonfatal, but one warning per dialect
    # reports that its expressions went unchecked. The field and metric below share
    # the dialect, so they collapse into a single warning carrying the count.
    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", True)
    expression = {"dialects": [{"dialect": "ACME_SQL", "expression": "x"}]}
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": expression}]}
    document = _document([dataset], [])
    document["metrics"] = [{"name": "total", "expression": expression}]

    result = _VALIDATE.validate_sql(document)

    assert result == ["[SQL] Warning: dialect 'ACME_SQL' is not supported by this validator; 2 expressions not syntax-checked"]
    assert all(isinstance(item, _VALIDATE.ValidationWarning) for item in result)


def test_distinct_unsupported_dialects_each_warn_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two different unsupported dialects produce two warnings, sorted by name.
    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", True)
    fields = [
        {"name": "a", "expression": {"dialects": [{"dialect": "ZETA_SQL", "expression": "x"}]}},
        {"name": "b", "expression": {"dialects": [{"dialect": "ACME_SQL", "expression": "y"}]}},
    ]
    dataset = {**_ORDERS, "fields": fields}

    assert _VALIDATE.validate_sql(_document([dataset], [])) == [
        "[SQL] Warning: dialect 'ACME_SQL' is not supported by this validator; 1 expression not syntax-checked",
        "[SQL] Warning: dialect 'ZETA_SQL' is not supported by this validator; 1 expression not syntax-checked",
    ]


def test_known_unparseable_dialect_is_not_flagged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # MDX is a known non-SQL dialect: it is skipped silently, not warned as unsupported.
    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", True)
    expression = {"dialects": [{"dialect": "MDX", "expression": "[Measures].[Sales]"}]}
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": expression}]}

    assert _VALIDATE.validate_sql(_document([dataset], [])) == []


def test_unsupported_dialect_warning_suggests_a_close_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A near-miss of a known dialect gets a "did you mean" hint.
    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", True)
    expression = {"dialects": [{"dialect": "SNOWFLKE", "expression": "x"}]}
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": expression}]}

    assert _VALIDATE.validate_sql(_document([dataset], [])) == [
        "[SQL] Warning: dialect 'SNOWFLKE' is not supported by this validator; "
        "1 expression not syntax-checked. Did you mean 'SNOWFLAKE'?"
    ]


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    [
        "amount; DROP TABLE customers",
        "SUM(amount); SELECT 1",
        "SELECT amount; DROP TABLE customers",
    ],
    ids=["column-then-statement", "aggregate-then-select", "select-then-statement"],
)
def test_rejects_an_expression_holding_more_than_one_statement(expression: str) -> None:
    """A field or metric holds one expression; `parse_one` returns a Block for a list."""
    error = _VALIDATE.validate_sql_expression(expression, "ANSI_SQL", "ctx")

    assert error == "[SQL] ctx: expected a single expression but found 2 statements"


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    ("expression", "kind"),
    [
        ("DROP TABLE t", "DROP"),
        ("DELETE FROM orders", "DELETE"),
        ("GRANT SELECT ON orders TO public", "GRANT"),
        ("REVOKE SELECT ON t FROM u", "REVOKE"),
        ("ANALYZE t", "ANALYZE"),
        ("INSERT INTO orders VALUES (1)", "INSERT"),
        ("UPDATE orders SET x = 1", "UPDATE"),
        ("CREATE TABLE t (id INT)", "CREATE"),
        ("TRUNCATE TABLE orders", "TRUNCATE TABLE"),
        ("ALTER TABLE orders ADD COLUMN x INT", "ALTER"),
        ("BEGIN TRANSACTION", "TRANSACTION"),
        ("COMMIT WORK", "COMMIT"),
        ("SET x = 1", "SET"),
        ("USE db", "USE"),
        ("SHOW TABLES", "COMMAND"),
        ("CALL p()", "COMMAND"),
        ("DESCRIBE t", "DESCRIBE"),
    ],
    ids=[
        "drop",
        "delete",
        "grant",
        "revoke",
        "analyze",
        "insert",
        "update",
        "create",
        "truncate",
        "alter",
        "begin-transaction",
        "commit-work",
        "set",
        "use",
        "show-tables",
        "call",
        "describe",
    ],
)
def test_rejects_a_sql_statement_used_as_an_expression(expression: str, kind: str) -> None:
    """A single DDL/DML/session statement is not a field or metric expression."""
    error = _VALIDATE.validate_sql_expression(expression, "ANSI_SQL", "ctx")

    assert error == f"[SQL] ctx: expected an expression, found a {kind} statement"


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_rejects_a_sql_statement_in_ossie_sql_2026() -> None:
    error = _VALIDATE.validate_sql_expression("DROP TABLE t", "OSSIE_SQL_2026", "ctx")

    assert error == "[SQL] ctx: expected an expression, found a DROP statement"


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    ("expression", "dialect", "kind"),
    [
        ("SHOW TABLES", "SNOWFLAKE", "SHOW"),
        ("UNDROP TABLE t", "SNOWFLAKE", "UNDROP"),
        ("PUT file://x @stage", "SNOWFLAKE", "PUT"),
        ("GET @stage 'file://x'", "SNOWFLAKE", "GET"),
        ("LIST @stage", "SNOWFLAKE", "LIST STAGE"),
        ("REFRESH TABLE t", "DATABRICKS", "REFRESH"),
        ("UNCACHE TABLE t", "DATABRICKS", "UNCACHE"),
        ("LOAD DATA INPATH 'x' INTO TABLE t", "DATABRICKS", "LOAD DATA"),
        ("ADD JAR /tmp/x.jar", "DATABRICKS", "COMMAND"),
        ("CACHE TABLE t", "ANSI_SQL", "CACHE"),
        ("KILL 1", "ANSI_SQL", "KILL"),
        ("DECLARE x INT64", "BIGQUERY", "DECLARE"),
        ("EXPORT DATA OPTIONS(uri='gs://b/p') AS SELECT 1", "BIGQUERY", "EXPORT"),
        ("BEGIN TRANSACTION", "BIGQUERY", "TRANSACTION"),
        ("BEGIN TRANSACTION;", "BIGQUERY", "TRANSACTION"),
    ],
    ids=[
        "snowflake-show",
        "snowflake-undrop",
        "snowflake-put",
        "snowflake-get",
        "snowflake-list",
        "databricks-refresh",
        "databricks-uncache",
        "databricks-load-data",
        "databricks-add-jar",
        "ansi-cache",
        "ansi-kill",
        "bigquery-declare",
        "bigquery-export",
        "bigquery-begin-transaction",
        "bigquery-begin-transaction-semicolon",
    ],
)
def test_rejects_a_dialect_sql_statement_used_as_an_expression(expression: str, dialect: str, kind: str) -> None:
    """Dialect-specific statement classes are not field or metric expressions."""
    error = _VALIDATE.validate_sql_expression(expression, dialect, "ctx")

    assert error == f"[SQL] ctx: expected an expression, found a {kind} statement"


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    [
        "SELECT 1",
        "SELECT amount FROM orders",
        "(SELECT amount FROM orders)",
        "SUM(amount)",
        "amount",
        "commit",
        "begin",
        "rollback",
        "show",
        "comment",
        "fetch",
        "set",
        '"commit"',
        "comment IS NOT NULL",
        "show = 1",
        "comment = 'a'",
        "set * 2",
        "drop * 2",
        "fetch * 2",
        "grant IS NULL",
        "create IS NULL",
        "cache.x",
    ],
    ids=[
        "select-literal",
        "select-query",
        "scalar-subquery",
        "aggregate",
        "column",
        "column-commit",
        "column-begin",
        "column-rollback",
        "column-show",
        "column-comment",
        "column-fetch",
        "column-set",
        "quoted-commit",
        "comment-is-not-null",
        "show-eq",
        "comment-eq",
        "set-times",
        "drop-times",
        "fetch-times",
        "grant-is-null",
        "create-is-null",
        "cache-dot",
    ],
)
def test_still_accepts_queries_and_value_expressions(expression: str) -> None:
    """Top-level SELECT is out of this change; value expressions stay valid.

    Bare commit/begin/rollback/show/comment parse as statements in sqlglot;
    they are still legal column names. Expressions that start with those
    names wrap to a bare SELECT projection. Keyword columns whose wrap
    cannot parse (drop * 2) are retried with the first token quoted.
    """
    assert _VALIDATE.validate_sql_expression(expression, "ANSI_SQL", "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    ("expression", "dialect"),
    [
        ("begin > 0", "BIGQUERY"),
        ("desc.x", "BIGQUERY"),
        ("undrop * 2", "SNOWFLAKE"),
    ],
    ids=["begin-gt-bigquery", "desc-dot-bigquery", "undrop-times-snowflake"],
)
def test_accepts_keyword_column_expressions_in_dialect(expression: str, dialect: str) -> None:
    assert _VALIDATE.validate_sql_expression(expression, dialect, "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    ["DROP TABLE", "DELETE FROM"],
    ids=["drop-missing-name", "delete-missing-name"],
)
def test_rejects_malformed_ddl_or_dml_used_as_an_expression(expression: str) -> None:
    """Incomplete DROP/DELETE still fail the wrap parse."""
    error = _VALIDATE.validate_sql_expression(expression, "ANSI_SQL", "ctx")

    assert error is not None
    assert error.startswith("[SQL] ctx:")


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    [
        "DELETE FROM orders GROUP BY x",
        "DELETE FROM orders FOR UPDATE",
        "MERGE FROM orders",
        "DELETE FROM orders UNION SELECT 1",
        "TRUNCATE TABLE",
    ],
    ids=[
        "delete-group-by",
        "delete-for-update",
        "merge-from",
        "delete-union",
        "truncate-missing-name",
    ],
)
def test_rejects_a_statement_rescued_by_select_wrap(expression: str) -> None:
    """A wrap that parses is kept only as a bare one-projection SELECT."""
    error = _VALIDATE.validate_sql_expression(expression, "ANSI_SQL", "ctx")

    assert error == "[SQL] ctx: expected an expression, found a statement"


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_validate_sql_rejects_a_delete_used_as_a_field_expression() -> None:
    expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": "DELETE FROM orders"}]}
    dataset = {
        **_ORDERS,
        "fields": [{"name": "evil", "expression": expression}],
    }

    assert _VALIDATE.validate_sql(_document([dataset], [])) == [
        "[SQL] Field 'orders.evil' in model 'm' (ANSI_SQL): expected an expression, found a DELETE statement"
    ]


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    [
        "amount",
        "amount;",
        "amount;;",
        "SUM(amount)",
        "a /* ; */ + b",
        "'a;b'",
        "CONCAT(a, ';', b)",
    ],
    ids=[
        "column",
        "trailing-semicolon",
        "repeated-trailing-semicolons",
        "aggregate",
        "in-comment",
        "in-literal",
        "in-argument",
    ],
)
def test_accepts_a_single_expression_containing_a_semicolon(expression: str) -> None:
    """A semicolon in a literal or comment does not make a statement list."""
    assert _VALIDATE.validate_sql_expression(expression, "ANSI_SQL", "ctx") is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "expression",
    ["", "   ", "\n", ";", ";;", "-- comment only", "/* comment only */"],
    ids=[
        "empty",
        "whitespace",
        "newline",
        "semicolon",
        "semicolons",
        "line-comment",
        "block-comment",
    ],
)
def test_rejects_an_expression_holding_no_statement(expression: str) -> None:
    """Nothing to validate is an error, not a pass through `SELECT {expr}`."""
    error = _VALIDATE.validate_sql_expression(expression, "ANSI_SQL", "ctx")

    assert error == "[SQL] ctx: expression is empty"


def _relationship(to_columns: list[str], to: str = "customers") -> dict:
    return {
        "name": "orders_to_customers",
        "from": "orders",
        "to": to,
        "from_columns": ["customer_id"],
        "to_columns": to_columns,
    }


def test_warns_when_to_columns_does_not_cover_a_declared_key() -> None:
    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["region"])]))

    assert errors == [
        "[Reference] Warning: Relationship 'orders_to_customers' in model 'm': "
        "to_columns ['region'] does not cover the primary key or a unique key of dataset 'customers'"
    ]


def test_accepts_to_columns_matching_the_primary_key() -> None:
    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["id"])]))

    assert errors == []


def test_accepts_to_columns_matching_a_unique_key() -> None:
    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["email"])]))

    assert errors == []


def test_accepts_to_columns_that_is_a_superset_of_a_key() -> None:
    # e.g. tenant-sharded joins carry extra columns on top of the key;
    # coverage still guarantees the many-to-one semantics.
    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["tenant_id", "id"])]))

    assert errors == []


def test_accepts_composite_key_regardless_of_column_order() -> None:
    composite = {
        "name": "order_lines",
        "source": "db.s.order_lines",
        "primary_key": ["order_id", "line_number"],
        "fields": [_field("order_id"), _field("line_number")],
    }
    rel = _relationship(to_columns=["line_number", "order_id"], to="order_lines")

    assert validate_references(_document([_ORDERS, composite], [rel])) == []


def test_skips_datasets_that_declare_no_keys() -> None:
    no_keys = {
        "name": "raw_table",
        "source": "db.s.raw_table",
        "fields": [_field("anything")],
    }
    rel = _relationship(to_columns=["anything"], to="raw_table")

    assert validate_references(_document([_ORDERS, no_keys], [rel])) == []


def test_still_reports_unknown_datasets() -> None:
    errors = validate_references(_document([_ORDERS], [_relationship(to_columns=["id"], to="nope")]))

    assert errors == ["[Reference] Relationship 'orders_to_customers' in model 'm' references unknown dataset 'nope'"]


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_deeply_nested_sql_reports_a_diagnostic_instead_of_crashing() -> None:
    # Pathologically nested SQL exhausts sqlglot's recursion limit; the
    # validator must turn that into a diagnostic rather than propagating
    # RecursionError and aborting the run.
    expression = "(" * 5000 + "1" + ")" * 5000
    result = _VALIDATE.validate_sql_expression(expression, "ANSI_SQL", "ctx")

    assert result == "[SQL] ctx: expression is too deeply nested to parse"


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_non_string_expression_surfaces_instead_of_being_reported_valid() -> None:
    # Only RecursionError is caught, so a genuine bug -- a non-string expression
    # reaching the parser -- raises loudly rather than being masked as valid SQL
    # by the SELECT-wrapped retry.
    with pytest.raises(TypeError):
        _VALIDATE.validate_sql_expression(123, "ANSI_SQL", "ctx")


def test_tolerates_null_unique_keys() -> None:
    # `unique_keys:` present but empty parses to None; the check must not crash.
    dataset = {
        "name": "customers",
        "source": "db.s.customers",
        "primary_key": ["id"],
        "unique_keys": None,
        "fields": [_field("id")],
    }
    errors = validate_references(_document([_ORDERS, dataset], [_relationship(to_columns=["id"])]))

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
    dataset = {
        "name": "customers",
        "source": "db.s.customers",
        "unique_keys": ["email"],
        "fields": [_field("email")],
    }
    errors = validate_references(_document([_ORDERS, dataset], [_relationship(to_columns=["email"])]))

    assert errors == []


def test_rejects_from_columns_that_are_not_fields() -> None:
    rel = _relationship(to_columns=["id"])
    rel["from_columns"] = ["GHOST_COLUMN"]

    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [rel]))

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm': from_columns "
        "references undeclared field 'GHOST_COLUMN'. from_columns must be field names "
        "of the from dataset 'orders'."
    ]


def test_rejects_to_columns_that_are_not_fields() -> None:
    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["GHOST_TO"])]))

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm': to_columns "
        "references undeclared field 'GHOST_TO'. to_columns must be field names "
        "of the to dataset 'customers'."
    ]


def test_rejects_multiple_undeclared_from_columns() -> None:
    rel = _relationship(to_columns=["id"])
    rel["from_columns"] = ["missing_a", "missing_b"]

    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [rel]))

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm': from_columns "
        "references undeclared fields 'missing_a', 'missing_b'. from_columns must be "
        "field names of the from dataset 'orders'."
    ]


def test_accepts_relationship_columns_that_differ_only_in_case() -> None:
    rel = _relationship(to_columns=["ID"])
    rel["from_columns"] = ["Customer_ID"]

    assert validate_references(_document([_ORDERS, _CUSTOMERS], [rel])) == []


def test_rejects_primary_key_that_is_not_a_field() -> None:
    dataset = {
        "name": "customers",
        "source": "db.s.customers",
        "primary_key": ["GHOST_PK"],
        "fields": [_field("id")],
    }

    errors = validate_references(_document([dataset], []))

    assert errors == [
        "[Reference] Dataset 'customers' in model 'm': primary_key references "
        "undeclared field 'GHOST_PK'. primary_key must be field names of dataset "
        "'customers'."
    ]


def test_rejects_unique_keys_that_are_not_fields() -> None:
    dataset = {
        "name": "customers",
        "source": "db.s.customers",
        "unique_keys": [["GHOST_UK"]],
        "fields": [_field("id")],
    }

    errors = validate_references(_document([dataset], []))

    assert errors == [
        "[Reference] Dataset 'customers' in model 'm': unique_keys references "
        "undeclared field 'GHOST_UK'. unique_keys must be field names of dataset "
        "'customers'."
    ]


def test_rejects_empty_from_column_name() -> None:
    rel = _relationship(to_columns=["id"])
    rel["from_columns"] = [""]

    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [rel]))

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm': from_columns "
        "references undeclared field ''. from_columns must be field names "
        "of the from dataset 'orders'."
    ]


def test_rejects_empty_to_column_name() -> None:
    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=[""])]))

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm': to_columns "
        "references undeclared field ''. to_columns must be field names "
        "of the to dataset 'customers'."
    ]


def test_rejects_empty_primary_key_name() -> None:
    dataset = {
        "name": "customers",
        "source": "db.s.customers",
        "primary_key": [""],
        "fields": [_field("id")],
    }

    errors = validate_references(_document([dataset], []))

    assert errors == [
        "[Reference] Dataset 'customers' in model 'm': primary_key references "
        "undeclared field ''. primary_key must be field names of dataset "
        "'customers'."
    ]


def test_rejects_empty_unique_key_name() -> None:
    dataset = {
        "name": "customers",
        "source": "db.s.customers",
        "unique_keys": [[""]],
        "fields": [_field("id")],
    }

    errors = validate_references(_document([dataset], []))

    assert errors == [
        "[Reference] Dataset 'customers' in model 'm': unique_keys references "
        "undeclared field ''. unique_keys must be field names of dataset "
        "'customers'."
    ]


def test_rejects_empty_name_mixed_with_a_declared_from_column() -> None:
    rel = _relationship(to_columns=["id"])
    rel["from_columns"] = ["customer_id", ""]

    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [rel]))

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm': from_columns "
        "references undeclared field ''. from_columns must be field names "
        "of the from dataset 'orders'."
    ]


def test_rejects_empty_name_mixed_with_a_declared_to_column() -> None:
    errors = validate_references(_document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["id", ""])]))

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm': to_columns "
        "references undeclared field ''. to_columns must be field names "
        "of the to dataset 'customers'."
    ]


def test_ghost_from_columns_fails_document_validation(core_schema: dict) -> None:
    rel = _relationship(to_columns=["id"])
    rel["from_columns"] = ["GHOST_COLUMN"]

    errors = _VALIDATE.validate_document(_document([_ORDERS, _CUSTOMERS], [rel]), core_schema)

    ghost_errors = [error for error in errors if "GHOST_COLUMN" in error and "from_columns" in error]
    assert ghost_errors
    assert all(not isinstance(error, _VALIDATE.ValidationWarning) for error in ghost_errors)


@pytest.fixture
def run_validator(tmp_path, monkeypatch, capsys):
    def run(document, schema_path=None):
        model_path = tmp_path / "model.json"
        model_path.write_text(json.dumps(document))
        args = [str(_VALIDATE_PATH), str(model_path)]
        if schema_path is not None:
            args.extend(["--schema", str(schema_path)])
        monkeypatch.setattr(_VALIDATE.sys, "argv", args)
        with pytest.raises(SystemExit) as caught:
            _VALIDATE.main()
        return caught.value.code, capsys.readouterr().out

    return run


@pytest.mark.parametrize("version, expected_exit", [("0.3.0.dev0", 0), ("0.1.0", 1)])
def test_ontology_cli_validates_embedded_documents_offline(run_validator, offline, version, expected_exit):
    model = _document([_ORDERS], [])
    model["version"] = version
    document = {
        "version": "0.3.0.dev0",
        "name": "sales",
        "ai_context": {"instructions": "Use for sales analysis"},
        "ontology": [{"concept": "Order", "type": "EntityType"}],
        "ontology_mappings": [{"semantic_model": model, "concept_mappings": []}],
    }

    exit_code, output = run_validator(document, Path(__file__).parents[2] / "ontology/ontology.json")

    assert exit_code == expected_exit
    if expected_exit == 0:
        assert "Validation PASSED" in output
    else:
        assert "ontology_mappings -> 0 -> semantic_model -> version" in output


def _ontology_with_embedded_model(model: dict) -> dict:
    return {
        "version": "0.3.0.dev0",
        "name": "sales",
        "ai_context": {"instructions": "Use for sales analysis"},
        "ontology": [{"concept": "Order", "type": "EntityType"}],
        "ontology_mappings": [{"semantic_model": model, "concept_mappings": []}],
    }


def test_embedded_model_runs_core_checks(run_validator, offline) -> None:
    orders = {
        "name": "orders",
        "source": "sales.public.orders",
        "primary_key": ["not_a_field"],
        "fields": [{"name": "order_id", "expression": {"sql": "o_orderkey"}}],
    }
    model = _document([orders, {"name": "orders", "source": "sales.public.orders"}], [])
    model["metrics"] = [
        {"name": "dropped", "expression": {"sql": "DROP TABLE x"}},
        {"name": "missing", "expression": {"sql": "SUM(no_such_field)"}},
    ]

    exit_code, output = run_validator(
        _ontology_with_embedded_model(model),
        Path(__file__).parents[2] / "ontology/ontology.json",
    )

    assert exit_code == 1
    assert "ontology_mappings -> 0 -> semantic_model" in output
    assert "Duplicate dataset name 'orders'" in output
    assert "not_a_field" in output
    assert "DROP" in output
    assert "no_such_field" in output


def test_embedded_model_core_errors_name_their_mapping_index(run_validator, offline) -> None:
    document = _ontology_with_embedded_model(_document([_ORDERS], []))
    document["ontology_mappings"].append(
        {
            "semantic_model": _document([_ORDERS, _ORDERS], []),
            "concept_mappings": [],
        }
    )

    exit_code, output = run_validator(document, Path(__file__).parents[2] / "ontology/ontology.json")

    assert exit_code == 1
    assert "ontology_mappings -> 1 -> semantic_model" in output
    assert "Duplicate dataset name 'orders'" in output


@pytest.mark.parametrize(
    "target",
    [
        "missing_customers",
        "Warning: missing_customers",
        "[SQL] Warning: missing_customers",
        "[Reference] Warning: missing_customers",
    ],
)
def test_unknown_dataset_is_an_error_regardless_of_its_name(run_validator, target):
    document = _document([_ORDERS], [_relationship(to_columns=["id"], to=target)])

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "Validation FAILED with 1 error(s)" in output
    assert f"references unknown dataset '{target}'" in output
    assert "Validation PASSED" not in output


def test_duplicate_dataset_with_warning_in_name_is_an_error(run_validator):
    dataset = {"name": "Warning: orders", "source": "db.s.orders"}

    exit_code, output = run_validator(_document([dataset, dataset], []))

    assert exit_code == 1
    assert "Validation FAILED with 1 error(s)" in output
    assert "Duplicate dataset name 'Warning: orders'" in output


def test_schema_error_containing_warning_text_is_an_error(run_validator):
    document = _document([_ORDERS], [])
    document["Warning: unexpected"] = True

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "Validation FAILED with 1 error(s)" in output
    assert "[Schema]" in output
    assert "Warning: unexpected" in output


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_sql_error_in_metric_with_warning_in_name_is_an_error(run_validator):
    document = _document([_ORDERS], [])
    document["metrics"] = [
        {
            "name": "Warning: broken_metric",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM("}]},
        }
    ]

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "Validation FAILED with 1 error(s)" in output
    assert "[SQL] Metric 'Warning: broken_metric'" in output


def test_empty_field_expression_fails_validation(run_validator):
    # An empty per-dialect expression now fails the schema (minLength 1), before
    # the SQL check runs.
    dataset = {
        **_ORDERS,
        "fields": [
            {
                "name": "blank",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": ""}]},
            }
        ],
    }
    document = _document([dataset], [])

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "[Schema] datasets -> 0 -> fields -> 0 -> expression -> dialects -> 0 -> expression: '' should be non-empty" in output


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_comment_only_metric_expression_fails_validation(run_validator):
    document = _document([_ORDERS], [])
    document["metrics"] = [
        {
            "name": "blank_total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "-- TODO"}]},
        }
    ]

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "[SQL] Metric 'blank_total' in model 'm' (ANSI_SQL): expression is empty" in output


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_field_expressed_only_in_ossie_sql_2026_validates(run_validator):
    dataset = {
        **_ORDERS,
        "fields": [
            {
                "name": "half_amount",
                "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "amount * 0.5"}]},
            }
        ],
    }
    document = _document([dataset], [])

    exit_code, output = run_validator(document)

    assert exit_code == 0
    assert "Validation PASSED" in output


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_metric_expressed_only_in_ossie_sql_2026_validates(run_validator):
    document = _document([_ORDERS], [])
    document["metrics"] = [
        {
            "name": "total_amount",
            "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(amount)"}]},
        }
    ]

    exit_code, output = run_validator(document)

    assert exit_code == 0
    assert "Validation PASSED" in output


def test_key_coverage_warning_remains_nonfatal(run_validator):
    document = _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["region"])])

    exit_code, output = run_validator(document)

    assert exit_code == 0
    assert "[Reference] Warning:" in output
    assert "Validation PASSED" in output


def test_missing_sqlglot_warning_remains_nonfatal(run_validator, monkeypatch):
    monkeypatch.setattr(_VALIDATE, "SQLGLOT_AVAILABLE", False)

    exit_code, output = run_validator(_document([_ORDERS], []))

    assert exit_code == 0
    assert "[SQL] Warning: sqlglot not installed" in output
    assert "Validation PASSED" in output


def test_genuine_warning_does_not_hide_reference_error(run_validator):
    document = _document(
        [_ORDERS, _CUSTOMERS],
        [
            _relationship(to_columns=["region"]),
            {**_relationship(to_columns=["id"], to="Warning: missing"), "name": "broken"},
        ],
    )

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "[Reference] Warning:" in output
    assert "references unknown dataset 'Warning: missing'" in output
    assert "Validation FAILED with 1 error(s)" in output


def _arity_relationship(from_columns: list[str], to_columns: list[str]) -> dict:
    return {
        "name": "orders_to_customers",
        "from": "orders",
        "to": "customers",
        "from_columns": from_columns,
        "to_columns": to_columns,
    }


@pytest.mark.parametrize(
    ("from_columns", "to_columns"),
    [
        (["customer_id"], ["id"]),
        (["product_id", "variant_id"], ["id", "variant_id"]),
    ],
)
def test_arity_accepts_equal_length_columns(from_columns: list[str], to_columns: list[str]) -> None:
    rel = _arity_relationship(from_columns, to_columns)

    assert validate_relationship_column_arity(_document([_ORDERS, _CUSTOMERS], [rel])) == []


@pytest.mark.parametrize(
    ("from_columns", "to_columns"),
    [
        (["product_id", "variant_id"], ["id"]),
        (["customer_id"], ["id", "variant_id"]),
    ],
)
def test_arity_rejects_mismatched_length_columns(from_columns: list[str], to_columns: list[str]) -> None:
    rel = _arity_relationship(from_columns, to_columns)

    errors = validate_relationship_column_arity(_document([_ORDERS, _CUSTOMERS], [rel]))

    assert errors == [
        f"[Arity] Relationship 'orders_to_customers' in model 'm': "
        f"from_columns ({len(from_columns)}) and to_columns ({len(to_columns)}) "
        f"must have the same number of columns"
    ]


def test_arity_skips_non_list_columns() -> None:
    # Schema validation reports the shape error; the arity check must not crash.
    rel = _arity_relationship(["customer_id"], ["id"])
    rel["to_columns"] = "id"

    assert validate_relationship_column_arity(_document([_ORDERS, _CUSTOMERS], [rel])) == []


def test_a_multi_statement_expression_fails_validation(run_validator):
    dataset = {
        **_ORDERS,
        "fields": [
            {
                "name": "amount",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "amount; DROP TABLE customers"}]},
            }
        ],
    }

    exit_code, output = run_validator(_document([dataset], []))

    assert exit_code == 1
    assert "expected a single expression but found 2 statements" in output


validate_ontology = _VALIDATE.validate_ontology

_ONTOLOGY_SCHEMA = Path(__file__).parents[2] / "ontology/ontology.json"


def _ontology(components: list[dict], **extra) -> dict:
    return {"version": "0.3.0.dev0", "name": "o", "ontology": components, **extra}


def _concept(name: str, **fields) -> dict:
    return {"concept": name, "type": "EntityType", **fields}


def _ontology_relationship(name: str, role: str, **fields) -> dict:
    return {
        "name": name,
        "roles": [{"concept": role}],
        "verbalizes": [f"{{x}} {name} {{{role}}}"],
        **fields,
    }


def test_ontology_checks_accept_the_flights_example() -> None:
    flights = yaml.safe_load((Path(__file__).parents[2] / "examples/flights.yaml").read_text())

    assert validate_ontology(flights) == []


@pytest.mark.parametrize("data", [None, [], {}, {"datasets": []}, {"ontology": {"concept": "A"}}])
def test_ontology_checks_skip_non_ontology_payloads(data: object) -> None:
    assert validate_ontology(data) == []


def test_duplicate_concepts_are_reported_once_per_repeat() -> None:
    errors = validate_ontology(_ontology([_concept("Order"), _concept("Order"), _concept("Item")]))

    assert errors == ["[Unique] Duplicate concept 'Order' in ontology 'o'"]


def test_a_duplicate_concept_does_not_repeat_its_reference_errors() -> None:
    first = _concept("Order", extends=["Ghost"], relationships=[_ontology_relationship("nr", "Integer")])
    second = _concept("Order", identify_by=["nr"])

    errors = validate_ontology(_ontology([first, second]))

    assert errors == [
        "[Unique] Duplicate concept 'Order' in ontology 'o'",
        "[Reference] Concept 'Order' extends unknown concept 'Ghost'",
    ]


def test_duplicate_relationship_names_are_scoped_to_their_concept() -> None:
    order = _concept("Order", relationships=[_ontology_relationship("nr", "Integer"), _ontology_relationship("nr", "String")])
    item = _concept("Item", relationships=[_ontology_relationship("nr", "Integer")])

    errors = validate_ontology(_ontology([order, item]))

    assert errors == ["[Unique] Duplicate relationship 'nr' in concept 'Order'"]


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        (
            _concept("Order", extends=["Ghost"]),
            "[Reference] Concept 'Order' extends unknown concept 'Ghost'",
        ),
        (
            _concept("Order", identify_by=["nr"]),
            "[Reference] Concept 'Order' identify_by references unknown relationship 'nr'",
        ),
        (
            _concept("Order", relationships=[_ontology_relationship("placed_by", "Nobody")]),
            "[Reference] Relationship 'Order.placed_by' has a role played by unknown concept 'Nobody'",
        ),
        (
            _concept("Order", iri="ex:Order"),
            "[Reference] Concept 'Order' iri uses undeclared prefix 'ex'",
        ),
        (
            _concept("Order", relationships=[_ontology_relationship("nr", "Integer", iri="ex:nr")]),
            "[Reference] Relationship 'Order.nr' iri uses undeclared prefix 'ex'",
        ),
    ],
)
def test_dangling_ontology_references_are_reported(component: dict, expected: str) -> None:
    assert validate_ontology(_ontology([component])) == [expected]


def test_built_in_concepts_resolve_without_being_declared() -> None:
    amount = {"concept": "Amount", "type": "ValueType", "extends": ["Decimal"]}
    order = _concept(
        "Order",
        extends=["Any"],
        relationships=[_ontology_relationship("total", "Amount"), _ontology_relationship("placed_on", "DateTime")],
    )

    assert validate_ontology(_ontology([amount, order])) == []


def test_identify_by_resolves_relationships_declared_on_a_supertype() -> None:
    party = _concept("Party", relationships=[_ontology_relationship("nr", "Integer")])
    person = _concept("Person", extends=["Party"], identify_by=["nr"])

    assert validate_ontology(_ontology([party, person])) == []


@pytest.mark.parametrize(
    ("components", "expected"),
    [
        (
            [_concept("A", extends=["B"]), _concept("B", extends=["A"])],
            ["[Reference] Concept 'A' extends itself: A -> B -> A"],
        ),
        (
            [_concept("A", extends=["A"])],
            ["[Reference] Concept 'A' extends itself: A -> A"],
        ),
        (
            [_concept("A", extends=["B"]), _concept("B", extends=["C"]), _concept("C", extends=["Any"])],
            [],
        ),
        (
            # A supertype reached through two branches is not a cycle.
            [
                _concept("A", extends=["B", "C"]),
                _concept("B", extends=["D"]),
                _concept("C", extends=["D"]),
                _concept("D"),
            ],
            [],
        ),
        (
            # After B, the walk resumes A's supertypes with B off the path.
            [_concept("A", extends=["B", "C"]), _concept("B", extends=["A"]), _concept("C", extends=["A"])],
            [
                "[Reference] Concept 'A' extends itself: A -> B -> A",
                "[Reference] Concept 'A' extends itself: A -> C -> A",
            ],
        ),
    ],
)
def test_extends_cycles_are_reported_once(components: list[dict], expected: list[str]) -> None:
    assert validate_ontology(_ontology(components)) == expected


# Five times the default recursion limit, so a recursive walk of the extends
# graph still fails here if the limit is raised.
_LONG_CHAIN = 5000


def _long_ontology_chain(size: int) -> dict:
    components = [_concept(f"Concept{i}", extends=[f"Concept{i + 1}"]) for i in range(size)]
    components.append(_concept(f"Concept{size}"))
    return _ontology(components)


def test_long_extends_chain_does_not_exceed_recursion_limit() -> None:
    assert validate_ontology(_long_ontology_chain(_LONG_CHAIN)) == []


def test_cycle_closing_a_long_extends_chain_is_reported_once() -> None:
    ontology = _long_ontology_chain(_LONG_CHAIN)
    ontology["ontology"][-1]["extends"] = ["Concept0"]
    cycle = [f"Concept{i}" for i in range(_LONG_CHAIN + 1)] + ["Concept0"]

    assert validate_ontology(ontology) == [f"[Reference] Concept 'Concept0' extends itself: {' -> '.join(cycle)}"]


def test_cli_accepts_ontology_with_long_extends_chain(run_validator, offline) -> None:
    # Just past the current limit: loading and schema-checking the document
    # cost about 2ms per concept, so _LONG_CHAIN would add seconds to the run.
    document = _long_ontology_chain(sys.getrecursionlimit() + 100)

    exit_code, output = run_validator(document, _ONTOLOGY_SCHEMA)

    assert exit_code == 0, output
    assert "Validation PASSED" in output


@pytest.mark.parametrize(
    "iri",
    [
        "ex:Order",
        "http://example.com/Order",
        "urn:isbn:0451450523",
        "urn:x",
        "https://example.com/ns#Order",
        "mailto:orders@example.com",
        "tel:+15551234567",
        "MAILTO:orders@example.com",
    ],
)
def test_declared_prefixes_and_full_iris_are_accepted(iri: str) -> None:
    document = _ontology([_concept("Order", iri=iri)], prefixes={"ex": "http://example.com/"})

    assert validate_ontology(document) == []


def test_undeclared_prefix_is_reported_even_when_prefixes_are_declared() -> None:
    document = _ontology([_concept("Order", iri="foaf:Order")], prefixes={"ex": "http://example.com/"})

    assert validate_ontology(document) == ["[Reference] Concept 'Order' iri uses undeclared prefix 'foaf'"]


@pytest.mark.parametrize(
    "iri",
    [
        "https://example.com/ns#",
        "urn:example:terms:",
    ],
)
def test_prefix_iris_accept_absolute_iris_without_whitespace(iri: str) -> None:
    document = _ontology([_concept("Order")], prefixes={"ex": iri})

    assert validate_ontology(document) == []


@pytest.mark.parametrize(
    "iri",
    [
        "not a valid iri at all !!!",
        "relative/path",
        "1invalid:namespace",
        "https://example.com/has space",
    ],
)
def test_prefix_iris_reject_values_without_absolute_iri_syntax(iri: str) -> None:
    document = _ontology([_concept("Order")], prefixes={"ex": iri})

    assert validate_ontology(document) == ["[Reference] Prefix 'ex' has an invalid IRI value"]


def test_ontology_cli_rejects_invalid_prefix_iri(run_validator, offline) -> None:
    document = _ontology([_concept("Order")], prefixes={"foaf": "not a valid iri at all !!!"})

    exit_code, output = run_validator(document, _ONTOLOGY_SCHEMA)

    assert exit_code == 1
    assert "[Reference] Prefix 'foaf' has an invalid IRI value" in output
    assert "Validation FAILED with 1 error(s)" in output


def test_ontology_checks_tolerate_malformed_shapes() -> None:
    # Schema validation owns shape errors; the semantic pass must not crash on them.
    document = _ontology(
        ["not a component", _concept("Order", extends="Any", identify_by="nr", relationships=[None, {}])],
        prefixes="foaf",
    )

    assert validate_ontology(document) == []


def test_ontology_cli_reports_dangling_references(run_validator, offline) -> None:
    document = _ontology([_concept("Order", extends=["Ghost"])])

    exit_code, output = run_validator(document, _ONTOLOGY_SCHEMA)

    assert exit_code == 1
    assert "[Reference] Concept 'Order' extends unknown concept 'Ghost'" in output


def test_ontology_cli_skips_semantic_checks_when_the_schema_fails(run_validator, offline) -> None:
    document = _ontology([{"concept": "Order", "extends": ["Ghost"]}])  # missing required type

    exit_code, output = run_validator(document, _ONTOLOGY_SCHEMA)

    assert exit_code == 1
    assert "[Schema]" in output
    assert "[Reference]" not in output


# --- command line: several files, schema lookup -----------------------------


def _write_document(tmp_path, name, document):
    path = tmp_path / name
    path.write_text(json.dumps(document))
    return path


def _run_cli(monkeypatch, capsys, *args):
    monkeypatch.setattr(_VALIDATE.sys, "argv", [str(_VALIDATE_PATH), *map(str, args)])
    with pytest.raises(SystemExit) as caught:
        _VALIDATE.main()
    return caught.value.code, capsys.readouterr().out


def test_cli_validates_every_file_and_summarises(tmp_path, monkeypatch, capsys, offline):
    first = _write_document(tmp_path, "first.json", _document([_CUSTOMERS], []))
    second = _write_document(tmp_path, "second.json", _document([_ORDERS], []))

    code, output = _run_cli(monkeypatch, capsys, first, second)

    assert code == 0
    assert "Validation PASSED: first.json" in output
    assert "Validation PASSED: second.json" in output
    assert "2 of 2 file(s) passed" in output


def test_cli_fails_when_any_file_fails_but_reports_them_all(tmp_path, monkeypatch, capsys, offline):
    good = _write_document(tmp_path, "good.json", _document([_CUSTOMERS], []))
    bad_document = _document([_CUSTOMERS], [])
    bad_document["version"] = "0.1.0"
    bad = _write_document(tmp_path, "bad.json", bad_document)

    code, output = _run_cli(monkeypatch, capsys, good, bad)

    assert code == 1
    assert "Validation PASSED: good.json" in output
    assert "Validation FAILED with 1 error(s)" in output
    assert "1 of 2 file(s) passed" in output


def test_cli_reports_a_missing_file_and_goes_on(tmp_path, monkeypatch, capsys, offline):
    good = _write_document(tmp_path, "good.json", _document([_CUSTOMERS], []))

    code, output = _run_cli(monkeypatch, capsys, tmp_path / "missing.json", good)

    assert code == 1
    assert "Error: File not found" in output and "missing.json" in output
    assert "Validation PASSED: good.json" in output


def test_cli_reports_an_unreadable_file_and_goes_on(tmp_path, monkeypatch, capsys, offline):
    good = _write_document(tmp_path, "good.json", _document([_CUSTOMERS], []))
    binary = tmp_path / "binary.yaml"
    binary.write_bytes(b"\xff\xfe\x00")

    code, output = _run_cli(monkeypatch, capsys, binary, tmp_path, good)

    assert code == 1
    assert "is not valid UTF-8 text" in output and "binary.yaml" in output
    assert f"Error: Could not read {tmp_path}" in output
    assert "Validation PASSED: good.json" in output


def test_cli_reports_a_check_that_raises_and_goes_on(tmp_path, monkeypatch, capsys, offline):
    good = _write_document(tmp_path, "good.json", _document([_CUSTOMERS], []))
    bad = _write_document(tmp_path, "bad.json", _document([_ORDERS], []))

    def explode(data, schema, base=None, vendors=None):
        if "orders" in json.dumps(data):
            raise KeyError("dialect")
        return []

    monkeypatch.setattr(_VALIDATE, "validate_document", explode)

    code, output = _run_cli(monkeypatch, capsys, bad, good)

    assert code == 1
    assert f"Error: KeyError while validating {bad}: 'dialect'" in output
    assert "Validation PASSED: good.json" in output
    assert "1 of 2 file(s) passed" in output


@pytest.mark.parametrize(
    "scalar, expected",
    [
        ("2024-02-30", "cannot build '2024-02-30' as timestamp"),
        ("!!int abc", "cannot build 'abc' as int"),
        ("!!timestamp abc", "cannot build 'abc' as timestamp"),
        ("!!bool abc", "cannot build 'abc' as bool"),
        ("!!int ''", "cannot build '' as int"),
    ],
)
def test_cli_reports_a_value_yaml_cannot_build_and_goes_on(tmp_path, monkeypatch, capsys, offline, scalar, expected):
    # SafeLoader raises ValueError, AttributeError, KeyError or IndexError for these,
    # not YAMLError.
    good = _write_document(tmp_path, "good.json", _document([_CUSTOMERS], []))
    bad = tmp_path / "bad.yaml"
    bad.write_text(f'version: "0.3.0.dev0"\nname: {scalar}\ndatasets: []\n')

    code, output = _run_cli(monkeypatch, capsys, bad, good)

    assert code == 1
    assert f"Error: Invalid YAML: {expected}" in output
    assert f'in "{bad}", line 2, column 7' in output
    assert "Validation PASSED: good.json" in output
    assert "1 of 2 file(s) passed" in output


def test_cli_escapes_a_lone_surrogate_in_a_diagnostic(tmp_path, monkeypatch, capsys, offline):
    # A "\ud800" escape loads as a lone surrogate, which the strict stdout encoder
    # refuses when the diagnostic that names it is printed.
    dataset = json.loads(json.dumps(_CUSTOMERS))
    field = dict(dataset["fields"][0], name="\ud800")
    dataset["fields"] = dataset["fields"] + [field, dict(field)]
    bad = _write_document(tmp_path, "bad.json", _document([dataset], []))
    good = _write_document(tmp_path, "good.json", _document([_CUSTOMERS], []))

    code, output = _run_cli(monkeypatch, capsys, bad, good)

    assert code == 1
    assert "[Unique] Duplicate field name '\\ud800' in dataset 'customers'" in output
    assert "Validation PASSED: good.json" in output
    assert "1 of 2 file(s) passed" in output


def test_cli_escapes_a_file_name_stdout_cannot_encode(tmp_path, monkeypatch, capsys, offline):
    # A name that is not valid UTF-8 decodes with a surrogate escape, which the
    # strict stdout encoder refuses when the report names the file. Windows has no
    # such escape, so the name does not decode there.
    try:
        model = _write_document(tmp_path, os.fsdecode(b"m\xff.json"), _document([_CUSTOMERS], []))
    except (OSError, UnicodeDecodeError):
        pytest.skip("the file system refuses a name that is not valid UTF-8")

    code, output = _run_cli(monkeypatch, capsys, model)

    assert code == 0
    assert "Validation PASSED: m\\udcff.json" in output


def test_cli_takes_files_after_a_double_dash_literally(tmp_path, monkeypatch, capsys, offline):
    _write_document(tmp_path, "-model.json", _document([_CUSTOMERS], []))
    monkeypatch.chdir(tmp_path)

    code, output = _run_cli(monkeypatch, capsys, "--", "-model.json")

    assert code == 0
    assert output.strip() == "Validation PASSED: -model.json"


def test_cli_single_file_output_has_no_summary(tmp_path, monkeypatch, capsys, offline):
    only = _write_document(tmp_path, "model.json", _document([_CUSTOMERS], []))

    code, output = _run_cli(monkeypatch, capsys, only)

    assert code == 0
    assert output.strip() == "Validation PASSED: model.json"


def test_cli_accepts_schema_option_before_the_files(tmp_path, monkeypatch, capsys, offline):
    schema = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    only = _write_document(tmp_path, "model.json", _document([_CUSTOMERS], []))

    code, output = _run_cli(monkeypatch, capsys, "--schema", schema, only)

    assert code == 0
    assert "Validation PASSED: model.json" in output


def test_cli_reads_the_schema_from_ossie_schema_env(tmp_path, monkeypatch, capsys, offline, core_schema):
    copied = tmp_path / "vendored-schema.json"
    copied.write_text(json.dumps(core_schema))
    monkeypatch.setenv("OSSIE_SCHEMA", str(copied))
    only = _write_document(tmp_path, "model.json", _document([_CUSTOMERS], []))

    code, output = _run_cli(monkeypatch, capsys, only)

    assert code == 0
    assert "Validation PASSED: model.json" in output


def test_cli_schema_option_wins_over_the_env(tmp_path, monkeypatch, capsys, offline):
    monkeypatch.setenv("OSSIE_SCHEMA", str(tmp_path / "nowhere.json"))
    schema = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    only = _write_document(tmp_path, "model.json", _document([_CUSTOMERS], []))

    code, output = _run_cli(monkeypatch, capsys, only, "--schema", schema)

    assert code == 0


def test_cli_missing_default_schema_names_both_overrides(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OSSIE_SCHEMA", str(tmp_path / "nowhere.json"))
    only = _write_document(tmp_path, "model.json", _document([_CUSTOMERS], []))

    code, output = _run_cli(monkeypatch, capsys, only)

    assert code == 1
    assert "Schema not found" in output
    assert "--schema" in output and "OSSIE_SCHEMA" in output


@pytest.mark.parametrize("argv", [["--schema"], ["--verbose", "x.yaml"], ["--schema", "s.json"], ["--"]])
def test_cli_rejects_malformed_arguments(monkeypatch, capsys, argv):
    code, output = _run_cli(monkeypatch, capsys, *argv)

    assert code == 1
    assert output.startswith("Usage:")


def test_validate_document_stops_at_schema_errors(core_schema, offline):
    document = _document([_CUSTOMERS, _CUSTOMERS], [])
    document["version"] = "0.1.0"

    errors = _VALIDATE.validate_document(document, core_schema)

    assert len(errors) == 1 and errors[0].startswith("[Schema]")


def test_validate_document_runs_semantic_checks_on_a_valid_shape(core_schema, offline):
    errors = _VALIDATE.validate_document(_document([_CUSTOMERS, _CUSTOMERS], []), core_schema)

    assert any(error.startswith("[Unique]") for error in errors)


# ---------------------------------------------------------------------------
# Dataset-scoped metrics: field/metric name uniqueness and single-part scoping.
# ---------------------------------------------------------------------------


def _ds_expr(sql: str, dialect: str = "OSSIE_SQL_2026") -> dict:
    return {"dialects": [{"dialect": dialect, "expression": sql}]}


def _ds_portable(sql: str) -> dict:
    # A dataset-scoped metric written with only the portable `sql` form, no dialects.
    return {"sql": sql}


def _orders_with(fields=None, metrics=None) -> dict:
    dataset = {"name": "orders", "source": "db.s.orders"}
    if fields is not None:
        dataset["fields"] = fields
    if metrics is not None:
        dataset["metrics"] = metrics
    return _document([dataset, _CUSTOMERS], [])


def test_dataset_scoped_metric_name_may_not_collide_with_a_field() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "amount", "expression": _ds_expr("SUM(amount)")}],
    )

    errors = _VALIDATE.validate_unique_names(doc)

    assert any("used by both a field" in error for error in errors)


def test_duplicate_dataset_scoped_metric_names_are_rejected() -> None:
    doc = _orders_with(
        metrics=[
            {"name": "total", "expression": _ds_expr("SUM(amount)")},
            {"name": "total", "expression": _ds_expr("COUNT(amount)")},
        ],
    )

    errors = _VALIDATE.validate_unique_names(doc)

    assert any("Duplicate metric name 'total' in dataset 'orders'" in error for error in errors)


def test_field_and_dataset_metric_with_distinct_names_are_unique() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total_amount", "expression": _ds_expr("SUM(amount)")}],
    )

    assert _VALIDATE.validate_unique_names(doc) == []


def test_dataset_scoped_metric_accepts_single_part_declared_field() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total_amount", "expression": _ds_expr("SUM(amount)")}],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_rejects_a_self_qualified_reference() -> None:
    doc = _orders_with(
        metrics=[{"name": "total_amount", "expression": _ds_expr("SUM(orders.amount)")}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert any(error.startswith("[Scope]") and "'orders'" in error for error in errors)


def test_dataset_scoped_metric_rejects_a_cross_dataset_reference() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "bad", "expression": _ds_expr("SUM(amount) / COUNT(customers.id)")}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert any(error.startswith("[Scope]") and "'customers'" in error for error in errors)


def test_dataset_scoped_metric_rejects_an_undeclared_field() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total_qty", "expression": _ds_expr("SUM(quantity)")}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert any(error.startswith("[Scope]") and "'quantity'" in error for error in errors)


def test_dataset_scoped_metric_rejects_a_quoted_identifier_that_does_not_match() -> None:
    # Quotes cause an exact match (expression_language.md). Field amount is AMOUNT.
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total_amount", "expression": _ds_expr('SUM("Amount")')}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert any(error.startswith("[Scope]") and '"Amount"' in error for error in errors)


def test_dataset_scoped_metric_accepts_a_quoted_identifier_in_normalized_form() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[
            {"name": "quoted", "expression": _ds_expr('SUM("AMOUNT")')},
            {"name": "mixed", "expression": _ds_expr("SUM(Amount)")},
            {"name": "lower", "expression": _ds_expr("SUM(amount)")},
        ],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_vendor_dialect_compares_quoted_names_without_regard_to_case() -> None:
    # Quoted versus unquoted matching is DB specific; vendor dialects keep the
    # case-insensitive comparison they had before the OSSIE_SQL_2026 quoting rule.
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[
            {"name": "pg", "expression": _ds_expr('SUM("amount")', "POSTGRES")},
            {"name": "bq", "expression": _ds_expr("SUM(`amount`)", "BIGQUERY")},
        ],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_accepts_source_columns() -> None:
    # The source qualifier is case insensitive, and reaches a column that a field
    # of the same name shadows.
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount * 100")}],
        metrics=[
            {"name": "total_amount", "expression": _ds_expr("SUM(source.amount)")},
            {"name": "total_qty", "expression": _ds_expr("SUM(SOURCE.quantity)")},
            {"name": "total_cents", "expression": _ds_expr("SUM(amount)")},
        ],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_quoted_source_is_not_the_qualifier() -> None:
    # Quoted "source" is the lowercase identifier, not the SOURCE qualifier.
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total", "expression": _ds_expr('SUM("source".amount)')}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert any(error.startswith("[Scope]") and "qualified" in error for error in errors)


def test_dataset_scoped_metric_rejects_a_three_part_source_reference() -> None:
    doc = _orders_with(
        metrics=[{"name": "total", "expression": _ds_expr("SUM(source.orders.amount)")}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert any(error.startswith("[Scope]") and "'source'" in error for error in errors)


def test_dataset_scoped_metric_rejects_source_columns_when_a_field_is_named_source() -> None:
    # A field named source takes precedence over the source qualifier.
    doc = _orders_with(
        fields=[{"name": "source", "expression": _ds_expr("channel")}],
        metrics=[
            {"name": "sources", "expression": _ds_expr("COUNT(DISTINCT source)")},
            {"name": "total_amount", "expression": _ds_expr("SUM(source.amount)")},
        ],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == [
        "[Scope] Dataset-scoped metric 'orders.total_amount' in model 'm' (OSSIE_SQL_2026) "
        "uses source.column, but dataset 'orders' declares a field named 'source', which "
        "takes precedence over the source qualifier. This dataset's metrics cannot refer "
        "to source columns."
    ]


def test_dataset_scoped_metric_count_star_references_no_field() -> None:
    doc = _orders_with(metrics=[{"name": "n", "expression": _ds_expr("COUNT(*)")}])

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_reads_a_date_part_as_a_unit_not_a_field() -> None:
    # sqlglot's default parser reads the unit of DATEDIFF as a column, so the
    # check walks iter_columns like the reference checks do.
    doc = _orders_with(
        fields=[
            {"name": "opened_at", "expression": _ds_expr("opened_at")},
            {"name": "closed_at", "expression": _ds_expr("closed_at")},
        ],
        metrics=[
            {"name": "days_open", "expression": _ds_expr("MAX(DATEDIFF(day, opened_at, closed_at))")},
            {"name": "source_days_open", "expression": _ds_expr("MAX(DATEDIFF(day, source.opened_at, source.closed_at))")},
            {"name": "minutes_since_open", "expression": _ds_expr("DATEDIFF(MINUTE, MAX(opened_at), CURRENT_TIMESTAMP)")},
            {"name": "ansi_days_open", "expression": _ds_expr("MAX(DATEDIFF(day, opened_at, closed_at))", "ANSI_SQL")},
        ],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_reads_a_column_in_the_unit_slot_as_a_field() -> None:
    # Under the default parser the last DATEDIFF argument lands in `unit`, so an
    # undeclared field there must still be reported.
    doc = _orders_with(
        fields=[{"name": "opened_at", "expression": _ds_expr("opened_at")}],
        metrics=[{"name": "days_open", "expression": _ds_expr("MAX(DATEDIFF(day, opened_at, closed_at))")}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert len(errors) == 1
    assert errors[0].startswith("[Scope]") and "'CLOSED_AT'" in errors[0] and "'day'" not in errors[0]


def test_dataset_scoped_metric_still_reports_a_bare_field_named_like_a_unit() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total_minutes", "expression": _ds_expr("SUM(minute)")}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert len(errors) == 1
    assert errors[0].startswith("[Scope]") and "'minute'" in errors[0]


def test_dataset_scoped_metric_still_rejects_a_qualified_star() -> None:
    # iter_columns skips every `*`; a qualified one names a dataset, or the
    # source, and keeps the scoping rule.
    doc = _orders_with(
        metrics=[
            {"name": "n_customers", "expression": _ds_expr("COUNT(customers.*)")},
            {"name": "n_source", "expression": _ds_expr("COUNT(source.*)")},
        ],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert len(errors) == 1
    assert errors[0].startswith("[Scope]") and "'customers'" in errors[0]


def test_dataset_scoped_metric_under_a_parser_that_knows_the_unit_reads_every_column_as_a_field() -> None:
    # Snowflake and BigQuery put the unit in `unit` themselves, so a field named
    # like a date part next to a unit the alias table lacks is still a field.
    doc = _orders_with(
        fields=[
            {"name": "opened_at", "expression": _ds_expr("opened_at", "SNOWFLAKE")},
            {"name": "d", "expression": _ds_expr("delivered_at", "SNOWFLAKE")},
            {"name": "day", "expression": _ds_expr("delivered_day", "BIGQUERY")},
        ],
        metrics=[
            {"name": "ns_open", "expression": _ds_expr("MAX(DATEDIFF(nanoseconds, opened_at, d))", "SNOWFLAKE")},
            {"name": "weeks_open", "expression": _ds_expr("MAX(DATE_DIFF(day, opened_at, ISOWEEK))", "BIGQUERY")},
        ],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_under_a_parser_that_knows_the_unit_reports_the_date_argument() -> None:
    # BigQuery puts `WEEK(MONDAY)` in `unit`, so `day` in `this` is the date
    # argument and is reported when undeclared.
    doc = _orders_with(
        fields=[{"name": "opened_at", "expression": _ds_expr("opened_at", "BIGQUERY")}],
        metrics=[{"name": "open", "expression": _ds_expr("MAX(DATE_DIFF(day, opened_at, WEEK(MONDAY)))", "BIGQUERY")}],
    )

    errors = _VALIDATE.validate_metric_scoping(doc)

    assert len(errors) == 1
    assert errors[0].startswith("[Scope]") and "undeclared field(s) 'day'." in errors[0]


def test_dataset_scoped_metric_scoping_skips_dialects_sqlglot_cannot_parse() -> None:
    doc = _orders_with(
        metrics=[{"name": "total", "expression": _ds_expr("SUM(orders.amount)", "MDX")}],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_qualified_dataset_scoped_metric_fails_document_validation(core_schema, offline) -> None:
    # Conformance test for the qualified-reference error case, per the dev@ossie
    # "[DISCUSS] Dataset-scoped metrics" thread: a qualified reference in a
    # dataset-scoped metric is a hard error (not a warning), so the whole
    # document fails validation.
    orders = {
        "name": "orders",
        "source": "db.s.orders",
        "metrics": [{"name": "total", "expression": _ds_expr("SUM(orders.amount)")}],
    }

    errors = _VALIDATE.validate_document(_document([orders, _CUSTOMERS], []), core_schema)

    scope_errors = [error for error in errors if error.startswith("[Scope]")]
    assert scope_errors
    assert all(not isinstance(error, _VALIDATE.ValidationWarning) for error in scope_errors)


def test_dataset_scoped_metric_portable_sql_accepts_declared_field() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total_amount", "expression": _ds_portable("SUM(amount)")}],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


def test_dataset_scoped_metric_portable_sql_rejects_a_cross_dataset_reference() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "bad", "expression": _ds_portable("SUM(amount) / COUNT(customers.id)")}],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == [
        "[Scope] Dataset-scoped metric 'orders.bad' in model 'm' (OSSIE_SQL_2026) uses "
        "qualified reference(s) 'customers'. A dataset-scoped metric expression must "
        "reference the dataset's fields by single-part (unqualified) name and its source "
        "columns as source.column; a metric that must reference another dataset belongs "
        "in the model-root metrics."
    ]


def test_dataset_scoped_metric_portable_sql_rejects_an_undeclared_field() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total_qty", "expression": _ds_portable("SUM(quantity)")}],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == [
        "[Scope] Dataset-scoped metric 'orders.total_qty' in model 'm' (OSSIE_SQL_2026) "
        "references undeclared field(s) 'quantity'. A dataset-scoped metric may reference "
        "only the dataset's declared fields, and its source columns as source.column."
    ]


def test_dataset_scoped_metric_portable_sql_is_sql_validated() -> None:
    doc = _orders_with(
        metrics=[{"name": "broken", "expression": _ds_portable("SUM(")}],
    )

    errors = _VALIDATE.validate_sql(doc)

    # The sqlglot error text varies by version, so assert the context prefix only.
    assert len(errors) == 1
    assert errors[0].startswith("[SQL] Metric 'orders.broken' in model 'm' (OSSIE_SQL_2026):")


def _metric_with_sql_and_ossie_dialect(sql: str, dialect_sql: str) -> dict:
    return _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[
            {
                "name": "total",
                "expression": {
                    "sql": sql,
                    "dialects": [{"dialect": "OSSIE_SQL_2026", "expression": dialect_sql}],
                },
            }
        ],
    )


def test_invalid_superseded_ossie_sql_2026_dialect_entry_warns_not_fails() -> None:
    # The top-level `sql` is authoritative, so an invalid OSSIE_SQL_2026 dialect
    # entry beside it is reported as a nonfatal warning, not a hard error.
    errors = _VALIDATE.validate_sql(_metric_with_sql_and_ossie_dialect("SUM(amount)", "SUM("))

    assert errors == [
        "[SQL] Warning: Metric 'orders.total' in model 'm' (OSSIE_SQL_2026): dialect entry "
        "is not valid SQL and is ignored; the top-level 'sql' is authoritative"
    ]
    assert all(isinstance(e, _VALIDATE.ValidationWarning) for e in errors)


def test_differing_superseded_ossie_sql_2026_dialect_entry_warns() -> None:
    # A valid OSSIE_SQL_2026 dialect entry that differs from the authoritative
    # top-level `sql` is dead text; warn so the drift is caught.
    errors = _VALIDATE.validate_sql(_metric_with_sql_and_ossie_dialect("SUM(amount)", "AVG(amount)"))

    assert errors == [
        "[SQL] Warning: Metric 'orders.total' in model 'm' (OSSIE_SQL_2026): dialect entry "
        "differs from the top-level 'sql' and is ignored; the top-level 'sql' is authoritative"
    ]


def test_identical_superseded_ossie_sql_2026_dialect_entry_is_silent() -> None:
    # An OSSIE_SQL_2026 dialect entry identical to the top-level `sql` is harmless
    # redundancy, not drift, so it is not warned.
    assert _VALIDATE.validate_sql(_metric_with_sql_and_ossie_dialect("SUM(amount)", "SUM(amount)")) == []


def test_superseded_ossie_sql_2026_dialect_entry_is_not_scope_checked() -> None:
    # The authoritative top-level `sql` is scope-checked; a superseded
    # OSSIE_SQL_2026 dialect entry beside it is not, so its cross-dataset
    # reference does not fail the document.
    expression = {
        "sql": "SUM(amount)",
        "dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(customers.id)"}],
    }
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total", "expression": expression}],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == []


# Direct tests for iter_expressions(), which validate_sql and
# validate_metric_scoping both walk.


def _iter(data: dict) -> list:
    return list(_VALIDATE.iter_expressions(data))


def _one_field(expression: dict) -> dict:
    return {
        "name": "m",
        "datasets": [{"name": "orders", "fields": [{"name": "amount", "expression": expression}]}],
    }


def test_iter_expressions_yields_top_level_sql_as_ossie_sql_2026() -> None:
    [expr] = _iter(_one_field({"sql": "amount"}))

    assert (expr.kind, expr.dataset_name, expr.name) == ("field", "orders", "amount")
    assert (expr.dialect, expr.text, expr.superseded_by) == ("OSSIE_SQL_2026", "amount", None)
    assert expr.field_names == frozenset({"AMOUNT"})


def test_iter_expressions_yields_each_dialect_entry() -> None:
    exprs = _iter(
        _one_field(
            {
                "dialects": [
                    {"dialect": "SNOWFLAKE", "expression": "amount::number"},
                    {"dialect": "DATABRICKS", "expression": "CAST(amount AS DECIMAL)"},
                ]
            }
        )
    )

    assert [(e.dialect, e.text) for e in exprs] == [
        ("SNOWFLAKE", "amount::number"),
        ("DATABRICKS", "CAST(amount AS DECIMAL)"),
    ]
    assert all(e.superseded_by is None for e in exprs)


def test_iter_expressions_yields_sql_before_dialects_and_marks_superseded_entry() -> None:
    exprs = _iter(
        _one_field(
            {
                "sql": "amount",
                "dialects": [
                    {"dialect": "SNOWFLAKE", "expression": "amount::number"},
                    {"dialect": "OSSIE_SQL_2026", "expression": "amount + 0"},
                ],
            }
        )
    )

    assert [(e.dialect, e.text, e.superseded_by) for e in exprs] == [
        ("OSSIE_SQL_2026", "amount", None),
        ("SNOWFLAKE", "amount::number", None),
        ("OSSIE_SQL_2026", "amount + 0", "amount"),
    ]


def test_iter_expressions_covers_dataset_and_model_metrics_in_order() -> None:
    data = {
        "name": "m",
        "datasets": [
            {
                "name": "orders",
                "fields": [{"name": "Amount", "expression": {"sql": "amount"}}],
                "metrics": [{"name": "total", "expression": {"sql": "SUM(amount)"}}],
            }
        ],
        "metrics": [{"name": "avg_total", "expression": {"sql": "AVG(orders.amount)"}}],
    }

    exprs = _iter(data)

    assert [(e.kind, e.dataset_name, e.name) for e in exprs] == [
        ("field", "orders", "Amount"),
        ("dataset_metric", "orders", "total"),
        ("model_metric", "", "avg_total"),
    ]
    # Dataset members see declared field names; model metrics see none.
    assert exprs[1].field_names == frozenset({"AMOUNT"})
    assert exprs[2].field_names == frozenset()


def test_iter_expressions_yields_empty_text_and_skips_missing_expressions() -> None:
    data = {
        "name": "m",
        "datasets": [
            {
                "name": "orders",
                "fields": [
                    {"name": "blank", "expression": {"sql": ""}},
                    {"name": "no_expression"},
                    {"name": "no_text", "expression": {"dialects": [{"dialect": "SNOWFLAKE"}]}},
                ],
            }
        ],
    }

    assert [(e.name, e.dialect, e.text) for e in _iter(data)] == [
        ("blank", "OSSIE_SQL_2026", ""),
    ]


@pytest.mark.parametrize(
    ("kind", "dataset_name", "expected"),
    [
        ("field", "orders", "Field 'orders.x' in model 'm' (SNOWFLAKE)"),
        ("dataset_metric", "orders", "Metric 'orders.x' in model 'm' (SNOWFLAKE)"),
        ("model_metric", "", "Metric 'x' in model 'm' (SNOWFLAKE)"),
    ],
)
def test_expression_context_names_the_declaring_element(kind, dataset_name, expected) -> None:
    expr = _VALIDATE.Expression(
        kind=kind,
        dataset_name=dataset_name,
        name="x",
        dialect="SNOWFLAKE",
        text="x",
        field_names=frozenset(),
    )

    assert expr.context("m") == expected


# ---------------------------------------------------------------------------
# Issue #279: cross-dialect metric drift and expression purity.
# Diagnostics come from validate_document, which runs the shipped checker.
# ---------------------------------------------------------------------------


def _metric_model(name: str, dialects: list[tuple[str, str]]) -> dict:
    document = _document([_ORDERS], [])
    document["metrics"] = [
        {
            "name": name,
            "expression": {"dialects": [{"dialect": dialect, "expression": expression} for dialect, expression in dialects]},
        }
    ]
    return document


def _warning_text(errors: list) -> list[str]:
    return [error for error in errors if isinstance(error, _VALIDATE.ValidationWarning)]


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    ("left", "right", "fragments"),
    [
        ("SUM(amount)", "AVG(amount)", ["SUM", "AVG"]),
        ("COUNT(amount)", "COUNT(DISTINCT amount)", ["COUNT", "COUNT|DISTINCT"]),
        ("SUM(amount)", "SUM(price)", ["amount", "price"]),
        ("SUM(amount) * 1.08", "SUM(amount) * 1.18", ["1.08", "1.18"]),
        ("SUM(amount) * (1.08)", "SUM(amount) * (1.18)", ["1.08", "1.18"]),
        ("SUM(amount) * -1.08", "SUM(amount) * -1.18", ["-1.08", "-1.18"]),
        ("SUM(amount) * -1.08", "SUM(amount) * 1.08", ["-1.08"]),
        (
            "SUM(CASE WHEN region = 'EU' THEN amount ELSE 0 END)",
            "SUM(CASE WHEN region = 'US' THEN amount ELSE 0 END)",
            ["EU", "US"],
        ),
        (
            "SUM(CASE WHEN amt > 100 THEN amt ELSE 0 END)",
            "SUM(CASE WHEN amt >= 100 THEN amt ELSE 0 END)",
            ["GT(amt,100)", "GTE(amt,100)"],
        ),
        (
            "SUM(CASE WHEN status IN (1, 2) THEN amt ELSE 0 END)",
            "SUM(CASE WHEN status NOT IN (1, 2) THEN amt ELSE 0 END)",
            ["IN(status,[1,2])", "NOT(IN(status,[1,2]))"],
        ),
        (
            "SUM(CASE WHEN col IS NULL THEN amt ELSE 0 END)",
            "SUM(CASE WHEN col IS NOT NULL THEN amt ELSE 0 END)",
            ["IS(col,null)", "NOT(IS(col,null))"],
        ),
        (
            "SUM(CASE WHEN col BETWEEN 1 AND 10 THEN amt ELSE 0 END)",
            "SUM(CASE WHEN col NOT BETWEEN 1 AND 10 THEN amt ELSE 0 END)",
            ["BETWEEN(col,1,10)", "NOT(BETWEEN(col,1,10))"],
        ),
        (
            "SUM(CASE WHEN col LIKE '%foo%' THEN amt ELSE 0 END)",
            "SUM(CASE WHEN col NOT LIKE '%foo%' THEN amt ELSE 0 END)",
            ["LIKE(col,'%foo%')", "NOT(LIKE(col,'%foo%'))"],
        ),
        (
            "SUM(amount) * 12345678901234567890123",
            "SUM(amount) * 12345678901234567890124",
            ["12345678901234567890123", "12345678901234567890124"],
        ),
        (
            "SUM(amount) * 1e309",
            "SUM(amount) * 1e310",
            ["1E+309", "1E+310"],
        ),
        (
            "SUM(amount) * 1e1000000",
            "SUM(amount) * 1e1000001",
            ["1E+1000000", "1E+1000001"],
        ),
        (
            "SUM(amount) * 1234567890123456789012345678901",
            "SUM(amount) * 1234567890123456789012345678902",
            ["1234567890123456789012345678901", "1234567890123456789012345678902"],
        ),
        ("SUM(orders.amount)", "SUM(returns.amount)", ["orders.amount", "returns.amount"]),
        # A filter that tests a column as a boolean, with no comparison to read.
        (
            "SUM(amount) FILTER (WHERE is_active)",
            "SUM(amount) FILTER (WHERE NOT is_active)",
            ["ANSI_SQL=['EQ(is_active,1)']", "SNOWFLAKE=['NOT(EQ(is_active,1))']"],
        ),
        (
            "SUM(CASE WHEN is_active THEN amount END)",
            "SUM(CASE WHEN NOT is_active THEN amount END)",
            ["ANSI_SQL=['EQ(is_active,1)']", "SNOWFLAKE=['NOT(EQ(is_active,1))']"],
        ),
        (
            "SUM(CASE WHEN orders.region = 'EU' THEN orders.amount ELSE 0 END)",
            "SUM(CASE WHEN returns.region = 'EU' THEN orders.amount ELSE 0 END)",
            ["EQ(orders.region,'EU')", "EQ(returns.region,'EU')"],
        ),
    ],
)
def test_metric_dialect_drift_is_reported(core_schema, offline, left, right, fragments):
    # Two parseable dialects of one metric must not silently disagree.
    errors = _VALIDATE.validate_document(
        _metric_model("revenue", [("ANSI_SQL", left), ("SNOWFLAKE", right)]),
        core_schema,
    )

    warnings = _warning_text(errors)
    assert any("revenue" in warning and all(fragment in warning for fragment in fragments) for warning in warnings)
    assert [error for error in errors if not isinstance(error, _VALIDATE.ValidationWarning)] == []


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    ("dialect", "expression", "call"),
    [
        ("ANSI_SQL", "NOW()", "NOW"),
        ("DATABRICKS", "NOW()", "NOW"),
        ("ANSI_SQL", "RANDOM()", "RANDOM"),
        ("ANSI_SQL", "pg_read_file('/etc/passwd')", "pg_read_file"),
    ],
)
def test_impure_metric_call_is_reported_and_not_executed(core_schema, offline, monkeypatch, dialect, expression, call):
    def executed(*args, **kwargs):
        raise AssertionError(f"metric SQL must not be executed: {args!r}")

    monkeypatch.setattr("subprocess.Popen", executed)
    monkeypatch.setattr("subprocess.run", executed)

    errors = _VALIDATE.validate_document(
        _metric_model("revenue", [(dialect, expression)]),
        core_schema,
    )

    assert [error for error in errors if not isinstance(error, _VALIDATE.ValidationWarning)] == []
    assert any("revenue" in warning and call in warning for warning in _warning_text(errors))


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize(
    "dialects",
    [
        [
            ("ANSI_SQL", "AVG(COALESCE(price, 0))"),
            ("SNOWFLAKE", "AVG(NVL(price, 0))"),
        ],
        [
            ("ANSI_SQL", "SUM(CASE WHEN s = 1 THEN amt ELSE 0 END)"),
            ("SNOWFLAKE", "SUM(amt) FILTER (WHERE s = 1)"),
        ],
        [
            ("ANSI_SQL", "SUM(CASE WHEN s = 1 THEN amt ELSE 0 END)"),
            ("BIGQUERY", "SUM(IF(s = 1, amt, 0))"),
        ],
        [
            ("ANSI_SQL", "is_active = TRUE"),
            ("SNOWFLAKE", "is_active = 1"),
        ],
        [
            # A tested column is the comparison it stands for, in every spelling
            # of the filter.
            ("ANSI_SQL", "SUM(amt) FILTER (WHERE is_active)"),
            ("SNOWFLAKE", "SUM(amt) FILTER (WHERE is_active = TRUE)"),
            ("DATABRICKS", "SUM(CASE WHEN is_active THEN amt END)"),
            ("BIGQUERY", "SUM(IF(is_active = TRUE, amt, NULL))"),
        ],
        [
            ("ANSI_SQL", "amt > 100"),
            ("SNOWFLAKE", "100 < amt"),
        ],
        [
            ("ANSI_SQL", "amt > -1"),
            ("SNOWFLAKE", "amt > -1.0"),
            ("DATABRICKS", "amt > -(1)"),
            ("BIGQUERY", "-1 < amt"),
        ],
        [
            ("ANSI_SQL", "a < b"),
            ("SNOWFLAKE", "b > a"),
        ],
        [
            ("ANSI_SQL", "a = b"),
            ("SNOWFLAKE", "b = a"),
        ],
        [
            ("ANSI_SQL", "5 < CAST(x AS INT)"),
            ("SNOWFLAKE", "CAST(x AS INT) > 5"),
        ],
        [
            ("ANSI_SQL", "status IN (1, 2)"),
            ("SNOWFLAKE", "status IN (2, 1)"),
        ],
        [
            # The default parser reads `day` as a column; it is the date unit.
            ("ANSI_SQL", "AVG(DATEDIFF(day, orders.opened_at, orders.closed_at))"),
            ("SNOWFLAKE", "AVG(DATEDIFF(day, orders.opened_at, orders.closed_at))"),
            ("REDSHIFT", "AVG(DATEDIFF(day, orders.opened_at, orders.closed_at))"),
        ],
    ],
)
def test_equivalent_spellings_are_not_reported_as_drift(core_schema, offline, dialects):
    errors = _VALIDATE.validate_document(_metric_model("revenue", dialects), core_schema)

    assert not any(isinstance(error, _VALIDATE.ValidationWarning) and "[Drift]" in error for error in errors)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_known_blind_spot_and_vs_or(core_schema, offline):
    # AND versus OR over the same comparisons is a documented heuristic blind
    # spot: _expression_signature collects predicates as an unordered set without
    # modeling the boolean connective tree.
    errors = _VALIDATE.validate_document(
        _metric_model(
            "revenue",
            [("ANSI_SQL", "a = 1 AND b = 2"), ("SNOWFLAKE", "a = 1 OR b = 2")],
        ),
        core_schema,
    )

    warnings = _warning_text(errors)
    assert not any("revenue" in w and "filter predicates" in w for w in warnings)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize("expression", ["SUM(amount)", "SUM(amount) * 1e1000000"])
def test_single_dialect_metric_has_no_drift(core_schema, offline, expression):
    errors = _VALIDATE.validate_document(
        _metric_model("revenue", [("ANSI_SQL", expression)]),
        core_schema,
    )

    assert not any(isinstance(error, _VALIDATE.ValidationWarning) and "[Drift]" in error for error in errors)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_same_named_metrics_are_not_compared_as_dialects(core_schema, offline):
    # Two metrics that share a name are a [Unique] error, not one metric whose
    # dialects disagree.
    doc = _document([_ORDERS], [])
    doc["metrics"] = [
        {"name": "revenue", "expression": {"sql": "SUM(amount)"}},
        {"name": "revenue", "expression": {"sql": "AVG(amount)"}},
    ]

    errors = _VALIDATE.validate_document(doc, core_schema)

    assert any(error.startswith("[Unique]") for error in errors)
    assert not any("[Drift]" in warning for warning in _warning_text(errors))


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_dataset_metric_source_column_and_field_are_not_the_same_column(core_schema, offline):
    # In a dataset metric, `source.amount` is the raw column and `amount` the
    # declared field, which can be defined differently.
    orders = copy.deepcopy(_ORDERS)
    orders["fields"].append(_field("amount", "amount_cents / 100"))
    orders["metrics"] = [
        {
            "name": "revenue",
            "expression": {
                "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "SUM(amount)"},
                    {"dialect": "SNOWFLAKE", "expression": "SUM(source.amount)"},
                ]
            },
        }
    ]

    errors = _VALIDATE.validate_document(_document([orders], []), core_schema)

    assert any(
        "[Drift]" in warning and "'orders.revenue'" in warning and "source.amount" in warning for warning in _warning_text(errors)
    )


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_unparsed_dialect_is_skipped_without_warning(core_schema, offline):
    # MDX is not parsed by sqlglot. It should not raise, drift, or purity-warn.
    errors = _VALIDATE.validate_document(
        _metric_model("revenue", [("ANSI_SQL", "SUM(amount)"), ("MDX", "[Measures].[Total]")]),
        core_schema,
    )

    warnings = _warning_text(errors)
    assert not any("revenue" in warning and ("[Drift]" in warning or "[Purity]" in warning) for warning in warnings)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_cli_exits_zero_when_document_has_drift_only(run_validator):
    document = _metric_model("revenue", [("ANSI_SQL", "SUM(amount)"), ("SNOWFLAKE", "AVG(amount)")])

    exit_code, output = run_validator(document)

    assert exit_code == 0
    assert "[Drift]" in output
    assert "revenue" in output
    assert "SUM" in output and "AVG" in output
    assert "Validation PASSED" in output
    assert "Validation FAILED" not in output


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_metric_portable_sql_and_dialect_drift_is_reported(core_schema, offline):
    doc = _document([_ORDERS], [])
    doc["metrics"] = [
        {
            "name": "revenue",
            "expression": {
                "sql": "SUM(amount)",
                "dialects": [{"dialect": "SNOWFLAKE", "expression": "AVG(amount)"}],
            },
        }
    ]

    errors = _VALIDATE.validate_document(doc, core_schema)

    warnings = _warning_text(errors)
    assert any("revenue" in w and "SUM" in w and "AVG" in w for w in warnings)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_metric_portable_sql_purity_is_reported(core_schema, offline):
    doc = _document([_ORDERS], [])
    doc["metrics"] = [
        {
            "name": "now_metric",
            "expression": {"sql": "NOW()"},
        }
    ]

    errors = _VALIDATE.validate_document(doc, core_schema)

    warnings = _warning_text(errors)
    assert any("now_metric" in w and "NOW" in w for w in warnings)


# --- field references (core-spec/spec.md "Name Resolution") ---


def _field_reference_findings(document: dict) -> list[str]:
    """Return the two Name Resolution checks together, in the order validate_document runs them."""
    return _VALIDATE.validate_metric_references(document) + _VALIDATE.validate_field_shadowing(document)


def _metric(name: str, expression: str, dialect: str = "ANSI_SQL") -> dict:
    """Build a metric carrying its expression the way a field does."""
    return _field(name, expression, dialect)


def _renamed_model(**overrides) -> dict:
    document = {
        "version": "0.3.0.dev0",
        "name": "m",
        "datasets": [
            {
                "name": "orders",
                "source": "db.s.orders",
                "primary_key": ["order_id"],
                "fields": [
                    _field("order_id", "o_orderkey"),
                    _field("customer_id", "o_custkey"),
                    _field("total_usd", "total_cents / 100.0"),
                ],
            },
            {
                "name": "customers",
                "source": "db.s.customers",
                "primary_key": ["id"],
                "unique_keys": [["email"]],
                "fields": [_field("id", "c_custkey"), _field("email", "c_email")],
            },
        ],
        "relationships": [
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["id"],
            },
        ],
        "metrics": [
            _metric("total_revenue", "SUM(orders.total_usd)"),
        ],
    }
    document.update(overrides)
    return document


def test_accepts_metrics_and_fields_that_name_declared_fields() -> None:
    assert _field_reference_findings(_renamed_model()) == []


def test_field_reference_findings_are_warnings_for_now() -> None:
    document = _renamed_model()
    document["metrics"][0]["expression"]["dialects"][0]["expression"] = "SUM(total_usd)"
    document["datasets"][0]["fields"].append(_field("total", "total_usd"))
    findings = _field_reference_findings(document)
    assert len(findings) == 2
    assert all(isinstance(f, _VALIDATE.ValidationWarning) for f in findings)


def test_warns_on_metric_that_references_a_source_column() -> None:
    document = _renamed_model()
    document["metrics"][0]["expression"]["dialects"][0]["expression"] = "SUM(orders.o_totalprice)"
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "'orders.o_totalprice' is not a declared field of dataset 'orders'" in errors[0]


def _model_metric_over_order_revenue(expression: str, dialect: str = "OSSIE_SQL_2026") -> dict:
    """Build the renamed model with metrics over orders.order_revenue.

    It has a dataset-scoped metric orders.order_revenue and a model-scoped
    metric whose expression and dialect are given. AGG is defined for
    OSSIE_SQL_2026, so that is the default.
    """
    document = _renamed_model()
    document["datasets"][0]["metrics"] = [_metric("order_revenue", "SUM(total_usd)")]
    document["metrics"] = [_metric("total_revenue", expression, dialect)]
    return document


def test_accepts_agg_of_a_dataset_scoped_metric() -> None:
    document = _model_metric_over_order_revenue("AGG(orders.order_revenue) / COUNT(DISTINCT customers.id)")
    assert _field_reference_findings(document) == []


def test_warns_on_a_bare_dataset_scoped_metric_reference() -> None:
    # A bare metric reference is the existing "names another metric" finding, so
    # it stays a warning, with a hint toward AGG.
    errors = _field_reference_findings(_model_metric_over_order_revenue("orders.order_revenue"))
    assert len(errors) == 1
    assert isinstance(errors[0], _VALIDATE.ValidationWarning)
    assert ("'orders.order_revenue' names a dataset-scoped metric; write AGG(orders.order_revenue) to evaluate it") in errors[0]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("AGG(orders.total_usd)", "names a field, and fields are never measures"),
        ("AGG(total_revenue)", "names the model-scoped metric 'total_revenue'"),
        ("AGG(order_revenue)", "AGG(order_revenue) is not qualified"),
        ("AGG(orders.order_revenue + 1)", "AGG(orders.order_revenue + 1): AGG takes a single"),
        ("AGG(orders.missing)", "'missing' is not a dataset-scoped metric of dataset 'orders'"),
        ("AGG(shipments.revenue)", "references unknown dataset 'shipments'"),
        (
            "AGG(orders.order_revenue) FILTER (WHERE orders.customer_id = 1)",
            "has a FILTER (WHERE ...) clause; AGG does not take one",
        ),
    ],
)
def test_rejects_an_invalid_agg_call(expression: str, expected: str) -> None:
    # A misused AGG call is an error, unlike the other reference findings: no
    # model written before AGG was allowed can contain one.
    errors = _field_reference_findings(_model_metric_over_order_revenue(expression))
    assert len(errors) == 1, errors
    assert expected in errors[0]
    assert errors[0].startswith("[Reference] Metric 'total_revenue'")
    assert not isinstance(errors[0], _VALIDATE.ValidationWarning)


def test_rejects_agg_of_a_quoted_metric_that_does_not_match() -> None:
    errors = _field_reference_findings(_model_metric_over_order_revenue('AGG(orders."order_revenue")'))
    assert len(errors) == 1, errors
    assert "'\"order_revenue\"' is not a dataset-scoped metric of dataset 'orders'" in errors[0]
    assert not isinstance(errors[0], _VALIDATE.ValidationWarning)


def test_accepts_agg_of_a_quoted_metric_in_normalized_form() -> None:
    assert _field_reference_findings(_model_metric_over_order_revenue('AGG(orders."ORDER_REVENUE")')) == []
    assert _field_reference_findings(_model_metric_over_order_revenue("AGG(ORDERS.order_revenue)")) == []


def test_rejects_agg_of_quoted_lowercase_total() -> None:
    document = _renamed_model()
    document["datasets"][0]["metrics"] = [_metric("total", "SUM(total_usd)")]
    document["metrics"] = [_metric("grand", 'AGG(orders."total")', "OSSIE_SQL_2026")]
    errors = _field_reference_findings(document)
    assert len(errors) == 1, errors
    assert "'\"total\"' is not a dataset-scoped metric of dataset 'orders'" in errors[0]


def test_accepts_agg_of_quoted_uppercase_total() -> None:
    document = _renamed_model()
    document["datasets"][0]["metrics"] = [_metric("total", "SUM(total_usd)")]
    document["metrics"] = [_metric("grand", 'AGG(orders."TOTAL")', "OSSIE_SQL_2026")]
    assert _field_reference_findings(document) == []


def test_rejects_agg_in_a_field_expression() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] = [{"name": "revenue", "expression": {"sql": "AGG(source.revenue)"}}]

    errors = _VALIDATE.validate_agg_placement(document)

    assert len(errors) == 1
    assert ("Field 'orders.revenue' in model 'm' (OSSIE_SQL_2026) calls AGG in a field expression") in errors[0]
    assert not isinstance(errors[0], _VALIDATE.ValidationWarning)


def _orders_metrics(**metrics: str) -> dict:
    """Build the renamed model with the given dataset-scoped metrics on orders.

    The OSSIE_SQL_2026 metrics are keyed by name. The source of orders stands
    for a measure relation.
    """
    document = _renamed_model()
    document["datasets"][0]["metrics"] = [{"name": name, "expression": {"sql": sql}} for name, sql in metrics.items()]
    return document


def _source_measure_findings(document: dict) -> list[str]:
    """Return the checks that read the AGG calls of dataset-scoped metrics."""
    return _VALIDATE.validate_metric_scoping(document) + _VALIDATE.validate_agg_placement(document)


@pytest.mark.parametrize(
    "expression",
    [
        "AGG(source.revenue)",
        "agg(SOURCE.revenue)",
        'AGG(source."Revenue")',
        "AGG(source.revenue) / COUNT(DISTINCT order_id)",
        "COALESCE(AGG(source.revenue), 0) - AGG(source.refunds)",
        "CASE WHEN COUNT(*) > 0 THEN AGG(source.revenue) END",
    ],
)
def test_accepts_agg_of_a_source_measure(expression: str) -> None:
    assert _source_measure_findings(_orders_metrics(revenue_total=expression)) == []


def test_accepts_agg_of_quoted_source_in_normalized_form() -> None:
    assert _source_measure_findings(_orders_metrics(revenue_total='AGG("SOURCE".revenue)')) == []


def test_rejects_agg_of_quoted_lowercase_source() -> None:
    errors = _source_measure_findings(_orders_metrics(revenue_total='AGG("source".revenue)'))
    assert len(errors) == 1, errors
    assert "AGG in a dataset-scoped metric takes" in errors[0]


def test_accepts_a_model_that_evaluates_source_measures(core_schema: dict) -> None:
    # A model-scoped metric can evaluate a dataset-scoped metric that evaluates
    # a source measure.
    document = _orders_metrics(
        revenue_total="AGG(source.revenue)",
        revenue_per_order="AGG(source.revenue) / COUNT(DISTINCT order_id)",
    )
    document["metrics"] = [
        {
            "name": "revenue_per_customer",
            "expression": {"sql": "AGG(orders.revenue_total) / COUNT(DISTINCT customers.id)"},
        }
    ]

    assert _VALIDATE.validate_document(document, core_schema) == []


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("AGG(total_usd)", "AGG(total_usd): AGG in a dataset-scoped metric takes"),
        ("AGG(order_count)", "AGG(order_count): AGG in a dataset-scoped metric takes"),
        ("AGG(orders.order_count)", "AGG(orders.order_count): AGG in a dataset-scoped metric"),
        ("AGG(source.revenue + 1)", "AGG(source.revenue + 1): AGG in a dataset-scoped metric"),
        ("AGG(DISTINCT source.revenue)", "AGG(DISTINCT source.revenue): AGG in a dataset-scoped"),
        ("AGG(source.*)", "AGG(source.*): AGG in a dataset-scoped metric takes"),
        ("AGG(db.source.revenue)", "AGG(db.source.revenue): AGG in a dataset-scoped metric"),
        ('AGG(source."")', 'AGG(source.""): AGG in a dataset-scoped metric takes'),
        (
            "AGG(source.revenue) FILTER (WHERE order_id = 1)",
            "AGG(source.revenue) has a FILTER (WHERE ...) clause; AGG does not take one",
        ),
    ],
)
def test_rejects_an_invalid_dataset_scoped_agg_call(expression: str, expected: str) -> None:
    # As in a model-scoped metric, a misused AGG call is an error: no model
    # written before AGG was allowed in dataset-scoped metrics can contain one.
    document = _orders_metrics(order_count="COUNT(*)", revenue_total=expression)

    errors = _source_measure_findings(document)

    assert len(errors) == 1, errors
    assert errors[0].startswith("[Scope] Metric 'orders.revenue_total' in model 'm' (OSSIE_SQL_2026): ")
    assert expected in errors[0]
    assert not isinstance(errors[0], _VALIDATE.ValidationWarning)


def test_rejects_agg_of_a_source_measure_when_a_field_is_named_source() -> None:
    document = _orders_metrics(revenue_total="AGG(source.revenue)")
    document["datasets"][0]["fields"].append(_field("source", "src"))

    errors = _source_measure_findings(document)

    assert len(errors) == 1, errors
    assert "declares a field named 'source'" in errors[0]


def test_agg_placement_accepts_agg_in_a_model_scoped_metric() -> None:
    document = _model_metric_over_order_revenue("AGG(orders.order_revenue)")
    assert _VALIDATE.validate_agg_placement(document) == []


def test_agg_placement_skips_other_dialects() -> None:
    # The AGG rules are for OSSIE_SQL_2026; other dialects are not affected.
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("revenue", "AGG(x)", "SNOWFLAKE"))
    document["datasets"][0]["metrics"] = [_metric("revenue_total", "AGG(total_usd)", "SNOWFLAKE")]

    assert _VALIDATE.validate_agg_placement(document) == []
    assert _VALIDATE.validate_metric_scoping(document) == []


def test_dataset_scoped_agg_is_reported_once() -> None:
    # The placement check reports the call; the scoping check does not also
    # report the name inside it as an undeclared field.
    document = _renamed_model()
    document["datasets"][0]["metrics"] = [{"name": "revenue", "expression": {"sql": "AGG(order_revenue)"}}]

    assert _VALIDATE.validate_metric_scoping(document) == []
    assert len(_VALIDATE.validate_agg_placement(document)) == 1


def test_model_metric_agg_checks_skip_other_dialects() -> None:
    # In another dialect, AGG is an ordinary function call: no AGG warnings, and
    # the names inside it are read as field references, as before AGG was defined.
    document = _model_metric_over_order_revenue("AGG(orders.total_usd) FILTER (WHERE orders.customer_id = 1)", "SNOWFLAKE")
    assert _field_reference_findings(document) == []


def _orders_with_field(expression: str | dict) -> dict:
    """Return the renamed model with one more field, revenue, on orders.

    A string is the field's top-level `sql`; a dict is its whole expression object.
    """
    document = _renamed_model()
    if isinstance(expression, str):
        expression = {"sql": expression}
    document["datasets"][0]["fields"].append({"name": "revenue", "expression": expression})
    return document


@pytest.mark.parametrize(
    ("expression", "names"),
    [
        ("SUM(total_cents)", "SUM"),
        ("COUNT(*)", "COUNT"),
        ("COUNT(DISTINCT o_custkey)", "COUNT"),
        ("SUM(total_cents) FILTER (WHERE total_cents > 0)", "SUM"),
        ("CASE WHEN AVG(total_cents) > 100 THEN 1 ELSE 0 END", "AVG"),
        ("total_cents / SUM(total_cents)", "SUM"),
        # Each function is named once, in the order it appears.
        ("MIN(total_cents) + MAX(total_cents) + MIN(total_cents)", "MIN, MAX"),
        ("PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_cents)", "PERCENTILE_CONT"),
        # sqlglot's default dialect reads this one as an unknown function.
        ("APPROX_PERCENTILE(total_cents, 0.95)", "APPROX_PERCENTILE"),
        # Only the outer call has the OVER clause; the inner one still aggregates.
        ("SUM(SUM(total_cents)) OVER ()", "SUM"),
        # An aggregate in a window's own clauses is not the window's function.
        ("ROW_NUMBER() OVER (ORDER BY SUM(total_cents))", "SUM"),
        ("ROW_NUMBER() OVER (PARTITION BY MAX(total_cents) ORDER BY o_orderkey)", "MAX"),
        ("SUM(total_cents) FILTER (WHERE total_cents > AVG(total_cents)) OVER ()", "AVG"),
    ],
)
def test_rejects_an_aggregate_in_a_field_expression(expression: str, names: str) -> None:
    errors = _VALIDATE.validate_field_aggregates(_orders_with_field(expression))

    assert len(errors) == 1, errors
    assert errors[0].startswith(
        f"[Scope] Field 'orders.revenue' in model 'm' (OSSIE_SQL_2026) calls {names} "
        "without an OVER clause in a field expression."
    )
    assert not isinstance(errors[0], _VALIDATE.ValidationWarning)


@pytest.mark.parametrize(
    ("call", "name"),
    [
        # expression_language.md, Aggregation Functions: core, statistical,
        # percentile and approximate.
        ("SUM(x)", "SUM"),
        ("COUNT(x)", "COUNT"),
        ("COUNT(*)", "COUNT"),
        ("COUNT(DISTINCT x)", "COUNT"),
        ("AVG(x)", "AVG"),
        ("MIN(x)", "MIN"),
        ("MAX(x)", "MAX"),
        ("STDDEV(x)", "STDDEV"),
        ("STDDEV_POP(x)", "STDDEV_POP"),
        ("STDDEV_SAMP(x)", "STDDEV_SAMP"),
        ("VARIANCE(x)", "VARIANCE"),
        ("VAR_POP(x)", "VAR_POP"),
        ("VAR_SAMP(x)", "VAR_SAMP"),
        ("MEDIAN(x)", "MEDIAN"),
        ("PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY x)", "PERCENTILE_CONT"),
        ("PERCENTILE_DISC(0.5) WITHIN GROUP (ORDER BY x)", "PERCENTILE_DISC"),
        ("APPROX_COUNT_DISTINCT(x)", "APPROX_COUNT_DISTINCT"),
        ("APPROX_PERCENTILE(x, 0.95)", "APPROX_PERCENTILE"),
    ],
)
def test_field_aggregates_knows_each_aggregate_of_the_expression_language(call: str, name: str) -> None:
    # The name is the one the expression writes, not sqlglot's: VAR_SAMP and
    # VARIANCE parse to the same class.
    errors = _VALIDATE.validate_field_aggregates(_orders_with_field(call))

    assert len(errors) == 1, errors
    assert f" calls {name} without an OVER clause in a field expression" in errors[0]


@pytest.mark.parametrize(
    ("expression", "names"),
    [
        ("var_samp(total_cents)", "VAR_SAMP"),
        ("VAR_SAMP(total_cents) + VARIANCE(total_cents)", "VAR_SAMP, VARIANCE"),
        ("VARIANCE(total_cents) + VAR_SAMP(total_cents)", "VARIANCE, VAR_SAMP"),
        ("  /* sample */ VAR_SAMP(total_cents)", "VAR_SAMP"),
    ],
)
def test_field_aggregates_names_each_function_as_written(expression: str, names: str) -> None:
    errors = _VALIDATE.validate_field_aggregates(_orders_with_field(expression))

    assert len(errors) == 1, errors
    assert f" calls {names} without an OVER clause in a field expression" in errors[0]


@pytest.mark.parametrize(
    "expression",
    [
        # Parsed as written.
        "x + VAR_POP(y)",
        # Parsed as `SELECT {expression}`.
        "begin + VAR_POP(y)",
        "comment IS NOT NULL OR VAR_POP(y) > 0",
        # Parsed with the first token quoted.
        "drop * VAR_POP(y)",
    ],
)
def test_unwindowed_aggregates_reads_the_name_on_each_parse(expression: str) -> None:
    # _parse_sql_expression can parse the text in three ways, which moves the
    # call's position in the string sqlglot read.
    tree, error = _VALIDATE._parse_sql_expression(expression, "OSSIE_SQL_2026", "ctx")
    assert tree is not None, error

    assert _VALIDATE.unwindowed_aggregates(tree, expression) == ["VAR_POP"]
    # Without the text, the name is sqlglot's.
    assert _VALIDATE.unwindowed_aggregates(tree) == ["VARIANCE_POP"]


@pytest.mark.parametrize("expression", ["RANK()", "ROW_NUMBER()", "total_cents - LAG(total_cents)"])
def test_a_field_window_function_without_over_is_reported_once(expression: str) -> None:
    # It is not an aggregation, so the portable SQL check reports it, not this one.
    document = _orders_with_field(expression)

    assert _VALIDATE.validate_field_aggregates(document) == []
    findings = _VALIDATE.validate_semantic_model(document)
    assert len(findings) == 1, findings
    assert findings[0].startswith("[SQL] Field 'orders.revenue' in model 'm' (OSSIE_SQL_2026): ")
    assert findings[0].endswith(" is a window function and needs an OVER clause")


@pytest.mark.parametrize(
    "expression",
    [
        "total_cents",
        "total_cents / 100.0",
        "COALESCE(total_cents, 0)",
        "GREATEST(total_cents, 0)",
        # An aggregate with an OVER clause is a window function: one value per row.
        "total_cents / SUM(total_cents) OVER ()",
        "total_cents / SUM(total_cents) OVER (PARTITION BY o_custkey)",
        "SUM(total_cents) FILTER (WHERE total_cents > 0) OVER ()",
        "COUNT(DISTINCT o_custkey) OVER ()",
        "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY total_cents) OVER (PARTITION BY o_custkey)",
        "ROW_NUMBER() OVER (PARTITION BY o_custkey ORDER BY o_orderkey)",
        # A source column may be named like an aggregate.
        "count",
        "sum",
    ],
)
def test_accepts_a_scalar_or_windowed_field_expression(expression: str) -> None:
    assert _VALIDATE.validate_field_aggregates(_orders_with_field(expression)) == []


def test_field_aggregates_skips_other_dialects() -> None:
    # The check is for OSSIE_SQL_2026, like the other checks of the portable language.
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("revenue", "SUM(total_cents)", "ANSI_SQL"),
        _field("orders_seen", "COUNT(*)", "SNOWFLAKE"),
    ]

    assert _VALIDATE.validate_field_aggregates(document) == []


def test_field_aggregates_reads_an_ossie_sql_dialect_entry() -> None:
    document = _orders_with_field({"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(total_cents)"}]})

    assert len(_VALIDATE.validate_field_aggregates(document)) == 1


def test_field_aggregates_skips_a_superseded_dialect_entry() -> None:
    # The top-level `sql` is authoritative, so it is the one that is checked.
    superseded = {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(total_cents)"}]}

    scalar = _orders_with_field({"sql": "total_cents", **superseded})
    assert _VALIDATE.validate_field_aggregates(scalar) == []

    aggregate = _orders_with_field({"sql": "MAX(total_cents)", **superseded})
    errors = _VALIDATE.validate_field_aggregates(aggregate)
    assert len(errors) == 1, errors
    assert "calls MAX without an OVER clause" in errors[0]


def test_field_aggregates_leaves_metrics_alone() -> None:
    document = _renamed_model()
    document["datasets"][0]["metrics"] = [{"name": "order_revenue", "expression": {"sql": "SUM(total_usd)"}}]
    document["metrics"] = [{"name": "orders_seen", "expression": {"sql": "COUNT(orders.order_id)"}}]

    assert _VALIDATE.validate_field_aggregates(document) == []


def test_agg_in_a_field_expression_is_reported_once() -> None:
    # sqlglot classes AGG as an aggregate too; the placement check owns it.
    document = _orders_with_field("AGG(source.revenue)")

    assert _VALIDATE.validate_field_aggregates(document) == []
    assert len(_VALIDATE.validate_agg_placement(document)) == 1


def test_field_aggregates_leaves_a_query_to_the_query_checks() -> None:
    # An aggregate inside a query belongs to that query. Whether an expression
    # may hold a query at all is a separate question.
    for expression in (
        "(SELECT MAX(o.total_cents) FROM db.s.orders AS o)",
        "total_cents IN (SELECT MAX(o.total_cents) FROM db.s.orders AS o)",
    ):
        assert _VALIDATE.validate_field_aggregates(_orders_with_field(expression)) == []


def test_a_field_aggregate_fails_the_model() -> None:
    findings = _VALIDATE.validate_semantic_model(_orders_with_field("SUM(total_cents)"))

    assert len(findings) == 1, findings
    assert "calls SUM without an OVER clause in a field expression" in findings[0]
    assert not isinstance(findings[0], _VALIDATE.ValidationWarning)


def test_warns_on_metric_with_unqualified_field_reference() -> None:
    document = _renamed_model()
    document["metrics"][0]["expression"]["dialects"][0]["expression"] = "SUM(total_usd)"
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "'total_usd' is not qualified" in errors[0]


def test_warns_on_metric_that_references_an_unknown_dataset() -> None:
    document = _renamed_model()
    document["metrics"][0]["expression"]["dialects"][0]["expression"] = "SUM(order.total_usd)"
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "references unknown dataset 'order'" in errors[0]


def test_metric_check_skips_dialects_that_are_not_sql() -> None:
    document = _renamed_model()
    dax = {"dialect": "DAX", "expression": "SUM(Orders[Total])"}
    document["metrics"][0]["expression"]["dialects"].append(dax)
    assert _field_reference_findings(document) == []


def test_metric_check_leaves_unparseable_sql_to_the_sql_check() -> None:
    document = _renamed_model()
    document["metrics"][0]["expression"]["dialects"][0]["expression"] = "SUM(CASE WHEN"
    assert _field_reference_findings(document) == []


def test_warns_when_a_field_expression_names_another_field_of_its_dataset() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("total_with_tax", "total_usd * 1.1"))
    errors = _field_reference_findings(document)
    assert errors == [
        "[Reference] Warning: Field 'orders.total_with_tax' in model 'm' (ANSI_SQL): "
        "'total_usd' is the source column, not the field 'total_usd' defined as "
        "'total_cents / 100.0'"
    ]
    assert isinstance(errors[0], _VALIDATE.ValidationWarning)


def test_field_expression_may_name_a_field_that_passes_its_column_through() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("amount"))
    document["datasets"][0]["fields"].append(_field("amount_with_tax", "amount * 1.1"))
    assert _field_reference_findings(document) == []


def test_field_expression_may_name_its_own_source_column() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("amount", "amount * 100"))
    assert _field_reference_findings(document) == []


def test_field_shadowing_check_reads_the_same_dialect_as_the_expression() -> None:
    document = _renamed_model()
    amount = {
        "name": "amount",
        "expression": {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": "amount"},
                {"dialect": "SNOWFLAKE", "expression": "AMT"},
            ]
        },
    }
    taxed = {
        "name": "amount_with_tax",
        "expression": {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": "amount * 1.1"},
                {"dialect": "SNOWFLAKE", "expression": "amount * 1.1"},
            ]
        },
    }
    document["datasets"][0]["fields"] += [amount, taxed]
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert errors[0].startswith("[Reference] Warning: Field 'orders.amount_with_tax' in model 'm' (SNOWFLAKE): 'amount'")
    assert "defined as 'AMT'" in errors[0]


def test_field_shadowing_check_skips_dialects_that_are_not_sql_and_unparseable_sql() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(
        {
            "name": "x",
            "expression": {
                "dialects": [
                    {"dialect": "DAX", "expression": "[total_usd] * 2"},
                    {"dialect": "ANSI_SQL", "expression": "total_usd * ("},
                ]
            },
        }
    )
    assert _field_reference_findings(document) == []


@pytest.mark.parametrize("definition", ["(amount)", '"amount"', "amount -- the amount", "((amount))"])
def test_pass_through_is_read_from_the_parsed_definition(definition: str) -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("amount", definition),
        _field("amount_cents", "amount * 100"),
    ]
    assert _field_reference_findings(document) == []


def test_pass_through_compares_names_without_regard_to_case() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("amount", "AMOUNT"),
        _field("amount_cents", "amount * 100"),
    ]
    assert _field_reference_findings(document) == []


def test_quoted_pass_through_does_not_match_an_unquoted_field() -> None:
    # "amount" is the lowercase identifier; field amount is the regular name AMOUNT.
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("amount", '"amount"', "OSSIE_SQL_2026"),
        _field("amount_cents", "amount * 100", "OSSIE_SQL_2026"),
    ]
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "'amount' is the source column, not the field 'amount' defined as '\"amount\"'" in errors[0]


def test_field_shadowing_check_compares_names_without_regard_to_case() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("total_eur", "Total_USD * 0.9"))

    errors = _field_reference_findings(document)

    assert len(errors) == 1
    assert "'Total_USD' is the source column, not the field 'total_usd'" in errors[0]


def test_quoted_identifier_does_not_shadow_a_regular_field() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("total_eur", '"total_usd" * 0.9', "OSSIE_SQL_2026"))
    assert _field_reference_findings(document) == []


def test_quoted_identifier_in_vendor_dialect_shadows_without_regard_to_case() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("total_eur", '"total_usd" * 0.9', "POSTGRES"))
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "'\"total_usd\"' is the source column, not the field 'total_usd'" in errors[0]


def test_field_shadowing_check_reads_a_date_part_as_a_unit_not_a_column() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("day", "EXTRACT(DAY FROM order_date)"),
        _field("month", "EXTRACT(MONTH FROM order_date)"),
        _field("days_open", "DATEDIFF(day, opened_at, closed_at)"),
        _field("months_open", "DATEDIFF(month, opened_at, closed_at)"),
    ]
    assert _field_reference_findings(document) == []


def test_field_shadowing_check_needs_a_sql_definition_to_compare() -> None:
    document = _renamed_model()
    dax_only = _field("amount", "[amount]", dialect="DAX")
    document["datasets"][0]["fields"] += [dax_only, _field("amount_cents", "amount * 100")]
    assert _field_reference_findings(document) == []


def test_field_shadowing_check_falls_back_to_any_dialect_of_the_other_field() -> None:
    document = _renamed_model()
    snowflake_only = _field("amount", dialect="SNOWFLAKE")
    document["datasets"][0]["fields"] += [snowflake_only, _field("amount_cents", "amount * 100")]
    assert _field_reference_findings(document) == []


def test_field_shadowing_check_ignores_qualified_names_and_reports_a_name_once() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("total_usd2", "orders.total_usd * 2"),
        _field("total_usd3", "total_usd + total_usd"),
    ]
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "Field 'orders.total_usd3'" in errors[0]


def test_metric_that_names_another_metric_is_told_so() -> None:
    document = _renamed_model()
    document["metrics"].append(
        {
            "name": "revenue_share",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "total_revenue / SUM(orders.total_usd)"}]},
        }
    )
    errors = _field_reference_findings(document)
    assert errors == [
        "[Reference] Warning: Metric 'revenue_share' in model 'm' (ANSI_SQL): "
        "'total_revenue' names the metric 'total_revenue'; metric expressions cannot refer "
        "to other metrics"
    ]


def test_metric_check_skips_star_and_reports_more_than_two_parts() -> None:
    document = _renamed_model()
    expression = "COUNT(orders.*) + SUM(db.orders.total_usd)"
    document["metrics"][0]["expression"]["dialects"][0]["expression"] = expression
    errors = _field_reference_findings(document)
    assert errors == [
        "[Reference] Warning: Metric 'total_revenue' in model 'm' (ANSI_SQL): "
        "'db.orders.total_usd' has more than two parts; metric expressions refer to fields "
        "as dataset.field"
    ]


def test_field_references_are_looked_up_on_the_named_dataset_only() -> None:
    document = _renamed_model()
    # id is a field of customers, not of orders.
    document["metrics"][0]["expression"]["dialects"][0]["expression"] = "SUM(orders.id)"
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "'orders.id' is not a declared field of dataset 'orders'" in errors[0]


def test_every_undeclared_name_is_reported_and_each_only_once() -> None:
    document = _renamed_model()
    document["metrics"][0]["expression"]["dialects"] = [
        {"dialect": "ANSI_SQL", "expression": "SUM(orders.a) + SUM(orders.b)"},
        {"dialect": "SNOWFLAKE", "expression": "SUM(orders.c)"},
    ]
    errors = _field_reference_findings(document)
    assert [e.split("'")[5] for e in errors] == ["orders.a", "orders.b", "orders.c"]


def test_field_shadowing_check_reports_the_other_dialect_definition_it_fell_back_to() -> None:
    document = _renamed_model()
    snowflake_only = _field("amount", "AMT", dialect="SNOWFLAKE")
    document["datasets"][0]["fields"] += [snowflake_only, _field("amount_cents", "amount * 100")]
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "(ANSI_SQL): 'amount' is the source column" in errors[0]
    assert "defined as 'AMT'" in errors[0]


def test_field_shadowing_check_looks_at_the_same_dataset_only() -> None:
    document = _renamed_model()
    # email is a field of customers, so in an orders expression it is only a source column.
    document["datasets"][0]["fields"].append(_field("email_domain", "SPLIT_PART(email, '@', 2)"))
    assert _field_reference_findings(document) == []


@pytest.mark.parametrize("definition", ["t.amount", "CAST(amount AS INT)"])
def test_pass_through_is_the_bare_column_and_nothing_else(definition: str) -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("amount", definition),
        _field("amount_cents", "amount * 100"),
    ]
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert f"defined as '{definition}'" in errors[0]


def test_pass_through_may_carry_an_alias() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("amount", "amount AS a"),
        _field("amount_cents", "amount * 100"),
    ]
    assert _field_reference_findings(document) == []


def test_field_shadowing_check_needs_a_definition_it_can_parse() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("amount", "amount * ("),
        _field("amount_cents", "amount * 100"),
    ]
    assert _field_reference_findings(document) == []


def test_qualified_metric_name_is_reported_as_a_missing_field() -> None:
    document = _renamed_model()
    document["metrics"].append(
        _metric("share", "SUM(orders.total_revenue)"),
    )
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "'orders.total_revenue' is not a declared field of dataset 'orders'" in errors[0]


@pytest.mark.parametrize(
    "name, expression",
    [
        ("total_revenue", "total_revenue + 1"),  # the metric's own name
        ("total_usd_sum", "SUM(total_usd)"),  # a field somewhere in the model
    ],
)
def test_bare_name_of_this_metric_or_a_field_is_reported_as_unqualified(name, expression) -> None:
    document = _renamed_model()
    document["metrics"] = [_metric(name, expression)]
    if name == "total_usd_sum":
        document["metrics"].append(_renamed_model()["metrics"][0] | {"name": "total_usd"})
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert "is not qualified" in errors[0]


def test_renamed_fields_example_passes_the_field_reference_check() -> None:
    import yaml

    example = Path(__file__).parents[2] / "examples" / "renamed_fields.yaml"
    assert _field_reference_findings(yaml.safe_load(example.read_text())) == []


@pytest.mark.parametrize(
    "expression",
    [
        "DATEDIFF(day, orders.shipped, orders.ordered)",
        "DATEDIFF(month, orders.shipped, orders.ordered)",
        "DATEDIFF(days, orders.shipped, orders.ordered)",
        "TIMESTAMPDIFF(MINUTE, orders.shipped, orders.ordered)",
        "TIMESTAMPDIFF(MONTH, orders.shipped, orders.ordered)",
        "DATEADD(day, 1, orders.ordered)",
        "DATEADD(dd, 1, orders.ordered)",
        "DATEADD(month, 1, orders.ordered)",
        "DATE_PART(year, orders.ordered)",
        "DATE_PART(month, orders.ordered)",
    ],
)
def test_metric_check_reads_a_date_part_as_a_unit_not_a_column(expression) -> None:
    document = _renamed_model(metrics=[_metric("age", expression)])
    document["datasets"][0]["fields"] += [_field("shipped"), _field("ordered")]

    assert _VALIDATE.validate_metric_references(document) == []


def test_metric_check_reads_the_redshift_pgdate_part_unit_as_a_unit() -> None:
    # The redshift reader leaves PGDATE_PART untyped, so without an entry in
    # DATE_PART_FUNCTIONS `day` would be read as an unqualified column.
    document = _renamed_model(metrics=[_metric("latest_day", "MAX(PGDATE_PART(day, orders.ordered))", "REDSHIFT")])
    document["datasets"][0]["fields"].append(_field("ordered"))

    assert _VALIDATE.validate_metric_references(document) == []


def test_metric_check_warns_on_an_unqualified_pgdate_part_date_expression() -> None:
    # Only the unit (the first argument) is read as a date part; a bare name in
    # the date-expression slot stays an unqualified column, even when it is
    # spelled like a date part, so `year` here is still reported.
    document = _renamed_model(metrics=[_metric("latest_day", "MAX(PGDATE_PART(day, year))", "REDSHIFT")])

    findings = _VALIDATE.validate_metric_references(document)
    assert any("'year' is not qualified" in str(finding) for finding in findings)


def test_metric_check_compares_names_without_regard_to_case() -> None:
    document = _renamed_model(
        metrics=[
            _metric("Revenue", "SUM(ORDERS.TOTAL_USD)"),
            _metric("revenue_eur", "SUM(ORDERS.TOTAL_EUR)"),
            _metric("doubled", "revenue * 2"),
        ]
    )

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 2
    assert "'ORDERS.TOTAL_EUR' is not a declared field of dataset 'ORDERS'" in errors[0]
    assert "'revenue' names the metric 'Revenue'" in errors[1]


def test_metric_quoted_identifier_matches_as_written() -> None:
    document = _renamed_model(
        metrics=[
            _metric("ok_quoted", 'SUM(orders."TOTAL_USD")', "OSSIE_SQL_2026"),
            _metric("ok_unquoted", "SUM(Orders.Total_Usd)", "OSSIE_SQL_2026"),
            _metric("bad_quoted", 'SUM(orders."total_usd")', "OSSIE_SQL_2026"),
            _metric("bad_amount", 'SUM(orders."amount")', "OSSIE_SQL_2026"),
        ]
    )

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 2
    assert all(isinstance(e, _VALIDATE.ValidationWarning) for e in errors)
    assert any("'orders.\"total_usd\"' is not a declared field" in e for e in errors)
    assert any("'orders.\"amount\"' is not a declared field" in e for e in errors)


def test_metric_vendor_dialect_compares_quoted_names_without_regard_to_case() -> None:
    # Quoted versus unquoted matching is DB specific; vendor dialects keep the
    # case-insensitive comparison they had before the OSSIE_SQL_2026 quoting rule.
    document = _renamed_model(
        metrics=[
            _metric("pg", 'SUM(orders."total_usd")', "POSTGRES"),
            _metric("bq", "SUM(orders.`total_usd`)", "BIGQUERY"),
        ]
    )

    assert _VALIDATE.validate_metric_references(document) == []


def test_datasets_that_differ_only_in_case_pool_their_fields() -> None:
    # Whether two such names may coexist is validate_unique_names' question;
    # the Name Resolution checks must not warn about either one's own fields.
    document = _renamed_model()
    document["datasets"].append(
        {"name": "Orders", "source": "db.s.orders2", "primary_key": ["order_id"], "fields": [_field("oid", "k")]}
    )
    assert _field_reference_findings(document) == []


def test_a_field_passing_through_its_own_column_in_another_case_is_not_shadowing() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("Total_USD", "total_usd"))

    assert _field_reference_findings(document) == []


def test_fields_that_differ_only_in_case_compare_against_the_first_declared() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("Total_USD", "total_cents"),
        _field("total_eur", "total_usd * 0.9"),
    ]

    errors = _field_reference_findings(document)

    assert len(errors) == 1
    assert "not the field 'total_usd' defined as 'total_cents / 100.0'" in errors[0]


def test_a_column_named_like_a_unit_outside_the_unit_position_is_a_column() -> None:
    document = _renamed_model(
        metrics=[
            _metric("age", "SUM(DATEDIFF(day, ms, orders.total_usd))"),
            _metric("month_start", "MIN(DATE_TRUNC(month, d))"),
        ]
    )
    document["datasets"][0]["fields"] += [_field("q", "quarter_start"), _field("closed_at")]
    document["datasets"][0]["fields"].append(_field("open_days", "DATEDIFF(day, q, closed_at)"))

    errors = _field_reference_findings(document)

    assert [e.split(": ", 2)[2] for e in errors] == [
        "'ms' is not qualified; metric expressions refer to fields as dataset.field",
        "'d' is not qualified; metric expressions refer to fields as dataset.field",
        "'q' is the source column, not the field 'q' defined as 'quarter_start'",
    ]


def test_a_parser_that_knows_the_unit_leaves_the_date_arguments_as_columns() -> None:
    # Snowflake reads the unit itself and puts the last date argument where
    # the default parser puts the unit, so `d` must still be seen.
    document = _renamed_model(
        metrics=[
            _metric("ok", "SUM(DATEDIFF(day, orders.total_usd, orders.total_usd))", dialect="SNOWFLAKE"),
            _metric("bad", "SUM(DATEDIFF(day, orders.total_usd, d))", dialect="SNOWFLAKE"),
            _metric("unlisted", "SUM(DATEDIFF(nanoseconds, orders.total_usd, d))", dialect="SNOWFLAKE"),
        ]
    )

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 2
    assert all("'d' is not qualified" in e for e in errors)


def test_metric_check_still_reports_a_bare_column_named_like_a_unit() -> None:
    document = _renamed_model(metrics=[_metric("days", "SUM(day)")])

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 1
    assert "'day' is not qualified" in errors[0]


@pytest.mark.parametrize(
    "document",
    [
        {
            "version": "0.3.0.dev0",
            "name": "m",
            "datasets": None,
            "metrics": None,
            "relationships": None,
        },
        {
            "version": "0.3.0.dev0",
            "name": "m",
            "datasets": [
                {
                    "name": "orders",
                    "primary_key": [["a"]],
                    "fields": [{"name": "a", "expression": "a"}],
                },
                {"name": None, "fields": [{"name": "b", "expression": {"dialects": None}}]},
            ],
            "metrics": [{"name": "m", "expression": "SUM(orders.a)"}, "not a metric"],
            "relationships": [{"name": "r", "from": ["orders"], "to": "orders", "to_columns": "a"}],
        },
    ],
)
def test_field_reference_checks_tolerate_shapes_the_schema_rejects(document) -> None:
    assert _field_reference_findings(document) == []


def test_cli_prints_reference_warnings_and_still_passes(run_validator) -> None:
    document = _renamed_model()
    document["metrics"][0]["expression"]["dialects"][0]["expression"] = "SUM(total_usd)"

    exit_code, output = run_validator(document)

    assert exit_code == 0
    assert "Validation PASSED" in output
    assert (
        "[Reference] Warning: Metric 'total_revenue' in model 'm' (ANSI_SQL): 'total_usd' is not "
        "qualified; metric expressions refer to fields as dataset.field"
    ) in output


def test_metric_check_reads_the_portable_sql_form() -> None:
    document = _renamed_model(metrics=[{"name": "revenue", "expression": {"sql": "SUM(total_usd)"}}])

    errors = _VALIDATE.validate_metric_references(document)

    assert errors == [
        "[Reference] Warning: Metric 'revenue' in model 'm' (OSSIE_SQL_2026): 'total_usd' is not "
        "qualified; metric expressions refer to fields as dataset.field"
    ]


def test_metric_check_skips_a_dialect_entry_the_portable_sql_supersedes() -> None:
    # The top-level `sql` is what a client reads, so the OSSIE_SQL_2026 dialect
    # entry beside it is not checked twice under a second name.
    document = _renamed_model(
        metrics=[
            {
                "name": "revenue",
                "expression": {
                    "sql": "SUM(orders.total_usd)",
                    "dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(total_usd)"}],
                },
            }
        ]
    )

    assert _VALIDATE.validate_metric_references(document) == []


def test_reference_checks_skip_a_dialect_this_validator_does_not_parse() -> None:
    # A dialect outside SQLGLOT_DIALECTS is carried, not read: validate_sql warns
    # once that it went unchecked, and these checks say nothing about it.
    document = _renamed_model(metrics=[_metric("revenue", "SUM(total_usd)", dialect="DUCKDB")])
    document["datasets"][0]["fields"].append(_field("total_eur", "total_usd * 0.9", dialect="DUCKDB"))

    assert _field_reference_findings(document) == []


def test_field_shadowing_check_reads_the_portable_sql_form() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append({"name": "total_eur", "expression": {"sql": "total_usd * 0.9"}})

    errors = _VALIDATE.validate_field_shadowing(document)

    assert errors == [
        "[Reference] Warning: Field 'orders.total_eur' in model 'm' (OSSIE_SQL_2026): 'total_usd' is "
        "the source column, not the field 'total_usd' defined as 'total_cents / 100.0'"
    ]


def test_metric_check_reads_a_column_in_the_unit_slot_as_a_column() -> None:
    # Under the default parser the last DATEDIFF argument lands in `unit` as a
    # Var, so without this it would escape every check.
    document = _renamed_model(
        metrics=[
            _metric("age", "SUM(DATEDIFF(day, orders.total_usd, closed_at))"),
        ]
    )

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 1
    assert "'CLOSED_AT' is not qualified" in errors[0]


def test_field_shadowing_check_reads_a_column_in_the_unit_slot_as_a_column() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        _field("closed_at", "closed_ts"),
        _field("open_days", "DATEDIFF(day, opened_at, closed_at)"),
    ]

    errors = _VALIDATE.validate_field_shadowing(document)

    assert errors == [
        "[Reference] Warning: Field 'orders.open_days' in model 'm' (ANSI_SQL): 'CLOSED_AT' is the "
        "source column, not the field 'closed_at' defined as 'closed_ts'"
    ]


def test_validate_module_imports_without_sqlglot(monkeypatch) -> None:
    # A None entry in sys.modules makes `import sqlglot` raise ImportError, the
    # way a missing install does; the module must still load and say so.
    import sys

    for name in [m for m in sys.modules if m == "sqlglot" or m.startswith("sqlglot.")]:
        monkeypatch.setitem(sys.modules, name, None)
    spec = spec_from_file_location("ossie_validate_without_sqlglot", _VALIDATE_PATH)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.SQLGLOT_AVAILABLE is False


@pytest.mark.parametrize(
    "expression, dialect",
    [
        ("SUM(DATEDIFF(day, orders.total_usd, '2026-01-01'))", "ANSI_SQL"),
        ("SUM(DATEDIFF(day, orders.total_usd, 1))", "ANSI_SQL"),
        ("SUM(DATE_DIFF(orders.total_usd, orders.total_usd, ISOWEEK))", "BIGQUERY"),
        ("SUM(DATEDIFF(nanoseconds, orders.total_usd, orders.total_usd))", "SNOWFLAKE"),
        ("SUM(DATEDIFF(day, orders.total_usd, orders.d))", "ANSI_SQL"),
    ],
)
def test_a_literal_or_an_unlisted_unit_in_the_unit_slot_is_not_a_column(expression, dialect) -> None:
    # A literal the default parser leaves in `unit`, a unit alias the table lacks
    # under a parser that knows the function, and a declared column named like
    # a unit alias in the last slot: none of them is a bare column.
    document = _renamed_model(metrics=[_metric("x", expression, dialect=dialect)])
    document["datasets"][0]["fields"].append(_field("d", "delivered_at"))

    assert _VALIDATE.validate_metric_references(document) == []


def test_a_repeated_reference_is_reported_once_per_expression() -> None:
    document = _renamed_model(
        metrics=[
            _metric("ratio", "SUM(orders.zz) / COUNT(orders.zz)"),
            _metric("mean", "SUM(total_usd) / COUNT(total_usd)"),
        ]
    )

    errors = _VALIDATE.validate_metric_references(document)

    assert [e.split(": ", 2)[2] for e in errors] == [
        "'orders.zz' is not a declared field of dataset 'orders'",
        "'total_usd' is not qualified; metric expressions refer to fields as dataset.field",
    ]


@pytest.mark.parametrize(
    ("function", "dialect", "reported"),
    [("DATE_ADD", "ANSI_SQL", "'SHIPPED'"), ("DATEADD", "SNOWFLAKE", "'shipped'")],
)
def test_date_add_reads_its_unit_as_a_unit_and_its_date_as_a_column(function, dialect, reported) -> None:
    # The default parser keeps DATE_ADD's unit in `this` and its date in `unit`;
    # Snowflake's reads DATEADD's date into `this` and keeps its spelling.
    document = _renamed_model(
        metrics=[
            _metric("ok", f"MAX({function}(day, 1, orders.total_usd))", dialect=dialect),
            _metric("bad", f"MAX({function}(day, 1, shipped))", dialect=dialect),
        ]
    )

    errors = _VALIDATE.validate_metric_references(document)

    assert [e.split(": ", 2)[2] for e in errors] == [
        f"{reported} is not qualified; metric expressions refer to fields as dataset.field",
    ]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("SUM(DATEDIFF(x, orders.total_usd, z))", ["'x' is not qualified"]),
        ("SUM(DATEDIFF(orders.day, orders.total_usd, z))", ["'orders.day' is not a declared field"]),
    ],
    ids=["first-argument-not-a-unit", "first-argument-qualified"],
)
def test_the_unit_slot_is_read_back_only_behind_a_bare_unit(expression, expected) -> None:
    # Only a bare date-part name in `this` means the parser shifted the arguments.
    document = _renamed_model(metrics=[_metric("m1", expression)])

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == len(expected)
    assert all(want in got for want, got in zip(expected, errors, strict=False))


def test_field_shadowing_check_falls_back_past_an_empty_definition() -> None:
    # An empty ANSI_SQL entry gives nothing to compare, so the other dialect is read.
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        {
            "name": "total",
            "expression": {
                "dialects": [
                    {"dialect": "ANSI_SQL", "expression": ""},
                    {"dialect": "SNOWFLAKE", "expression": "total_cents / 100"},
                ]
            },
        },
        _field("doubled", "total * 2"),
    ]

    errors = _VALIDATE.validate_field_shadowing(document)

    assert [e.split(": ", 2)[2] for e in errors] == [
        "'total' is the source column, not the field 'total' defined as 'total_cents / 100'",
    ]


@pytest.mark.parametrize("expression", ["MAX(DATE_ADD(d, 7))", "SUM(DATEDIFF(d, orders.total_usd))"])
def test_a_two_argument_date_function_reads_its_first_argument_as_a_column(expression) -> None:
    # With no unit slot filled, a name like `d` is the date, not a unit.
    document = _renamed_model(metrics=[_metric("m1", expression)])

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 1
    assert "'d' is not qualified" in errors[0]


def test_postgres_and_redshift_are_parsed_as_sql() -> None:
    # Both are SQL dialects sqlglot reads, so an expression in either is
    # parsed the way SNOWFLAKE, BIGQUERY and DATABRICKS are, not skipped.
    assert _VALIDATE.SQLGLOT_DIALECTS["POSTGRES"] == "postgres"
    assert _VALIDATE.SQLGLOT_DIALECTS["REDSHIFT"] == "redshift"
    assert "POSTGRES" not in _VALIDATE.NON_SQL_DIALECTS
    assert "REDSHIFT" not in _VALIDATE.NON_SQL_DIALECTS


def _regex_match_document(dialect: str) -> dict:
    # `~` is the Postgres regular-expression match, which Redshift shares and
    # sqlglot's default reader rejects, so a dialect passing it shows its own
    # reader was used. (`::` and ILIKE parse under the default too.)
    body = "orders.status ~ '^deliv'"
    expression = {"dialects": [{"dialect": dialect, "expression": body}]}
    dataset = {**_ORDERS, "fields": [{"name": "delivered", "expression": expression}]}
    return _document([dataset], [])


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_a_regex_match_is_valid_sql_under_postgres() -> None:
    assert _VALIDATE.validate_sql(_regex_match_document("POSTGRES")) == []


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_a_regex_match_is_valid_sql_under_redshift() -> None:
    assert _VALIDATE.validate_sql(_regex_match_document("REDSHIFT")) == []


def _is_error_not_warning(result: list) -> bool:
    # A registered dialect is parsed, so a body sqlglot cannot read is an error
    # rather than the unchecked-dialect warning an unregistered token gets.
    return result != [] and not any(isinstance(item, _VALIDATE.ValidationWarning) for item in result)


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_the_regex_match_is_invalid_under_ansi_sql() -> None:
    # The control for the two tests above: the same body under ANSI_SQL is an error.
    assert _is_error_not_warning(_VALIDATE.validate_sql(_regex_match_document("ANSI_SQL")))


def _unparseable_document(dialect: str) -> dict:
    expression = {"dialects": [{"dialect": dialect, "expression": "SELECT FROM WHERE"}]}
    dataset = {**_ORDERS, "fields": [{"name": "broken", "expression": expression}]}
    return _document([dataset], [])


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_invalid_sql_is_reported_under_postgres() -> None:
    assert _is_error_not_warning(_VALIDATE.validate_sql(_unparseable_document("POSTGRES")))


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_invalid_sql_is_reported_under_redshift() -> None:
    assert _is_error_not_warning(_VALIDATE.validate_sql(_unparseable_document("REDSHIFT")))


class _CollectRecords(logging.Handler):
    """Keeps whatever sqlglot's own logger still emits."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextlib.contextmanager
def _sqlglot_log():
    """Collect sqlglot's log records, with its logger pinned at WARNING.

    The handler hangs off sqlglot's own logger rather than the root one, so
    what arrives is what that logger's level allowed through, which is the
    thing under test. The level is pinned rather than left to whatever the
    session configured, so a warning would certainly be emitted if nothing
    held it back.
    """
    logger = logging.getLogger("sqlglot")
    handler = _CollectRecords()
    previous = logger.level
    logger.setLevel(logging.WARNING)
    logger.addHandler(handler)
    try:
        yield handler.records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)


# Columns named with a SQL keyword. sqlglot parses neither as an expression:
# it falls back to Command and says so, and validate.py reads both as columns
# anyway, the first by quoting the keyword, the second through SELECT {expr}.
_KEYWORD_COLUMN_EXPRESSIONS = ("drop * 2", "comment IS NOT NULL")


def _keyword_column_document() -> dict:
    fields = [_field(f"f{index}", expression) for index, expression in enumerate(_KEYWORD_COLUMN_EXPRESSIONS)]
    return _document([{**_ORDERS, "fields": fields}], [])


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
@pytest.mark.parametrize("expression", _KEYWORD_COLUMN_EXPRESSIONS)
def test_sqlglot_warns_about_a_keyword_named_column_on_its_own(expression: str) -> None:
    """The control: these are the parses the validator has to keep quiet.

    Without this, the tests below would still pass on the day sqlglot stops
    warning, and would no longer be checking anything.
    """
    with _sqlglot_log() as records:
        _VALIDATE.sqlglot.parse(expression)

    assert [record.getMessage() for record in records] != []


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_a_keyword_named_column_validates_without_a_parser_warning() -> None:
    document = _keyword_column_document()

    with _sqlglot_log() as records:
        findings = _VALIDATE.validate_sql(document)

    assert findings == []
    assert [record.getMessage() for record in records] == []


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_an_emptiness_check_leaves_no_parser_warning() -> None:
    # The mapping checks read an expression through this, and a keyword-named
    # column is no emptier than any other.
    with _sqlglot_log() as records:
        empty = _VALIDATE.expression_is_empty("drop * 2")

    assert empty is False
    assert [record.getMessage() for record in records] == []


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_the_sqlglot_logger_is_left_as_it_was_found() -> None:
    # Quieting the probes must not outlive them: whoever imports this module
    # keeps the logging they configured.
    logger = logging.getLogger("sqlglot")
    before = logger.level

    _VALIDATE.validate_sql(_keyword_column_document())

    assert logger.level == before


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_another_thread_keeps_its_sqlglot_warnings_while_one_probes(monkeypatch) -> None:
    """Quieting a probe must not reach past the thread doing it.

    Holding a shared logger level down would silence sqlglot for the whole
    process while any one validation ran, so a caller validating documents in
    a pool, or using sqlglot for anything of its own, would lose warnings it
    never asked to lose.

    The worker is stopped inside a probe, on its first parse, and both threads
    log while it is held there. That the worker's own message is dropped is
    what proves it really is inside a probe, so the other assertion cannot
    pass by looking at the wrong moment.
    """
    probing = threading.Event()
    release = threading.Event()
    parse_one = _VALIDATE.sqlglot.parse_one

    def stop_in_the_first_probe(*args, **kwargs):
        if not probing.is_set():
            logging.getLogger("sqlglot").warning("the worker, mid-probe")
            probing.set()
            assert release.wait(10), "the test never released the worker"
        return parse_one(*args, **kwargs)

    monkeypatch.setattr(_VALIDATE.sqlglot, "parse_one", stop_in_the_first_probe)
    worker = threading.Thread(target=_VALIDATE.validate_sql, args=(_keyword_column_document(),), daemon=True)

    with _sqlglot_log() as records:
        worker.start()
        try:
            assert probing.wait(10), "the worker never reached a probe"
            logging.getLogger("sqlglot").warning("this thread, probing nothing")
        finally:
            release.set()
            worker.join(10)
        messages = [record.getMessage() for record in records]

    assert "this thread, probing nothing" in messages
    assert "the worker, mid-probe" not in messages


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_a_validation_that_finishes_does_not_unquiet_one_still_running(monkeypatch) -> None:
    """Two probes overlapping must not hand each other the wrong state back.

    Threads that each saved and restored a shared logger level would restore
    each other's. `first` saves the level it found, `second` saves the one
    `first` had already set, so when `first` finishes it unquiets `second`
    mid-probe, and when `second` finishes it leaves sqlglot quiet for the rest
    of the process.

    Each thread is stopped inside its own probe, on its first parse, and let
    out in the order that does the damage: `second` enters while `first` is
    still inside, and `first` finishes while `second` is still inside.
    """
    inside = {name: threading.Event() for name in ("first", "second")}
    first_finished = threading.Event()
    parse_one = _VALIDATE.sqlglot.parse_one

    def stop_in_the_first_probe(*args, **kwargs):
        name = threading.current_thread().name
        if name in inside and not inside[name].is_set():
            inside[name].set()
            if name == "first":
                # Hold the outer probe open until the inner one has started.
                assert inside["second"].wait(10), "second never reached a probe"
            else:
                # Report from inside a probe that outlived the other one.
                assert first_finished.wait(10), "first never finished"
                logging.getLogger("sqlglot").warning("second, still probing")
        return parse_one(*args, **kwargs)

    monkeypatch.setattr(_VALIDATE.sqlglot, "parse_one", stop_in_the_first_probe)
    document = _keyword_column_document()

    def validate_then(signal=None):
        _VALIDATE.validate_sql(document)
        if signal is not None:
            signal.set()

    first = threading.Thread(target=validate_then, args=(first_finished,), name="first", daemon=True)
    second = threading.Thread(target=validate_then, name="second", daemon=True)

    with _sqlglot_log() as records:
        first.start()
        assert inside["first"].wait(10), "first never reached a probe"
        second.start()
        first.join(10)
        second.join(10)
        assert not first.is_alive() and not second.is_alive()
        level_after = logging.getLogger("sqlglot").level

    assert [record.getMessage() for record in records] == []
    assert level_after == logging.WARNING
