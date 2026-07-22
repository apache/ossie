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

"""Tests for the offline Apache Ossie ↔ NVIDIA GSF converter."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from ossie_gsf.converter import (
    GSFConversionError,
    _parse_source,
    _simple_source_column,
    convert_gsf_to_ossie,
    convert_ossie_to_gsf,
    main,
)

OSSIE_VERSION = "0.2.0.dev0"
FIXTURES = Path(__file__).parent / "fixtures"
VALIDATOR = Path(__file__).resolve().parents[3] / "validation" / "validate.py"


def _ossie_yaml() -> str:
    return (FIXTURES / "sales.ossie.yaml").read_text(encoding="utf-8")


def test_checked_in_fixture_pair_matches_conversion() -> None:
    expected = yaml.safe_load((FIXTURES / "sales.gsf.yaml").read_text(encoding="utf-8"))
    actual = yaml.safe_load(convert_ossie_to_gsf(_ossie_yaml()))

    assert actual == expected


def test_generated_ossie_passes_official_validation(tmp_path: Path) -> None:
    gsf_yaml = (FIXTURES / "sales.gsf.yaml").read_text(encoding="utf-8")
    output_path = tmp_path / "converted.ossie.yaml"
    output_path.write_text(
        convert_gsf_to_ossie(gsf_yaml),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(output_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validation PASSED" in result.stdout


def test_ossie_to_gsf_is_offline_and_maps_graph_entities() -> None:
    result = yaml.safe_load(convert_ossie_to_gsf(_ossie_yaml()))

    assert result["version"] == "1.0"
    assert result["model"] == {
        "name": "sales",
        "database": "analytics",
        "description": "Sales model",
        "ai_context": {"instructions": "Use approved metrics"},
    }
    terms = {term["name"]: term for term in result["terms"]}
    assert len(terms["orders"]["column_attributes"]) == 3
    sql_attributes = {
        attribute["name"]: attribute for attribute in terms["orders"]["sql_attributes"]
    }
    assert sql_attributes["net_total"]["kind"] == "field"
    metric = sql_attributes["revenue_per_customer"]
    assert metric["kind"] == "metric"
    assert metric["table_refs"] == ["orders", "customers"]
    assert " JOIN " in metric["sql"]
    assert len(metric["expressions"]) == 2
    assert result["semantic_foreign_keys"][0] == {
        "name": "orders_to_customers",
        "from_term": "orders",
        "to_term": "customers",
        "from_columns": ["customer_id"],
        "to_columns": ["customer_id"],
    }


def test_gsf_to_ossie_round_trip_preserves_semantics() -> None:
    gsf_yaml = convert_ossie_to_gsf(_ossie_yaml())
    result = yaml.safe_load(convert_gsf_to_ossie(gsf_yaml))

    assert set(result) == {"version", "semantic_model"}
    model = result["semantic_model"][0]
    assert model["name"] == "sales"
    datasets = {dataset["name"]: dataset for dataset in model["datasets"]}
    assert datasets["orders"]["primary_key"] == ["order_id"]
    assert datasets["orders"]["ai_context"]["synonyms"] == ["purchases"]
    fields = {field["name"]: field for field in datasets["orders"]["fields"]}
    assert (
        fields["net_total"]["expression"]["dialects"][0]["expression"]
        == "subtotal - discount"
    )
    metric = model["metrics"][0]
    assert len(metric["expression"]["dialects"]) == 2
    extension = next(
        item for item in metric["custom_extensions"] if item["vendor_name"] == "GSF"
    )
    assert json.loads(extension["data"])["term"] == "orders"
    assert model["relationships"][0]["from"] == "orders"


def test_gsf_sql_field_preserves_sql_and_multi_term_references() -> None:
    gsf = yaml.safe_load(convert_ossie_to_gsf(_ossie_yaml()))
    orders = next(term for term in gsf["terms"] if term["name"] == "orders")
    field = next(
        item for item in orders["sql_attributes"] if item["name"] == "net_total"
    )
    field["table_refs"] = ["orders", "customers"]
    field["sql"] = "SELECT custom_joined_value FROM orders JOIN customers"

    ossie = convert_gsf_to_ossie(yaml.safe_dump(gsf))
    round_trip = yaml.safe_load(convert_ossie_to_gsf(ossie))
    round_trip_orders = next(
        term for term in round_trip["terms"] if term["name"] == "orders"
    )
    round_trip_field = next(
        item
        for item in round_trip_orders["sql_attributes"]
        if item["name"] == "net_total"
    )

    assert round_trip_field["table_refs"] == ["orders", "customers"]
    assert round_trip_field["sql"] == field["sql"]


def test_ossie_extensions_round_trip_through_native_metadata() -> None:
    root = yaml.safe_load(_ossie_yaml())
    root["semantic_model"][0]["custom_extensions"] = [
        {"vendor_name": "DBT", "data": '{"project": "analytics"}'}
    ]

    native = yaml.safe_load(convert_ossie_to_gsf(yaml.safe_dump(root)))
    extensions = native["model"]["metadata"]["apache_ossie"]["custom_extensions"]
    assert extensions[0]["vendor_name"] == "DBT"

    ossie = yaml.safe_load(convert_gsf_to_ossie(yaml.safe_dump(native)))
    assert ossie["semantic_model"][0]["custom_extensions"][0]["vendor_name"] == "DBT"


def test_gsf_to_ossie_allows_model_name_override() -> None:
    output = convert_gsf_to_ossie(
        convert_ossie_to_gsf(_ossie_yaml()),
        model_name="renamed_sales",
    )
    assert yaml.safe_load(output)["semantic_model"][0]["name"] == ("renamed_sales")


def test_disconnected_metric_fails_instead_of_cross_join() -> None:
    root = yaml.safe_load(_ossie_yaml())
    root["semantic_model"][0]["relationships"] = []

    with pytest.raises(GSFConversionError, match="disconnected dataset"):
        convert_ossie_to_gsf(yaml.safe_dump(root))


def test_duplicate_physical_column_mapping_is_rejected() -> None:
    root = yaml.safe_load(_ossie_yaml())
    root["semantic_model"][0]["datasets"][0]["fields"].append(
        {
            "name": "alternate_order_id",
            "expression": {
                "dialects": [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": "order_id",
                    }
                ]
            },
        }
    )

    with pytest.raises(GSFConversionError, match="Multiple fields"):
        convert_ossie_to_gsf(yaml.safe_dump(root))


def test_multiple_catalog_databases_are_rejected() -> None:
    root = yaml.safe_load(_ossie_yaml())
    root["semantic_model"][0]["datasets"][1]["source"] = "crm.public.customers"

    with pytest.raises(GSFConversionError, match="exactly one database"):
        convert_ossie_to_gsf(yaml.safe_dump(root))


def test_wrong_versions_are_rejected() -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    ossie["version"] = "0.1"
    with pytest.raises(GSFConversionError, match="Unsupported Ossie"):
        convert_ossie_to_gsf(yaml.safe_dump(ossie))

    gsf = yaml.safe_load(convert_ossie_to_gsf(_ossie_yaml()))
    gsf["version"] = "2.0"
    with pytest.raises(GSFConversionError, match="Unsupported GSF"):
        convert_gsf_to_ossie(yaml.safe_dump(gsf))


@pytest.mark.parametrize(
    ("source", "default_database", "expected"),
    [
        (
            "analytics.public.orders",
            None,
            {
                "database": "analytics",
                "schema": "public",
                "table": "orders",
            },
        ),
        (
            "public.orders",
            "analytics",
            {
                "database": "analytics",
                "schema": "public",
                "table": "orders",
            },
        ),
        (
            {
                "database": "analytics",
                "schema": "public",
                "table": "orders",
            },
            None,
            {
                "database": "analytics",
                "schema": "public",
                "table": "orders",
            },
        ),
    ],
)
def test_parse_source(
    source: Any,
    default_database: str | None,
    expected: dict[str, str],
) -> None:
    assert _parse_source(source, default_database) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("order_id", "order_id"),
        ("orders.order_id", "order_id"),
        ("subtotal - discount", None),
        ("UPPER(name)", None),
    ],
)
def test_simple_source_column(
    expression: str,
    expected: str | None,
) -> None:
    assert _simple_source_column(expression, "orders", "orders") == expected


def test_cli_converts_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ossie_path = tmp_path / "model.yaml"
    gsf_path = tmp_path / "model.gsf.yaml"
    ossie_path.write_text(_ossie_yaml(), encoding="utf-8")

    main(["export", "-i", str(ossie_path), "-o", str(gsf_path)])
    assert yaml.safe_load(gsf_path.read_text())["version"] == "1.0"

    main(["import", "-i", str(gsf_path)])
    output = yaml.safe_load(capsys.readouterr().out)
    assert output["version"] == OSSIE_VERSION
