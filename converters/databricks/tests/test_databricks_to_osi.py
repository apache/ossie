"""Tests for the Databricks UC Metric View -> OSI converter."""

import json
import warnings

import pytest
import yaml

from databricks_metric_view_to_osi import (
    DatabricksConversionError,
    _name_from_source,
    _parse_join_clause,
    convert_databricks_to_osi,
)


def _basic_metric_view():
    return yaml.dump({
        "version": "0.1",
        "source": "main.sales.orders",
        "comment": "Sales metric view",
        "joins": [
            {
                "name": "orders_to_customers",
                "source": "main.sales.customers",
                "sql_on": "orders.customer_id = customers.id",
            },
        ],
        "dimensions": [
            {"name": "order_id", "expr": "orders.order_id"},
            {"name": "customer_email", "expr": "customers.email"},
        ],
        "measures": [
            {"name": "total_revenue", "expr": "SUM(orders.amount)",
             "comment": "Total order revenue"},
        ],
    })


def test_round_trip_top_level_shape():
    out = yaml.safe_load(convert_databricks_to_osi(_basic_metric_view()))
    assert out["version"] == "0.1.1"
    model = out["semantic_model"][0]
    assert model["name"] == "orders_model"
    assert model["description"] == "Sales metric view"

    ds_names = [d["name"] for d in model["datasets"]]
    assert ds_names == ["orders", "customers"]

    rels = model["relationships"]
    assert len(rels) == 1
    assert rels[0]["from_columns"] == ["customer_id"]
    assert rels[0]["to_columns"] == ["id"]

    metric_names = [m["name"] for m in model["metrics"]]
    assert metric_names == ["total_revenue"]


def test_dimensions_are_attributed_to_table_in_expression():
    out = yaml.safe_load(convert_databricks_to_osi(_basic_metric_view()))
    model = out["semantic_model"][0]
    by_ds = {d["name"]: d for d in model["datasets"]}
    assert [f["name"] for f in by_ds["orders"]["fields"]] == ["order_id"]
    assert [f["name"] for f in by_ds["customers"]["fields"]] == ["customer_email"]


def test_explicit_model_name_used():
    out = yaml.safe_load(convert_databricks_to_osi(
        _basic_metric_view(), model_name="my_model"
    ))
    assert out["semantic_model"][0]["name"] == "my_model"


def test_missing_source_raises():
    with pytest.raises(DatabricksConversionError, match="missing 'source'"):
        convert_databricks_to_osi(yaml.dump({"version": "0.1"}))


def test_root_must_be_mapping():
    with pytest.raises(DatabricksConversionError, match="mapping at the root"):
        convert_databricks_to_osi("- nope")


def test_using_clause_produces_same_columns_on_both_sides():
    mv = yaml.dump({
        "version": "0.1",
        "source": "main.sales.orders",
        "joins": [
            {"name": "j", "source": "main.sales.tax_rates", "using": ["region"]}
        ],
        "dimensions": [{"name": "k", "expr": "orders.k"}],
        "measures": [{"name": "m", "expr": "SUM(orders.x)"}],
    })
    out = yaml.safe_load(convert_databricks_to_osi(mv))
    rels = out["semantic_model"][0]["relationships"]
    assert rels[0]["from_columns"] == ["region"]
    assert rels[0]["to_columns"] == ["region"]


def test_unparseable_sql_on_falls_back_to_extension_warning():
    mv = yaml.dump({
        "version": "0.1",
        "source": "main.sales.orders",
        "joins": [{
            "name": "complex_join",
            "source": "main.sales.customers",
            "sql_on": "DATEDIFF(orders.dt, customers.dt) < 30",
        }],
        "dimensions": [{"name": "k", "expr": "orders.k"}],
        "measures": [{"name": "m", "expr": "SUM(orders.x)"}],
    })
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = yaml.safe_load(convert_databricks_to_osi(mv))
    assert any("Could not parse `sql_on`" in str(w.message) for w in caught)
    model = out["semantic_model"][0]
    # No relationship was inferred for the complex join
    assert "relationships" not in model or not model.get("relationships")
    # The raw join is preserved in the DATABRICKS extension
    ext = model["custom_extensions"][0]
    data = json.loads(ext["data"])
    assert any(j["name"] == "complex_join" for j in data["raw_joins"])


def test_filter_preserved_in_databricks_extension():
    mv = yaml.dump({
        "version": "0.1",
        "source": "main.sales.orders",
        "filter": "orders.status = 'COMPLETED'",
        "dimensions": [{"name": "k", "expr": "orders.k"}],
        "measures": [{"name": "m", "expr": "SUM(orders.x)"}],
    })
    out = yaml.safe_load(convert_databricks_to_osi(mv))
    ext = out["semantic_model"][0]["custom_extensions"][0]
    data = json.loads(ext["data"])
    assert data["filter"] == "orders.status = 'COMPLETED'"
    assert data["primary_dataset"] == "orders"


def test_subquery_source_yields_generic_name():
    assert _name_from_source("SELECT 1 AS x") == "metric_view_source"
    assert _name_from_source("WITH c AS (SELECT 1) SELECT * FROM c") == (
        "metric_view_source"
    )


def test_three_part_source_uses_last_segment():
    assert _name_from_source("main.sales.orders") == "orders"
    assert _name_from_source("orders") == "orders"


def test_parse_join_clause_handles_swapped_table_order():
    rel = _parse_join_clause(
        {"name": "j", "sql_on": "customers.id = orders.customer_id"},
        primary_name="orders",
        joined_name="customers",
    )
    assert rel["from_columns"] == ["customer_id"]
    assert rel["to_columns"] == ["id"]


def test_parse_join_returns_none_for_unrelated_tables():
    assert _parse_join_clause(
        {"name": "j", "sql_on": "x.id = y.id"},
        primary_name="orders",
        joined_name="customers",
    ) is None


def test_dimension_with_unqualified_expression_attached_to_primary():
    mv = yaml.dump({
        "version": "0.1",
        "source": "main.sales.orders",
        "dimensions": [{"name": "k", "expr": "UPPER(unqualified)"}],
        "measures": [{"name": "m", "expr": "SUM(unqualified)"}],
    })
    out = yaml.safe_load(convert_databricks_to_osi(mv))
    model = out["semantic_model"][0]
    primary = model["datasets"][0]
    assert primary["name"] == "orders"
    assert [f["name"] for f in primary["fields"]] == ["k"]
