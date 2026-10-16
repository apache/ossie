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

"""Tests for the Ossie to LookML converter."""

import re
import warnings
from pathlib import Path

import lkml
import pytest
import yaml

from ossie_bigquery.ossie_to_lookml import (
    OssieConversionError,
    convert_ossie_to_lookml,
    _convert_source,
    _entity_description,
    _extract_expression,
    _strip_time_suffix,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TPCDS = Path(__file__).resolve().parents[3] / "examples" / "tpcds_semantic_model.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ansi(expr):
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": expr}]}


def _field(name, expr=None, is_time=None, datatype=None, label=None, description=None):
    field = {"name": name, "expression": _ansi(expr or name)}
    if is_time is not None:
        field["dimension"] = {"is_time": is_time}
    if datatype is not None:
        field["datatype"] = datatype
    if label is not None:
        field["label"] = label
    if description is not None:
        field["description"] = description
    return field


def _dataset(name, fields, source=None, primary_key=None):
    dataset = {"name": name, "source": source or f"proj.sch.{name}", "fields": fields}
    if primary_key is not None:
        dataset["primary_key"] = primary_key
    return dataset


def _rel(name, from_ds, to_ds, from_cols, to_cols):
    return {
        "name": name,
        "from": from_ds,
        "to": to_ds,
        "from_columns": from_cols,
        "to_columns": to_cols,
    }


def _metric(name, expr, **extra):
    return {"name": name, "expression": _ansi(expr), **extra}


def _model(datasets, relationships=None, metrics=None, **extra):
    model = {"version": "0.2.0.dev0", "name": "test_model", "datasets": datasets}
    if relationships is not None:
        model["relationships"] = relationships
    if metrics is not None:
        model["metrics"] = metrics
    model.update(extra)
    return yaml.dump(model, default_flow_style=False)


def _minimal_model(**overrides):
    base = {
        "name": "test_model",
        "datasets": [_dataset("my_table", [_field("col1", is_time=False)])],
    }
    base.update(overrides)
    return base


def _wrap_ossie(model_dict):
    return yaml.dump({"version": "0.2.0.dev0", **model_dict}, default_flow_style=False)


def _convert(yaml_str):
    """Converts, swallowing warnings (they are asserted on separately)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return convert_ossie_to_lookml(yaml_str)


def _block(out, header):
    """Returns the text of the `<header> {` ... `}` block at its own indent."""
    match = re.search(rf"^( *){re.escape(header)} \{{\n(.*?)^\1\}}", out, re.S | re.M)
    assert match, f"no block '{header}' in:\n{out}"
    return match.group(2)


ORDERS = _dataset(
    "orders",
    [
        _field("order_id", datatype="Integer"),
        _field("customer_id", datatype="Integer"),
        _field("amount", datatype="Decimal"),
    ],
    primary_key=["order_id"],
)
CUSTOMERS = _dataset(
    "customers",
    [_field("id", datatype="Integer"), _field("region_id", datatype="Integer")],
    primary_key=["id"],
)
REGIONS = _dataset("regions", [_field("id", datatype="Integer")], primary_key=["id"])


# ---------------------------------------------------------------------------
# Version / envelope handling
# ---------------------------------------------------------------------------

class TestEnvelope:
    def test_rejects_wrong_version(self):
        bad = yaml.dump({"version": "0.0.0", **_minimal_model()})
        with pytest.raises(OssieConversionError, match="Unsupported Ossie"):
            convert_ossie_to_lookml(bad)

    def test_rejects_non_mapping_root(self):
        with pytest.raises(OssieConversionError, match="expected a mapping"):
            convert_ossie_to_lookml("- just\n- a\n- list\n")

    def test_rejects_legacy_semantic_model_wrapper(self):
        bad = yaml.dump({"version": "0.2.0.dev0", "semantic_model": [_minimal_model()]})
        with pytest.raises(OssieConversionError, match="Legacy 'semantic_model'"):
            convert_ossie_to_lookml(bad)

    def test_rejects_root_dialects(self):
        bad = yaml.dump({"version": "0.2.0.dev0", "dialects": ["ANSI_SQL"], **_minimal_model()})
        with pytest.raises(OssieConversionError, match="Root dialects"):
            convert_ossie_to_lookml(bad)

    def test_version_not_reported_as_dropped(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            convert_ossie_to_lookml(_wrap_ossie(_minimal_model()))

    def test_legacy_names_are_aliases(self):
        from ossie_bigquery import converter

        assert converter.OsiConversionError is OssieConversionError
        assert converter.convert_osi_to_lookml is convert_ossie_to_lookml
        assert converter.convert_ossie_to_lookml is convert_ossie_to_lookml


# ---------------------------------------------------------------------------
# _convert_source
# ---------------------------------------------------------------------------

class TestConvertSource:
    def test_three_part_is_backtick_quoted(self):
        assert _convert_source("proj.dataset.tbl", "ds") == (
            "sql_table_name: `proj.dataset.tbl` ;;"
        )

    def test_subquery_becomes_derived_table(self):
        out = _convert_source("SELECT * FROM t", "ds")
        assert out.startswith("derived_table: {")
        assert "sql: SELECT * FROM t ;;" in out

    def test_two_part_emitted_as_is(self):
        assert _convert_source("dataset.tbl", "ds") == "sql_table_name: dataset.tbl ;;"

    def test_empty_warns_and_returns_none(self):
        with pytest.warns(UserWarning, match="no source"):
            assert _convert_source("", "ds") is None


# ---------------------------------------------------------------------------
# _extract_expression — dialect preference
# ---------------------------------------------------------------------------

class TestExtractExpression:
    def test_prefers_bigquery_over_ansi(self):
        expression = {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": "LOWER(email)"},
                {"dialect": "BIGQUERY", "expression": "SAFE_CAST(LOWER(email) AS STRING)"},
            ]
        }
        assert _extract_expression(expression, "f") == "SAFE_CAST(LOWER(email) AS STRING)"

    def test_falls_back_to_ansi(self):
        expression = {"dialects": [{"dialect": "ANSI_SQL", "expression": "x"}]}
        assert _extract_expression(expression, "f") == "x"

    def test_falls_back_to_ossie_sql_after_ansi(self):
        expression = {
            "dialects": [
                {"dialect": "OSSIE_SQL_2026", "expression": "ossie_expr"},
                {"dialect": "SNOWFLAKE", "expression": "snow_expr"},
            ]
        }
        assert _extract_expression(expression, "f") == "ossie_expr"

    def test_unsupported_only_warns_and_skips(self):
        expression = {"dialects": [{"dialect": "SNOWFLAKE", "expression": "x"}]}
        with pytest.warns(UserWarning, match="Skipping"):
            assert _extract_expression(expression, "f") is None

    def test_missing_expression_raises(self):
        with pytest.raises(OssieConversionError, match="Missing or malformed"):
            _extract_expression(None, "f")


# ---------------------------------------------------------------------------
# Fields -> dimensions / dimension_groups
# ---------------------------------------------------------------------------

class TestFields:
    def _view(self, *fields, **dataset_kwargs):
        out = _convert(_model([_dataset("t", list(fields), **dataset_kwargs)]))
        return _block(out, "view: t")

    def test_plain_field_becomes_string_dimension_when_untyped(self):
        view = self._view(_field("customer_id", is_time=False))
        dim = _block(view, "dimension: customer_id")
        assert "type: string" in dim
        assert "sql: ${TABLE}.customer_id ;;" in dim

    def test_time_field_becomes_dimension_group(self):
        view = self._view(_field("order_date", is_time=True))
        group = _block(view, "dimension_group: order")
        assert "type: time" in group
        assert "timeframes: [raw, time, date, week, month, quarter, year]" in group
        assert "sql: ${TABLE}.order_date ;;" in group
        assert "datatype:" not in group

    def test_is_time_without_datatype_warns(self):
        model = _model([_dataset("t", [_field("order_date", is_time=True)])])
        with pytest.warns(UserWarning, match="Field 't.order_date'.*is_time without a datatype"):
            convert_ossie_to_lookml(model)

    def test_strip_time_suffix(self):
        assert _strip_time_suffix("order_date") == "order"
        assert _strip_time_suffix("created_timestamp") == "created"
        assert _strip_time_suffix("revenue") == "revenue"

    def test_dimension_group_keeps_name_on_collision(self):
        # Both would strip to `ship`; LookML names must be unique, so keep the
        # original names.
        view = self._view(_field("ship_date", is_time=True), _field("ship_time", is_time=True))
        assert "dimension_group: ship_date {" in view
        assert "dimension_group: ship_time {" in view

    def test_dimension_group_keeps_name_when_it_shadows_a_field(self):
        view = self._view(_field("created"), _field("created_date", is_time=True))
        assert "dimension: created {" in view
        assert "dimension_group: created_date {" in view

    def test_label_and_description_are_emitted(self):
        view = self._view(_field("id", label="Order ID", description="The order"))
        dim = _block(view, "dimension: id")
        assert 'label: "Order ID"' in dim
        assert 'description: "The order"' in dim

    def test_computed_expression_columns_are_qualified(self):
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            out = convert_ossie_to_lookml(
                _model([_dataset("t", [_field("email_norm", expr="LOWER(email)")])])
            )
        assert "sql: LOWER(${TABLE}.email) ;;" in out

    def test_bigquery_functions_survive_qualification(self):
        out = self._view(
            _field("month", expr="DATE_TRUNC(order_date, MONTH)"),
            _field("email", expr="SAFE_CAST(LOWER(email) AS STRING)"),
        )
        assert "sql: DATE_TRUNC(${TABLE}.order_date, MONTH) ;;" in out
        assert "sql: SAFE_CAST(LOWER(${TABLE}.email) AS STRING) ;;" in out

    def test_unparseable_expression_is_verbatim_with_warning(self):
        model = _model([_dataset("t", [_field("weird", expr="SELECT FROM WHERE ((")])])
        with pytest.warns(UserWarning, match="Field 't.weird'.*verbatim"):
            out = convert_ossie_to_lookml(model)
        assert "sql: SELECT FROM WHERE (( ;;" in out


class TestDatatypes:
    def _dim(self, **field_kwargs):
        out = _convert(_model([_dataset("t", [_field("f", **field_kwargs)])]))
        return _block(out, "view: t")

    @pytest.mark.parametrize(
        "datatype, lookml_type",
        [
            ("String", "string"),
            ("Integer", "number"),
            ("Decimal", "number"),
            ("Float", "number"),
            ("Boolean", "yesno"),
            ("Time", "string"),
        ],
    )
    def test_scalar_datatypes(self, datatype, lookml_type):
        view = self._dim(datatype=datatype)
        assert f"type: {lookml_type}" in _block(view, "dimension: f")

    @pytest.mark.parametrize(
        "datatype, lookml_datatype, has_time_frame",
        [
            ("Date", "date", False),
            ("DateTime", "datetime", True),
            ("DateTimeTz", "timestamp", True),
        ],
    )
    def test_temporal_datatypes_become_dimension_groups(self, datatype, lookml_datatype, has_time_frame):
        view = self._dim(datatype=datatype)
        group = _block(view, "dimension_group: f")
        assert "type: time" in group
        assert f"datatype: {lookml_datatype}" in group
        assert ("time," in group) is has_time_frame

    def test_is_time_with_non_temporal_datatype_stays_a_dimension(self):
        # TPC-DS marks `d_year` (Integer) as is_time; a time dimension_group
        # over an integer column would not load in Looker.
        view = self._dim(is_time=True, datatype="Integer")
        assert "dimension_group:" not in view
        assert "type: number" in _block(view, "dimension: f")

    def test_opaque_datatype_warns_and_defaults_to_string(self):
        with pytest.warns(UserWarning, match="Opaque"):
            out = convert_ossie_to_lookml(_model([_dataset("t", [_field("f", datatype="Opaque")])]))
        assert "type: string" in _block(out, "dimension: f")

    def test_unknown_datatype_warns_and_defaults_to_string(self):
        with pytest.warns(UserWarning, match="unrecognized"):
            out = convert_ossie_to_lookml(_model([_dataset("t", [_field("f", datatype="Blob")])]))
        assert "type: string" in _block(out, "dimension: f")


class TestPrimaryKey:
    def test_single_column_key_marks_the_dimension(self):
        out = _convert(_model([ORDERS]))
        assert "primary_key: yes" in _block(out, "dimension: order_id")
        assert "dimension: primary_key" not in out

    def test_composite_key_becomes_hidden_concat_dimension(self):
        ds = _dataset("t", [_field("a"), _field("b")], primary_key=["a", "b"])
        out = _convert(_model([ds]))
        pk = _block(out, "dimension: primary_key")
        assert "primary_key: yes" in pk
        assert "hidden: yes" in pk
        assert "type: string" in pk
        assert "sql: CONCAT(CAST(${TABLE}.a AS STRING), '|', CAST(${TABLE}.b AS STRING)) ;;" in pk

    def test_time_column_key_becomes_hidden_dimension(self):
        # A dimension_group cannot carry primary_key, so fall back to a hidden
        # dimension over the raw column.
        ds = _dataset("t", [_field("created_at", is_time=True)], primary_key=["created_at"])
        out = _convert(_model([ds]))
        pk = _block(out, "dimension: primary_key")
        assert "sql: CAST(${TABLE}.created_at AS STRING) ;;" in pk

    def test_join_target_without_key_warns(self):
        customers = _dataset("customers", [_field("id")])
        model = _model([ORDERS, customers], [_rel("o2c", "orders", "customers", ["customer_id"], ["id"])])
        with pytest.warns(UserWarning, match="customers.*no primary_key"):
            convert_ossie_to_lookml(model)


# ---------------------------------------------------------------------------
# Metrics -> measures
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_qualified_sum_becomes_typed_measure_in_its_dataset(self):
        model = _model(
            [CUSTOMERS, ORDERS],
            metrics=[_metric("total_revenue", "SUM(orders.amount)", description="Total revenue")],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            out = convert_ossie_to_lookml(model)
        measure = _block(_block(out, "view: orders"), "measure: total_revenue")
        assert "type: sum" in measure
        assert "sql: ${orders.amount} ;;" in measure
        assert 'description: "Total revenue"' in measure
        assert "measure:" not in _block(out, "view: customers")

    @pytest.mark.parametrize(
        "expr, lookml_type, sql",
        [
            ("AVG(orders.amount)", "average", "${orders.amount}"),
            ("MIN(orders.amount)", "min", "${orders.amount}"),
            ("MAX(orders.amount)", "max", "${orders.amount}"),
            ("COUNT(DISTINCT orders.customer_id)", "count_distinct", "${orders.customer_id}"),
        ],
    )
    def test_single_column_aggregates_map_to_native_types(self, expr, lookml_type, sql):
        out = _convert(_model([ORDERS], metrics=[_metric("m", expr)]))
        measure = _block(out, "measure: m")
        assert f"type: {lookml_type}" in measure
        assert f"sql: {sql} ;;" in measure

    def test_count_star_becomes_count_without_sql(self):
        out = _convert(_model([ORDERS], metrics=[_metric("n", "COUNT(*)")]))
        measure = _block(out, "measure: n")
        assert "type: count" in measure
        assert "sql:" not in measure

    def test_complex_expression_hoists_aggregates_into_hidden_measures(self):
        # Looker's symmetric aggregates only protect native typed measures, so
        # each aggregate becomes a hidden helper and the metric is arithmetic.
        expr = "SUM(orders.amount) / NULLIF(COUNT(DISTINCT orders.order_id), 0)"
        out = _convert(_model([ORDERS], metrics=[_metric("aov", expr)]))
        measure = _block(out, "measure: aov")
        assert "type: number" in measure
        assert "sql: ${aov_sum_1} / NULLIF(${aov_count_distinct_2}, 0) ;;" in measure
        helper = _block(out, "measure: aov_sum_1")
        assert "hidden: yes" in helper
        assert "type: sum" in helper
        assert "sql: ${orders.amount} ;;" in helper
        helper = _block(out, "measure: aov_count_distinct_2")
        assert "type: count_distinct" in helper
        assert "sql: ${orders.order_id} ;;" in helper

    def test_aggregate_over_row_expression_is_native(self):
        out = _convert(_model([ORDERS], metrics=[_metric("gross", "SUM(orders.amount * 1.1)")]))
        measure = _block(out, "measure: gross")
        assert "type: sum" in measure
        assert "sql: ${orders.amount} * 1.1 ;;" in measure

    def test_untypeable_aggregate_stays_inline_with_warning(self):
        model = _model([ORDERS], metrics=[_metric("n", "COUNT(orders.amount)")])
        with pytest.warns(UserWarning, match="Metric 'n'.*cannot type natively"):
            out = convert_ossie_to_lookml(model)
        measure = _block(out, "measure: n")
        assert "type: number" in measure
        assert "sql: COUNT(${orders.amount}) ;;" in measure

    def test_window_function_warns_and_stays_inline(self):
        expr = "SUM(SUM(orders.amount)) OVER (ORDER BY orders.order_id)"
        with pytest.warns(UserWarning, match="Metric 'running'.*window"):
            out = convert_ossie_to_lookml(_model([ORDERS], metrics=[_metric("running", expr)]))
        measure = _block(out, "measure: running")
        assert "type: number" in measure
        assert "SUM(SUM(${orders.amount})) OVER (ORDER BY ${orders.order_id}" in measure

    def test_unqualified_column_homes_in_its_only_owner(self):
        out = _convert(_model([CUSTOMERS, ORDERS], metrics=[_metric("rev", "SUM(amount)")]))
        measure = _block(_block(out, "view: orders"), "measure: rev")
        assert "sql: ${TABLE}.amount ;;" in measure

    def test_ambiguous_unqualified_column_warns_and_uses_first_dataset(self):
        model = _model([CUSTOMERS, REGIONS], metrics=[_metric("ids", "COUNT(DISTINCT id)")])
        with pytest.warns(UserWarning, match="Metric 'ids'.*several datasets.*'customers'"):
            out = convert_ossie_to_lookml(model)
        assert "measure: ids" in _block(out, "view: customers")

    def test_unknown_unqualified_column_warns(self):
        model = _model([CUSTOMERS, ORDERS], metrics=[_metric("t", "SUM(tax)")])
        with pytest.warns(UserWarning, match="Metric 't'.*no dataset exposes.*'tax'"):
            convert_ossie_to_lookml(model)

    def test_qualified_unknown_field_falls_back_to_physical_column_with_warning(self):
        model = _model([ORDERS], metrics=[_metric("t", "SUM(orders.tax)")])
        with pytest.warns(UserWarning, match="Metric 't'.*'orders.tax'"):
            out = convert_ossie_to_lookml(model)
        assert "sql: ${TABLE}.tax ;;" in _block(out, "measure: t")

    def test_cross_dataset_metric_warns_per_metric(self):
        expr = "SUM(orders.amount) / COUNT(DISTINCT customers.id)"
        model = _model([CUSTOMERS, ORDERS], metrics=[_metric("rpc", expr)])
        with pytest.warns(UserWarning, match="Metric 'rpc'.*customers"):
            out = convert_ossie_to_lookml(model)
        measure = _block(_block(out, "view: orders"), "measure: rpc")
        assert "sql: ${rpc_sum_1} / ${rpc_count_distinct_2} ;;" in measure
        assert "sql: ${customers.id} ;;" in _block(out, "measure: rpc_count_distinct_2")

    def test_unparseable_metric_is_verbatim_with_warning(self):
        model = _model([ORDERS], metrics=[_metric("bad", "SUM((")])
        with pytest.warns(UserWarning, match="Metric 'bad'.*verbatim"):
            out = convert_ossie_to_lookml(model)
        assert "sql: SUM(( ;;" in _block(out, "measure: bad")


# ---------------------------------------------------------------------------
# Relationships -> explore/join
# ---------------------------------------------------------------------------

class TestRelationships:
    def test_simple_join(self):
        out = _convert(_model([ORDERS, CUSTOMERS], [_rel("o2c", "orders", "customers", ["customer_id"], ["id"])]))
        join = _block(_block(out, "explore: orders"), "join: customers")
        assert "from:" not in join
        assert "type: left_outer" in join
        assert "relationship: many_to_one" in join
        assert "sql_on: ${orders.customer_id} = ${customers.id} ;;" in join

    def test_composite_join_uses_and(self):
        a = _dataset("a", [_field("x"), _field("y")], primary_key=["x", "y"])
        b = _dataset("b", [_field("x"), _field("y")], primary_key=["x", "y"])
        out = _convert(_model([a, b], [_rel("a2b", "a", "b", ["x", "y"], ["x", "y"])]))
        assert "sql_on: ${a.x} = ${b.x} AND ${a.y} = ${b.y} ;;" in out

    def test_mismatched_columns_raise(self):
        model = _model([ORDERS, CUSTOMERS], [_rel("o2c", "orders", "customers", ["customer_id", "x"], ["id"])])
        with pytest.raises(OssieConversionError, match="same length"):
            convert_ossie_to_lookml(model)

    @pytest.mark.parametrize("from_cols, to_cols", [(None, ["id"]), ([], ["id"]), (["customer_id"], None)])
    def test_missing_or_empty_columns_raise_conversion_error(self, from_cols, to_cols):
        model = _model([ORDERS, CUSTOMERS], [_rel("o2c", "orders", "customers", from_cols, to_cols)])
        with pytest.raises(OssieConversionError, match="Relationship 'o2c'"):
            convert_ossie_to_lookml(model)

    def test_two_relationships_to_same_dataset_are_aliased(self):
        orders = _dataset("orders", [_field("id"), _field("billing_addr"), _field("shipping_addr")], primary_key=["id"])
        addresses = _dataset("addresses", [_field("id")], primary_key=["id"])
        model = _model(
            [orders, addresses],
            [
                _rel("billing_address", "orders", "addresses", ["billing_addr"], ["id"]),
                _rel("shipping_address", "orders", "addresses", ["shipping_addr"], ["id"]),
            ],
        )
        out = _convert(model)
        explore = _block(out, "explore: orders")
        assert "join: addresses {" not in explore
        billing = _block(explore, "join: billing_address")
        assert "from: addresses" in billing
        assert "sql_on: ${orders.billing_addr} = ${billing_address.id} ;;" in billing
        shipping = _block(explore, "join: shipping_address")
        assert "from: addresses" in shipping
        assert "sql_on: ${orders.shipping_addr} = ${shipping_address.id} ;;" in shipping
        assert lkml.load(out)["explores"][0]["joins"][0]["from"] == "addresses"

    def test_alias_must_be_a_valid_lookml_name(self):
        orders = _dataset("orders", [_field("a"), _field("b")])
        addresses = _dataset("addresses", [_field("id")], primary_key=["id"])
        model = _model(
            [orders, addresses],
            [
                _rel("billing address", "orders", "addresses", ["a"], ["id"]),
                _rel("shipping-address", "orders", "addresses", ["b"], ["id"]),
            ],
        )
        with pytest.raises(OssieConversionError, match="'billing address'.*LookML"):
            convert_ossie_to_lookml(model)

    def test_chain_is_joined_transitively(self):
        model = _model(
            [ORDERS, CUSTOMERS, REGIONS],
            [
                _rel("o2c", "orders", "customers", ["customer_id"], ["id"]),
                _rel("c2r", "customers", "regions", ["region_id"], ["id"]),
            ],
        )
        out = _convert(model)
        orders = _block(out, "explore: orders")
        assert "sql_on: ${orders.customer_id} = ${customers.id} ;;" in _block(orders, "join: customers")
        assert "sql_on: ${customers.region_id} = ${regions.id} ;;" in _block(orders, "join: regions")
        customers = _block(out, "explore: customers")
        assert "join: regions {" in customers
        assert "explore: regions" not in out

    def test_transitive_join_through_alias_uses_the_alias(self):
        orders = _dataset("orders", [_field("id"), _field("bill_id"), _field("ship_id")], primary_key=["id"])
        addresses = _dataset("addresses", [_field("id"), _field("region_id")], primary_key=["id"])
        model = _model(
            [orders, addresses, REGIONS],
            [
                _rel("billing", "orders", "addresses", ["bill_id"], ["id"]),
                _rel("shipping", "orders", "addresses", ["ship_id"], ["id"]),
                _rel("a2r", "addresses", "regions", ["region_id"], ["id"]),
            ],
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            out = convert_ossie_to_lookml(model)
        explore = _block(out, "explore: orders")
        # Each aliased path keeps its own copy of the downstream view.
        billing_regions = _block(explore, "join: billing_regions")
        assert "from: regions" in billing_regions
        assert "sql_on: ${billing.region_id} = ${billing_regions.id} ;;" in billing_regions
        assert "sql_on: ${shipping.region_id} = ${shipping_regions.id} ;;" in _block(explore, "join: shipping_regions")
        assert "join: regions {" not in explore

    def test_diamond_joins_the_shared_target_once_with_warning(self):
        stores = _dataset("stores", [_field("id"), _field("region_id")], primary_key=["id"])
        orders = _dataset("orders", [_field("id"), _field("customer_id"), _field("store_id")], primary_key=["id"])
        model = _model(
            [orders, CUSTOMERS, stores, REGIONS],
            [
                _rel("o2c", "orders", "customers", ["customer_id"], ["id"]),
                _rel("o2s", "orders", "stores", ["store_id"], ["id"]),
                _rel("c2r", "customers", "regions", ["region_id"], ["id"]),
                _rel("s2r", "stores", "regions", ["region_id"], ["id"]),
            ],
        )
        with pytest.warns(UserWarning, match="Relationship 's2r'.*'regions' is already joined"):
            out = convert_ossie_to_lookml(model)
        explore = _block(out, "explore: orders")
        assert explore.count("join: regions {") == 1
        assert "sql_on: ${customers.region_id} = ${regions.id} ;;" in explore

    def test_cycle_terminates_with_warning(self):
        a = _dataset("a", [_field("id"), _field("b_id")], primary_key=["id"])
        b = _dataset("b", [_field("id"), _field("a_id")], primary_key=["id"])
        model = _model([a, b], [_rel("a2b", "a", "b", ["b_id"], ["id"]), _rel("b2a", "b", "a", ["a_id"], ["id"])])
        with pytest.warns(UserWarning, match="Relationship 'b2a'.*cycle"):
            out = convert_ossie_to_lookml(model)
        assert _block(_block(out, "explore: a"), "join: b")
        assert "join: a {" not in _block(out, "explore: a")

    def test_sql_on_uses_raw_timeframe_for_dimension_groups(self):
        events = _dataset("events", [_field("id"), _field("created_timestamp", is_time=True)], primary_key=["id"])
        days = _dataset("days", [_field("day_ts", is_time=True)], primary_key=["day_ts"])
        model = _model([events, days], [_rel("e2d", "events", "days", ["created_timestamp"], ["day_ts"])])
        out = _convert(model)
        assert "sql_on: ${events.created_raw} = ${days.day_ts_raw} ;;" in out

    def test_sql_on_resolves_a_column_to_the_field_that_exposes_it(self):
        orders = _dataset("orders", [_field("id"), _field("cust", expr="customer_id")], primary_key=["id"])
        model = _model([orders, CUSTOMERS], [_rel("o2c", "orders", "customers", ["customer_id"], ["id"])])
        out = _convert(model)
        assert "sql_on: ${orders.cust} = ${customers.id} ;;" in out

    def test_sql_on_unknown_column_warns(self):
        orders = _dataset("orders", [_field("id")], primary_key=["id"])
        model = _model([orders, CUSTOMERS], [_rel("o2c", "orders", "customers", ["customer_id"], ["id"])])
        with pytest.warns(UserWarning, match="Relationship 'o2c'.*'customer_id'.*no field"):
            out = convert_ossie_to_lookml(model)
        assert "sql_on: ${orders.customer_id} = ${customers.id} ;;" in out

    def test_unknown_dataset_warns(self):
        model = _model([ORDERS], [_rel("o2x", "orders", "ghosts", ["customer_id"], ["id"])])
        with pytest.warns(UserWarning, match="'ghosts' is not defined"):
            convert_ossie_to_lookml(model)

    def test_relationship_extensions_are_reported_as_dropped(self):
        rel = _rel("o2c", "orders", "customers", ["customer_id"], ["id"])
        rel["ai_context"] = {"instructions": "x"}
        rel["custom_extensions"] = [{"vendor": "X", "attributes": {}}]
        with pytest.warns(UserWarning, match="relationship 'o2c'") as record:
            convert_ossie_to_lookml(_model([ORDERS, CUSTOMERS], [rel]))
        messages = [str(w.message) for w in record if "relationship 'o2c'" in str(w.message)]
        assert any("ai_context" in m and "custom_extensions" in m for m in messages)


class TestExplores:
    def test_every_unjoined_dataset_gets_a_bare_explore(self):
        out = _convert(_model([ORDERS, CUSTOMERS]))
        assert "explore: orders {}" in out
        assert "explore: customers {}" in out

    def test_join_targets_do_not_get_their_own_explore(self):
        out = _convert(_model([ORDERS, CUSTOMERS], [_rel("o2c", "orders", "customers", ["customer_id"], ["id"])]))
        assert "explore: customers" not in out

    def test_output_has_view_and_model_sections(self):
        out = _convert(_model([ORDERS]))
        assert out.index("# Views") < out.index("view: orders") < out.index("# Explores") < out.index("explore: orders")


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------

class TestEntityDescription:
    def test_combines_description_and_synonyms(self):
        entity = {"description": "Orders", "ai_context": {"synonyms": ["purchases", "sales"]}}
        assert _entity_description(entity) == "Orders (synonyms: purchases, sales)"

    def test_string_ai_context_appended(self):
        assert _entity_description({"description": "A", "ai_context": "B"}) == "A B"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_bigquery_fixture(self):
        out = _convert((FIXTURES / "ossie_bigquery_example.yaml").read_text())
        orders = _block(out, "view: orders")
        assert "sql_table_name: `my_project.sales.orders` ;;" in orders
        # Looker has no view-level description: it is kept as a comment in the
        # view and emitted on the explore.
        assert "# Customer orders (synonyms: purchases, sales)" in orders
        assert "description:" not in orders.split("dimension:")[0]
        assert 'description: "Customer orders (synonyms: purchases, sales)"' in _block(out, "explore: orders")
        assert "primary_key: yes" in _block(orders, "dimension: order_id")
        assert "type: number" in _block(orders, "dimension: amount")
        assert "datatype: timestamp" in _block(orders, "dimension_group: order")
        assert "type: sum" in _block(orders, "measure: total_revenue")
        assert "sql: SAFE_CAST(LOWER(${TABLE}.email) AS STRING) ;;" in _block(out, "view: customers")
        join = _block(_block(out, "explore: orders"), "join: customers")
        assert "sql_on: ${orders.customer_id} = ${customers.id} ;;" in join

    def test_bigquery_fixture_is_valid_lookml(self):
        parsed = lkml.load(_convert((FIXTURES / "ossie_bigquery_example.yaml").read_text()))
        assert {v["name"] for v in parsed["views"]} == {"orders", "customers"}
        assert [e["name"] for e in parsed["explores"]] == ["orders"]

    @pytest.mark.skipif(not TPCDS.exists(), reason="TPC-DS example not found")
    def test_tpcds_example_converts_to_valid_lookml(self):
        out = _convert(TPCDS.read_text())
        parsed = lkml.load(out)
        views = {v["name"]: v for v in parsed["views"]}
        assert "store_sales" in views
        store_sales = _block(out, "view: store_sales")
        assert "primary_key: yes" in _block(store_sales, "dimension: primary_key")
        assert "type: sum" in _block(store_sales, "measure: total_sales")
        assert "sql: ${store_sales.ss_ext_sales_price} ;;" in _block(store_sales, "measure: total_sales")
        # d_year is an Integer marked is_time: a number dimension, not a group.
        assert "type: number" in _block(_block(out, "view: date_dim"), "dimension: d_year")
        explore = _block(out, "explore: store_sales")
        for target in ("date_dim", "item", "customer", "store"):
            assert f"join: {target} {{" in explore


# ---------------------------------------------------------------------------
# The review's principles applied to the rest of the input: malformed input
# is a clean OssieConversionError, every emitted name is a unique valid LookML
# name, and nothing is dropped without a warning naming the entity.
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_invalid_yaml_is_a_conversion_error(self):
        with pytest.raises(OssieConversionError, match="Invalid Ossie YAML"):
            convert_ossie_to_lookml("name: [unclosed\nversion: x")

    @pytest.mark.parametrize(
        "model, message",
        [
            ({"datasets": ["orders"]}, "Dataset #1.*mapping"),
            ({"datasets": "orders"}, "'datasets' must be a list"),
            ({"datasets": [{"name": "t", "source": "s", "fields": [42]}]}, "Dataset 't'.*field #1.*mapping"),
            ({"datasets": [{"name": "t", "source": "s", "fields": {"a": 1}}]}, "Dataset 't'.*'fields' must be a list"),
            ({"datasets": [{"name": "t", "source": "s", "primary_key": "id"}]}, "Dataset 't'.*'primary_key' must be a list"),
            ({"datasets": [{"name": "t", "source": "s", "primary_key": [1]}]}, "Dataset 't'.*'primary_key'.*non-empty strings"),
            ({"datasets": [_dataset("t", [])], "metrics": [None]}, "Metric #1.*mapping"),
            ({"datasets": [_dataset("t", [])], "relationships": ["x"]}, "Relationship #1.*mapping"),
            ({"datasets": [_dataset("t", [{"name": "f", "expression": {"dialects": ["ANSI_SQL"]}}])]}, "Field 't.f'.*dialect #1.*mapping"),
            ({"datasets": [_dataset("t", [{"name": "f", "expression": {"dialects": "x"}}])]}, "Field 't.f'.*'dialects' must be a list"),
        ],
    )
    def test_wrongly_typed_containers_are_conversion_errors(self, model, message):
        doc = yaml.dump({"version": "0.2.0.dev0", "name": "m", **model})
        with pytest.raises(OssieConversionError, match=message):
            convert_ossie_to_lookml(doc)

    def test_relationship_columns_must_be_strings(self):
        model = _model([ORDERS, CUSTOMERS], [_rel("o2c", "orders", "customers", [1], ["id"])])
        with pytest.raises(OssieConversionError, match="Relationship 'o2c'.*from_columns.*non-empty strings"):
            convert_ossie_to_lookml(model)


class TestNameSafety:
    def test_field_name_must_be_a_valid_lookml_name(self):
        with pytest.raises(OssieConversionError, match="Field 't.order id'.*LookML"):
            convert_ossie_to_lookml(_model([_dataset("t", [_field("order id")])]))

    def test_duplicate_field_names_raise(self):
        with pytest.raises(OssieConversionError, match="Dataset 't'.*duplicate field name 'a'"):
            convert_ossie_to_lookml(_model([_dataset("t", [_field("a"), _field("a")])]))

    def test_duplicate_dataset_names_raise(self):
        with pytest.raises(OssieConversionError, match="Duplicate dataset name 't'"):
            convert_ossie_to_lookml(_model([_dataset("t", [_field("a")]), _dataset("t", [_field("b")])]))

    def test_duplicate_metric_names_raise(self):
        model = _model([ORDERS], metrics=[_metric("m", "SUM(orders.amount)"), _metric("m", "COUNT(*)")])
        with pytest.raises(OssieConversionError, match="Duplicate metric name 'm'"):
            convert_ossie_to_lookml(model)

    def test_duplicate_relationship_names_raise(self):
        rels = [_rel("r", "orders", "customers", ["customer_id"], ["id"])] * 2
        with pytest.raises(OssieConversionError, match="Duplicate relationship name 'r'"):
            convert_ossie_to_lookml(_model([ORDERS, CUSTOMERS], rels))

    def test_dimension_group_avoids_timeframe_collisions_with_fields(self):
        # `created_timestamp` would become group `created`, which exposes
        # `created_date`; that already exists as a plain field.
        fields = [_field("created_date"), _field("created_timestamp", datatype="DateTimeTz")]
        out = _convert(_model([_dataset("t", fields)]))
        assert "dimension: created_date {" in out
        assert "dimension_group: created_timestamp {" in out
        assert "dimension_group: created {" not in out

    def test_measure_colliding_with_a_field_is_renamed_with_warning(self):
        orders = _dataset("orders", [_field("id"), _field("amount")], primary_key=["id"])
        model = _model([orders], metrics=[_metric("amount", "SUM(orders.amount)")])
        with pytest.warns(UserWarning, match="Metric 'amount'.*clashes with a field.*'amount_measure'"):
            out = convert_ossie_to_lookml(model)
        assert "measure: amount_measure {" in out
        assert "measure: amount {" not in out

    def test_helper_measure_names_avoid_existing_fields(self):
        orders = _dataset("orders", [_field("id"), _field("amount"), _field("aov_sum_1")], primary_key=["id"])
        model = _model([orders], metrics=[_metric("aov", "SUM(orders.amount) / COUNT(DISTINCT orders.id)")])
        out = _convert(model)
        assert "measure: aov_sum_1 {" not in out
        assert "measure: aov_sum_2 {" in out
        assert "sql: ${aov_sum_2} / ${aov_count_distinct_2} ;;" in _block(out, "measure: aov")

    def test_synthetic_primary_key_avoids_a_field_named_primary_key(self):
        ds = _dataset("t", [_field("primary_key"), _field("b")], primary_key=["primary_key", "b"])
        out = _convert(_model([ds]))
        assert "dimension: pk {" in out
        assert "primary_key: yes" in _block(out, "dimension: pk")


class TestNotSilentlyDropped:
    def test_unique_keys_are_reported(self):
        ds = _dataset("t", [_field("a")], primary_key=["a"])
        ds["unique_keys"] = [["a"]]
        with pytest.warns(UserWarning, match="dataset 't'.*unique_keys"):
            convert_ossie_to_lookml(_model([ds]))

    def test_entity_instructions_join_the_description(self):
        entity = {"description": "Orders", "ai_context": {"instructions": "Use for sales.", "synonyms": ["purchases"]}}
        assert _entity_description(entity) == "Orders Use for sales. (synonyms: purchases)"

    def test_other_ai_context_keys_are_reported(self):
        field = _field("a")
        field["ai_context"] = {"examples": ["What sold last week?"]}
        with pytest.warns(UserWarning, match="Field 't.a'.*ai_context.examples"):
            convert_ossie_to_lookml(_model([_dataset("t", [field])]))


class TestTimezones:
    def test_civil_datetime_does_not_convert_timezone(self):
        # Ossie DateTime (and BigQuery DATETIME) carry no timezone; Looker's
        # default convert_tz would shift them.
        view = _block(_convert(_model([_dataset("t", [_field("f", datatype="DateTime")])])), "view: t")
        assert "convert_tz: no" in _block(view, "dimension_group: f")

    @pytest.mark.parametrize("datatype", ["Date", "DateTimeTz"])
    def test_instants_and_dates_keep_looker_defaults(self, datatype):
        view = _block(_convert(_model([_dataset("t", [_field("f", datatype=datatype)])])), "view: t")
        assert "convert_tz" not in _block(view, "dimension_group: f")
