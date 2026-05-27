"""Tests for the bidirectional OSI ↔ Honeydew converter."""

from __future__ import annotations

import json
import os
import sys
import warnings
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from honeydew_osi_converter import (
    HoneydewConversionError,
    _assign_metrics_to_entities,
    _build_osi_metadata,
    _find_entity_in_expression,
    _honeydew_datatype_to_osi_dimension,
    _is_simple_identifier,
    _osi_field_to_honeydew_datatype,
    _parse_osi_source,
    _pick_ansi_expression,
    _read_osi_metadata,
    convert_honeydew_to_osi,
    convert_osi_to_honeydew,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

OSI_VERSION = "0.2.0.dev0"


def _osi(model_dict):
    return yaml.dump(
        {"version": OSI_VERSION, "semantic_model": [model_dict]},
        default_flow_style=False,
        sort_keys=False,
    )


def _minimal_osi_field(name, expr, is_dimension=True, is_time=False):
    field = {
        "name": name,
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": expr}]},
    }
    if is_dimension:
        field["dimension"] = {"is_time": is_time}
    return field


def _minimal_model():
    return {
        "name": "test_model",
        "datasets": [
            {
                "name": "orders",
                "source": "db.schema.orders",
                "primary_key": ["order_id"],
                "fields": [
                    _minimal_osi_field("order_id", "order_id"),
                    _minimal_osi_field("order_date", "order_date", is_time=True),
                    _minimal_osi_field("total", "total_amount", is_dimension=False),
                ],
            }
        ],
    }


def _write_workspace(tmp_dir, workspace_name, entities):
    """Write a minimal Honeydew workspace to tmp_dir."""
    workspace_path = os.path.join(tmp_dir, "workspace.yml")
    with open(workspace_path, "w") as f:
        yaml.dump({"type": "workspace", "name": workspace_name}, f)

    for e in entities:
        ename = e["name"]
        base = os.path.join(tmp_dir, "schema", ename)
        os.makedirs(os.path.join(base, "datasets"), exist_ok=True)
        os.makedirs(os.path.join(base, "attributes"), exist_ok=True)
        os.makedirs(os.path.join(base, "metrics"), exist_ok=True)

        entity_dict = {
            "type": "entity",
            "name": ename,
            "keys": e.get("keys", []),
            "key_dataset": e.get("key_dataset", ename),
            "relations": e.get("relations", []),
        }
        with open(os.path.join(base, f"{ename}.yml"), "w") as f:
            yaml.dump(entity_dict, f)

        ds_name = e.get("key_dataset", ename)
        ds_dict = {
            "type": "dataset",
            "entity": ename,
            "name": ds_name,
            "sql": e.get("sql", "DB.SCHEMA." + ename.upper()),
            "dataset_type": "table",
            "attributes": e.get("dataset_attrs", []),
        }
        with open(os.path.join(base, "datasets", f"{ds_name}.yml"), "w") as f:
            yaml.dump(ds_dict, f)

        for attr in e.get("calc_attrs", []):
            with open(os.path.join(base, "attributes", f"{attr['name']}.yml"), "w") as f:
                yaml.dump(attr, f)

        for m in e.get("metrics", []):
            with open(os.path.join(base, "metrics", f"{m['name']}.yml"), "w") as f:
                yaml.dump(m, f)


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests – helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestIsSimpleIdentifier:
    def test_plain_name(self):
        assert _is_simple_identifier("order_id") is True

    def test_with_spaces(self):
        assert _is_simple_identifier("SUM(x)") is False

    def test_with_dot(self):
        assert _is_simple_identifier("orders.id") is False

    def test_leading_number(self):
        assert _is_simple_identifier("1col") is False

    def test_underscore_prefix(self):
        assert _is_simple_identifier("_hidden") is True


class TestParseOsiSource:
    def test_table_reference(self):
        sql, dtype = _parse_osi_source("db.schema.table")
        assert sql == "db.schema.table" and dtype == "table"

    def test_select_query(self):
        _, dtype = _parse_osi_source("SELECT id FROM foo")
        assert dtype == "sql"

    def test_with_query(self):
        _, dtype = _parse_osi_source("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert dtype == "sql"

    def test_empty(self):
        sql, dtype = _parse_osi_source("")
        assert sql == "" and dtype == "table"


class TestPickAnsiExpression:
    def test_ansi_preferred(self):
        expr = {"dialects": [
            {"dialect": "SNOWFLAKE", "expression": "col::VARCHAR"},
            {"dialect": "ANSI_SQL", "expression": "col"},
        ]}
        assert _pick_ansi_expression(expr, "f") == "col"

    def test_fallback_to_first(self):
        expr = {"dialects": [{"dialect": "SNOWFLAKE", "expression": "col::VARCHAR"}]}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            result = _pick_ansi_expression(expr, "f")
        assert result == "col::VARCHAR"
        assert any("ANSI_SQL" in str(x.message) for x in w)

    def test_none_on_missing(self):
        assert _pick_ansi_expression(None, "f") is None
        assert _pick_ansi_expression({"dialects": []}, "f") is None


class TestOsiFieldDatatypes:
    def test_time_dimension(self):
        assert _osi_field_to_honeydew_datatype({"dimension": {"is_time": True}}) == "timestamp"

    def test_dimension(self):
        assert _osi_field_to_honeydew_datatype({"dimension": {"is_time": False}}) == "string"

    def test_fact(self):
        assert _osi_field_to_honeydew_datatype({}) == "number"


class TestHoneydewDatatypeToOsiDimension:
    def test_date(self):
        assert _honeydew_datatype_to_osi_dimension("date") == {"is_time": True}

    def test_timestamp(self):
        assert _honeydew_datatype_to_osi_dimension("timestamp") == {"is_time": True}

    def test_string(self):
        assert _honeydew_datatype_to_osi_dimension("string") == {"is_time": False}

    def test_bool(self):
        assert _honeydew_datatype_to_osi_dimension("bool") == {"is_time": False}

    def test_number(self):
        assert _honeydew_datatype_to_osi_dimension("number") is None

    def test_float(self):
        assert _honeydew_datatype_to_osi_dimension("float") is None


class TestFindEntityInExpression:
    def test_finds_entity(self):
        assert _find_entity_in_expression("SUM(orders.total)", {"orders", "customers"}) == "orders"

    def test_returns_first_match(self):
        result = _find_entity_in_expression("orders.a / customers.b", {"orders", "customers"})
        assert result == "orders"

    def test_no_match(self):
        assert _find_entity_in_expression("COUNT(*)", {"orders"}) is None

    def test_ignores_non_entity_prefixes(self):
        assert _find_entity_in_expression("SUM(foo.col)", {"orders"}) is None


class TestOsiMetadataHelpers:
    def test_build_and_read_ai_context_string(self):
        section = _build_osi_metadata(ai_context="orders, purchases")
        obj = {"metadata": [section]}
        result = _read_osi_metadata(obj)
        assert result["ai_context"] == "orders, purchases"

    def test_build_and_read_ai_context_dict(self):
        ctx = {"instructions": "Use for sales", "synonyms": ["orders", "purchases"]}
        section = _build_osi_metadata(ai_context=ctx)
        obj = {"metadata": [section]}
        result = _read_osi_metadata(obj)
        assert result["ai_context"] == ctx

    def test_build_and_read_unique_keys(self):
        uks = [["col1", "col2"], ["col3"]]
        section = _build_osi_metadata(unique_keys=uks)
        obj = {"metadata": [section]}
        result = _read_osi_metadata(obj)
        assert result["unique_keys"] == uks

    def test_build_and_read_custom_extensions(self):
        exts = [{"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "WH"}'}]
        section = _build_osi_metadata(custom_extensions=exts)
        obj = {"metadata": [section]}
        result = _read_osi_metadata(obj)
        assert result["custom_extensions"] == exts

    def test_returns_empty_when_no_osi_section(self):
        obj = {"metadata": [{"name": "other", "metadata": []}]}
        assert _read_osi_metadata(obj) == {}

    def test_returns_empty_when_no_metadata(self):
        assert _read_osi_metadata({}) == {}

    def test_build_returns_none_when_nothing_to_store(self):
        assert _build_osi_metadata() is None


class TestAssignMetricsToEntities:
    def test_assigns_by_expression(self):
        metrics = [{"name": "total", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.total)"}]}}]
        result = _assign_metrics_to_entities(metrics, ["orders", "customers"])
        assert "total" in [m["name"] for m in result.get("orders", [])]

    def test_honeydew_hint_takes_priority(self):
        metrics = [{
            "name": "cnt",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.x)"}]},
            "custom_extensions": [{"vendor_name": "HONEYDEW", "data": '{"entity": "customers"}'}],
        }]
        result = _assign_metrics_to_entities(metrics, ["orders", "customers"])
        # hint says customers even though expression references orders
        assert "cnt" in [m["name"] for m in result.get("customers", [])]
        assert "orders" not in result

    def test_falls_back_to_first_entity(self):
        metrics = [{"name": "cnt", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(*)"}]}}]
        with warnings.catch_warnings(record=True):
            result = _assign_metrics_to_entities(metrics, ["orders"])
        assert "cnt" in [m["name"] for m in result.get("orders", [])]

    def test_no_entities(self):
        metrics = [{"name": "m", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(*)"}]}}]
        with warnings.catch_warnings(record=True):
            result = _assign_metrics_to_entities(metrics, [])
        assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# OSI → Honeydew integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestOsiToHoneydew:
    def test_workspace_yml_created(self):
        files = convert_osi_to_honeydew(_osi(_minimal_model()))
        ws = yaml.safe_load(files["workspace.yml"])
        assert ws["name"] == "test_model" and ws["type"] == "workspace"

    def test_entity_yml_created(self):
        files = convert_osi_to_honeydew(_osi(_minimal_model()))
        entity = yaml.safe_load(files["schema/orders/orders.yml"])
        assert entity["name"] == "orders"
        assert entity["keys"] == ["order_id"]
        assert entity["key_dataset"] == "orders"

    def test_dataset_yml_created(self):
        files = convert_osi_to_honeydew(_osi(_minimal_model()))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        assert ds["sql"] == "db.schema.orders"
        assert ds["dataset_type"] == "table"

    def test_simple_fields_become_dataset_attributes(self):
        files = convert_osi_to_honeydew(_osi(_minimal_model()))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        names = [a["name"] for a in ds["attributes"]]
        assert "order_id" in names and "order_date" in names and "total" in names

    def test_time_field_gets_timestamp_datatype(self):
        files = convert_osi_to_honeydew(_osi(_minimal_model()))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        attrs = {a["name"]: a for a in ds["attributes"]}
        assert attrs["order_date"]["datatype"] == "timestamp"

    def test_fact_field_gets_number_datatype(self):
        files = convert_osi_to_honeydew(_osi(_minimal_model()))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        attrs = {a["name"]: a for a in ds["attributes"]}
        assert attrs["total"]["datatype"] == "number"

    def test_complex_expression_becomes_calculated_attribute(self):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "disc_price",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "price * (1 - discount)"}]},
            "dimension": {"is_time": False},
        }]}]}
        files = convert_osi_to_honeydew(_osi(model))
        assert "schema/orders/attributes/disc_price.yml" in files
        calc = yaml.safe_load(files["schema/orders/attributes/disc_price.yml"])
        assert calc["type"] == "calculated_attribute"
        assert calc["sql"] == "price * (1 - discount)"

    def test_label_mapped_to_honeydew_labels(self):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "status",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]},
            "dimension": {"is_time": False},
            "label": "sales",
        }]}]}
        files = convert_osi_to_honeydew(_osi(model))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        attrs = {a["name"]: a for a in ds["attributes"]}
        assert "sales" in attrs["status"]["labels"]

    def test_ai_context_string_merged_into_description(self):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "total"}]},
            "description": "Base desc",
            "ai_context": "revenue, earnings",
        }]}]}
        files = convert_osi_to_honeydew(_osi(model))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        attrs = {a["name"]: a for a in ds["attributes"]}
        assert "revenue, earnings" in attrs["total"]["description"]

    def test_ai_context_dict_instructions_merged_into_description(self):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "total"}]},
            "ai_context": {"instructions": "Use for revenue", "synonyms": ["rev", "earnings"]},
        }]}]}
        files = convert_osi_to_honeydew(_osi(model))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        attrs = {a["name"]: a for a in ds["attributes"]}
        assert "Use for revenue" in attrs["total"]["description"]
        assert "rev" in attrs["total"]["labels"]

    def test_ai_context_dict_stored_in_metadata(self):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "total"}]},
            "ai_context": {"instructions": "Use for revenue", "synonyms": ["rev"]},
        }]}]}
        files = convert_osi_to_honeydew(_osi(model))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        attr = next(a for a in ds["attributes"] if a["name"] == "total")
        # Should be in the osi metadata section
        osi_section = next((s for s in attr.get("metadata", []) if s["name"] == "osi"), None)
        assert osi_section is not None
        ai_item = next((i for i in osi_section["metadata"] if i["name"] == "ai_context"), None)
        assert ai_item is not None

    def test_unique_keys_stored_in_entity_metadata(self):
        model = {"name": "m", "datasets": [{"name": "items", "source": "db.s.items",
            "primary_key": ["item_id"],
            "unique_keys": [["sku"], ["item_id", "variant"]],
            "fields": []}]}
        files = convert_osi_to_honeydew(_osi(model))
        entity = yaml.safe_load(files["schema/items/items.yml"])
        osi_section = next((s for s in entity.get("metadata", []) if s["name"] == "osi"), None)
        assert osi_section is not None
        uk_item = next((i for i in osi_section["metadata"] if i["name"] == "unique_keys"), None)
        assert uk_item is not None
        assert json.loads(uk_item["value"]) == [["sku"], ["item_id", "variant"]]

    def test_non_honeydew_custom_extensions_stored_in_metadata(self):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "custom_extensions": [{"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "WH"}'}],
            "fields": []}]}
        files = convert_osi_to_honeydew(_osi(model))
        entity = yaml.safe_load(files["schema/orders/orders.yml"])
        osi_section = next((s for s in entity.get("metadata", []) if s["name"] == "osi"), None)
        assert osi_section is not None
        ext_item = next((i for i in osi_section["metadata"] if i["name"] == "custom_extensions"), None)
        assert ext_item is not None
        exts = json.loads(ext_item["value"])
        assert any(e["vendor_name"] == "SNOWFLAKE" for e in exts)

    def test_relationship_name_stored_in_relation(self):
        model = {"name": "m", "datasets": [
            {"name": "orders", "source": "db.s.orders", "fields": []},
            {"name": "customers", "source": "db.s.customers", "fields": []},
        ], "relationships": [{"name": "orders_to_customers", "from": "orders", "to": "customers",
            "from_columns": ["cid"], "to_columns": ["id"]}]}
        files = convert_osi_to_honeydew(_osi(model))
        entity = yaml.safe_load(files["schema/orders/orders.yml"])
        assert entity["relations"][0]["name"] == "orders_to_customers"

    def test_model_ai_context_stored_in_workspace_metadata(self):
        model = {"name": "m", "datasets": [],
            "ai_context": {"instructions": "Use for retail analytics", "synonyms": ["store"]}}
        files = convert_osi_to_honeydew(_osi(model))
        ws = yaml.safe_load(files["workspace.yml"])
        osi_section = next((s for s in ws.get("metadata", []) if s["name"] == "osi"), None)
        assert osi_section is not None

    def test_relationship_added_to_entity(self):
        model = {"name": "m", "datasets": [
            {"name": "orders", "source": "db.s.orders", "fields": []},
            {"name": "customers", "source": "db.s.customers", "fields": []},
        ], "relationships": [{"name": "r", "from": "orders", "to": "customers",
            "from_columns": ["cid"], "to_columns": ["id"]}]}
        files = convert_osi_to_honeydew(_osi(model))
        entity = yaml.safe_load(files["schema/orders/orders.yml"])
        assert len(entity["relations"]) == 1
        rel = entity["relations"][0]
        assert rel["target_entity"] == "customers"
        assert rel["rel_type"] == "many-to-one"
        assert rel["connection"] == [{"src_field": "cid", "target_field": "id"}]

    def test_to_entity_has_no_relation(self):
        model = {"name": "m", "datasets": [
            {"name": "orders", "source": "db.s.orders", "fields": []},
            {"name": "customers", "source": "db.s.customers", "fields": []},
        ], "relationships": [{"name": "r", "from": "orders", "to": "customers",
            "from_columns": ["cid"], "to_columns": ["id"]}]}
        files = convert_osi_to_honeydew(_osi(model))
        entity = yaml.safe_load(files["schema/customers/customers.yml"])
        assert entity["relations"] == []

    def test_metric_assigned_by_expression_entity(self):
        model = {"name": "m",
            "datasets": [{"name": "orders", "source": "db.s.orders", "fields": []}],
            "metrics": [{"name": "total", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.total)"}]}}]}
        files = convert_osi_to_honeydew(_osi(model))
        assert "schema/orders/metrics/total.yml" in files

    def test_metric_entity_hint_overrides_expression(self):
        model = {"name": "m",
            "datasets": [
                {"name": "orders", "source": "db.s.orders", "fields": []},
                {"name": "customers", "source": "db.s.customers", "fields": []},
            ],
            "metrics": [{
                "name": "cnt",
                "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.x)"}]},
                "custom_extensions": [{"vendor_name": "HONEYDEW", "data": '{"entity": "customers"}'}],
            }]}
        files = convert_osi_to_honeydew(_osi(model))
        assert "schema/customers/metrics/cnt.yml" in files
        assert "schema/orders/metrics/cnt.yml" not in files

    def test_invalid_version_raises(self):
        with pytest.raises(HoneydewConversionError, match="Unsupported"):
            convert_osi_to_honeydew("version: '9.9.9'\nsemantic_model:\n  - name: m\n")

    def test_missing_semantic_model_raises(self):
        with pytest.raises(HoneydewConversionError):
            convert_osi_to_honeydew(f"version: '{OSI_VERSION}'\n")

    def test_subquery_source_uses_sql_type(self):
        model = {"name": "m", "datasets": [{"name": "orders",
            "source": "SELECT * FROM raw.orders WHERE active = true", "fields": []}]}
        files = convert_osi_to_honeydew(_osi(model))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        assert ds["dataset_type"] == "sql"

    def test_composite_primary_key(self):
        model = {"name": "m", "datasets": [{"name": "li", "source": "db.s.li",
            "primary_key": ["order_id", "line_number"], "fields": []}]}
        files = convert_osi_to_honeydew(_osi(model))
        entity = yaml.safe_load(files["schema/li/li.yml"])
        assert entity["keys"] == ["order_id", "line_number"]

    def test_multiple_semantic_models_warns(self):
        doc = yaml.dump({"version": OSI_VERSION, "semantic_model": [
            {"name": "m1", "datasets": []},
            {"name": "m2", "datasets": []},
        ]})
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            files = convert_osi_to_honeydew(doc)
        assert any("only the first" in str(x.message) for x in w)
        assert yaml.safe_load(files["workspace.yml"])["name"] == "m1"


# ─────────────────────────────────────────────────────────────────────────────
# Honeydew → OSI integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHoneydewToOsi:
    def test_basic_conversion(self, tmp_path):
        _write_workspace(str(tmp_path), "tpch", [{
            "name": "orders", "keys": ["orderkey"], "key_dataset": "tpch_orders",
            "sql": "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS",
            "dataset_attrs": [
                {"column": "o_orderkey", "name": "orderkey", "datatype": "number"},
                {"column": "o_orderdate", "name": "orderdate", "datatype": "date"},
            ],
        }])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        sm = result["semantic_model"][0]
        assert sm["name"] == "tpch"
        ds = sm["datasets"][0]
        assert ds["source"] == "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS"
        assert ds["primary_key"] == ["orderkey"]

    def test_field_types_from_datatypes(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [{"name": "orders", "keys": ["id"],
            "key_dataset": "orders", "sql": "db.s.orders",
            "dataset_attrs": [
                {"column": "id", "name": "id", "datatype": "number"},
                {"column": "status", "name": "status", "datatype": "string"},
                {"column": "created_at", "name": "created_at", "datatype": "timestamp"},
            ]}])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        fields = {f["name"]: f for f in result["semantic_model"][0]["datasets"][0]["fields"]}
        assert fields["id"].get("dimension") is None
        assert fields["status"]["dimension"] == {"is_time": False}
        assert fields["created_at"]["dimension"] == {"is_time": True}

    def test_labels_become_osi_label_and_ai_context(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [{"name": "orders", "keys": ["id"],
            "key_dataset": "orders", "sql": "db.s.orders",
            "dataset_attrs": [
                {"column": "status", "name": "status", "datatype": "string",
                 "labels": ["sales", "reporting"]},
            ]}])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        f = next(f for f in result["semantic_model"][0]["datasets"][0]["fields"] if f["name"] == "status")
        assert f["label"] == "sales"
        assert "sales" in (f.get("ai_context") or {}).get("synonyms", [])

    def test_many_to_one_relation_to_osi(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [
            {"name": "orders", "keys": ["order_id"], "key_dataset": "orders", "sql": "db.s.orders",
             "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                            "connection": [{"src_field": "customer_id", "target_field": "id"}]}],
             "dataset_attrs": []},
            {"name": "customers", "keys": ["id"], "key_dataset": "customers", "sql": "db.s.customers", "dataset_attrs": []},
        ])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        rels = result["semantic_model"][0]["relationships"]
        assert len(rels) == 1
        assert rels[0]["from"] == "orders" and rels[0]["to"] == "customers"

    def test_one_to_many_direction_flipped(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [
            {"name": "customers", "keys": ["id"], "key_dataset": "customers", "sql": "db.s.customers",
             "relations": [{"target_entity": "orders", "rel_type": "one-to-many",
                            "connection": [{"src_field": "id", "target_field": "customer_id"}]}],
             "dataset_attrs": []},
            {"name": "orders", "keys": ["order_id"], "key_dataset": "orders", "sql": "db.s.orders", "dataset_attrs": []},
        ])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        rel = result["semantic_model"][0]["relationships"][0]
        assert rel["from"] == "orders" and rel["to"] == "customers"

    def test_duplicate_relations_deduplicated(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [
            {"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "db.s.orders",
             "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                            "connection": [{"src_field": "cid", "target_field": "id"}]}],
             "dataset_attrs": []},
            {"name": "customers", "keys": ["id"], "key_dataset": "customers", "sql": "db.s.customers",
             "relations": [{"target_entity": "orders", "rel_type": "one-to-many",
                            "connection": [{"src_field": "id", "target_field": "cid"}]}],
             "dataset_attrs": []},
        ])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        assert len(result["semantic_model"][0].get("relationships", [])) == 1

    def test_metrics_converted(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [{"name": "orders", "keys": ["id"],
            "key_dataset": "orders", "sql": "db.s.orders", "dataset_attrs": [],
            "metrics": [{"type": "metric", "entity": "orders", "name": "count",
                         "datatype": "number", "sql": "COUNT(*)"}]}])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        m = result["semantic_model"][0]["metrics"][0]
        assert m["name"] == "count"
        assert m["expression"]["dialects"][0]["expression"] == "COUNT(*)"

    def test_metric_entity_preserved_in_custom_extension(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [{"name": "orders", "keys": ["id"],
            "key_dataset": "orders", "sql": "db.s.orders", "dataset_attrs": [],
            "metrics": [{"type": "metric", "entity": "orders", "name": "cnt",
                         "datatype": "number", "sql": "COUNT(*)"}]}])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        m = result["semantic_model"][0]["metrics"][0]
        ext = m["custom_extensions"][0]
        assert ext["vendor_name"] == "HONEYDEW"
        assert json.loads(ext["data"])["entity"] == "orders"

    def test_calculated_attribute_as_field(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [{"name": "orders", "keys": ["id"],
            "key_dataset": "orders", "sql": "db.s.orders", "dataset_attrs": [],
            "calc_attrs": [{"type": "calculated_attribute", "entity": "orders",
                            "name": "discounted", "datatype": "number",
                            "sql": "orders.price * (1 - orders.discount)"}]}])
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        fields = {f["name"]: f for f in result["semantic_model"][0]["datasets"][0]["fields"]}
        assert "discounted" in fields
        assert "orders.price" in fields["discounted"]["expression"]["dialects"][0]["expression"]

    def test_missing_workspace_yml_raises(self, tmp_path):
        with pytest.raises(HoneydewConversionError, match="workspace.yml"):
            convert_honeydew_to_osi(str(tmp_path))

    def test_missing_schema_dir_produces_empty_model(self, tmp_path):
        (tmp_path / "workspace.yml").write_text(yaml.dump({"type": "workspace", "name": "ws"}))
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        assert result["semantic_model"][0]["datasets"] == []

    def test_vendors_includes_honeydew(self, tmp_path):
        (tmp_path / "workspace.yml").write_text(yaml.dump({"type": "workspace", "name": "ws"}))
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        assert "HONEYDEW" in result.get("vendors", [])

    def test_empty_metrics_skipped(self, tmp_path):
        _write_workspace(str(tmp_path), "ws", [{"name": "orders", "keys": ["id"],
            "key_dataset": "orders", "sql": "db.s.orders", "dataset_attrs": [],
            "metrics": [{"type": "metric", "entity": "orders", "name": "bad",
                         "datatype": "number", "sql": ""}]}])
        with warnings.catch_warnings(record=True):
            result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        assert "metrics" not in result["semantic_model"][0]


# ─────────────────────────────────────────────────────────────────────────────
# Round-trip tests (idempotency)
# ─────────────────────────────────────────────────────────────────────────────

class TestOsiToHoneydewToOsiRoundTrip:
    """OSI → Honeydew → OSI: verify key fields survive both legs."""

    def _roundtrip(self, model_dict, tmp_path):
        files = convert_osi_to_honeydew(_osi(model_dict))
        for rel_path, content in files.items():
            p = tmp_path / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))["semantic_model"][0]

    def test_name_and_description_preserved(self, tmp_path):
        model = {"name": "retail", "description": "Retail model", "datasets": []}
        sm = self._roundtrip(model, tmp_path)
        assert sm["name"] == "retail"
        assert sm["description"] == "Retail model"

    def test_primary_key_preserved(self, tmp_path):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "primary_key": ["order_id"], "fields": []}]}
        sm = self._roundtrip(model, tmp_path)
        assert sm["datasets"][0]["primary_key"] == ["order_id"]

    def test_composite_primary_key_preserved(self, tmp_path):
        model = {"name": "m", "datasets": [{"name": "li", "source": "db.s.li",
            "primary_key": ["order_id", "line_no"], "fields": []}]}
        sm = self._roundtrip(model, tmp_path)
        assert sm["datasets"][0]["primary_key"] == ["order_id", "line_no"]

    def test_unique_keys_preserved(self, tmp_path):
        model = {"name": "m", "datasets": [{"name": "items", "source": "db.s.items",
            "primary_key": ["id"],
            "unique_keys": [["sku"], ["id", "variant"]],
            "fields": []}]}
        sm = self._roundtrip(model, tmp_path)
        assert sm["datasets"][0]["unique_keys"] == [["sku"], ["id", "variant"]]

    def test_field_label_preserved(self, tmp_path):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "status", "label": "sales",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]},
                        "dimension": {"is_time": False}}]}]}
        sm = self._roundtrip(model, tmp_path)
        f = next(f for f in sm["datasets"][0]["fields"] if f["name"] == "status")
        assert f["label"] == "sales"

    def test_ai_context_string_preserved(self, tmp_path):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "status",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]},
                        "ai_context": "order status, order state",
                        "dimension": {"is_time": False}}]}]}
        sm = self._roundtrip(model, tmp_path)
        f = next(f for f in sm["datasets"][0]["fields"] if f["name"] == "status")
        # String ai_context is merged into description; exact form may vary
        assert f.get("description") or f.get("ai_context")

    def test_ai_context_dict_preserved(self, tmp_path):
        ctx = {"instructions": "Use for revenue analysis", "synonyms": ["revenue", "sales"]}
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "total",
                        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "total"}]},
                        "ai_context": ctx}]}]}
        sm = self._roundtrip(model, tmp_path)
        f = next(f for f in sm["datasets"][0]["fields"] if f["name"] == "total")
        assert f.get("ai_context") == ctx

    def test_model_ai_context_preserved(self, tmp_path):
        ctx = {"instructions": "Retail analytics", "synonyms": ["store"]}
        model = {"name": "m", "ai_context": ctx, "datasets": []}
        sm = self._roundtrip(model, tmp_path)
        assert sm.get("ai_context") == ctx

    def test_non_honeydew_custom_extensions_preserved(self, tmp_path):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "custom_extensions": [{"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "WH"}'}],
            "fields": []}]}
        sm = self._roundtrip(model, tmp_path)
        exts = sm["datasets"][0].get("custom_extensions") or []
        assert any(e["vendor_name"] == "SNOWFLAKE" for e in exts)

    def test_relationship_name_preserved(self, tmp_path):
        model = {"name": "m", "datasets": [
            {"name": "orders", "source": "db.s.orders", "fields": []},
            {"name": "customers", "source": "db.s.customers", "fields": []},
        ], "relationships": [{"name": "orders_to_customers", "from": "orders", "to": "customers",
            "from_columns": ["cid"], "to_columns": ["id"]}]}
        sm = self._roundtrip(model, tmp_path)
        assert sm["relationships"][0]["name"] == "orders_to_customers"

    def test_relationship_columns_preserved(self, tmp_path):
        model = {"name": "m", "datasets": [
            {"name": "orders", "source": "db.s.orders", "fields": []},
            {"name": "customers", "source": "db.s.customers", "fields": []},
        ], "relationships": [{"name": "r", "from": "orders", "to": "customers",
            "from_columns": ["cid"], "to_columns": ["id"]}]}
        sm = self._roundtrip(model, tmp_path)
        rel = sm["relationships"][0]
        assert rel["from_columns"] == ["cid"] and rel["to_columns"] == ["id"]

    def test_metric_name_and_expression_preserved(self, tmp_path):
        model = {"name": "m",
            "datasets": [{"name": "orders", "source": "db.s.orders", "fields": []}],
            "metrics": [{"name": "total_revenue", "description": "Sum of sales",
                         "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.total)"}]}}]}
        sm = self._roundtrip(model, tmp_path)
        m = sm["metrics"][0]
        assert m["name"] == "total_revenue"
        assert m["expression"]["dialects"][0]["expression"] == "SUM(orders.total)"
        assert m["description"] == "Sum of sales"

    def test_tpcds_example_roundtrip(self, tmp_path):
        tpcds_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "examples" / "tpcds_semantic_model.yaml"
        )
        if not tpcds_path.exists():
            pytest.skip("TPC-DS example not found")
        osi_yaml = tpcds_path.read_text()
        files = convert_osi_to_honeydew(osi_yaml)
        for rel_path, content in files.items():
            p = tmp_path / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        sm = result["semantic_model"][0]
        assert sm["name"] == "tpcds_retail_model"
        ds_names = {ds["name"] for ds in sm["datasets"]}
        assert "store_sales" in ds_names and "customer" in ds_names


class TestHoneydewToOsiToHoneydewRoundTrip:
    """Honeydew → OSI → Honeydew: verify Honeydew-specific fields survive."""

    def _roundtrip(self, entities, tmp_path):
        _write_workspace(str(tmp_path), "ws", entities)
        osi_yaml = convert_honeydew_to_osi(str(tmp_path))
        files = convert_osi_to_honeydew(osi_yaml)
        # Write to a second directory
        out_dir = tmp_path / "out"
        for rel_path, content in files.items():
            p = out_dir / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return out_dir

    def test_entity_name_and_keys_preserved(self, tmp_path):
        out_dir = self._roundtrip([{
            "name": "orders", "keys": ["order_id"], "key_dataset": "orders",
            "sql": "DB.S.ORDERS", "dataset_attrs": [],
        }], tmp_path)
        entity = yaml.safe_load((out_dir / "schema/orders/orders.yml").read_text())
        assert entity["name"] == "orders"
        assert entity["keys"] == ["order_id"]

    def test_source_preserved(self, tmp_path):
        out_dir = self._roundtrip([{
            "name": "orders", "keys": ["id"], "key_dataset": "orders",
            "sql": "DB.SCHEMA.ORDERS", "dataset_attrs": [],
        }], tmp_path)
        ds = yaml.safe_load((out_dir / "schema/orders/datasets/orders.yml").read_text())
        assert ds["sql"] == "DB.SCHEMA.ORDERS"

    def test_column_attributes_preserved(self, tmp_path):
        out_dir = self._roundtrip([{
            "name": "orders", "keys": ["id"], "key_dataset": "orders",
            "sql": "DB.S.ORDERS",
            "dataset_attrs": [
                {"column": "o_id", "name": "id", "datatype": "number"},
                {"column": "o_status", "name": "status", "datatype": "string"},
            ],
        }], tmp_path)
        ds = yaml.safe_load((out_dir / "schema/orders/datasets/orders.yml").read_text())
        attrs = {a["name"]: a for a in ds["attributes"]}
        assert attrs["id"]["column"] == "o_id"
        assert attrs["status"]["datatype"] == "string"

    def test_labels_preserved_on_column(self, tmp_path):
        out_dir = self._roundtrip([{
            "name": "orders", "keys": ["id"], "key_dataset": "orders",
            "sql": "DB.S.ORDERS",
            "dataset_attrs": [
                {"column": "status", "name": "status", "datatype": "string", "labels": ["sales"]},
            ],
        }], tmp_path)
        ds = yaml.safe_load((out_dir / "schema/orders/datasets/orders.yml").read_text())
        attrs = {a["name"]: a for a in ds["attributes"]}
        assert "sales" in attrs["status"].get("labels", [])

    def test_calculated_attribute_sql_preserved(self, tmp_path):
        out_dir = self._roundtrip([{
            "name": "orders", "keys": ["id"], "key_dataset": "orders",
            "sql": "DB.S.ORDERS", "dataset_attrs": [],
            "calc_attrs": [{"type": "calculated_attribute", "entity": "orders",
                            "name": "disc", "datatype": "number",
                            "sql": "orders.price * (1 - orders.discount)"}],
        }], tmp_path)
        calc = yaml.safe_load((out_dir / "schema/orders/attributes/disc.yml").read_text())
        assert calc["sql"] == "orders.price * (1 - orders.discount)"

    def test_metric_entity_assignment_preserved(self, tmp_path):
        out_dir = self._roundtrip([{
            "name": "orders", "keys": ["id"], "key_dataset": "orders",
            "sql": "DB.S.ORDERS", "dataset_attrs": [],
            "metrics": [{"type": "metric", "entity": "orders", "name": "cnt",
                         "datatype": "number", "sql": "COUNT(*)"}],
        }], tmp_path)
        m = yaml.safe_load((out_dir / "schema/orders/metrics/cnt.yml").read_text())
        assert m["entity"] == "orders"
        assert m["sql"] == "COUNT(*)"

    def test_relation_preserved(self, tmp_path):
        out_dir = self._roundtrip([
            {"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
             "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                            "connection": [{"src_field": "cid", "target_field": "id"}]}],
             "dataset_attrs": []},
            {"name": "customers", "keys": ["id"], "key_dataset": "customers",
             "sql": "DB.S.CUSTOMERS", "dataset_attrs": []},
        ], tmp_path)
        entity = yaml.safe_load((out_dir / "schema/orders/orders.yml").read_text())
        assert entity["relations"][0]["target_entity"] == "customers"
        assert entity["relations"][0]["connection"][0]["src_field"] == "cid"

    def test_bool_datatype_preserved(self, tmp_path):
        out_dir = self._roundtrip([{
            "name": "orders", "keys": ["id"], "key_dataset": "orders",
            "sql": "DB.S.ORDERS",
            "dataset_attrs": [
                {"column": "is_active", "name": "is_active", "datatype": "bool"},
            ],
        }], tmp_path)
        ds = yaml.safe_load((out_dir / "schema/orders/datasets/orders.yml").read_text())
        attrs = {a["name"]: a for a in ds["attributes"]}
        assert attrs["is_active"]["datatype"] == "bool"

    def test_connection_expr_preserved(self, tmp_path):
        out_dir = self._roundtrip([
            {"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
             "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                            "connection_expr": {"sql": "orders.cid = customers.id AND orders.region = customers.region"}}],
             "dataset_attrs": []},
            {"name": "customers", "keys": ["id"], "key_dataset": "customers",
             "sql": "DB.S.CUSTOMERS", "dataset_attrs": []},
        ], tmp_path)
        entity = yaml.safe_load((out_dir / "schema/orders/orders.yml").read_text())
        rel = entity["relations"][0]
        assert rel.get("connection_expr", {}).get("sql") == "orders.cid = customers.id AND orders.region = customers.region"

    def test_calc_attr_with_simple_identifier_sql_preserved(self, tmp_path):
        out_dir = self._roundtrip([{
            "name": "orders", "keys": ["id"], "key_dataset": "orders",
            "sql": "DB.S.ORDERS", "dataset_attrs": [],
            "calc_attrs": [{"type": "calculated_attribute", "entity": "orders",
                            "name": "revenue", "datatype": "number", "sql": "revenue"}],
        }], tmp_path)
        # sql='revenue' is a simple identifier — must still come back as calculated_attribute
        calc_path = out_dir / "schema/orders/attributes/revenue.yml"
        assert calc_path.exists(), "calculated_attribute with simple-id sql should not become a dataset column"
        calc = yaml.safe_load(calc_path.read_text())
        assert calc["sql"] == "revenue"


# ─────────────────────────────────────────────────────────────────────────────
# Bug-fix regression tests
# ─────────────────────────────────────────────────────────────────────────────

class TestBugFixes:
    def test_empty_string_expression_skipped(self):
        model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "bad",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": ""}]},
            "dimension": {"is_time": False},
        }]}]}
        files = convert_osi_to_honeydew(_osi(model))
        ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
        names = [a["name"] for a in ds["attributes"]]
        assert "bad" not in names
        assert "schema/orders/attributes/bad.yml" not in files

    def test_duplicate_metric_name_warns(self):
        model = {"name": "m",
            "datasets": [{"name": "orders", "source": "db.s.orders", "fields": []}],
            "metrics": [
                {"name": "total", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.a)"}]}},
                {"name": "total", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.b)"}]}},
            ]}
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            files = convert_osi_to_honeydew(_osi(model))
        assert any("total" in str(x.message) for x in w)
        # Last definition wins
        m = yaml.safe_load(files["schema/orders/metrics/total.yml"])
        assert "orders.b" in m["sql"]

    def test_metric_string_ai_context_preserved_in_roundtrip(self, tmp_path):
        model = {"name": "m",
            "datasets": [{"name": "orders", "source": "db.s.orders", "fields": []}],
            "metrics": [{"name": "rev", "ai_context": "Use for revenue analysis",
                         "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.total)"}]}}]}
        files = convert_osi_to_honeydew(_osi(model))
        for rel_path, content in files.items():
            p = tmp_path / rel_path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
        m = result["semantic_model"][0]["metrics"][0]
        assert m.get("ai_context") == "Use for revenue analysis"

    def test_malformed_osi_metadata_json_warns(self, tmp_path):
        ws_path = tmp_path / "workspace.yml"
        ws_path.write_text(yaml.dump({"type": "workspace", "name": "ws"}))
        base = tmp_path / "schema" / "orders"
        (base / "datasets").mkdir(parents=True)
        entity = {
            "type": "entity", "name": "orders", "keys": ["id"], "key_dataset": "orders",
            "relations": [],
            "metadata": [{"name": "osi", "metadata": [
                {"name": "unique_keys", "value": "[broken json"},
            ]}],
        }
        (base / "orders.yml").write_text(yaml.dump(entity))
        (base / "datasets" / "orders.yml").write_text(yaml.dump(
            {"type": "dataset", "entity": "orders", "name": "orders",
             "sql": "DB.S.ORDERS", "dataset_type": "table", "attributes": []}
        ))
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            convert_honeydew_to_osi(str(tmp_path))
        assert any("unique_keys" in str(x.message) for x in w)
