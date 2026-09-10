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


def _document(datasets: list[dict], relationships: list[dict]) -> dict:
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "m",
                "datasets": datasets,
                "relationships": relationships,
            }
        ],
    }


_CUSTOMERS = {
    "name": "customers",
    "source": "db.s.customers",
    "primary_key": ["id"],
    "unique_keys": [["email"]],
}

_ORDERS = {"name": "orders", "source": "db.s.orders"}


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


@pytest.fixture
def run_validator(tmp_path, monkeypatch, capsys):
    def run(document):
        model_path = tmp_path / "model.json"
        model_path.write_text(json.dumps(document))
        monkeypatch.setattr(_VALIDATE.sys, "argv", [str(_VALIDATE_PATH), str(model_path)])
        with pytest.raises(SystemExit) as caught:
            _VALIDATE.main()
        return caught.value.code, capsys.readouterr().out

    return run


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
    document["semantic_model"][0]["metrics"] = [{
        "name": "Warning: broken_metric",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM("}]},
    }]

    exit_code, output = run_validator(document)

    assert exit_code == 1
    assert "Validation FAILED with 1 error(s)" in output
    assert "[SQL] Metric 'Warning: broken_metric'" in output


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
