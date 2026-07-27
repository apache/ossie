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

from pathlib import Path
from typing import Any

import pytest
import yaml

from ossie_gsf.converter import (
    GSFConversionError,
    convert_gsf_to_ossie,
    convert_ossie_to_gsf,
)


FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    return yaml.safe_load((FIXTURES / name).read_text(encoding="utf-8"))


def _dump(value: dict[str, Any]) -> str:
    return yaml.safe_dump(value, sort_keys=False)


def _native_attribute(
    document: dict[str, Any],
    term_name: str,
    attribute_name: str,
) -> dict[str, Any]:
    term = next(term for term in document["terms"] if term["name"] == term_name)
    attributes = (term.get("column_attributes") or []) + (
        term.get("sql_attributes") or []
    )
    return next(
        attribute for attribute in attributes if attribute["name"] == attribute_name
    )


def test_quoted_metric_references_are_resolved() -> None:
    document = _load_fixture("sales.ossie.yaml")
    model = document["semantic_model"][0]
    metric = model["metrics"][0]
    metric.pop("custom_extensions", None)
    metric["expression"]["dialects"] = [
        {
            "dialect": "ANSI_SQL",
            "expression": (
                'SUM("orders"."subtotal") + '
                'COUNT("customers"."customer_id")'
            ),
        }
    ]

    converted = yaml.safe_load(convert_ossie_to_gsf(_dump(document)))
    native_metric = _native_attribute(converted, "orders", metric["name"])

    assert set(native_metric["table_refs"]) == {"orders", "customers"}


def test_bigquery_wrapper_uses_backtick_identifiers() -> None:
    document = _load_fixture("sales.ossie.yaml")
    model = document["semantic_model"][0]
    metric = model["metrics"][0]
    metric.pop("custom_extensions", None)
    metric["expression"]["dialects"] = [
        {
            "dialect": "BIGQUERY",
            "expression": "SUM(`orders`.`subtotal`)",
        }
    ]

    converted = yaml.safe_load(convert_ossie_to_gsf(_dump(document)))
    native_metric = _native_attribute(converted, "orders", metric["name"])
    sql = native_metric["sql"]

    assert " AS `" in sql
    assert "FROM `" in sql
    assert ' AS "' not in sql
    assert 'FROM "' not in sql


def test_field_label_round_trips_through_native_metadata() -> None:
    document = _load_fixture("sales.ossie.yaml")
    model = document["semantic_model"][0]
    dataset = model["datasets"][0]
    field = dataset["fields"][0]
    field["label"] = "Order Identifier"

    native_yaml = convert_ossie_to_gsf(_dump(document))
    native = yaml.safe_load(native_yaml)
    native_field = _native_attribute(native, dataset["name"], field["name"])
    assert native_field["metadata"]["apache_ossie"]["label"] == field["label"]

    round_tripped = yaml.safe_load(convert_gsf_to_ossie(native_yaml))
    restored_model = round_tripped["semantic_model"][0]
    restored_dataset = next(
        item for item in restored_model["datasets"] if item["name"] == dataset["name"]
    )
    restored_field = next(
        item for item in restored_dataset["fields"] if item["name"] == field["name"]
    )
    assert restored_field["label"] == field["label"]


def test_scalar_relationship_columns_are_rejected() -> None:
    document = _load_fixture("sales.gsf.yaml")
    relationship = document["semantic_foreign_keys"][0]
    relationship["from_columns"] = "customer_id"

    with pytest.raises(
        GSFConversionError,
        match=r"from_columns must be a non-empty list of strings",
    ):
        convert_gsf_to_ossie(_dump(document))
