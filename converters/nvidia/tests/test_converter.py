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

"""Tests for Apache Ossie ↔ native NVIDIA Auto Ontology conversion."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from ossie_nvidia_auto_ontology.converter import (
    AutoOntologyConversionError,
    convert_auto_ontology_to_ossie,
    convert_ossie_to_auto_ontology,
    main,
)
from ossie_nvidia_auto_ontology.native_converter import (
    _SQL_TYPE_BY_OSSIE_DATATYPE,
    _index_native_document,
    _ossie_datatype,
    _parse_source,
    _reconcile_native_relationships,
    _simple_source_column,
)

OSSIE_VERSION = "0.2.0.dev0"
FIXTURES = Path(__file__).parent / "fixtures"
VALIDATOR = Path(__file__).resolve().parents[3] / "validation" / "validate.py"
SCHEMA = Path(__file__).resolve().parents[3] / "core-spec" / "ossie-schema.json"


def _ossie_yaml() -> str:
    return (FIXTURES / "sales.ossie.yaml").read_text(encoding="utf-8")


def _auto_ontology_yaml() -> str:
    return (FIXTURES / "sales.auto_ontology.yaml").read_text(encoding="utf-8")


def _native_extension(item: dict[str, Any]) -> dict[str, Any]:
    extension = next(
        value
        for value in item.get("custom_extensions") or []
        if value["vendor_name"] == "NVIDIA_AUTO_ONTOLOGY"
    )
    return json.loads(extension["data"])


def _manual_sql(native: dict[str, Any], name: str) -> str:
    attribute = next(
        item
        for item in native["semantic_layer"]["sql_attributes"]["manual"]
        if item["name"] == name
    )
    return str(attribute["sql"])


def _ids(value: Any) -> set[str]:
    result: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("id"), str):
            result.add(value["id"])
        for child in value.values():
            result.update(_ids(child))
    elif isinstance(value, list):
        for child in value:
            result.update(_ids(child))
    return result


def test_checked_in_fixture_is_exact_native_contract() -> None:
    expected = yaml.safe_load(_auto_ontology_yaml())
    actual = yaml.safe_load(convert_ossie_to_auto_ontology(_ossie_yaml()))

    assert actual == expected
    assert set(actual) == {"data_layer", "semantic_layer", "zones"}
    assert "version" not in actual
    assert "model" not in actual
    assert "terms" not in actual
    assert set(actual["semantic_layer"]["sql_attributes"]) == {
        "manual",
        "table",
        "sql",
        "bridge_table",
    }
    assert "columns_attributes" in actual["semantic_layer"]["terms"][0]


def test_ossie_ids_are_deterministic_and_references_resolve() -> None:
    first = yaml.safe_load(convert_ossie_to_auto_ontology(_ossie_yaml()))
    second = yaml.safe_load(convert_ossie_to_auto_ontology(_ossie_yaml()))

    assert _ids(first) == _ids(second)
    assert first == second
    catalog_column_ids = {
        column["id"]
        for database in first["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        for column in table["columns"]
    }
    for attribute in first["semantic_layer"]["sql_attributes"]["manual"]:
        assert set(attribute["sql_column_is"]) <= catalog_column_ids
    for analysis in first["semantic_layer"]["custom_analyses"]:
        assert analysis["sql_column_is"]
        assert set(analysis["sql_column_is"]) <= catalog_column_ids


def test_generated_ossie_passes_official_validation(tmp_path: Path) -> None:
    output_path = tmp_path / "converted.ossie.yaml"
    output_path.write_text(
        convert_auto_ontology_to_ossie(_auto_ontology_yaml()), encoding="utf-8"
    )

    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(output_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validation PASSED" in result.stdout


def test_round_trip_preserves_ossie_semantics_and_global_metrics() -> None:
    result = yaml.safe_load(
        convert_auto_ontology_to_ossie(convert_ossie_to_auto_ontology(_ossie_yaml()))
    )
    model = result
    datasets = {dataset["name"]: dataset for dataset in model["datasets"]}
    order_fields = {field["name"]: field for field in datasets["orders"]["fields"]}

    assert model["name"] == "analytics"
    assert datasets["orders"]["primary_key"] == ["order_id"]
    assert (
        order_fields["net_total"]["expression"]["dialects"][0]["expression"]
        == "subtotal - discount"
    )
    assert [metric["name"] for metric in model["metrics"]] == ["revenue_per_customer"]
    assert _native_extension(model)["native_document"]["zones"] == []
    assert model["relationships"][0] == {
        "name": "orders_to_customers",
        "from": "orders",
        "to": "customers",
        "from_columns": ["customer_id"],
        "to_columns": ["customer_id"],
    }


def test_edited_ossie_expressions_replace_preserved_native_sql() -> None:
    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(_auto_ontology_yaml()))
    model = ossie
    orders = next(
        dataset for dataset in model["datasets"] if dataset["name"] == "orders"
    )
    net_total = next(
        field for field in orders["fields"] if field["name"] == "net_total"
    )
    net_total["expression"]["dialects"][0]["expression"] = "subtotal + discount"
    model["metrics"][0]["expression"]["dialects"][0]["expression"] = (
        "SUM(orders.discount)"
    )

    regenerated = yaml.safe_load(
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie, sort_keys=False))
    )
    sql_attribute = regenerated["semantic_layer"]["sql_attributes"]["manual"][0]
    analysis = regenerated["semantic_layer"]["custom_analyses"][0]

    assert "subtotal + discount" in sql_attribute["sql"]
    assert "subtotal - discount" not in sql_attribute["sql"]
    assert "SUM(orders.discount)" in analysis["sql"]
    assert "COUNT(DISTINCT customers.customer_id)" not in analysis["sql"]


def test_native_round_trip_preserves_ids_catalog_sql_source_and_zones() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    database = native["data_layer"]["databases"][0]
    database["dialect"] = "snowflake"
    first_column = database["schemas"][0]["tables"][0]["columns"][0]
    first_column["type"] = "NUMBER"
    first_column["sample_values"] = ["1", "2"]
    database["schemas"][0]["tables"].append(
        {
            "id": "native-audit-table",
            "name": "audit_log",
            "description": "Catalog-only table",
            "pk": [],
            "type": "table",
            "columns": [
                {
                    "id": "native-audit-column",
                    "name": "message",
                    "description": "",
                    "type": "TEXT",
                    "sample_values": [],
                    "is_nullable": True,
                    "is_unique": False,
                }
            ],
        }
    )
    native["zones"] = [{"id": "zone-1", "name": "finance"}]
    manual = native["semantic_layer"]["sql_attributes"]["manual"]
    native["semantic_layer"]["sql_attributes"]["table"] = manual
    native["semantic_layer"]["sql_attributes"]["manual"] = []

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    assert _native_extension(ossie)["native_document"] == native

    restored = yaml.safe_load(
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie, sort_keys=False))
    )
    restored_database = restored["data_layer"]["databases"][0]
    restored_first_column = restored_database["schemas"][0]["tables"][0]["columns"][0]

    assert _ids(restored) == _ids(native)
    assert restored_database["dialect"] == "snowflake"
    assert restored_first_column["type"] == "NUMBER"
    assert restored_first_column["sample_values"] == ["1", "2"]
    assert any(
        table["id"] == "native-audit-table"
        for table in restored_database["schemas"][0]["tables"]
    )
    assert restored["semantic_layer"]["sql_attributes"]["manual"] == []
    assert (
        restored["semantic_layer"]["sql_attributes"]["table"][0]["id"]
        == manual[0]["id"]
    )
    assert restored["zones"] == [{"id": "zone-1", "name": "finance"}]


def test_relationship_edits_replace_preserved_native_records() -> None:
    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(_auto_ontology_yaml()))
    relationship = ossie["relationships"][0]
    relationship["from_columns"] = ["order_id"]

    regenerated = yaml.safe_load(
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie, sort_keys=False))
    )

    assert regenerated["data_layer"]["joins"][0]["join_columns"] == [
        {"source": "order_id", "target": "customer_id"}
    ]
    orders_table = next(
        table
        for table in regenerated["data_layer"]["databases"][0]["schemas"][0]["tables"]
        if table["name"] == "orders"
    )
    order_id = next(
        column["id"]
        for column in orders_table["columns"]
        if column["name"] == "order_id"
    )
    assert regenerated["data_layer"]["foreign_keys"][0]["source_column_id"] == order_id


def test_relationship_deletion_removes_preserved_native_records() -> None:
    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(_auto_ontology_yaml()))
    ossie.pop("relationships")

    regenerated = yaml.safe_load(
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie, sort_keys=False))
    )

    assert regenerated["data_layer"]["joins"] == []
    assert regenerated["data_layer"]["foreign_keys"] == []
    assert regenerated["semantic_layer"]["semantic_fks"] == []


def test_a_semantic_only_relationship_does_not_become_a_physical_constraint() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    native["data_layer"]["joins"] = []
    native["data_layer"]["foreign_keys"] = []

    ossie = convert_auto_ontology_to_ossie(yaml.safe_dump(native))
    assert yaml.safe_load(ossie)["relationships"]
    restored = yaml.safe_load(convert_ossie_to_auto_ontology(ossie))

    assert restored["data_layer"]["joins"] == []
    assert restored["data_layer"]["foreign_keys"] == []
    assert (
        restored["semantic_layer"]["semantic_fks"]
        == native["semantic_layer"]["semantic_fks"]
    )


def test_a_physical_only_relationship_gains_no_join_or_semantic_fk() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    native["data_layer"]["joins"] = []
    native["semantic_layer"]["semantic_fks"] = []

    restored = yaml.safe_load(
        convert_ossie_to_auto_ontology(
            convert_auto_ontology_to_ossie(yaml.safe_dump(native))
        )
    )

    assert restored["data_layer"]["joins"] == []
    assert restored["semantic_layer"]["semantic_fks"] == []
    assert (
        restored["data_layer"]["foreign_keys"] == native["data_layer"]["foreign_keys"]
    )


def test_relationship_records_survive_a_native_cycle_unchanged() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())

    restored = yaml.safe_load(
        convert_ossie_to_auto_ontology(
            convert_auto_ontology_to_ossie(_auto_ontology_yaml())
        )
    )

    assert restored["data_layer"]["joins"] == native["data_layer"]["joins"]
    assert (
        restored["data_layer"]["foreign_keys"] == native["data_layer"]["foreign_keys"]
    )
    assert (
        restored["semantic_layer"]["semantic_fks"]
        == native["semantic_layer"]["semantic_fks"]
    )


def test_only_column_pairs_added_in_ossie_are_generated() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    native["data_layer"]["joins"] = []
    native["data_layer"]["foreign_keys"] = []
    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    relationship = ossie["relationships"][0]
    relationship["from_columns"].append("order_id")
    relationship["to_columns"].append("customer_id")

    restored = yaml.safe_load(
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie, sort_keys=False))
    )

    assert [join["join_columns"] for join in restored["data_layer"]["joins"]] == [
        [{"source": "order_id", "target": "customer_id"}]
    ]
    assert len(restored["data_layer"]["foreign_keys"]) == 1
    assert (
        restored["semantic_layer"]["semantic_fks"][0]
        == native["semantic_layer"]["semantic_fks"][0]
    )
    assert len(restored["semantic_layer"]["semantic_fks"]) == 2


def _physical_fk_native(
    extra_orders_columns: list[str],
    extra_customers_columns: list[str],
    foreign_keys: list[tuple[str, str]],
    customers_pk: list[str] | None = None,
) -> dict[str, Any]:
    """The sample model linked only by physical foreign keys, orders -> customers."""
    native = yaml.safe_load(_auto_ontology_yaml())
    native["data_layer"]["joins"] = []
    native["semantic_layer"]["semantic_fks"] = []
    tables = {
        table["name"]: table
        for table in native["data_layer"]["databases"][0]["schemas"][0]["tables"]
    }
    for table_name, names in (
        ("orders", extra_orders_columns),
        ("customers", extra_customers_columns),
    ):
        for name in names:
            tables[table_name]["columns"].append(
                {
                    "id": f"{table_name}-{name}",
                    "name": name,
                    "description": "",
                    "type": "",
                    "sample_values": [],
                    "is_nullable": True,
                    "is_unique": False,
                }
            )
    if customers_pk is not None:
        tables["customers"]["pk"] = customers_pk
    column_ids = {
        (table_name, column["name"]): column["id"]
        for table_name, table in tables.items()
        for column in table["columns"]
    }
    native["data_layer"]["foreign_keys"] = [
        {
            "source_column_id": column_ids[("orders", source)],
            "target_column_id": column_ids[("customers", target)],
        }
        for source, target in foreign_keys
    ]
    return native


def _relationship_columns(ossie: dict[str, Any]) -> dict[str, list[tuple[str, str]]]:
    return {
        relationship["name"]: list(
            zip(relationship["from_columns"], relationship["to_columns"], strict=True)
        )
        for relationship in ossie["relationships"]
    }


def test_independent_foreign_keys_into_one_column_are_separate_relationships() -> None:
    native = _physical_fk_native(
        ["referrer_id"],
        [],
        [("customer_id", "customer_id"), ("referrer_id", "customer_id")],
    )

    ossie = convert_auto_ontology_to_ossie(yaml.safe_dump(native))

    assert _relationship_columns(yaml.safe_load(ossie)) == {
        "orders_customer_id_to_customers": [("customer_id", "customer_id")],
        "orders_referrer_id_to_customers": [("referrer_id", "customer_id")],
    }
    restored = yaml.safe_load(convert_ossie_to_auto_ontology(ossie))
    assert restored["data_layer"]["joins"] == []
    assert (
        restored["data_layer"]["foreign_keys"] == native["data_layer"]["foreign_keys"]
    )


def test_foreign_keys_covering_a_composite_primary_key_stay_one_relationship() -> None:
    native = _physical_fk_native(
        ["region"],
        ["region"],
        [("region", "region"), ("customer_id", "customer_id")],
        customers_pk=["customer_id", "region"],
    )

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))

    assert _relationship_columns(ossie) == {
        "orders_to_customers": [
            ("customer_id", "customer_id"),
            ("region", "region"),
        ],
    }


def test_a_composite_key_and_an_independent_link_are_kept_apart() -> None:
    native = _physical_fk_native(
        ["region", "referrer_code"],
        ["region", "referral_code"],
        [
            ("customer_id", "customer_id"),
            ("region", "region"),
            ("referrer_code", "referral_code"),
        ],
        customers_pk=["customer_id", "region"],
    )

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))

    assert _relationship_columns(ossie) == {
        "orders_to_customers": [
            ("customer_id", "customer_id"),
            ("region", "region"),
        ],
        "orders_referrer_code_to_customers": [("referrer_code", "referral_code")],
    }


def test_links_that_cover_a_composite_key_ambiguously_are_all_kept_apart() -> None:
    """Two links reach ``region``, so which one completes the key is unknown."""
    native = _physical_fk_native(
        ["region", "billing_region"],
        ["region"],
        [
            ("customer_id", "customer_id"),
            ("region", "region"),
            ("billing_region", "region"),
        ],
        customers_pk=["customer_id", "region"],
    )

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))

    assert all(len(columns) == 1 for columns in _relationship_columns(ossie).values())
    assert len(ossie["relationships"]) == 3


def test_relationship_reconciliation_preserves_catalog_only_records() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    schema = native["data_layer"]["databases"][0]["schemas"][0]
    orders = next(table for table in schema["tables"] if table["name"] == "orders")
    order_id = next(
        column["id"] for column in orders["columns"] if column["name"] == "order_id"
    )
    schema["tables"].append(
        {
            "id": "audit-table",
            "name": "audit_log",
            "description": "",
            "pk": [],
            "type": "table",
            "columns": [
                {
                    "id": "audit-column",
                    "name": "order_id",
                    "description": "",
                    "type": "",
                    "sample_values": [],
                    "is_nullable": True,
                    "is_unique": False,
                }
            ],
        }
    )
    audit_join = {
        "source_table_id": orders["id"],
        "target_table_id": "audit-table",
        "join_columns": [{"source": "order_id", "target": "order_id"}],
    }
    audit_fk = {
        "source_column_id": order_id,
        "target_column_id": "audit-column",
    }
    native["data_layer"]["joins"].append(audit_join)
    native["data_layer"]["foreign_keys"].append(audit_fk)

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    ossie.pop("relationships")
    regenerated = yaml.safe_load(
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie, sort_keys=False))
    )

    assert regenerated["data_layer"]["joins"] == [audit_join]
    assert regenerated["data_layer"]["foreign_keys"] == [audit_fk]


def test_multiple_databases_are_supported_and_name_falls_back() -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    model = ossie
    model["datasets"][1]["source"] = "crm.public.customers"
    model["relationships"] = []
    model["metrics"] = []

    native_yaml = convert_ossie_to_auto_ontology(yaml.safe_dump(ossie))
    native = yaml.safe_load(native_yaml)
    database_names = {
        schema["database_name"]
        for database in native["data_layer"]["databases"]
        for schema in database["schemas"]
    }
    restored = yaml.safe_load(convert_auto_ontology_to_ossie(native_yaml))

    assert database_names == {"analytics", "crm"}
    assert len(native["data_layer"]["databases"]) == 2
    assert restored["name"] == "auto_ontology_model"


def test_shared_physical_source_uses_one_catalog_table_and_valid_ossie(
    tmp_path: Path,
) -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    model = ossie
    model["datasets"].append(
        {
            "name": "order_amounts",
            "source": "analytics.public.orders",
            "fields": [
                {
                    "name": "subtotal",
                    "expression": {
                        "dialects": [{"dialect": "ANSI_SQL", "expression": "subtotal"}]
                    },
                }
            ],
        }
    )

    native_yaml = convert_ossie_to_auto_ontology(yaml.safe_dump(ossie, sort_keys=False))
    native = yaml.safe_load(native_yaml)
    order_tables = [
        table
        for database in native["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        if table["name"] == "orders"
    ]
    represented_ids = {
        term["name"]: term["represents"][0]
        for term in native["semantic_layer"]["terms"]
    }

    assert len(order_tables) == 1
    assert {column["name"] for column in order_tables[0]["columns"]} >= {
        "order_id",
        "subtotal",
        "discount",
    }
    assert represented_ids["orders"] == represented_ids["order_amounts"]

    restored = yaml.safe_load(convert_auto_ontology_to_ossie(native_yaml))
    assert {
        dataset["name"] for dataset in restored["datasets"]
    } >= {"orders", "order_amounts"}
    output_path = tmp_path / "shared-source.ossie.yaml"
    output_path.write_text(yaml.safe_dump(restored), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), str(output_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_cross_database_ossie_metric_is_rejected() -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    ossie["datasets"][1]["source"] = "crm.public.customers"

    with pytest.raises(AutoOntologyConversionError, match="spans multiple databases"):
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie))


def test_cross_database_full_query_field_is_rejected() -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    model = ossie
    model["datasets"][1]["source"] = "crm.public.customers"
    model["metrics"] = []
    model["relationships"] = []
    model["datasets"][0]["fields"].append(
        {
            "name": "remote_customer",
            "expression": {
                "dialects": [
                    {
                        "dialect": "ANSI_SQL",
                        "expression": (
                            "SELECT customers.customer_id "
                            "FROM crm.public.customers AS customers"
                        ),
                    }
                ]
            },
        }
    )

    with pytest.raises(
        AutoOntologyConversionError, match="SQL attribute.*multiple databases"
    ):
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie))


@pytest.mark.parametrize("kind", ["sql_attribute", "custom_analysis"])
def test_cross_database_auto_ontology_sql_objects_are_rejected(kind: str) -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    model = ossie
    model["datasets"][1]["source"] = "crm.public.customers"
    model["metrics"] = []
    model["relationships"] = []
    native = yaml.safe_load(convert_ossie_to_auto_ontology(yaml.safe_dump(ossie)))
    terms = {term["name"]: term for term in native["semantic_layer"]["terms"]}
    columns = {
        (schema["database_name"], table["name"], column["name"]): column["id"]
        for database in native["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        for column in table["columns"]
    }
    sql = (
        "SELECT orders.order_id, customers.customer_id "
        "FROM analytics.public.orders AS orders "
        "JOIN crm.public.customers AS customers "
        "ON orders.customer_id = customers.customer_id"
    )
    sql_column_is = [
        columns[("analytics", "orders", "order_id")],
        columns[("crm", "customers", "customer_id")],
    ]
    if kind == "sql_attribute":
        native["semantic_layer"]["sql_attributes"]["manual"].append(
            {
                "id": "cross-db-attribute",
                "name": "cross_db",
                "description": "",
                "sql": sql,
                "sql_column_is": sql_column_is,
                "term_id": terms["orders"]["id"],
            }
        )
    else:
        native["semantic_layer"]["custom_analyses"].append(
            {
                "id": "cross-db-analysis",
                "name": "cross_db",
                "description": "",
                "sql": sql,
                "sql_column_is": sql_column_is,
            }
        )

    with pytest.raises(AutoOntologyConversionError, match="spans multiple databases"):
        convert_auto_ontology_to_ossie(yaml.safe_dump(native))


def test_relationships_emit_join_physical_fk_and_semantic_fk() -> None:
    native = yaml.safe_load(convert_ossie_to_auto_ontology(_ossie_yaml()))

    assert len(native["data_layer"]["joins"]) == 1
    assert native["data_layer"]["joins"][0]["join_columns"] == [
        {"source": "customer_id", "target": "customer_id"}
    ]
    assert len(native["data_layer"]["foreign_keys"]) == 1
    assert len(native["semantic_layer"]["semantic_fks"]) == 1

    native["data_layer"]["joins"] = []
    restored = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    assert restored["relationships"][0]["from"] == "orders"
    assert restored["relationships"][0]["to"] == "customers"


def test_auto_ontology_requires_one_represented_table_per_term() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    term = native["semantic_layer"]["terms"][0]
    term["represents"].append(native["semantic_layer"]["terms"][1]["represents"][0])

    with pytest.raises(AutoOntologyConversionError, match="exactly one table"):
        convert_auto_ontology_to_ossie(yaml.safe_dump(native))


def test_duplicate_auto_ontology_term_names_are_rejected() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    native["semantic_layer"]["terms"][1]["name"] = "orders"

    with pytest.raises(
        AutoOntologyConversionError, match="Duplicate Auto Ontology term name"
    ):
        convert_auto_ontology_to_ossie(yaml.safe_dump(native))


def test_duplicate_auto_ontology_field_names_across_attribute_kinds_are_rejected() -> (
    None
):
    native = yaml.safe_load(_auto_ontology_yaml())
    native["semantic_layer"]["sql_attributes"]["manual"][0]["name"] = "order_id"

    with pytest.raises(AutoOntologyConversionError, match="Duplicate field name"):
        convert_auto_ontology_to_ossie(yaml.safe_dump(native))


def test_catalog_only_auto_ontology_has_no_representable_terms() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    native["semantic_layer"]["terms"] = []
    native["semantic_layer"]["sql_attributes"]["manual"] = []
    native["semantic_layer"]["custom_analyses"] = []

    with pytest.raises(AutoOntologyConversionError, match="no representable terms"):
        convert_auto_ontology_to_ossie(yaml.safe_dump(native))


@pytest.mark.parametrize(
    ("expression", "unit"),
    [
        ("DATEDIFF(day, order_date, CURRENT_TIMESTAMP())", "day"),
        ("DATEDIFF(hour, order_date, CURRENT_TIMESTAMP())", "hour"),
        ("TIMESTAMPDIFF(second, order_date, CURRENT_TIMESTAMP())", "second"),
        ("DATEADD(month, 1, order_date)", "month"),
    ],
)
def test_date_part_keywords_do_not_become_catalog_columns(
    expression: str,
    unit: str,
) -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    ossie["datasets"][0]["fields"].append(
        {
            "name": "order_age",
            "expression": {
                "dialects": [{"dialect": "ANSI_SQL", "expression": expression}]
            },
        }
    )

    native = yaml.safe_load(convert_ossie_to_auto_ontology(yaml.safe_dump(ossie)))
    orders = next(
        table
        for database in native["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        if table["name"] == "orders"
    )
    column_names = {column["name"] for column in orders["columns"]}
    attribute = next(
        item
        for item in native["semantic_layer"]["sql_attributes"]["manual"]
        if item["name"] == "order_age"
    )
    referenced = {column["id"]: column["name"] for column in orders["columns"]}

    assert unit not in column_names
    assert "order_date" in column_names
    assert unit not in {referenced.get(item) for item in attribute["sql_column_is"]}


def test_auto_ontology_sourced_catalog_is_never_widened_by_sql_identifiers() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    manual = native["semantic_layer"]["sql_attributes"]["manual"][0]
    manual["sql"] = (
        "SELECT DATEDIFF(day, order_date, CURRENT_TIMESTAMP()) + not_a_real_column "
        'AS "net_total" FROM "analytics"."public"."orders" AS "orders"'
    )
    before = {
        column["id"]
        for database in native["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        for column in table["columns"]
    }

    ossie = convert_auto_ontology_to_ossie(yaml.safe_dump(native))
    restored = yaml.safe_load(convert_ossie_to_auto_ontology(ossie))
    after_columns = [
        column
        for database in restored["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        for column in table["columns"]
    ]

    assert {column["id"] for column in after_columns} == before
    assert "not_a_real_column" not in {column["name"] for column in after_columns}


@pytest.mark.parametrize("version", ["0.2.0.dev0", "0.2.0", "0.2.1", "0.2.7.dev3"])
def test_any_release_in_the_supported_series_is_accepted(version: str) -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    ossie["version"] = version

    native = yaml.safe_load(convert_ossie_to_auto_ontology(yaml.safe_dump(ossie)))

    assert native["semantic_layer"]["terms"]


@pytest.mark.parametrize("version", ["0.1.9", "0.3.0", "1.0.0", "", "dev"])
def test_versions_outside_the_supported_series_are_rejected(version: str) -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    ossie["version"] = version

    with pytest.raises(AutoOntologyConversionError, match="Unsupported Ossie version"):
        convert_ossie_to_auto_ontology(yaml.safe_dump(ossie))


def test_dialect_specific_native_sql_survives_a_round_trip() -> None:
    """``TOP n`` is valid Snowflake but the default parser rejects it."""
    native = yaml.safe_load(_auto_ontology_yaml())
    manual = native["semantic_layer"]["sql_attributes"]["manual"][0]
    manual["sql"] = (
        'SELECT TOP 1 "orders"."subtotal" AS "net_total" '
        'FROM "analytics"."public"."orders" AS "orders"'
    )

    ossie = convert_auto_ontology_to_ossie(yaml.safe_dump(native))
    restored = yaml.safe_load(convert_ossie_to_auto_ontology(ossie))

    assert _manual_sql(restored, "net_total") == manual["sql"]


def test_native_sql_no_dialect_can_parse_is_carried_through_verbatim() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    manual = native["semantic_layer"]["sql_attributes"]["manual"][0]
    manual["sql"] = "SELECT not ((parseable by any dialect"

    ossie = convert_auto_ontology_to_ossie(yaml.safe_dump(native))
    restored = yaml.safe_load(convert_ossie_to_auto_ontology(ossie))

    assert _manual_sql(restored, "net_total") == manual["sql"]


@pytest.mark.parametrize("legacy_vendor", ["NVIDIA_GSF", "GSF"])
def test_extensions_written_under_the_gsf_name_are_still_read(
    legacy_vendor: str,
) -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    native["zones"] = [{"id": "zone-emea", "name": "emea"}]
    ossie = convert_auto_ontology_to_ossie(yaml.safe_dump(native))
    legacy_ossie = ossie.replace(
        "vendor_name: NVIDIA_AUTO_ONTOLOGY", f"vendor_name: {legacy_vendor}"
    )
    assert legacy_ossie != ossie

    restored = yaml.safe_load(convert_ossie_to_auto_ontology(legacy_ossie))

    assert restored == yaml.safe_load(convert_ossie_to_auto_ontology(ossie))
    assert restored["zones"] == native["zones"]


@pytest.mark.parametrize(
    "expression",
    [
        "DATEDIFF(month, day, CURRENT_TIMESTAMP())",
        "DATEDIFF(day, order_date)",
        "LAST_DAY(day)",
        "TRUNC(day)",
        "SUM(day)",
    ],
)
def test_columns_named_like_units_survive_outside_the_unit_slot(
    expression: str,
) -> None:
    """Only the unit argument itself is treated as a keyword."""
    ossie = yaml.safe_load(_ossie_yaml())
    ossie["datasets"][0]["fields"].append(
        {
            "name": "order_age",
            "expression": {
                "dialects": [{"dialect": "ANSI_SQL", "expression": expression}]
            },
        }
    )

    native = yaml.safe_load(convert_ossie_to_auto_ontology(yaml.safe_dump(ossie)))
    orders = next(
        table
        for database in native["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        if table["name"] == "orders"
    )

    assert "day" in {column["name"] for column in orders["columns"]}


def test_malformed_native_snapshot_fails_alike_in_both_paths() -> None:
    """Indexing and relationship reconciliation read the same snapshot."""
    native = yaml.safe_load(_auto_ontology_yaml())
    native["data_layer"]["databases"].append(
        deepcopy(native["data_layer"]["databases"][0])
    )

    with pytest.raises(
        AutoOntologyConversionError, match="Malformed NVIDIA_AUTO_ONTOLOGY"
    ):
        _index_native_document(native)

    with pytest.raises(
        AutoOntologyConversionError, match="Malformed NVIDIA_AUTO_ONTOLOGY"
    ):
        _reconcile_native_relationships(
            native,
            represented_table_ids=set(),
            foreign_keys=[],
            joins=[],
            semantic_fks=[],
        )


def test_expression_dialect_follows_the_auto_ontology_connection() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    native["data_layer"]["databases"][0]["dialect"] = "snowflake"

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    orders = next(
        dataset
        for dataset in ossie["datasets"]
        if dataset["name"] == "orders"
    )
    dialects = {
        field["name"]: field["expression"]["dialects"][0]["dialect"]
        for field in orders["fields"]
    }

    assert dialects["net_total"] == "SNOWFLAKE"
    # A bare column reference is dialect-neutral.
    assert dialects["order_id"] == "ANSI_SQL"


def test_dialects_ossie_cannot_name_stay_ansi() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    native["data_layer"]["databases"][0]["dialect"] = "mysql"

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    orders = next(
        dataset
        for dataset in ossie["datasets"]
        if dataset["name"] == "orders"
    )
    net_total = next(
        field for field in orders["fields"] if field["name"] == "net_total"
    )

    assert net_total["expression"]["dialects"][0]["dialect"] == "ANSI_SQL"


@pytest.mark.parametrize("datatype", sorted(_SQL_TYPE_BY_OSSIE_DATATYPE))
def test_every_mappable_datatype_survives_a_round_trip(datatype: str) -> None:
    ossie = yaml.safe_load(_ossie_yaml())
    orders = next(
        dataset
        for dataset in ossie["datasets"]
        if dataset["name"] == "orders"
    )
    next(field for field in orders["fields"] if field["name"] == "order_id")[
        "datatype"
    ] = datatype

    native = convert_ossie_to_auto_ontology(yaml.safe_dump(ossie))
    restored = yaml.safe_load(convert_auto_ontology_to_ossie(native))
    field = next(
        item
        for dataset in restored["datasets"]
        if dataset["name"] == "orders"
        for item in dataset["fields"]
        if item["name"] == "order_id"
    )

    assert field["datatype"] == datatype


def test_the_physical_type_chosen_for_each_datatype_maps_back_to_it() -> None:
    """The two directions have to be inverses or a cycle would drift."""
    for datatype, sql_type in _SQL_TYPE_BY_OSSIE_DATATYPE.items():
        assert _ossie_datatype(sql_type) == datatype


def test_mapping_covers_the_specs_datatype_vocabulary() -> None:
    """Fail loudly if the spec grows a logical type the mapping ignores."""
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    assert set(schema["$defs"]["DataType"]["enum"]) == {
        *_SQL_TYPE_BY_OSSIE_DATATYPE,
        "Opaque",
    }


@pytest.mark.parametrize(
    ("sql_type", "expected"),
    [
        ("TEXT", "String"),
        ("VARCHAR(255)", "String"),
        ("NUMBER(38,0)", "Integer"),
        ("NUMBER(12,2)", "Decimal"),
        ("DECIMAL", "Decimal"),
        ("double precision", "Float"),
        ("TIMESTAMP_NTZ(9)", "DateTime"),
        ("TIMESTAMP(6) WITH TIME ZONE", "DateTimeTz"),
        ("VARIANT", "Opaque"),
        ("GEOGRAPHY", "Opaque"),
        ("", None),
        (None, None),
    ],
)
def test_physical_types_map_onto_the_ossie_vocabulary(
    sql_type: str | None,
    expected: str | None,
) -> None:
    assert _ossie_datatype(sql_type) == expected


def test_auto_ontology_column_types_reach_the_ossie_field() -> None:
    native = yaml.safe_load(_auto_ontology_yaml())
    orders = next(
        table
        for database in native["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        if table["name"] == "orders"
    )
    for column in orders["columns"]:
        column["type"] = "NUMBER(38,0)" if column["name"] == "order_id" else "TEXT"

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    fields = {
        field["name"]: field.get("datatype")
        for dataset in ossie["datasets"]
        if dataset["name"] == "orders"
        for field in dataset["fields"]
    }

    assert fields["order_id"] == "Integer"
    assert fields["customer_id"] == "String"
    # A computed attribute has no column, so Auto Ontology holds no type for it.
    assert fields["net_total"] is None


def test_a_live_auto_ontology_column_type_outranks_an_ossie_datatype() -> None:
    """Auto Ontology reports the physical type; Ossie only names a logical one."""
    native = yaml.safe_load(_auto_ontology_yaml())
    orders = next(
        table
        for database in native["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        if table["name"] == "orders"
    )
    next(column for column in orders["columns"] if column["name"] == "order_id")[
        "type"
    ] = "NUMBER(38,0)"

    ossie = yaml.safe_load(convert_auto_ontology_to_ossie(yaml.safe_dump(native)))
    next(
        field
        for dataset in ossie["datasets"]
        if dataset["name"] == "orders"
        for field in dataset["fields"]
        if field["name"] == "order_id"
    )["datatype"] = "String"

    restored = yaml.safe_load(convert_ossie_to_auto_ontology(yaml.safe_dump(ossie)))
    column = next(
        column
        for database in restored["data_layer"]["databases"]
        for schema in database["schemas"]
        for table in schema["tables"]
        for column in table["columns"]
        if column["name"] == "order_id"
    )

    assert column["type"] == "NUMBER(38,0)"


def test_old_fictional_auto_ontology_root_is_rejected() -> None:
    old_shape = {
        "version": "1.0",
        "model": {"name": "sales"},
        "terms": [],
    }

    with pytest.raises(
        AutoOntologyConversionError, match="Unsupported Auto Ontology root"
    ):
        convert_auto_ontology_to_ossie(yaml.safe_dump(old_shape))


def test_model_name_override() -> None:
    result = yaml.safe_load(
        convert_auto_ontology_to_ossie(_auto_ontology_yaml(), model_name="sales")
    )
    assert result["name"] == "sales"


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
    ],
)
def test_simple_source_column(expression: str, expected: str | None) -> None:
    assert _simple_source_column(expression, "orders", "orders") == expected


def test_cli_converts_native_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ossie_path = tmp_path / "model.yaml"
    auto_ontology_path = tmp_path / "model.auto_ontology.yaml"
    ossie_path.write_text(_ossie_yaml(), encoding="utf-8")

    main(["export", "-i", str(ossie_path), "-o", str(auto_ontology_path)])
    assert set(yaml.safe_load(auto_ontology_path.read_text(encoding="utf-8"))) == {
        "data_layer",
        "semantic_layer",
        "zones",
    }

    main(["import", "-i", str(auto_ontology_path), "--name", "sales"])
    output = yaml.safe_load(capsys.readouterr().out)
    assert output["version"] == OSSIE_VERSION
    assert output["name"] == "sales"


@pytest.mark.parametrize("wrapper", [None, [], {}, [{"name": "old", "datasets": []}]])
def test_rejects_legacy_wrapper_even_with_root_model(wrapper: Any) -> None:
    document = yaml.safe_load(_ossie_yaml())
    document["semantic_model"] = wrapper

    with pytest.raises(AutoOntologyConversionError, match="at the root"):
        convert_ossie_to_auto_ontology(yaml.safe_dump(document))


@pytest.mark.parametrize("property_name", ["dialects", "vendors"])
def test_rejects_removed_root_metadata(property_name: str) -> None:
    document = yaml.safe_load(_ossie_yaml())
    document[property_name] = []

    with pytest.raises(
        AutoOntologyConversionError, match="Unsupported Ossie root properties"
    ):
        convert_ossie_to_auto_ontology(yaml.safe_dump(document))
