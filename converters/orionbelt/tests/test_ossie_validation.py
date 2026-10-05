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

import sys

import pytest

from ossie_orionbelt.cli import _report_validation
from ossie_orionbelt.validation import (
    _OSSIE_SCHEMA_PATH,
    _find_duplicates,
    validate_ossie,
    validate_ossie_ontology,
)


@pytest.fixture(params=["available", "missing_file", "missing_package"])
def schema_mode(request):
    return request.param


@pytest.fixture
def schema_path(schema_mode, tmp_path, monkeypatch):
    if schema_mode == "missing_file":
        return tmp_path / "missing-schema.json"
    if schema_mode == "missing_package":
        monkeypatch.setitem(sys.modules, "jsonschema", None)
    return _OSSIE_SCHEMA_PATH


@pytest.mark.parametrize(
    "wrapper",
    [None, [], {}, [{"name": "legacy", "datasets": []}]],
)
@pytest.mark.parametrize("include_root_model", [False, True])
def test_legacy_wrapper_is_always_invalid(schema_path, wrapper, include_root_model):
    document = {"version": "0.2.0.dev0", "semantic_model": wrapper}
    if include_root_model:
        document.update(name="m", datasets=[{"name": "t", "source": "a.b.c"}])

    result = validate_ossie(document, schema_path=schema_path)

    assert not result.valid
    assert any("[LEGACY_WRAPPER]" in error for error in result.semantic_errors)


@pytest.mark.parametrize("document", [None, [], "not a mapping", 42])
def test_non_mapping_document_is_always_invalid(schema_path, document):
    result = validate_ossie(document, schema_path=schema_path)

    assert not result.valid
    assert any("[INVALID_DOCUMENT]" in error for error in result.semantic_errors)


def test_valid_flat_document_can_be_checked_without_schema(schema_path, schema_mode):
    document = {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": [{"name": "t", "source": "a.b.c"}],
    }

    result = validate_ossie(document, schema_path=schema_path)

    assert result.valid
    assert result.schema_validation_performed == (schema_mode == "available")
    expected_status = "✓ valid" if schema_mode == "available" else "skipped"
    assert result.summary_lines()[0] == f"  JSON Schema: {expected_status}"
    if schema_mode != "available":
        assert result.semantic_warnings


@pytest.mark.parametrize(
    "model",
    [
        {"name": "m", "datasets": "not a list"},
        {"name": "m"},
        {"datasets": [{"name": "t", "source": "a.b.c"}]},
        {"name": "m", "datasets": [{"name": "t"}]},
        {"name": "m", "datasets": ["not an object"]},
    ],
    ids=["datasets_type", "datasets_missing", "name_missing", "source_missing", "dataset_type"],
)
def test_structural_validation_coverage_is_reported(schema_path, schema_mode, model):
    result = validate_ossie({"version": "0.2.0.dev0", **model}, schema_path=schema_path)

    assert not result.semantic_errors
    if schema_mode == "available":
        assert result.schema_validation_performed
        assert not result.valid
        assert result.schema_errors
        assert result.summary_lines()[0] == f"  JSON Schema: {len(result.schema_errors)} error(s)"
    else:
        assert not result.schema_validation_performed
        assert result.valid
        assert not result.schema_errors
        assert result.semantic_warnings
        assert result.summary_lines()[0] == "  JSON Schema: skipped"


@pytest.mark.parametrize("error_code", ["DUPLICATE_DATASET", "UNKNOWN_DATASET_REF"])
def test_semantic_checks_still_run_without_schema(schema_path, error_code):
    document = {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": [{"name": "t", "source": "a.b.c"}],
    }
    if error_code == "DUPLICATE_DATASET":
        document["datasets"].append({"name": "t", "source": "a.b.d"})
    else:
        document["relationships"] = [
            {
                "name": "r",
                "from": "t",
                "to": "missing",
                "from_columns": ["id"],
                "to_columns": ["id"],
            }
        ]

    result = validate_ossie(document, schema_path=schema_path)

    assert not result.valid
    assert any(f"[{error_code}]" in error for error in result.semantic_errors)


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["a", "a"], ["a"]),
        (["a", "a", "a"], ["a"]),
        (["a", "b", "b", "a"], ["a", "b"]),
        (["a", "b", "c"], []),
        ([], []),
    ],
    ids=["twice", "three-times", "repeats-in-reverse-order", "all-unique", "empty"],
)
def test_find_duplicates_reports_each_name_once_in_first_appearance_order(names, expected):
    """Mirrors the sibling test in validation/tests/test_validate.py."""
    assert _find_duplicates(names) == expected


def _triplicate_document(kind: str) -> dict:
    """A flat document whose *kind* names collide three times over."""
    expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": "x"}]}
    document: dict = {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": [{"name": "orders", "source": "a.b.orders"}],
    }
    if kind == "dataset":
        document["datasets"] *= 3
    elif kind == "field":
        document["datasets"][0]["fields"] = [
            {"name": "amount", "expression": expression} for _ in range(3)
        ]
    elif kind == "metric":
        document["metrics"] = [
            {"name": "revenue", "expression": expression} for _ in range(3)
        ]
    elif kind == "relationship":
        document["datasets"].append({"name": "customers", "source": "a.b.customers"})
        document["relationships"] = [
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["id"],
            }
            for _ in range(3)
        ]
    return document


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("dataset", "DUPLICATE_DATASET"),
        ("field", "DUPLICATE_FIELD"),
        ("metric", "DUPLICATE_METRIC"),
        ("relationship", "DUPLICATE_RELATIONSHIP"),
    ],
)
def test_a_name_repeated_three_times_is_reported_once(schema_path, kind, code):
    """Three copies of a name are one problem, matching validation/validate.py."""
    result = validate_ossie(_triplicate_document(kind), schema_path=schema_path)

    reported = [error for error in result.semantic_errors if f"[{code}]" in error]
    assert len(reported) == 1


def test_a_concept_repeated_three_times_is_reported_once():
    ontology = {"ontology": [{"concept": {"name": "Party"}} for _ in range(3)]}

    result = validate_ossie_ontology(ontology)

    reported = [e for e in result.semantic_errors if "[DUPLICATE_CONCEPT]" in e]
    assert len(reported) == 1


def test_duplicate_concepts_still_resolve_as_defined_references():
    """Collecting concept names up front must not change reference integrity."""
    ontology = {
        "ontology": [
            {"concept": {"name": "Party"}},
            {"concept": {"name": "Party"}},
            {
                "concept": {"name": "Order"},
                "relationships": [
                    {"name": "placed_by", "roles": [{"concept": "Party"}]}
                ],
            },
        ]
    }

    result = validate_ossie_ontology(ontology)

    assert not any("UNKNOWN" in error for error in result.semantic_errors)


@pytest.mark.parametrize("has_name", [True, False])
def test_cli_reports_validation_coverage(schema_path, schema_mode, has_name, capsys):
    document = {"version": "0.2.0.dev0", "datasets": [{"name": "t", "source": "a.b.c"}]}
    if has_name:
        document["name"] = "m"

    has_errors = _report_validation(
        "Ossie output", document, lambda doc: validate_ossie(doc, schema_path=schema_path)
    )
    stderr = capsys.readouterr().err

    if schema_mode != "available":
        assert not has_errors
        assert "JSON Schema: skipped" in stderr
        assert "passed available checks (JSON Schema validation skipped)" in stderr
        assert "Ossie output is valid" not in stderr
    elif has_name:
        assert not has_errors
        assert "Ossie output is valid" in stderr
    else:
        assert has_errors
        assert "Ossie output has validation errors" in stderr
