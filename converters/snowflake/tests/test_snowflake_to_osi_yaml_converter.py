"""Tests for the Snowflake to OSI YAML converter."""

import sys
import json
from pathlib import Path

import pytest
import yaml

# Make src/ importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from snowflake_to_osi_yaml_converter import (
    SnowflakeConversionError,
    convert_snowflake_to_osi,
    _convert_model,
    _convert_table,
    _convert_field,
    _convert_relationship,
    _convert_named_expr,
    _convert_base_table_to_source,
)

# Also import the OSI-to-Snowflake converter for round-trip testing
from osi_to_snowflake_yaml_converter import convert_osi_to_snowflake


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_snowflake_model(**overrides):
    """Return a minimal valid Snowflake model dict."""
    base = {
        "name": "test_model",
        "tables": [
            {
                "name": "my_table",
                "base_table": {
                    "database": "DB",
                    "schema": "SCHEMA",
                    "table": "TBL",
                },
                "dimensions": [
                    {
                        "name": "col1",
                        "expr": "col1",
                    }
                ],
            }
        ],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _convert_base_table_to_source
# ---------------------------------------------------------------------------

class TestConvertBaseTableToSource:
    def test_three_part_name(self):
        base_table = {"database": "DB", "schema": "SCHEMA", "table": "TABLE"}
        result = _convert_base_table_to_source(base_table)
        assert result == "DB.SCHEMA.TABLE"

    def test_definition_subquery(self):
        base_table = {"definition": "SELECT * FROM foo"}
        result = _convert_base_table_to_source(base_table)
        assert result == "SELECT * FROM foo"

    def test_none_returns_none(self):
        assert _convert_base_table_to_source(None) is None

    def test_empty_dict_returns_none(self):
        assert _convert_base_table_to_source({}) is None


# ---------------------------------------------------------------------------
# _convert_field
# ---------------------------------------------------------------------------

class TestConvertField:
    def test_dimension_field(self):
        field = {"name": "col1", "expr": "col1"}
        result = _convert_field(field, is_time=False)
        assert result["name"] == "col1"
        assert result["expression"]["dialects"][0]["dialect"] == "SNOWFLAKE"
        assert result["expression"]["dialects"][0]["expression"] == "col1"
        assert result["dimension"]["is_time"] is False

    def test_time_dimension_field(self):
        field = {"name": "col1", "expr": "col1"}
        result = _convert_field(field, is_time=True)
        assert result["dimension"]["is_time"] is True

    def test_fact_field(self):
        field = {"name": "col1", "expr": "col1"}
        result = _convert_field(field, is_time=None)
        assert "dimension" not in result

    def test_field_with_description(self):
        field = {"name": "col1", "expr": "col1", "description": "A column"}
        result = _convert_field(field, is_time=False)
        assert result["description"] == "A column"

    def test_field_with_synonyms(self):
        field = {
            "name": "col1",
            "expr": "col1",
            "synonyms": ["column_1", "col"],
        }
        result = _convert_field(field, is_time=False)
        assert result["ai_context"]["synonyms"] == ["column_1", "col"]

    def test_field_missing_name(self):
        field = {"expr": "col1"}
        result = _convert_field(field, is_time=False)
        assert result is None

    def test_field_missing_expr(self):
        field = {"name": "col1"}
        result = _convert_field(field, is_time=False)
        assert result is None


# ---------------------------------------------------------------------------
# _convert_named_expr (metrics)
# ---------------------------------------------------------------------------

class TestConvertNamedExpr:
    def test_metric_basic(self):
        metric = {"name": "total_sales", "expr": "SUM(sales)"}
        result = _convert_named_expr(metric, "metric")
        assert result["name"] == "total_sales"
        assert result["expression"]["dialects"][0]["expression"] == "SUM(sales)"

    def test_metric_with_description(self):
        metric = {"name": "total_sales", "expr": "SUM(sales)", "description": "Total revenue"}
        result = _convert_named_expr(metric, "metric")
        assert result["description"] == "Total revenue"

    def test_metric_with_synonyms(self):
        metric = {
            "name": "total_sales",
            "expr": "SUM(sales)",
            "synonyms": ["revenue", "total_amount"],
        }
        result = _convert_named_expr(metric, "metric")
        assert result["ai_context"]["synonyms"] == ["revenue", "total_amount"]

    def test_metric_missing_name(self):
        metric = {"expr": "SUM(sales)"}
        result = _convert_named_expr(metric, "metric")
        assert result is None

    def test_metric_missing_expr(self):
        metric = {"name": "total_sales"}
        result = _convert_named_expr(metric, "metric")
        assert result is None


# ---------------------------------------------------------------------------
# _convert_relationship
# ---------------------------------------------------------------------------

class TestConvertRelationship:
    def test_simple_relationship(self):
        rel = {
            "name": "orders_to_customers",
            "left_table": "orders",
            "right_table": "customers",
            "relationship_columns": [
                {"left_column": "customer_id", "right_column": "id"}
            ],
        }
        result = _convert_relationship(rel)
        assert result["name"] == "orders_to_customers"
        assert result["from"] == "orders"
        assert result["to"] == "customers"
        assert result["from_columns"] == ["customer_id"]
        assert result["to_columns"] == ["id"]

    def test_composite_key_relationship(self):
        rel = {
            "name": "items_to_products",
            "left_table": "items",
            "right_table": "products",
            "relationship_columns": [
                {"left_column": "product_id", "right_column": "id"},
                {"left_column": "variant_id", "right_column": "variant_id"},
            ],
        }
        result = _convert_relationship(rel)
        assert result["from_columns"] == ["product_id", "variant_id"]
        assert result["to_columns"] == ["id", "variant_id"]

    def test_missing_left_table(self):
        rel = {
            "name": "bad_rel",
            "right_table": "customers",
        }
        with pytest.raises(SnowflakeConversionError, match="left_table"):
            _convert_relationship(rel)

    def test_missing_right_table(self):
        rel = {
            "name": "bad_rel",
            "left_table": "orders",
        }
        with pytest.raises(SnowflakeConversionError, match="right_table"):
            _convert_relationship(rel)

    def test_missing_name(self):
        rel = {
            "left_table": "orders",
            "right_table": "customers",
        }
        with pytest.raises(SnowflakeConversionError, match="name"):
            _convert_relationship(rel)


# ---------------------------------------------------------------------------
# _convert_table
# ---------------------------------------------------------------------------

class TestConvertTable:
    def test_minimal_table(self):
        table = {
            "name": "my_table",
            "base_table": {"database": "DB", "schema": "SCHEMA", "table": "TBL"},
        }
        dataset, metrics = _convert_table(table)
        assert dataset["name"] == "my_table"
        assert dataset["source"] == "DB.SCHEMA.TBL"
        assert metrics == []

    def test_table_with_primary_key(self):
        table = {
            "name": "my_table",
            "primary_key": {"columns": ["id"]},
        }
        dataset, metrics = _convert_table(table)
        assert dataset["primary_key"] == ["id"]

    def test_table_with_composite_primary_key(self):
        table = {
            "name": "my_table",
            "primary_key": {"columns": ["id1", "id2"]},
        }
        dataset, metrics = _convert_table(table)
        assert dataset["primary_key"] == ["id1", "id2"]

    def test_table_with_unique_keys(self):
        table = {
            "name": "my_table",
            "unique_keys": [
                {"columns": ["email"]},
                {"columns": ["id1", "id2"]},
            ],
        }
        dataset, metrics = _convert_table(table)
        assert dataset["unique_keys"] == [["email"], ["id1", "id2"]]

    def test_table_with_description(self):
        table = {
            "name": "my_table",
            "description": "A test table",
        }
        dataset, metrics = _convert_table(table)
        assert dataset["description"] == "A test table"

    def test_table_with_synonyms(self):
        table = {
            "name": "my_table",
            "synonyms": ["table_1", "tbl"],
        }
        dataset, metrics = _convert_table(table)
        assert dataset["ai_context"]["synonyms"] == ["table_1", "tbl"]

    def test_table_with_dimensions_and_facts(self):
        table = {
            "name": "my_table",
            "dimensions": [
                {"name": "dim1", "expr": "dim1"},
            ],
            "facts": [
                {"name": "fact1", "expr": "fact1"},
            ],
        }
        dataset, metrics = _convert_table(table)
        assert len(dataset["fields"]) == 2
        # Find dim1 and fact1
        field_names = {f["name"] for f in dataset["fields"]}
        assert "dim1" in field_names
        assert "fact1" in field_names

    def test_table_with_time_dimensions(self):
        table = {
            "name": "my_table",
            "time_dimensions": [
                {"name": "date", "expr": "date"},
            ],
        }
        dataset, metrics = _convert_table(table)
        fields = dataset["fields"]
        assert len(fields) == 1
        assert fields[0]["name"] == "date"
        assert fields[0]["dimension"]["is_time"] is True

    def test_table_with_table_level_metrics(self):
        """Test that table-level metrics are extracted and returned separately."""
        table = {
            "name": "my_table",
            "base_table": {"database": "DB", "schema": "SCHEMA", "table": "TBL"},
            "dimensions": [
                {"name": "date", "expr": "date"},
            ],
            "metrics": [
                {"name": "total_sales", "expr": "SUM(sales)"},
                {"name": "avg_price", "expr": "AVG(price)"},
            ],
        }
        dataset, metrics = _convert_table(table)
        assert dataset["name"] == "my_table"
        # Verify metrics were extracted
        assert len(metrics) == 2
        metric_names = {m["name"] for m in metrics}
        assert "total_sales" in metric_names
        assert "avg_price" in metric_names

    def test_missing_table_name(self):
        table = {"base_table": {"database": "DB", "schema": "SCHEMA", "table": "TBL"}}
        with pytest.raises(SnowflakeConversionError, match="name"):
            _convert_table(table)


# ---------------------------------------------------------------------------
# _convert_model
# ---------------------------------------------------------------------------

class TestConvertModel:
    def test_minimal_model(self):
        model = _minimal_snowflake_model()
        result = _convert_model(model)
        assert result["name"] == "test_model"
        assert "datasets" in result

    def test_model_with_relationships(self):
        model = _minimal_snowflake_model(
            relationships=[
                {
                    "name": "rel1",
                    "left_table": "my_table",
                    "right_table": "other_table",
                    "relationship_columns": [],
                }
            ]
        )
        result = _convert_model(model)
        assert "relationships" in result

    def test_model_with_metrics(self):
        model = _minimal_snowflake_model(
            metrics=[
                {
                    "name": "metric1",
                    "expr": "SUM(amount)",
                }
            ]
        )
        result = _convert_model(model)
        assert "metrics" in result

    def test_missing_model_name(self):
        model = {"tables": []}
        with pytest.raises(SnowflakeConversionError, match="name"):
            _convert_model(model)


# ---------------------------------------------------------------------------
# Integration tests: Full YAML conversion
# ---------------------------------------------------------------------------

class TestFullConversion:
    def test_minimal_valid_snowflake_yaml(self):
        snowflake_yaml = """
name: test_model
tables:
  - name: my_table
    base_table:
      database: DB
      schema: SCHEMA
      table: TBL
    dimensions:
      - name: col1
        expr: col1
"""
        result = convert_snowflake_to_osi(snowflake_yaml)
        parsed = yaml.safe_load(result)
        assert parsed["version"] == "0.2.0.dev0"
        assert "semantic_model" in parsed
        assert len(parsed["semantic_model"]) == 1
        model = parsed["semantic_model"][0]
        assert model["name"] == "test_model"
        assert len(model["datasets"]) == 1

    def test_comprehensive_snowflake_yaml(self):
        snowflake_yaml = """
name: retail_model
description: Retail analytics model
tables:
  - name: orders
    base_table:
      database: RETAIL
      schema: PUBLIC
      table: ORDERS
    primary_key:
      columns: [order_id]
    synonyms:
      - order_transactions
      - order_records
    dimensions:
      - name: order_date
        expr: order_date
        description: Date of order
    time_dimensions:
      - name: created_at
        expr: created_at
    facts:
      - name: quantity
        expr: quantity
  - name: customers
    base_table:
      database: RETAIL
      schema: PUBLIC
      table: CUSTOMERS
    dimensions:
      - name: customer_id
        expr: customer_id
relationships:
  - name: orders_customers
    left_table: orders
    right_table: customers
    relationship_columns:
      - left_column: customer_id
        right_column: customer_id
metrics:
  - name: total_sales
    expr: SUM(amount)
    description: Total sales amount
"""
        result = convert_snowflake_to_osi(snowflake_yaml)
        parsed = yaml.safe_load(result)
        assert parsed["version"] == "0.2.0.dev0"
        model = parsed["semantic_model"][0]
        assert model["name"] == "retail_model"
        assert len(model["datasets"]) == 2
        assert "relationships" in model
        assert "metrics" in model

    def test_table_level_and_top_level_metrics(self):
        """Test that both table-level and top-level metrics are combined."""
        snowflake_yaml = """
name: analytics_model
tables:
  - name: sales
    base_table:
      database: DB
      schema: PUBLIC
      table: SALES
    dimensions:
      - name: date
        expr: date
    metrics:
      - name: sales_revenue
        expr: SUM(revenue)
        description: Total revenue per table
      - name: sales_transactions
        expr: COUNT(*)
        description: Transaction count
  - name: costs
    base_table:
      database: DB
      schema: PUBLIC
      table: COSTS
    dimensions:
      - name: date
        expr: date
    metrics:
      - name: cost_total
        expr: SUM(cost_amount)
metrics:
  - name: profit_margin
    expr: (sales_revenue - cost_total) / sales_revenue
    description: Profit margin calculated from derived metrics
"""
        result = convert_snowflake_to_osi(snowflake_yaml)
        parsed = yaml.safe_load(result)
        model = parsed["semantic_model"][0]
        
        # Check that all metrics are present (3 from tables + 1 top-level = 4 total)
        assert "metrics" in model
        metrics = model["metrics"]
        assert len(metrics) == 4
        
        metric_names = {m["name"] for m in metrics}
        # Table-level metrics from sales table
        assert "sales_revenue" in metric_names
        assert "sales_transactions" in metric_names
        # Table-level metrics from costs table
        assert "cost_total" in metric_names
        # Top-level metric
        assert "profit_margin" in metric_names

    def test_spec_fields_not_dumped_to_custom_extensions(self):
        """Test that recognized Snowflake spec fields are NOT treated as unknown vendor extensions.
        
        Per the semantic view spec, fields like data_type, unique, is_enum, tags, etc.
        are recognized but intentionally not mapped to OSI. They should be dropped silently,
        not dumped into custom_extensions.
        """
        snowflake_yaml = """
name: test_model
tables:
  - name: sales
    base_table:
      database: DB
      schema: PUBLIC
      table: SALES
    tags:
      - production
      - critical
    filters:
      - name: row_filter
        expr: user_id = current_user()
    dimensions:
      - name: product_id
        expr: product_id
        data_type: NUMBER
        is_enum: true
        unique: true
      - name: category
        expr: category
        tags:
          - business_critical
        labels:
          - dimension
    facts:
      - name: quantity
        expr: quantity
        data_type: INTEGER
    metrics:
      - name: revenue
        expr: SUM(amount)
        access_modifier: PUBLIC
"""
        result = convert_snowflake_to_osi(snowflake_yaml)
        parsed = yaml.safe_load(result)
        model = parsed["semantic_model"][0]
        dataset = model["datasets"][0]
        
        # The dataset should NOT have custom_extensions (unless there are truly unknown fields)
        # Spec fields like tags, filters should not appear in custom_extensions
        if "custom_extensions" in dataset:
            # If custom_extensions exist, verify they don't contain spec field names
            for ext in dataset.get("custom_extensions", []):
                if ext.get("vendor_name") == "SNOWFLAKE":
                    data = json.loads(ext.get("data", "{}"))
                    # Should not have 'tags' or 'filters' which are recognized spec fields
                    assert "tags" not in data, "Recognized spec field 'tags' should not be in custom_extensions"
                    assert "filters" not in data, "Recognized spec field 'filters' should not be in custom_extensions"

    def test_invalid_snowflake_yaml(self):
        # Valid YAML but structurally invalid for Snowflake (missing 'name')
        snowflake_yaml = """
tables:
  - name: test
"""
        with pytest.raises(SnowflakeConversionError, match="name"):
            convert_snowflake_to_osi(snowflake_yaml)


# ---------------------------------------------------------------------------
# Round-trip tests: OSI -> Snowflake -> OSI
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_roundtrip_preserves_core_structure(self):
        """
        Test that converting OSI -> Snowflake -> OSI preserves the core structure.
        
        Note: Some fields may be lost in round-tripping due to vendor-specific
        differences (e.g., custom_extensions), but core datasets, fields, relationships,
        and metrics should be preserved.
        """
        # Start with an OSI model
        osi_yaml = """
version: "0.2.0.dev0"
semantic_model:
  - name: test_model
    datasets:
      - name: orders
        source: db.schema.orders
        primary_key: [order_id]
        fields:
          - name: order_date
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: order_date
            dimension:
              is_time: false
          - name: amount
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: amount
      - name: customers
        source: db.schema.customers
        fields:
          - name: customer_id
            expression:
              dialects:
                - dialect: SNOWFLAKE
                  expression: customer_id
            dimension:
              is_time: false
    relationships:
      - name: orders_to_customers
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [customer_id]
"""
        # Convert OSI -> Snowflake
        snowflake_yaml = convert_osi_to_snowflake(osi_yaml)
        
        # Convert Snowflake -> OSI
        osi_yaml_round_trip = convert_snowflake_to_osi(snowflake_yaml)
        
        # Parse both
        original = yaml.safe_load(osi_yaml)
        round_trip = yaml.safe_load(osi_yaml_round_trip)
        
        # Check that core structure is preserved
        assert round_trip["version"] == "0.2.0.dev0"
        original_model = original["semantic_model"][0]
        round_trip_model = round_trip["semantic_model"][0]
        
        # Check model name
        assert round_trip_model["name"] == original_model["name"]
        
        # Check datasets
        assert len(round_trip_model["datasets"]) == len(original_model["datasets"])
        original_dataset_names = {d["name"] for d in original_model["datasets"]}
        round_trip_dataset_names = {d["name"] for d in round_trip_model["datasets"]}
        assert original_dataset_names == round_trip_dataset_names
        
        # Check relationships
        if "relationships" in original_model:
            assert "relationships" in round_trip_model
            assert len(round_trip_model["relationships"]) == len(original_model["relationships"])
