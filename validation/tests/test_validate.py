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

import copy
import json
import re
import urllib.request
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
validate_relationship_column_arity = _VALIDATE.validate_relationship_column_arity


@pytest.fixture
def core_schema() -> dict:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    return json.loads(schema_path.read_text())


def _document(datasets: list[dict], relationships: list[dict]) -> dict:
    # Deep-copied so a test can edit the document it gets back without
    # changing the module-level fixtures below for every test after it.
    return {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": copy.deepcopy(datasets),
        "relationships": copy.deepcopy(relationships),
    }


def _field(name: str, expression: str | None = None, dialect: str = "ANSI_SQL") -> dict:
    """A field defined by one expression; by default the same-named source column."""
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


@pytest.mark.parametrize("uri", [
    "https://github.com/apache/ossie/core-spec/ossie-schema.json",
    "https://raw.githubusercontent.com/apache/ossie/main/core-spec/ossie-schema.json",
])
@pytest.mark.parametrize("fragment, document", [
    ("", _document([_ORDERS], [])),
    ("#/$defs/AIContext", {"instructions": "Use for sales analysis"}),
])
def test_core_schema_references_resolve_offline(offline, uri, fragment, document):
    assert _VALIDATE.validate_schema(document, {"$ref": uri + fragment}) == []


def test_unresolvable_reference_is_a_validation_error(offline):
    uri = "https://example.invalid/unknown-schema.json"

    errors = _VALIDATE.validate_schema({}, {"$ref": uri})

    assert errors == [f"[Schema] Cannot resolve schema reference: {uri}"]


def test_rejects_empty_root_datasets(core_schema: dict) -> None:
    errors = _VALIDATE.validate_schema(_document([], []), core_schema)

    assert errors == ["[Schema] datasets: [] should be non-empty"]


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

    assert any(
        "Additional properties are not allowed" in error and unknown_property in error
        for error in errors
    )


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
    assert validate_relationship_column_arity(data) == []
    assert _VALIDATE.validate_sql(data) == []


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

    assert _VALIDATE.validate_schema(document, core_schema) == [
        f"[Schema] {path}: '' should be non-empty"
    ]


def test_schema_rejects_empty_relationship_endpoints(core_schema: dict) -> None:
    document = _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["id"])])
    document["relationships"][0]["to"] = ""

    assert _VALIDATE.validate_schema(document, core_schema) == [
        "[Schema] relationships -> 0 -> to: '' should be non-empty"
    ]


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


def test_ossie_sql_2026_maps_to_the_ansi_default() -> None:
    # OSSIE_SQL_2026 is ANSI-SQL-compatible, so it resolves to the same sqlglot
    # default (None) as ANSI_SQL, and is parseable rather than a non-SQL dialect.
    assert (
        _VALIDATE.SQLGLOT_DIALECTS["OSSIE_SQL_2026"]
        is _VALIDATE.SQLGLOT_DIALECTS["ANSI_SQL"]
    )
    assert "OSSIE_SQL_2026" not in _VALIDATE.NON_SQL_DIALECTS


def test_holistics_aql_is_skipped_rather_than_parsed_as_sql() -> None:
    # AQL is relationship-aware and is not SQL, so sqlglot cannot parse it.
    # Skipping it is what lets a Holistics field carry its body at all, the way
    # MDX, MAQL, THOUGHTSPOT and DAX already do.
    assert "HOLISTICS_AQL" in _VALIDATE.NON_SQL_DIALECTS
    assert "HOLISTICS_AQL" not in _VALIDATE.SQLGLOT_DIALECTS


def _registered_dialects() -> set[str]:
    """The tokens in the table of extensions/dialects.md."""
    registry = Path(__file__).parents[2] / "extensions" / "dialects.md"
    return {
        match.group(1)
        for line in registry.read_text(encoding="utf-8").splitlines()
        if (match := re.match(r"^\|\s*`([^`]+)`\s*\|", line))
    }


def test_the_dialect_tables_match_the_registry() -> None:
    # A dialect registered in extensions/dialects.md has to be listed here as
    # well, as the sqlglot reader that parses it or as one sqlglot cannot read,
    # or the validator warns about a token the registry says is fine. A dialect
    # listed here but not in the registry is a registration someone skipped.
    known = set(_VALIDATE.SQLGLOT_DIALECTS) | _VALIDATE.NON_SQL_DIALECTS

    assert known == _registered_dialects(), (
        "extensions/dialects.md and SQLGLOT_DIALECTS / NON_SQL_DIALECTS in"
        " validate.py do not list the same dialects; register a dialect in both"
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


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_validates_a_valid_ossie_sql_2026_expression() -> None:
    error = _VALIDATE.validate_sql_expression("SUM(amount)", "OSSIE_SQL_2026", "ctx")

    assert error is None


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_flags_an_invalid_ossie_sql_2026_expression() -> None:
    error = _VALIDATE.validate_sql_expression("SUM(", "OSSIE_SQL_2026", "ctx")

    assert error is not None
    assert error.startswith("[SQL] ctx:")


def _parse_ossie_sql(expression: str):
    """Parse *expression* the way the validator parses an OSSIE_SQL_2026 body."""
    return _VALIDATE.sqlglot.parse_one(
        expression, dialect=_VALIDATE.SQLGLOT_DIALECTS[_VALIDATE.PORTABLE_DIALECT]
    )


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
    error = _VALIDATE.validate_sql_expression(
        "this is not sql @ all", "DB_DIALECT", "ctx"
    )

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
        "[Schema] datasets -> 0 -> fields -> 0 -> expression: "
        "{} is not valid under any of the given schemas"
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
        "[Schema] datasets -> 0 -> fields -> 0 -> expression -> dialects -> 0 -> dialect: "
        "'' should be non-empty"
    ]


def test_schema_rejects_empty_dialect_expression(core_schema: dict) -> None:
    # The per-dialect `expression` requires minLength 1, like `sql`.
    expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": ""}]}
    dataset = {**_ORDERS, "fields": [{"name": "f", "expression": expression}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == [
        "[Schema] datasets -> 0 -> fields -> 0 -> expression -> dialects -> 0 -> "
        "expression: '' should be non-empty"
    ]


def test_schema_rejects_empty_vendor_name(core_schema: dict) -> None:
    # `vendor_name` requires minLength 1.
    dataset = {**_ORDERS, "custom_extensions": [{"vendor_name": "", "data": "{}"}]}

    assert _VALIDATE.validate_schema(_document([dataset], []), core_schema) == [
        "[Schema] datasets -> 0 -> custom_extensions -> 0 -> vendor_name: "
        "'' should be non-empty"
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

    assert result == [
        "[SQL] Warning: dialect 'ACME_SQL' is not supported by this validator; "
        "2 expressions not syntax-checked"
    ]
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
        "[SQL] Warning: dialect 'ACME_SQL' is not supported by this validator; "
        "1 expression not syntax-checked",
        "[SQL] Warning: dialect 'ZETA_SQL' is not supported by this validator; "
        "1 expression not syntax-checked",
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
    errors = validate_references(
        _document([_ORDERS], [_relationship(to_columns=["id"], to="nope")])
    )

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm' references unknown dataset 'nope'"
    ]


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
    dataset = {
        "name": "customers",
        "source": "db.s.customers",
        "unique_keys": ["email"],
        "fields": [_field("email")],
    }
    errors = validate_references(
        _document([_ORDERS, dataset], [_relationship(to_columns=["email"])])
    )

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
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["GHOST_TO"])])
    )

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
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=[""])])
    )

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
    errors = validate_references(
        _document([_ORDERS, _CUSTOMERS], [_relationship(to_columns=["id", ""])])
    )

    assert errors == [
        "[Reference] Relationship 'orders_to_customers' in model 'm': to_columns "
        "references undeclared field ''. to_columns must be field names "
        "of the to dataset 'customers'."
    ]


def test_ghost_from_columns_fails_document_validation(core_schema: dict) -> None:
    rel = _relationship(to_columns=["id"])
    rel["from_columns"] = ["GHOST_COLUMN"]

    errors = _VALIDATE.validate_document(
        _document([_ORDERS, _CUSTOMERS], [rel]), core_schema
    )

    ghost_errors = [
        error for error in errors if "GHOST_COLUMN" in error and "from_columns" in error
    ]
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


@pytest.mark.parametrize("version, expected_exit", [("0.2.0.dev0", 0), ("0.1.0", 1)])
def test_ontology_cli_validates_embedded_documents_offline(
    run_validator, offline, version, expected_exit
):
    model = _document([_ORDERS], [])
    model["version"] = version
    document = {
        "version": "0.2.0.dev0",
        "name": "sales",
        "ai_context": {"instructions": "Use for sales analysis"},
        "ontology": [{"concept": "Order", "type": "EntityType"}],
        "ontology_mappings": [{"semantic_model": model, "concept_mappings": []}],
    }

    exit_code, output = run_validator(
        document, Path(__file__).parents[2] / "ontology/ontology.json"
    )

    assert exit_code == expected_exit
    if expected_exit == 0:
        assert "Validation PASSED" in output
    else:
        assert "ontology_mappings -> 0 -> semantic_model -> version" in output


@pytest.mark.parametrize("target", [
    "missing_customers",
    "Warning: missing_customers",
    "[SQL] Warning: missing_customers",
    "[Reference] Warning: missing_customers",
])
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
    document["metrics"] = [{
        "name": "Warning: broken_metric",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM("}]},
    }]

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "Validation FAILED with 1 error(s)" in output
    assert "[SQL] Metric 'Warning: broken_metric'" in output


def test_empty_field_expression_fails_validation(run_validator):
    # An empty per-dialect expression now fails the schema (minLength 1), before
    # the SQL check runs.
    dataset = {**_ORDERS, "fields": [{
        "name": "blank",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": ""}]},
    }]}
    document = _document([dataset], [])

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert (
        "[Schema] datasets -> 0 -> fields -> 0 -> expression -> dialects -> 0 -> "
        "expression: '' should be non-empty" in output
    )


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_comment_only_metric_expression_fails_validation(run_validator):
    document = _document([_ORDERS], [])
    document["metrics"] = [{
        "name": "blank_total",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "-- TODO"}]},
    }]

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "[SQL] Metric 'blank_total' in model 'm' (ANSI_SQL): expression is empty" in output


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_field_expressed_only_in_ossie_sql_2026_validates(run_validator):
    dataset = {**_ORDERS, "fields": [{
        "name": "half_amount",
        "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "amount * 0.5"}]},
    }]}
    document = _document([dataset], [])

    exit_code, output = run_validator(document)

    assert exit_code == 0
    assert "Validation PASSED" in output


@pytest.mark.skipif(not _VALIDATE.SQLGLOT_AVAILABLE, reason="sqlglot is not installed")
def test_metric_expressed_only_in_ossie_sql_2026_validates(run_validator):
    document = _document([_ORDERS], [])
    document["metrics"] = [{
        "name": "total_amount",
        "expression": {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(amount)"}]},
    }]

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
    document = _document([_ORDERS, _CUSTOMERS], [
        _relationship(to_columns=["region"]),
        {**_relationship(to_columns=["id"], to="Warning: missing"), "name": "broken"},
    ])

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
def test_arity_accepts_equal_length_columns(
    from_columns: list[str], to_columns: list[str]
) -> None:
    rel = _arity_relationship(from_columns, to_columns)

    assert validate_relationship_column_arity(_document([_ORDERS, _CUSTOMERS], [rel])) == []


@pytest.mark.parametrize(
    ("from_columns", "to_columns"),
    [
        (["product_id", "variant_id"], ["id"]),
        (["customer_id"], ["id", "variant_id"]),
    ],
)
def test_arity_rejects_mismatched_length_columns(
    from_columns: list[str], to_columns: list[str]
) -> None:
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
    dataset = {**_ORDERS, "fields": [
        {
            "name": "amount",
            "expression": {
                "dialects": [
                    {"dialect": "ANSI_SQL", "expression": "amount; DROP TABLE customers"}
                ]
            },
        }
    ]}

    exit_code, output = run_validator(_document([dataset], []))

    assert exit_code == 1
    assert "expected a single expression but found 2 statements" in output


validate_ontology = _VALIDATE.validate_ontology

_ONTOLOGY_SCHEMA = Path(__file__).parents[2] / "ontology/ontology.json"


def _ontology(components: list[dict], **extra) -> dict:
    return {"version": "0.2.0.dev0", "name": "o", "ontology": components, **extra}


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
    ],
)
def test_extends_cycles_are_reported_once(components: list[dict], expected: list[str]) -> None:
    assert validate_ontology(_ontology(components)) == expected


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

    def explode(data, schema, base=None):
        if "orders" in json.dumps(data):
            raise KeyError("dialect")
        return []

    monkeypatch.setattr(_VALIDATE, "validate_document", explode)

    code, output = _run_cli(monkeypatch, capsys, bad, good)

    assert code == 1
    assert f"Error: KeyError while validating {bad}: 'dialect'" in output
    assert "Validation PASSED: good.json" in output
    assert "1 of 2 file(s) passed" in output


def test_cli_takes_files_after_a_double_dash_literally(tmp_path, monkeypatch, capsys, offline):
    dashed = _write_document(tmp_path, "-model.json", _document([_CUSTOMERS], []))
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


def test_dataset_scoped_metric_count_star_references_no_field() -> None:
    doc = _orders_with(metrics=[{"name": "n", "expression": _ds_expr("COUNT(*)")}])

    assert _VALIDATE.validate_metric_scoping(doc) == []


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
        metrics=[
            {"name": "bad", "expression": _ds_portable("SUM(amount) / COUNT(customers.id)")}
        ],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == [
        "[Scope] Dataset-scoped metric 'orders.bad' in model 'm' (OSSIE_SQL_2026) uses "
        "qualified reference(s) 'customers'. A dataset-scoped metric expression must "
        "reference the dataset's fields by single-part (unqualified) name; a metric that "
        "must reference another dataset belongs in the model-root metrics."
    ]


def test_dataset_scoped_metric_portable_sql_rejects_an_undeclared_field() -> None:
    doc = _orders_with(
        fields=[{"name": "amount", "expression": _ds_expr("amount")}],
        metrics=[{"name": "total_qty", "expression": _ds_portable("SUM(quantity)")}],
    )

    assert _VALIDATE.validate_metric_scoping(doc) == [
        "[Scope] Dataset-scoped metric 'orders.total_qty' in model 'm' (OSSIE_SQL_2026) "
        "references undeclared field(s) 'quantity'. A dataset-scoped metric may reference "
        "only the dataset's declared fields."
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
    errors = _VALIDATE.validate_sql(
        _metric_with_sql_and_ossie_dialect("SUM(amount)", "AVG(amount)")
    )

    assert errors == [
        "[SQL] Warning: Metric 'orders.total' in model 'm' (OSSIE_SQL_2026): dialect entry "
        "differs from the top-level 'sql' and is ignored; the top-level 'sql' is authoritative"
    ]


def test_identical_superseded_ossie_sql_2026_dialect_entry_is_silent() -> None:
    # An OSSIE_SQL_2026 dialect entry identical to the top-level `sql` is harmless
    # redundancy, not drift, so it is not warned.
    assert _VALIDATE.validate_sql(
        _metric_with_sql_and_ossie_dialect("SUM(amount)", "SUM(amount)")
    ) == []


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
        "datasets": [
            {"name": "orders", "fields": [{"name": "amount", "expression": expression}]}
        ],
    }


def test_iter_expressions_yields_top_level_sql_as_ossie_sql_2026() -> None:
    [expr] = _iter(_one_field({"sql": "amount"}))

    assert (expr.kind, expr.dataset_name, expr.name) == ("field", "orders", "amount")
    assert (expr.dialect, expr.text, expr.superseded_by) == ("OSSIE_SQL_2026", "amount", None)
    assert expr.field_names == frozenset({"amount"})


def test_iter_expressions_yields_each_dialect_entry() -> None:
    exprs = _iter(_one_field({"dialects": [
        {"dialect": "SNOWFLAKE", "expression": "amount::number"},
        {"dialect": "DATABRICKS", "expression": "CAST(amount AS DECIMAL)"},
    ]}))

    assert [(e.dialect, e.text) for e in exprs] == [
        ("SNOWFLAKE", "amount::number"),
        ("DATABRICKS", "CAST(amount AS DECIMAL)"),
    ]
    assert all(e.superseded_by is None for e in exprs)


def test_iter_expressions_yields_sql_before_dialects_and_marks_superseded_entry() -> None:
    exprs = _iter(_one_field({
        "sql": "amount",
        "dialects": [
            {"dialect": "SNOWFLAKE", "expression": "amount::number"},
            {"dialect": "OSSIE_SQL_2026", "expression": "amount + 0"},
        ],
    }))

    assert [(e.dialect, e.text, e.superseded_by) for e in exprs] == [
        ("OSSIE_SQL_2026", "amount", None),
        ("SNOWFLAKE", "amount::number", None),
        ("OSSIE_SQL_2026", "amount + 0", "amount"),
    ]


def test_iter_expressions_covers_dataset_and_model_metrics_in_order() -> None:
    data = {
        "name": "m",
        "datasets": [{
            "name": "orders",
            "fields": [{"name": "Amount", "expression": {"sql": "amount"}}],
            "metrics": [{"name": "total", "expression": {"sql": "SUM(amount)"}}],
        }],
        "metrics": [{"name": "avg_total", "expression": {"sql": "AVG(orders.amount)"}}],
    }

    exprs = _iter(data)

    assert [(e.kind, e.dataset_name, e.name) for e in exprs] == [
        ("field", "orders", "Amount"),
        ("dataset_metric", "orders", "total"),
        ("model_metric", "", "avg_total"),
    ]
    # Dataset members see the casefolded field names; model metrics see none.
    assert exprs[1].field_names == frozenset({"amount"})
    assert exprs[2].field_names == frozenset()


def test_iter_expressions_yields_empty_text_and_skips_missing_expressions() -> None:
    data = {
        "name": "m",
        "datasets": [{
            "name": "orders",
            "fields": [
                {"name": "blank", "expression": {"sql": ""}},
                {"name": "no_expression"},
                {"name": "no_text", "expression": {"dialects": [{"dialect": "SNOWFLAKE"}]}},
            ],
        }],
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
        kind=kind, dataset_name=dataset_name, name="x", dialect="SNOWFLAKE",
        text="x", field_names=frozenset(),
    )

    assert expr.context("m") == expected


# --- field references (core-spec/spec.md "Name Resolution") ---

def _field_reference_findings(document: dict) -> list[str]:
    """The two Name Resolution checks together, in the order validate_document runs them."""
    return _VALIDATE.validate_metric_references(document) + _VALIDATE.validate_field_shadowing(document)


def _metric(name: str, expression: str, dialect: str = "ANSI_SQL") -> dict:
    """A metric carries its expression the way a field does."""
    return _field(name, expression, dialect)


def _renamed_model(**overrides) -> dict:
    document = {
        "version": "0.2.0.dev0",
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
    amount = {"name": "amount", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "amount"},
        {"dialect": "SNOWFLAKE", "expression": "AMT"},
    ]}}
    taxed = {"name": "amount_with_tax", "expression": {"dialects": [
        {"dialect": "ANSI_SQL", "expression": "amount * 1.1"},
        {"dialect": "SNOWFLAKE", "expression": "amount * 1.1"},
    ]}}
    document["datasets"][0]["fields"] += [amount, taxed]
    errors = _field_reference_findings(document)
    assert len(errors) == 1
    assert errors[0].startswith(
        "[Reference] Warning: Field 'orders.amount_with_tax' in model 'm' (SNOWFLAKE): 'amount'"
    )
    assert "defined as 'AMT'" in errors[0]


def test_field_shadowing_check_skips_dialects_that_are_not_sql_and_unparseable_sql() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(
        {"name": "x", "expression": {"dialects": [
            {"dialect": "DAX", "expression": "[total_usd] * 2"},
            {"dialect": "ANSI_SQL", "expression": "total_usd * ("},
        ]}}
    )
    assert _field_reference_findings(document) == []


@pytest.mark.parametrize(
    "definition", ["(amount)", '"amount"', "amount -- the amount", "((amount))"]
)
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


def test_field_shadowing_check_compares_names_without_regard_to_case() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(_field("total_eur", "Total_USD * 0.9"))

    errors = _field_reference_findings(document)

    assert len(errors) == 1
    assert "'Total_USD' is the source column, not the field 'total_usd'" in errors[0]


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
        {"name": "revenue_share", "expression": {"dialects": [
            {"dialect": "ANSI_SQL", "expression": "total_revenue / SUM(orders.total_usd)"}
        ]}}
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


@pytest.mark.parametrize("expression", [
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
])
def test_metric_check_reads_a_date_part_as_a_unit_not_a_column(expression) -> None:
    document = _renamed_model(metrics=[_metric("age", expression)])
    document["datasets"][0]["fields"] += [_field("shipped"), _field("ordered")]

    assert _VALIDATE.validate_metric_references(document) == []


def test_metric_check_compares_names_without_regard_to_case() -> None:
    document = _renamed_model(metrics=[
        _metric("Revenue", "SUM(ORDERS.TOTAL_USD)"),
        _metric("revenue_eur", "SUM(ORDERS.TOTAL_EUR)"),
        _metric("doubled", "revenue * 2"),
    ])

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 2
    assert "'ORDERS.TOTAL_EUR' is not a declared field of dataset 'ORDERS'" in errors[0]
    assert "'revenue' names the metric 'Revenue'" in errors[1]


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
    document = _renamed_model(metrics=[
        _metric("age", "SUM(DATEDIFF(day, ms, orders.total_usd))"),
        _metric("month_start", "MIN(DATE_TRUNC(month, d))"),
    ])
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
    document = _renamed_model(metrics=[
        _metric("ok", "SUM(DATEDIFF(day, orders.total_usd, orders.total_usd))", dialect="SNOWFLAKE"),
        _metric("bad", "SUM(DATEDIFF(day, orders.total_usd, d))", dialect="SNOWFLAKE"),
    ])

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 1
    assert "'d' is not qualified" in errors[0]


def test_metric_check_still_reports_a_bare_column_named_like_a_unit() -> None:
    document = _renamed_model(metrics=[_metric("days", "SUM(day)")])

    errors = _VALIDATE.validate_metric_references(document)

    assert len(errors) == 1
    assert "'day' is not qualified" in errors[0]


@pytest.mark.parametrize("document", [
    {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": None,
        "metrics": None,
        "relationships": None,
    },
    {
        "version": "0.2.0.dev0",
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
])
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
    document = _renamed_model(metrics=[{
        "name": "revenue",
        "expression": {
            "sql": "SUM(orders.total_usd)",
            "dialects": [{"dialect": "OSSIE_SQL_2026", "expression": "SUM(total_usd)"}],
        },
    }])

    assert _VALIDATE.validate_metric_references(document) == []


def test_reference_checks_skip_a_dialect_this_validator_does_not_parse() -> None:
    # A dialect outside SQLGLOT_DIALECTS is carried, not read: validate_sql warns
    # once that it went unchecked, and these checks say nothing about it.
    document = _renamed_model(metrics=[_metric("revenue", "SUM(total_usd)", dialect="DUCKDB")])
    document["datasets"][0]["fields"].append(_field("total_eur", "total_usd * 0.9", dialect="DUCKDB"))

    assert _field_reference_findings(document) == []


def test_field_shadowing_check_reads_the_portable_sql_form() -> None:
    document = _renamed_model()
    document["datasets"][0]["fields"].append(
        {"name": "total_eur", "expression": {"sql": "total_usd * 0.9"}}
    )

    errors = _VALIDATE.validate_field_shadowing(document)

    assert errors == [
        "[Reference] Warning: Field 'orders.total_eur' in model 'm' (OSSIE_SQL_2026): 'total_usd' is "
        "the source column, not the field 'total_usd' defined as 'total_cents / 100.0'"
    ]


def test_metric_check_reads_a_column_in_the_unit_slot_as_a_column() -> None:
    # Under the default parser the last DATEDIFF argument lands in `unit` as a
    # Var, so without this it would escape every check.
    document = _renamed_model(metrics=[
        _metric("age", "SUM(DATEDIFF(day, orders.total_usd, closed_at))"),
    ])

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


@pytest.mark.parametrize("expression, dialect", [
    ("SUM(DATEDIFF(day, orders.total_usd, '2026-01-01'))", "ANSI_SQL"),
    ("SUM(DATEDIFF(day, orders.total_usd, 1))", "ANSI_SQL"),
    ("SUM(DATE_DIFF(orders.total_usd, orders.total_usd, ISOWEEK))", "BIGQUERY"),
    ("SUM(DATEDIFF(nanoseconds, orders.total_usd, orders.total_usd))", "SNOWFLAKE"),
    ("SUM(DATEDIFF(day, orders.total_usd, orders.d))", "ANSI_SQL"),
])
def test_a_literal_or_an_unlisted_unit_in_the_unit_slot_is_not_a_column(expression, dialect) -> None:
    # A literal the default parser leaves in `unit`, a unit alias the table lacks
    # under a parser that knows the function, and a declared column named like
    # a unit alias in the last slot: none of them is a bare column.
    document = _renamed_model(metrics=[_metric("x", expression, dialect=dialect)])
    document["datasets"][0]["fields"].append(_field("d", "delivered_at"))

    assert _VALIDATE.validate_metric_references(document) == []


def test_a_repeated_reference_is_reported_once_per_expression() -> None:
    document = _renamed_model(metrics=[
        _metric("ratio", "SUM(orders.zz) / COUNT(orders.zz)"),
        _metric("mean", "SUM(total_usd) / COUNT(total_usd)"),
    ])

    errors = _VALIDATE.validate_metric_references(document)

    assert [e.split(": ", 2)[2] for e in errors] == [
        "'orders.zz' is not a declared field of dataset 'orders'",
        "'total_usd' is not qualified; metric expressions refer to fields as dataset.field",
    ]


@pytest.mark.parametrize("dialect", ["ANSI_SQL", "SNOWFLAKE"])
def test_date_add_reads_its_unit_as_a_unit_and_its_date_as_a_column(dialect) -> None:
    # The default parser keeps DATE_ADD's unit in `this` and its date in `unit`.
    document = _renamed_model(metrics=[
        _metric("ok", "MAX(DATE_ADD(day, 1, orders.total_usd))", dialect=dialect),
        _metric("bad", "MAX(DATE_ADD(day, 1, shipped))", dialect=dialect),
    ])

    errors = _VALIDATE.validate_metric_references(document)

    assert [e.split(": ", 2)[2] for e in errors] == [
        "'SHIPPED' is not qualified; metric expressions refer to fields as dataset.field",
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
    assert all(want in got for want, got in zip(expected, errors))


def test_field_shadowing_check_falls_back_past_an_empty_definition() -> None:
    # An empty ANSI_SQL entry gives nothing to compare, so the other dialect is read.
    document = _renamed_model()
    document["datasets"][0]["fields"] += [
        {"name": "total", "expression": {"dialects": [
            {"dialect": "ANSI_SQL", "expression": ""},
            {"dialect": "SNOWFLAKE", "expression": "total_cents / 100"},
        ]}},
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
