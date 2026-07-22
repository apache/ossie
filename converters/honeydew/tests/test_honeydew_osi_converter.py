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

"""Tests for the bidirectional OSI ↔ Honeydew converter."""

import json
import os
import sys
import warnings
from pathlib import Path

import pytest
import yaml

from honeydew_osi.converter import (
    HoneydewConversionError,
    _assign_metrics_to_entities,
    _build_osi_metadata,
    _check_safe_path,
    _fields_to_honeydew,
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
        for k in ("owner", "display_name", "hidden", "folder"):
            if k in e:
                entity_dict[k] = e[k]
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


def _osi_roundtrip(model_dict, tmp_path):
    """OSI → Honeydew → OSI; returns the semantic model dict."""
    files = convert_osi_to_honeydew(_osi(model_dict))
    for rel_path, content in files.items():
        p = tmp_path / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))["semantic_model"][0]


def _honeydew_roundtrip(entities, tmp_path):
    """Honeydew → OSI → Honeydew; returns Path to the output workspace directory."""
    _write_workspace(str(tmp_path), "ws", entities)
    osi_yaml = convert_honeydew_to_osi(str(tmp_path))
    files = convert_osi_to_honeydew(osi_yaml)
    out_dir = tmp_path / "out"
    for rel_path, content in files.items():
        p = out_dir / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return out_dir


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests – helpers
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expr,expected", [
    ("order_id", True),
    ("SUM(x)", False),
    ("orders.id", False),
    ("1col", False),
    ("_hidden", True),
])
def test_is_simple_identifier(expr, expected):
    assert _is_simple_identifier(expr) is expected


@pytest.mark.parametrize("source,expected_sql,expected_type", [
    ("db.schema.table", "db.schema.table", "table"),
    ("SELECT id FROM foo", "SELECT id FROM foo", "sql"),
    ("WITH cte AS (SELECT 1) SELECT * FROM cte", "WITH cte AS (SELECT 1) SELECT * FROM cte", "sql"),
    ("", "", "table"),
])
def test_parse_osi_source(source, expected_sql, expected_type):
    sql, dtype = _parse_osi_source(source)
    assert sql == expected_sql and dtype == expected_type


@pytest.mark.parametrize("field,expected_dt", [
    ({"dimension": {"is_time": True}}, "timestamp"),
    ({"dimension": {"is_time": False}}, "string"),
    ({}, "number"),
])
def test_osi_field_to_honeydew_datatype(field, expected_dt):
    assert _osi_field_to_honeydew_datatype(field) == expected_dt


@pytest.mark.parametrize("datatype,expected_dim", [
    ("date", {"is_time": True}),
    ("timestamp", {"is_time": True}),
    ("string", {"is_time": False}),
    ("bool", {"is_time": False}),
    ("number", None),
    ("float", None),
])
def test_honeydew_datatype_to_osi_dimension(datatype, expected_dim):
    assert _honeydew_datatype_to_osi_dimension(datatype) == expected_dim


@pytest.mark.parametrize("expr,entities,expected", [
    ("SUM(orders.total)", {"orders", "customers"}, "orders"),
    ("orders.a / customers.b", {"orders", "customers"}, "orders"),
    ("COUNT(*)", {"orders"}, None),
    ("SUM(foo.col)", {"orders"}, None),
])
def test_find_entity_in_expression(expr, entities, expected):
    assert _find_entity_in_expression(expr, entities) == expected


def test_pick_ansi_expression_ansi_preferred():
    expr = {"dialects": [
        {"dialect": "SNOWFLAKE", "expression": "col::VARCHAR"},
        {"dialect": "ANSI_SQL", "expression": "col"},
    ]}
    assert _pick_ansi_expression(expr, "f") == "col"


def test_pick_ansi_expression_fallback_warns():
    expr = {"dialects": [{"dialect": "SNOWFLAKE", "expression": "col::VARCHAR"}]}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _pick_ansi_expression(expr, "f")
    assert result == "col::VARCHAR"
    assert any("ANSI_SQL" in str(x.message) for x in w)


@pytest.mark.parametrize("expression", [None, {"dialects": []}])
def test_pick_ansi_expression_returns_none(expression):
    assert _pick_ansi_expression(expression, "f") is None


def test_pick_ansi_expression_non_dict_warns():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = _pick_ansi_expression("just_a_string", "f")
    assert result is None
    assert any("must be a mapping" in str(x.message) for x in w)


# ─────────────────────────────────────────────────────────────────────────────
# OSI metadata helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_build_and_read_ai_context_string():
    section = _build_osi_metadata(ai_context="orders, purchases")
    result = _read_osi_metadata({"metadata": [section]})
    assert result["ai_context"] == "orders, purchases"


def test_build_and_read_ai_context_dict():
    ctx = {"instructions": "Use for sales", "synonyms": ["orders", "purchases"]}
    section = _build_osi_metadata(ai_context=ctx)
    result = _read_osi_metadata({"metadata": [section]})
    assert result["ai_context"] == ctx


def test_build_and_read_unique_keys():
    uks = [["col1", "col2"], ["col3"]]
    section = _build_osi_metadata(unique_keys=uks)
    result = _read_osi_metadata({"metadata": [section]})
    assert result["unique_keys"] == uks


def test_build_and_read_custom_extensions():
    exts = [{"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "WH"}'}]
    section = _build_osi_metadata(custom_extensions=exts)
    result = _read_osi_metadata({"metadata": [section]})
    assert result["custom_extensions"] == exts


def test_read_osi_metadata_no_osi_section():
    assert _read_osi_metadata({"metadata": [{"name": "other", "metadata": []}]}) == {}


def test_read_osi_metadata_no_metadata():
    assert _read_osi_metadata({}) == {}


def test_build_osi_metadata_nothing_to_store():
    assert _build_osi_metadata() is None


# ─────────────────────────────────────────────────────────────────────────────
# Assign metrics to entities
# ─────────────────────────────────────────────────────────────────────────────

def test_assign_metrics_by_expression():
    metrics = [{"name": "total", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.total)"}]}}]
    result = _assign_metrics_to_entities(metrics, ["orders", "customers"])
    assert "total" in [m["name"] for m in result.get("orders", [])]


def test_assign_metrics_honeydew_hint_takes_priority():
    metrics = [{
        "name": "cnt",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.x)"}]},
        "custom_extensions": [{"vendor_name": "HONEYDEW", "data": '{"entity": "customers"}'}],
    }]
    result = _assign_metrics_to_entities(metrics, ["orders", "customers"])
    assert "cnt" in [m["name"] for m in result.get("customers", [])]
    assert "orders" not in result


def test_assign_metrics_fallback_to_first_entity():
    metrics = [{"name": "cnt", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(*)"}]}}]
    with warnings.catch_warnings(record=True):
        result = _assign_metrics_to_entities(metrics, ["orders"])
    assert "cnt" in [m["name"] for m in result.get("orders", [])]


def test_assign_metrics_no_entities():
    metrics = [{"name": "m", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(*)"}]}}]
    with warnings.catch_warnings(record=True):
        result = _assign_metrics_to_entities(metrics, [])
    assert result == {}


# ─────────────────────────────────────────────────────────────────────────────
# Path traversal guard
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rel_path,expected", [
    ("workspace.yml", True),
    ("schema/orders/orders.yml", True),
    ("schema/orders/datasets/orders.yml", True),
    ("../evil.yml", False),
    ("../../etc/passwd", False),
    ("schema/../../../evil", False),
])
def test_check_safe_path(rel_path, expected):
    output_abs = os.path.abspath("/tmp/test_output")
    assert _check_safe_path(output_abs, rel_path) is expected


# ─────────────────────────────────────────────────────────────────────────────
# OSI → Honeydew: file content
# ─────────────────────────────────────────────────────────────────────────────

_REL_MODEL = {
    "name": "m",
    "datasets": [
        {"name": "orders", "source": "db.s.orders", "fields": []},
        {"name": "customers", "source": "db.s.customers", "fields": []},
    ],
    "relationships": [{"name": "orders_to_customers", "from": "orders", "to": "customers",
                       "from_columns": ["cid"], "to_columns": ["id"]}],
}

@pytest.mark.parametrize("model,path,expected", [
    # ── minimal model ──────────────────────────────────────────────────────────
    pytest.param(
        _minimal_model(),
        "workspace.yml",
        {"type": "workspace", "name": "test_model"},
        id="minimal-workspace",
    ),
    pytest.param(
        _minimal_model(),
        "schema/orders/orders.yml",
        {"type": "entity", "name": "orders", "keys": ["order_id"],
         "key_dataset": "orders", "relations": []},
        id="minimal-entity",
    ),
    pytest.param(
        _minimal_model(),
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "db.schema.orders", "dataset_type": "table",
            "attributes": [
                {"column": "order_id", "name": "order_id", "datatype": "string"},
                {"column": "order_date", "name": "order_date", "datatype": "timestamp"},
                {"column": "total_amount", "name": "total", "datatype": "number"},
            ],
        },
        id="minimal-dataset",
    ),
    # ── complex expression → calculated attribute ──────────────────────────────
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "disc_price",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "price * (1 - discount)"}]},
            "dimension": {"is_time": False},
        }]}]},
        "schema/orders/attributes/disc_price.yml",
        {"type": "calculated_attribute", "entity": "orders", "name": "disc_price",
         "datatype": "string", "sql": "price * (1 - discount)"},
        id="calc-attr-file",
    ),
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "disc_price",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "price * (1 - discount)"}]},
            "dimension": {"is_time": False},
        }]}]},
        "schema/orders/datasets/orders.yml",
        {"type": "dataset", "entity": "orders", "name": "orders",
         "sql": "db.s.orders", "dataset_type": "table", "attributes": []},
        id="calc-attr-dataset-empty",
    ),
    # ── label → labels in attr ────────────────────────────────────────────────
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "status",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]},
            "dimension": {"is_time": False},
            "label": "sales",
        }]}]},
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "db.s.orders", "dataset_type": "table",
            "attributes": [{"column": "status", "name": "status",
                            "datatype": "string", "labels": ["sales"],
                            "metadata": [{"name": "osi", "metadata": [
                                {"name": "label", "value": "sales"}
                            ]}]}],
        },
        id="label-in-attr",
    ),
    # ── ai_context string → description merged ────────────────────────────────
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "total"}]},
            "description": "Base desc",
            "ai_context": "revenue, earnings",
        }]}]},
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "db.s.orders", "dataset_type": "table",
            "attributes": [{"column": "total", "name": "total", "datatype": "number",
                            "description": "Base desc\nrevenue, earnings"}],
        },
        id="ai-context-string",
    ),
    # ── ai_context dict → labels + description + metadata ────────────────────
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
            "name": "total",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "total"}]},
            "ai_context": {"instructions": "Use for revenue", "synonyms": ["rev", "earnings"]},
        }]}]},
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "db.s.orders", "dataset_type": "table",
            "attributes": [{
                "column": "total", "name": "total", "datatype": "number",
                "description": "Use for revenue",
                "labels": ["rev", "earnings"],
                "metadata": [{"name": "osi", "metadata": [
                    {"name": "ai_context",
                     "value": '{"instructions": "Use for revenue", "synonyms": ["rev", "earnings"]}'},
                ]}],
            }],
        },
        id="ai-context-dict",
    ),
    # ── unique_keys → entity metadata ─────────────────────────────────────────
    pytest.param(
        {"name": "m", "datasets": [{"name": "items", "source": "db.s.items",
            "primary_key": ["item_id"],
            "unique_keys": [["sku"], ["item_id", "variant"]],
            "fields": []}]},
        "schema/items/items.yml",
        {
            "type": "entity", "name": "items", "keys": ["item_id"],
            "key_dataset": "items", "relations": [],
            "metadata": [{"name": "osi", "metadata": [
                {"name": "unique_keys", "value": '[["sku"], ["item_id", "variant"]]'},
            ]}],
        },
        id="unique-keys",
    ),
    # ── non-HONEYDEW custom_extensions → entity metadata ──────────────────────
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "custom_extensions": [{"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "WH"}'}],
            "fields": []}]},
        "schema/orders/orders.yml",
        {
            "type": "entity", "name": "orders", "key_dataset": "orders", "relations": [],
            "metadata": [{"name": "osi", "metadata": [
                {"name": "custom_extensions",
                 "value": '[{"vendor_name": "SNOWFLAKE", "data": "{\\"warehouse\\": \\"WH\\"}"}]'},
            ]}],
        },
        id="custom-ext",
    ),
    # ── relationship on from-entity; nothing on to-entity ────────────────────
    pytest.param(
        _REL_MODEL,
        "schema/orders/orders.yml",
        {
            "type": "entity", "name": "orders", "key_dataset": "orders",
            "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                           "name": "orders_to_customers",
                           "connection": [{"src_field": "cid", "target_field": "id"}]}],
        },
        id="relation-from-entity",
    ),
    pytest.param(
        _REL_MODEL,
        "schema/customers/customers.yml",
        {"type": "entity", "name": "customers", "key_dataset": "customers", "relations": []},
        id="relation-to-entity-empty",
    ),
    # ── model-level ai_context → workspace metadata ───────────────────────────
    pytest.param(
        {"name": "m", "datasets": [],
         "ai_context": {"instructions": "Use for retail analytics", "synonyms": ["store"]}},
        "workspace.yml",
        {
            "type": "workspace", "name": "m",
            "metadata": [{"name": "osi", "metadata": [
                {"name": "ai_context",
                 "value": '{"instructions": "Use for retail analytics", "synonyms": ["store"]}'},
            ]}],
        },
        id="model-ai-context",
    ),
    # ── metric ────────────────────────────────────────────────────────────────
    pytest.param(
        {"name": "m",
         "datasets": [{"name": "orders", "source": "db.s.orders", "fields": []}],
         "metrics": [{"name": "total_rev", "description": "Sum of sales",
                      "expression": {"dialects": [{"dialect": "ANSI_SQL",
                                                   "expression": "SUM(orders.total)"}]}}]},
        "schema/orders/metrics/total_rev.yml",
        {"type": "metric", "entity": "orders", "name": "total_rev",
         "datatype": "number", "sql": "SUM(orders.total)", "description": "Sum of sales"},
        id="metric",
    ),
    # ── subquery source → dataset_type sql ───────────────────────────────────
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders",
            "source": "SELECT * FROM raw.orders WHERE active = true", "fields": []}]},
        "schema/orders/datasets/orders.yml",
        {"type": "dataset", "entity": "orders", "name": "orders",
         "sql": "SELECT * FROM raw.orders WHERE active = true",
         "dataset_type": "sql", "attributes": []},
        id="subquery-source",
    ),
    # ── composite primary key ─────────────────────────────────────────────────
    pytest.param(
        {"name": "m", "datasets": [{"name": "li", "source": "db.s.li",
            "primary_key": ["order_id", "line_number"], "fields": []}]},
        "schema/li/li.yml",
        {"type": "entity", "name": "li", "keys": ["order_id", "line_number"],
         "key_dataset": "li", "relations": []},
        id="composite-pk",
    ),
])
def test_osi_to_honeydew_file_content(model, path, expected):
    files = convert_osi_to_honeydew(_osi(model))
    assert path in files
    assert yaml.safe_load(files[path]) == expected


def test_osi_to_honeydew_metric_entity_hint_overrides_expression():
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


def test_osi_to_honeydew_invalid_version_raises():
    with pytest.raises(HoneydewConversionError, match="Unsupported"):
        convert_osi_to_honeydew("version: '9.9.9'\nsemantic_model:\n  - name: m\n")


def test_osi_to_honeydew_missing_semantic_model_raises():
    with pytest.raises(HoneydewConversionError):
        convert_osi_to_honeydew(f"version: '{OSI_VERSION}'\n")


def test_osi_to_honeydew_multiple_models_warns():
    doc = yaml.dump({"version": OSI_VERSION, "semantic_model": [
        {"name": "m1", "datasets": []},
        {"name": "m2", "datasets": []},
    ]})
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        files = convert_osi_to_honeydew(doc)
    assert any("only the first" in str(x.message) for x in w)
    assert yaml.safe_load(files["workspace.yml"]) == {"type": "workspace", "name": "m1"}


# ─────────────────────────────────────────────────────────────────────────────
# Honeydew → OSI: full document
# ─────────────────────────────────────────────────────────────────────────────

def _hd_root(sm):
    return {"version": OSI_VERSION, "vendors": ["HONEYDEW"], "semantic_model": [sm]}


def _ansi(expr):
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": expr}]}


@pytest.mark.parametrize("ws_name,entities,expected_root", [
    # ── basic entity with two dataset attributes ──────────────────────────────
    pytest.param(
        "tpch",
        [{"name": "orders", "keys": ["orderkey"], "key_dataset": "tpch_orders",
          "sql": "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS",
          "dataset_attrs": [
              {"column": "o_orderkey", "name": "orderkey", "datatype": "number"},
              {"column": "o_orderdate", "name": "orderdate", "datatype": "date"},
          ]}],
        _hd_root({
            "name": "tpch",
            "datasets": [{
                "name": "orders",
                "source": "SNOWFLAKE_SAMPLE_DATA.TPCH_SF1.ORDERS",
                "primary_key": ["orderkey"],
                "unique_keys": [["orderkey"]],
                "fields": [
                    {"name": "orderkey", "expression": _ansi("o_orderkey")},
                    {"name": "orderdate", "expression": _ansi("o_orderdate"),
                     "dimension": {"is_time": True}},
                ],
            }],
        }),
        id="basic",
    ),
    # ── field types ───────────────────────────────────────────────────────────
    pytest.param(
        "ws",
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "db.s.orders",
          "dataset_attrs": [{"column": "id", "name": "id", "datatype": "number"}]}],
        _hd_root({"name": "ws", "datasets": [{
            "name": "orders", "source": "db.s.orders", "primary_key": ["id"],
            "unique_keys": [["id"]],
            "fields": [{"name": "id", "expression": _ansi("id")}],
        }]}),
        id="field-number",
    ),
    pytest.param(
        "ws",
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "db.s.orders",
          "dataset_attrs": [{"column": "status", "name": "status", "datatype": "string"}]}],
        _hd_root({"name": "ws", "datasets": [{
            "name": "orders", "source": "db.s.orders", "primary_key": ["id"],
            "unique_keys": [["id"]],
            "fields": [{"name": "status", "expression": _ansi("status"),
                        "dimension": {"is_time": False}}],
        }]}),
        id="field-string",
    ),
    pytest.param(
        "ws",
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "db.s.orders",
          "dataset_attrs": [{"column": "created_at", "name": "created_at", "datatype": "timestamp"}]}],
        _hd_root({"name": "ws", "datasets": [{
            "name": "orders", "source": "db.s.orders", "primary_key": ["id"],
            "unique_keys": [["id"]],
            "fields": [{"name": "created_at", "expression": _ansi("created_at"),
                        "dimension": {"is_time": True}}],
        }]}),
        id="field-timestamp",
    ),
    # ── labels → label + ai_context + custom_extension ───────────────────────
    pytest.param(
        "ws",
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "db.s.orders",
          "dataset_attrs": [{"column": "status", "name": "status", "datatype": "string",
                             "labels": ["sales", "reporting"]}]}],
        _hd_root({"name": "ws", "datasets": [{
            "name": "orders", "source": "db.s.orders", "primary_key": ["id"],
            "unique_keys": [["id"]],
            "fields": [{
                "name": "status", "expression": _ansi("status"),
                "dimension": {"is_time": False},
                "ai_context": {"synonyms": ["sales", "reporting"]},
                "label": "sales",
                "custom_extensions": [
                    {"vendor_name": "HONEYDEW", "data": '{"labels": ["sales", "reporting"]}'},
                ],
            }],
        }]}),
        id="labels",
    ),
    # ── many-to-one relationship ──────────────────────────────────────────────
    pytest.param(
        "ws",
        [
            {"name": "orders", "keys": ["order_id"], "key_dataset": "orders", "sql": "db.s.orders",
             "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                            "connection": [{"src_field": "customer_id", "target_field": "id"}]}],
             "dataset_attrs": []},
            {"name": "customers", "keys": ["id"], "key_dataset": "customers",
             "sql": "db.s.customers", "dataset_attrs": []},
        ],
        _hd_root({
            "name": "ws",
            "datasets": [
                {"name": "customers", "source": "db.s.customers", "primary_key": ["id"],
                 "unique_keys": [["id"]]},
                {"name": "orders", "source": "db.s.orders", "primary_key": ["order_id"],
                 "unique_keys": [["order_id"]]},
            ],
            "relationships": [{"name": "orders_to_customers", "from": "orders", "to": "customers",
                               "from_columns": ["customer_id"], "to_columns": ["id"]}],
        }),
        id="many-to-one",
    ),
    # ── one-to-many (direction flipped) ──────────────────────────────────────
    pytest.param(
        "ws",
        [
            {"name": "customers", "keys": ["id"], "key_dataset": "customers", "sql": "db.s.customers",
             "relations": [{"target_entity": "orders", "rel_type": "one-to-many",
                            "connection": [{"src_field": "id", "target_field": "customer_id"}]}],
             "dataset_attrs": []},
            {"name": "orders", "keys": ["order_id"], "key_dataset": "orders",
             "sql": "db.s.orders", "dataset_attrs": []},
        ],
        _hd_root({
            "name": "ws",
            "datasets": [
                {"name": "customers", "source": "db.s.customers", "primary_key": ["id"],
                 "unique_keys": [["id"]]},
                {"name": "orders", "source": "db.s.orders", "primary_key": ["order_id"],
                 "unique_keys": [["order_id"]]},
            ],
            "relationships": [{"name": "orders_to_customers", "from": "orders", "to": "customers",
                               "from_columns": ["customer_id"], "to_columns": ["id"]}],
        }),
        id="one-to-many-flipped",
    ),
    # ── metric ────────────────────────────────────────────────────────────────
    pytest.param(
        "ws",
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "db.s.orders",
          "dataset_attrs": [],
          "metrics": [{"type": "metric", "entity": "orders", "name": "count",
                       "datatype": "number", "sql": "COUNT(*)"}]}],
        _hd_root({"name": "ws", "datasets": [
            {"name": "orders", "source": "db.s.orders", "primary_key": ["id"],
             "unique_keys": [["id"]]},
        ], "metrics": [{
            "name": "count",
            "expression": _ansi("COUNT(*)"),
            "custom_extensions": [{"vendor_name": "HONEYDEW", "data": '{"entity": "orders"}'}],
        }]}),
        id="metric",
    ),
    # ── calculated attribute → OSI field with HONEYDEW extension ─────────────
    pytest.param(
        "ws",
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "db.s.orders",
          "dataset_attrs": [],
          "calc_attrs": [{"type": "calculated_attribute", "entity": "orders",
                          "name": "discounted", "datatype": "number",
                          "sql": "orders.price * (1 - orders.discount)"}]}],
        _hd_root({"name": "ws", "datasets": [{
            "name": "orders", "source": "db.s.orders", "primary_key": ["id"],
            "unique_keys": [["id"]],
            "fields": [{
                "name": "discounted",
                "expression": _ansi("orders.price * (1 - orders.discount)"),
                "custom_extensions": [
                    {"vendor_name": "HONEYDEW",
                     "data": '{"type": "calculated_attribute", "entity": "orders"}'},
                ],
            }],
        }]}),
        id="calc-attr",
    ),
])
def test_honeydew_to_osi_output(tmp_path, ws_name, entities, expected_root):
    _write_workspace(str(tmp_path), ws_name, entities)
    result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
    assert result == expected_root


def test_honeydew_to_osi_missing_workspace_raises(tmp_path):
    with pytest.raises(HoneydewConversionError, match="workspace.yml"):
        convert_honeydew_to_osi(str(tmp_path))


def test_honeydew_to_osi_missing_schema_dir_empty_model(tmp_path):
    (tmp_path / "workspace.yml").write_text(yaml.dump({"type": "workspace", "name": "ws"}))
    result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
    assert result == {"version": OSI_VERSION, "vendors": ["HONEYDEW"],
                      "semantic_model": [{"name": "ws", "datasets": []}]}


def test_honeydew_to_osi_empty_metric_sql_skipped(tmp_path):
    _write_workspace(str(tmp_path), "ws", [{"name": "orders", "keys": ["id"],
        "key_dataset": "orders", "sql": "db.s.orders", "dataset_attrs": [],
        "metrics": [{"type": "metric", "entity": "orders", "name": "bad",
                     "datatype": "number", "sql": ""}]}])
    with warnings.catch_warnings(record=True):
        result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
    assert "metrics" not in result["semantic_model"][0]


def test_honeydew_to_osi_duplicate_relations_deduplicated(tmp_path):
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


def test_honeydew_to_osi_relation_target_columns_are_unique_keys(tmp_path):
    # Honeydew validates that a many-to-one relation joins to the target entity's
    # keys, so the relation's to_columns are always covered by the target dataset's
    # unique_keys (derived from those keys). This preserves the cardinality metadata
    # for OSI consumers (e.g. Snowflake) without inspecting the relation itself.
    _write_workspace(str(tmp_path), "ws", [
        {"name": "orders", "keys": ["order_id"], "key_dataset": "orders", "sql": "db.s.orders",
         "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                        "connection": [{"src_field": "customer_id", "target_field": "id"}]}],
         "dataset_attrs": []},
        {"name": "customers", "keys": ["id"], "key_dataset": "customers",
         "sql": "db.s.customers", "dataset_attrs": []},
    ])
    sm = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))["semantic_model"][0]
    datasets = {ds["name"]: ds for ds in sm["datasets"]}
    rel = sm["relationships"][0]
    target_ds = datasets[rel["to"]]
    assert rel["to_columns"] in target_ds["unique_keys"]


# ─────────────────────────────────────────────────────────────────────────────
# OSI → Honeydew → OSI round-trip: full semantic model
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("model,expected_sm", [
    pytest.param(
        {"name": "retail", "description": "Retail model", "datasets": []},
        {"name": "retail", "datasets": [], "description": "Retail model"},
        id="name-desc",
    ),
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "primary_key": ["order_id"], "fields": []}]},
        # OSI → Honeydew → OSI normalizes: the primary key surfaces as a unique key,
        # because Honeydew expresses uniqueness through entity keys.
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "primary_key": ["order_id"], "unique_keys": [["order_id"]]}]},
        id="pk-single",
    ),
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "primary_key": ["order_id", "line_no"], "fields": []}]},
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "primary_key": ["order_id", "line_no"],
            "unique_keys": [["order_id", "line_no"]]}]},
        id="pk-composite",
    ),
    pytest.param(
        {"name": "m", "datasets": [{"name": "items", "source": "db.s.items",
            "primary_key": ["id"],
            "unique_keys": [["sku"], ["id", "variant"]],
            "fields": []}]},
        # The primary key is appended as a unique key on the way back out.
        {"name": "m", "datasets": [{"name": "items", "source": "db.s.items",
            "primary_key": ["id"],
            "unique_keys": [["sku"], ["id", "variant"], ["id"]]}]},
        id="unique-keys",
    ),
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "status", "label": "sales",
                        "expression": _ansi("status"),
                        "dimension": {"is_time": False}}]}]},
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "status", "expression": _ansi("status"),
                        "dimension": {"is_time": False},
                        "label": "sales"}]}]},
        id="field-label",
    ),
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "total",
                        "expression": _ansi("total"),
                        "ai_context": {"instructions": "Use for revenue analysis",
                                       "synonyms": ["revenue", "sales"]}}]}]},
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "total", "expression": _ansi("total"),
                        "description": "Use for revenue analysis",
                        "ai_context": {"instructions": "Use for revenue analysis",
                                       "synonyms": ["revenue", "sales"]},
                        "custom_extensions": [
                            {"vendor_name": "HONEYDEW",
                             "data": '{"labels": ["revenue", "sales"]}'},
                        ]}]}]},
        id="ai-context-dict",
    ),
    pytest.param(
        {"name": "m", "ai_context": {"instructions": "Retail analytics", "synonyms": ["store"]},
         "datasets": []},
        {"name": "m", "datasets": [],
         "ai_context": {"instructions": "Retail analytics", "synonyms": ["store"]}},
        id="model-ai-context",
    ),
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "custom_extensions": [{"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "WH"}'}],
            "fields": []}]},
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "custom_extensions": [{"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "WH"}'}]}]},
        id="custom-ext",
    ),
    pytest.param(
        {"name": "m", "datasets": [
            {"name": "orders", "source": "db.s.orders", "fields": []},
            {"name": "customers", "source": "db.s.customers", "fields": []},
        ], "relationships": [{"name": "orders_to_customers", "from": "orders", "to": "customers",
            "from_columns": ["cid"], "to_columns": ["id"]}]},
        {"name": "m", "datasets": [
            {"name": "customers", "source": "db.s.customers"},
            {"name": "orders", "source": "db.s.orders"},
        ], "relationships": [{"name": "orders_to_customers", "from": "orders", "to": "customers",
            "from_columns": ["cid"], "to_columns": ["id"]}]},
        id="relationship",
    ),
    pytest.param(
        {"name": "m",
         "datasets": [{"name": "orders", "source": "db.s.orders", "fields": []}],
         "metrics": [{"name": "total_revenue", "description": "Sum of sales",
                      "expression": _ansi("SUM(orders.total)")}]},
        {"name": "m",
         "datasets": [{"name": "orders", "source": "db.s.orders"}],
         "metrics": [{"name": "total_revenue",
                      "expression": _ansi("SUM(orders.total)"),
                      "custom_extensions": [
                          {"vendor_name": "HONEYDEW", "data": '{"entity": "orders"}'},
                      ],
                      "description": "Sum of sales"}]},
        id="metric",
    ),
    # ai_context string is merged into description and not stored in metadata for fields
    pytest.param(
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "status",
                        "expression": _ansi("status"),
                        "ai_context": "order status, order state",
                        "dimension": {"is_time": False}}]}]},
        {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders",
            "fields": [{"name": "status", "expression": _ansi("status"),
                        "dimension": {"is_time": False},
                        "description": "order status, order state"}]}]},
        id="ai-context-string-becomes-desc",
    ),
])
def test_osi_roundtrip_sm(tmp_path, model, expected_sm):
    assert _osi_roundtrip(model, tmp_path) == expected_sm


def test_osi_roundtrip_tpcds_example(tmp_path):
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


# ─────────────────────────────────────────────────────────────────────────────
# Honeydew → OSI → Honeydew round-trip: full file content
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("entities,path,expected", [
    # ── entity name + keys ────────────────────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["order_id"], "key_dataset": "orders",
          "sql": "DB.S.ORDERS", "dataset_attrs": []}],
        "schema/orders/orders.yml",
        {"type": "entity", "name": "orders", "keys": ["order_id"],
         "key_dataset": "orders", "relations": []},
        id="entity-keys",
    ),
    # ── dataset source ────────────────────────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders",
          "sql": "DB.SCHEMA.ORDERS", "dataset_attrs": []}],
        "schema/orders/datasets/orders.yml",
        {"type": "dataset", "entity": "orders", "name": "orders",
         "sql": "DB.SCHEMA.ORDERS", "dataset_type": "table", "attributes": []},
        id="dataset-source",
    ),
    # ── column attributes ─────────────────────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [
              {"column": "o_id", "name": "id", "datatype": "number"},
              {"column": "o_status", "name": "status", "datatype": "string"},
          ]}],
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "DB.S.ORDERS", "dataset_type": "table",
            "attributes": [
                {"column": "o_id", "name": "id", "datatype": "number"},
                {"column": "o_status", "name": "status", "datatype": "string"},
            ],
        },
        id="column-attrs",
    ),
    # ── labels on column ──────────────────────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [{"column": "status", "name": "status", "datatype": "string",
                             "labels": ["sales"]}]}],
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "DB.S.ORDERS", "dataset_type": "table",
            "attributes": [{
                "column": "status", "name": "status", "datatype": "string",
                "labels": ["sales"],
                "metadata": [{"name": "osi", "metadata": [
                    {"name": "ai_context", "value": '{"synonyms": ["sales"]}'},
                ]}],
            }],
        },
        id="labels-on-column",
    ),
    # ── calculated attribute sql ───────────────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [],
          "calc_attrs": [{"type": "calculated_attribute", "entity": "orders",
                          "name": "disc", "datatype": "number",
                          "sql": "orders.price * (1 - orders.discount)"}]}],
        "schema/orders/attributes/disc.yml",
        {"type": "calculated_attribute", "entity": "orders", "name": "disc",
         "datatype": "number", "sql": "orders.price * (1 - orders.discount)"},
        id="calc-attr-sql",
    ),
    # ── calc attr with simple identifier stays as calc_attr ──────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [],
          "calc_attrs": [{"type": "calculated_attribute", "entity": "orders",
                          "name": "revenue", "datatype": "number", "sql": "revenue"}]}],
        "schema/orders/attributes/revenue.yml",
        {"type": "calculated_attribute", "entity": "orders", "name": "revenue",
         "datatype": "number", "sql": "revenue"},
        id="calc-simple-stays-calc",
    ),
    # ── metric entity assignment ──────────────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [],
          "metrics": [{"type": "metric", "entity": "orders", "name": "cnt",
                       "datatype": "number", "sql": "COUNT(*)"}]}],
        "schema/orders/metrics/cnt.yml",
        {"type": "metric", "entity": "orders", "name": "cnt",
         "datatype": "number", "sql": "COUNT(*)"},
        id="metric",
    ),
    # ── many-to-one relation ──────────────────────────────────────────────────
    pytest.param(
        [
            {"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
             "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                            "connection": [{"src_field": "cid", "target_field": "id"}]}],
             "dataset_attrs": []},
            {"name": "customers", "keys": ["id"], "key_dataset": "customers",
             "sql": "DB.S.CUSTOMERS", "dataset_attrs": []},
        ],
        "schema/orders/orders.yml",
        {
            "type": "entity", "name": "orders", "keys": ["id"],
            "key_dataset": "orders",
            "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                           "name": "orders_to_customers",
                           "connection": [{"src_field": "cid", "target_field": "id"}]}],
        },
        id="relation",
    ),
    # ── connection_expr round-trip ────────────────────────────────────────────
    pytest.param(
        [
            {"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
             "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                            "connection_expr": {"sql": "orders.cid = customers.id AND orders.region = customers.region"}}],
             "dataset_attrs": []},
            {"name": "customers", "keys": ["id"], "key_dataset": "customers",
             "sql": "DB.S.CUSTOMERS", "dataset_attrs": []},
        ],
        "schema/orders/orders.yml",
        {
            "type": "entity", "name": "orders", "keys": ["id"],
            "key_dataset": "orders",
            "relations": [{"target_entity": "customers", "rel_type": "many-to-one",
                           "name": "orders_to_customers",
                           "connection_expr": {"sql": "orders.cid = customers.id AND orders.region = customers.region"}}],
        },
        id="connection-expr",
    ),
    # ── bool datatype ─────────────────────────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [{"column": "is_active", "name": "is_active", "datatype": "bool"}]}],
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "DB.S.ORDERS", "dataset_type": "table",
            "attributes": [{"column": "is_active", "name": "is_active", "datatype": "bool"}],
        },
        id="bool-datatype",
    ),
    # ── Honeydew-specific attribute fields ───────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [{"column": "status", "name": "status", "datatype": "string",
                             "display_name": "Order Status"}]}],
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "DB.S.ORDERS", "dataset_type": "table",
            "attributes": [{"column": "status", "name": "status", "datatype": "string",
                            "display_name": "Order Status"}],
        },
        id="attr-display-name",
    ),
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [{"column": "status", "name": "status", "datatype": "string",
                             "hidden": True}]}],
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "DB.S.ORDERS", "dataset_type": "table",
            "attributes": [{"column": "status", "name": "status", "datatype": "string",
                            "hidden": True}],
        },
        id="attr-hidden",
    ),
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [{"column": "status", "name": "status", "datatype": "string",
                             "format_string": "##,###"}]}],
        "schema/orders/datasets/orders.yml",
        {
            "type": "dataset", "entity": "orders", "name": "orders",
            "sql": "DB.S.ORDERS", "dataset_type": "table",
            "attributes": [{"column": "status", "name": "status", "datatype": "string",
                            "format_string": "##,###"}],
        },
        id="attr-format-string",
    ),
    # ── Honeydew-specific calc attr fields ────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [],
          "calc_attrs": [{"type": "calculated_attribute", "entity": "orders",
                          "name": "disc", "datatype": "number",
                          "sql": "orders.price * 0.9", "display_name": "Discounted Price"}]}],
        "schema/orders/attributes/disc.yml",
        {"type": "calculated_attribute", "entity": "orders", "name": "disc",
         "datatype": "number", "sql": "orders.price * 0.9", "display_name": "Discounted Price"},
        id="calc-display-name",
    ),
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [],
          "calc_attrs": [{"type": "calculated_attribute", "entity": "orders",
                          "name": "disc", "datatype": "number",
                          "sql": "orders.price * 0.9", "timegrain": "day"}]}],
        "schema/orders/attributes/disc.yml",
        {"type": "calculated_attribute", "entity": "orders", "name": "disc",
         "datatype": "number", "sql": "orders.price * 0.9", "timegrain": "day"},
        id="calc-timegrain",
    ),
    # ── Honeydew-specific entity fields ──────────────────────────────────────
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [], "owner": "analytics_team"}],
        "schema/orders/orders.yml",
        {"type": "entity", "name": "orders", "keys": ["id"], "key_dataset": "orders",
         "owner": "analytics_team", "relations": []},
        id="entity-owner",
    ),
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [], "display_name": "Orders Table"}],
        "schema/orders/orders.yml",
        {"type": "entity", "name": "orders", "keys": ["id"], "key_dataset": "orders",
         "display_name": "Orders Table", "relations": []},
        id="entity-display-name",
    ),
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [], "hidden": True}],
        "schema/orders/orders.yml",
        {"type": "entity", "name": "orders", "keys": ["id"], "key_dataset": "orders",
         "hidden": True, "relations": []},
        id="entity-hidden",
    ),
    pytest.param(
        [{"name": "orders", "keys": ["id"], "key_dataset": "orders", "sql": "DB.S.ORDERS",
          "dataset_attrs": [], "folder": "finance"}],
        "schema/orders/orders.yml",
        {"type": "entity", "name": "orders", "keys": ["id"], "key_dataset": "orders",
         "folder": "finance", "relations": []},
        id="entity-folder",
    ),
])
def test_honeydew_roundtrip_file(tmp_path, entities, path, expected):
    out_dir = _honeydew_roundtrip(entities, tmp_path)
    p = out_dir / path
    assert p.exists(), f"Expected file {path!r} was not generated"
    assert yaml.safe_load(p.read_text()) == expected


# ─────────────────────────────────────────────────────────────────────────────
# Bug-fix regression tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("expression", [
    {"dialects": [{"dialect": "ANSI_SQL", "expression": ""}]},
    {"dialects": [{"dialect": "ANSI_SQL", "expression": "   "}]},
])
def test_empty_or_whitespace_field_expression_skipped(expression):
    model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
        "name": "bad",
        "expression": expression,
        "dimension": {"is_time": False},
    }]}]}
    files = convert_osi_to_honeydew(_osi(model))
    ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
    assert ds == {"type": "dataset", "entity": "orders", "name": "orders",
                  "sql": "db.s.orders", "dataset_type": "table", "attributes": []}
    assert "schema/orders/attributes/bad.yml" not in files


@pytest.mark.parametrize("expression", [
    {"dialects": [{"dialect": "ANSI_SQL", "expression": ""}]},
    {"dialects": [{"dialect": "ANSI_SQL", "expression": "   "}]},
])
def test_empty_or_whitespace_metric_expression_skipped(expression):
    model = {"name": "m",
        "datasets": [{"name": "orders", "source": "db.s.orders", "fields": []}],
        "metrics": [{"name": "bad_m", "expression": expression}]}
    files = convert_osi_to_honeydew(_osi(model))
    assert "schema/orders/metrics/bad_m.yml" not in files


def test_non_dict_expression_warns():
    model = {"name": "m", "datasets": [{"name": "orders", "source": "db.s.orders", "fields": [{
        "name": "bad",
        "expression": "just_a_string",
        "dimension": {"is_time": False},
    }]}]}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        files = convert_osi_to_honeydew(_osi(model))
    assert any("must be a mapping" in str(x.message) for x in w)
    ds = yaml.safe_load(files["schema/orders/datasets/orders.yml"])
    assert ds == {"type": "dataset", "entity": "orders", "name": "orders",
                  "sql": "db.s.orders", "dataset_type": "table", "attributes": []}


def test_duplicate_metric_name_warns():
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
    assert yaml.safe_load(files["schema/orders/metrics/total.yml"]) == {
        "type": "metric", "entity": "orders", "name": "total",
        "datatype": "number", "sql": "SUM(orders.b)",
    }


def test_metric_string_ai_context_preserved_in_roundtrip(tmp_path):
    model = {"name": "m",
        "datasets": [{"name": "orders", "source": "db.s.orders", "fields": []}],
        "metrics": [{"name": "rev", "ai_context": "Use for revenue analysis",
                     "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.total)"}]}}]}
    sm = _osi_roundtrip(model, tmp_path)
    assert sm == {
        "name": "m",
        "datasets": [{"name": "orders", "source": "db.s.orders"}],
        "metrics": [{
            "name": "rev",
            "expression": _ansi("SUM(orders.total)"),
            "custom_extensions": [{"vendor_name": "HONEYDEW", "data": '{"entity": "orders"}'}],
            "description": "Use for revenue analysis",
            "ai_context": "Use for revenue analysis",
        }],
    }


def test_malformed_osi_metadata_json_warns(tmp_path):
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


# ─────────────────────────────────────────────────────────────────────────────
# _fields_to_honeydew unit tests
# ─────────────────────────────────────────────────────────────────────────────

def test_fields_to_honeydew_simple_identifier_goes_to_dataset():
    fields = [{"name": "status", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "status"}]},
               "dimension": {"is_time": False}}]
    dataset_attrs, calc_attrs = _fields_to_honeydew(fields, "orders")
    assert dataset_attrs == [{"column": "status", "name": "status", "datatype": "string"}]
    assert calc_attrs == []


def test_fields_to_honeydew_complex_sql_goes_to_calc():
    fields = [{"name": "disc", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "price * 0.9"}]}}]
    dataset_attrs, calc_attrs = _fields_to_honeydew(fields, "orders")
    assert dataset_attrs == []
    assert calc_attrs == [{"type": "calculated_attribute", "entity": "orders", "name": "disc",
                           "datatype": "number", "sql": "price * 0.9"}]


def test_fields_to_honeydew_missing_name_raises():
    fields = [{"expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "col"}]}}]
    with pytest.raises(HoneydewConversionError, match="missing 'name'"):
        _fields_to_honeydew(fields, "orders")


def test_fields_to_honeydew_empty_expression_skipped():
    fields = [{"name": "bad", "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": ""}]}}]
    dataset_attrs, calc_attrs = _fields_to_honeydew(fields, "orders")
    assert dataset_attrs == [] and calc_attrs == []


# ─────────────────────────────────────────────────────────────────────────────
# Connectionless relation warning
# ─────────────────────────────────────────────────────────────────────────────

def test_connectionless_relation_warns():
    model = {"name": "m", "datasets": [
        {"name": "orders", "source": "db.s.orders", "fields": []},
        {"name": "customers", "source": "db.s.customers", "fields": []},
    ], "relationships": [{"name": "r", "from": "orders", "to": "customers"}]}
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        files = convert_osi_to_honeydew(_osi(model))
    assert any("resolve the join" in str(x.message) for x in w)
    assert yaml.safe_load(files["schema/orders/orders.yml"]) == {
        "type": "entity", "name": "orders", "key_dataset": "orders",
        "relations": [{"target_entity": "customers", "rel_type": "many-to-one", "name": "r"}],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Vendors round-trip
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("input_vendors,expected_vendors", [
    (["SNOWFLAKE", "HONEYDEW"], ["HONEYDEW", "SNOWFLAKE"]),
    (["SNOWFLAKE"], ["HONEYDEW", "SNOWFLAKE"]),
    (["HONEYDEW"], ["HONEYDEW"]),
])
def test_vendors_roundtrip(tmp_path, input_vendors, expected_vendors):
    doc = yaml.dump({
        "version": OSI_VERSION,
        "vendors": input_vendors,
        "semantic_model": [{"name": "m", "datasets": []}],
    })
    files = convert_osi_to_honeydew(doc)
    for rel_path, content in files.items():
        p = tmp_path / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    result = yaml.safe_load(convert_honeydew_to_osi(str(tmp_path)))
    assert result == {"version": OSI_VERSION, "vendors": expected_vendors,
                      "semantic_model": [{"name": "m", "datasets": []}]}


# ─────────────────────────────────────────────────────────────────────────────
# main() CLI smoke tests
# ─────────────────────────────────────────────────────────────────────────────

def test_main_osi_to_honeydew(tmp_path):
    import subprocess
    input_file = tmp_path / "model.yaml"
    input_file.write_text(yaml.dump({
        "version": OSI_VERSION,
        "semantic_model": [{"name": "m", "datasets": [
            {"name": "orders", "source": "db.s.orders", "fields": []}
        ]}],
    }))
    output_dir = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "honeydew_osi.converter",
         "osi-to-honeydew", "-i", str(input_file), "-o", str(output_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert yaml.safe_load((output_dir / "workspace.yml").read_text()) == {
        "type": "workspace", "name": "m",
    }


def test_main_honeydew_to_osi(tmp_path):
    import subprocess
    _write_workspace(str(tmp_path), "ws", [{
        "name": "orders", "keys": ["id"], "key_dataset": "orders",
        "sql": "DB.S.ORDERS", "dataset_attrs": [],
    }])
    output_file = tmp_path / "output.yaml"
    result = subprocess.run(
        [sys.executable, "-m", "honeydew_osi.converter",
         "honeydew-to-osi", "-i", str(tmp_path), "-o", str(output_file)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert yaml.safe_load(output_file.read_text()) == {
        "version": OSI_VERSION,
        "vendors": ["HONEYDEW"],
        "semantic_model": [{"name": "ws", "datasets": [
            {"name": "orders", "source": "DB.S.ORDERS", "primary_key": ["id"],
             "unique_keys": [["id"]]},
        ]}],
    }


def test_main_path_traversal_rejected(tmp_path):
    import subprocess
    input_file = tmp_path / "model.yaml"
    input_file.write_text(
        f"version: '{OSI_VERSION}'\nsemantic_model:\n"
        "  - name: m\n    datasets:\n"
        "      - name: '../../evil'\n        source: db.s.evil\n        fields: []\n"
    )
    output_dir = tmp_path / "out"
    result = subprocess.run(
        [sys.executable, "-m", "honeydew_osi.converter",
         "osi-to-honeydew", "-i", str(input_file), "-o", str(output_dir)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "refusing to write" in result.stderr
    assert not (tmp_path / "evil.yml").exists()
