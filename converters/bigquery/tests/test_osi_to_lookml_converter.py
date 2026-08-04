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

import warnings
from pathlib import Path

import pytest
import yaml

from ossie_bigquery.converter import (
    OsiConversionError,
    convert_osi_to_lookml,
    _convert_field,
    _convert_metric,
    _convert_relationships,
    _convert_source,
    _entity_description,
    _extract_expression,
    _is_time_dimension,
    _strip_time_suffix,
    _to_lookml_sql,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wrap_osi(model_dict):
    return yaml.dump(
        {"version": "0.2.0.dev0", "semantic_model": [model_dict]},
        default_flow_style=False,
    )


def _minimal_model(**overrides):
    base = {
        "name": "test_model",
        "datasets": [
            {
                "name": "my_table",
                "source": "proj.dataset.tbl",
                "fields": [
                    {
                        "name": "col1",
                        "expression": {
                            "dialects": [{"dialect": "ANSI_SQL", "expression": "col1"}]
                        },
                        "dimension": {"is_time": False},
                    }
                ],
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Version / envelope handling
# ---------------------------------------------------------------------------

class TestEnvelope:
    def test_rejects_wrong_version(self):
        bad = yaml.dump({"version": "0.0.0", "semantic_model": [_minimal_model()]})
        with pytest.raises(OsiConversionError, match="Unsupported OSI"):
            convert_osi_to_lookml(bad)

    def test_rejects_non_mapping_root(self):
        with pytest.raises(OsiConversionError, match="expected a mapping"):
            convert_osi_to_lookml("- just\n- a\n- list\n")

    def test_rejects_empty_semantic_model(self):
        bad = yaml.dump({"version": "0.2.0.dev0", "semantic_model": []})
        with pytest.raises(OsiConversionError, match="non-empty list"):
            convert_osi_to_lookml(bad)

    def test_multiple_models_warns(self):
        payload = yaml.dump(
            {
                "version": "0.2.0.dev0",
                "semantic_model": [_minimal_model(), _minimal_model(name="second")],
            }
        )
        with pytest.warns(UserWarning, match="only the first"):
            convert_osi_to_lookml(payload)


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
        expr = {
            "dialects": [
                {"dialect": "ANSI_SQL", "expression": "LOWER(email)"},
                {"dialect": "BIGQUERY", "expression": "SAFE_CAST(LOWER(email) AS STRING)"},
            ]
        }
        assert _extract_expression(expr, "email") == "SAFE_CAST(LOWER(email) AS STRING)"

    def test_falls_back_to_ansi(self):
        expr = {"dialects": [{"dialect": "ANSI_SQL", "expression": "col"}]}
        assert _extract_expression(expr, "col") == "col"

    def test_unsupported_only_warns_and_skips(self):
        expr = {"dialects": [{"dialect": "MDX", "expression": "[Measures].[X]"}]}
        with pytest.warns(UserWarning, match="no BigQuery-compatible"):
            assert _extract_expression(expr, "x") is None

    def test_missing_expression_raises(self):
        with pytest.raises(OsiConversionError, match="Missing or malformed"):
            _extract_expression(None, "x")


# ---------------------------------------------------------------------------
# _to_lookml_sql
# ---------------------------------------------------------------------------

class TestToLookmlSql:
    def test_bare_identifier_qualified(self):
        assert _to_lookml_sql("customer_id", "f") == "${TABLE}.customer_id"

    def test_computed_expression_verbatim_with_warning(self):
        with pytest.warns(UserWarning, match="computed expression"):
            assert _to_lookml_sql("LOWER(email)", "f") == "LOWER(email)"


# ---------------------------------------------------------------------------
# Field / dimension classification
# ---------------------------------------------------------------------------

class TestFields:
    def test_time_field_becomes_dimension_group(self):
        field = {
            "name": "order_date",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "order_date"}]},
            "dimension": {"is_time": True},
        }
        block = _convert_field(field)
        assert block.startswith("dimension_group: order {")
        assert "type: time" in block
        assert "timeframes:" in block

    def test_plain_field_becomes_dimension(self):
        field = {
            "name": "customer_id",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "customer_id"}]},
            "dimension": {"is_time": False},
        }
        block = _convert_field(field)
        assert block.startswith("dimension: customer_id {")
        assert "${TABLE}.customer_id" in block

    def test_is_time_dimension_helper(self):
        assert _is_time_dimension({"dimension": {"is_time": True}}) is True
        assert _is_time_dimension({"dimension": {"is_time": False}}) is False
        assert _is_time_dimension({}) is False

    def test_strip_time_suffix(self):
        assert _strip_time_suffix("order_date") == "order"
        assert _strip_time_suffix("created_timestamp") == "created"
        assert _strip_time_suffix("revenue") == "revenue"


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_metric_becomes_measure(self):
        metric = {
            "name": "total_revenue",
            "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "SUM(orders.amount)"}]},
            "description": "Total revenue",
        }
        block = _convert_metric(metric)
        assert block.startswith("measure: total_revenue {")
        assert "sql: SUM(orders.amount) ;;" in block
        assert 'description: "Total revenue"' in block


# ---------------------------------------------------------------------------
# Relationships -> explore/join
# ---------------------------------------------------------------------------

class TestRelationships:
    def test_simple_join(self):
        rels = [
            {
                "name": "orders_to_customers",
                "from": "orders",
                "to": "customers",
                "from_columns": ["customer_id"],
                "to_columns": ["id"],
            }
        ]
        out = _convert_relationships(rels, [{"name": "orders"}, {"name": "customers"}])
        assert "explore: orders {" in out
        assert "join: customers {" in out
        assert "sql_on: ${orders.customer_id} = ${customers.id} ;;" in out
        assert "relationship: many_to_one" in out

    def test_composite_join_uses_and(self):
        rels = [
            {
                "name": "lines_to_products",
                "from": "order_lines",
                "to": "products",
                "from_columns": ["product_id", "variant_id"],
                "to_columns": ["id", "variant_id"],
            }
        ]
        out = _convert_relationships(rels, [{"name": "order_lines"}, {"name": "products"}])
        assert (
            "sql_on: ${order_lines.product_id} = ${products.id} AND "
            "${order_lines.variant_id} = ${products.variant_id} ;;"
        ) in out

    def test_mismatched_columns_raise(self):
        rels = [
            {
                "name": "bad",
                "from": "a",
                "to": "b",
                "from_columns": ["x"],
                "to_columns": ["y", "z"],
            }
        ]
        with pytest.raises(OsiConversionError, match="same length"):
            _convert_relationships(rels, [{"name": "a"}, {"name": "b"}])


# ---------------------------------------------------------------------------
# _entity_description
# ---------------------------------------------------------------------------

class TestEntityDescription:
    def test_combines_description_and_synonyms(self):
        out = _entity_description(
            {"description": "Orders", "ai_context": {"synonyms": ["purchases", "sales"]}}
        )
        assert "Orders" in out
        assert "purchases" in out and "sales" in out

    def test_string_ai_context_appended(self):
        out = _entity_description({"description": "Orders", "ai_context": "retail"})
        assert out == "Orders retail"


# ---------------------------------------------------------------------------
# End-to-end against fixtures
# ---------------------------------------------------------------------------

class TestEndToEnd:
    def test_bigquery_fixture_round(self):
        osi_yaml = (FIXTURES / "osi_bigquery_example.yaml").read_text()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = convert_osi_to_lookml(osi_yaml)
        # Views for both datasets.
        assert "view: orders {" in out
        assert "view: customers {" in out
        # BigQuery dialect preferred for the normalized email field.
        assert "SAFE_CAST(LOWER(email) AS STRING)" in out
        # Backtick-quoted BigQuery table reference.
        assert "sql_table_name: `my_project.sales.orders` ;;" in out
        # Time field -> dimension_group.
        assert "dimension_group: order {" in out
        # Metric -> measure, emitted in the first view.
        assert "measure: total_revenue {" in out
        # Relationship -> explore/join.
        assert "explore: orders {" in out
        assert "join: customers {" in out

    def test_tpcds_example_converts(self):
        # The repo's canonical example is ANSI_SQL-only; the converter must
        # handle it with no BigQuery dialect present.
        tpcds = Path(__file__).resolve().parents[3] / "examples" / "tpcds_semantic_model.yaml"
        if not tpcds.exists():
            pytest.skip("TPC-DS example not found")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            out = convert_osi_to_lookml(tpcds.read_text())
        assert "view: store_sales {" in out
        assert "explore:" in out
