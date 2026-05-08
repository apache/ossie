"""Round-trip tests: OSI -> Databricks UC Metric View -> OSI.

The point of round-trips is to confirm the converter preserves the structure
that both formats can express. Lossy fields (e.g. ai_context.synonyms,
non-DATABRICKS dialects, dimension.is_time) are not expected to survive a
round trip and are explicitly preserved out of band where the spec allows it.
"""

from pathlib import Path

import pytest
import yaml

from databricks_metric_view_to_osi import convert_databricks_to_osi
from osi_to_databricks_metric_view import convert_osi_to_databricks


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
TPCDS_PATH = REPO_ROOT / "examples" / "tpcds_semantic_model.yaml"


def _wrap(model):
    return yaml.dump(
        {"version": "0.1.1", "semantic_model": [model]},
        default_flow_style=False,
    )


def _two_table_model():
    return {
        "name": "sales",
        "datasets": [
            {
                "name": "orders",
                "source": "main.sales.orders",
                "fields": [
                    {"name": "order_id", "expression": {"dialects": [
                        {"dialect": "ANSI_SQL", "expression": "order_id"}
                    ]}},
                    {"name": "customer_id", "expression": {"dialects": [
                        {"dialect": "ANSI_SQL", "expression": "customer_id"}
                    ]}},
                ],
            },
            {
                "name": "customers",
                "source": "main.sales.customers",
                "fields": [
                    {"name": "id", "expression": {"dialects": [
                        {"dialect": "ANSI_SQL", "expression": "id"}
                    ]}},
                    {"name": "email", "expression": {"dialects": [
                        {"dialect": "ANSI_SQL", "expression": "email"}
                    ]}},
                ],
            },
        ],
        "relationships": [{
            "name": "orders_to_customers",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }],
        "metrics": [{
            "name": "total_revenue",
            "expression": {"dialects": [
                {"dialect": "ANSI_SQL", "expression": "SUM(orders.amount)"}
            ]},
        }],
    }


def test_round_trip_preserves_datasets_and_relationships():
    osi_in = _wrap(_two_table_model())
    mv_yaml = convert_osi_to_databricks(osi_in)
    osi_out = yaml.safe_load(convert_databricks_to_osi(mv_yaml))

    model = osi_out["semantic_model"][0]
    ds_names = sorted(d["name"] for d in model["datasets"])
    assert ds_names == ["customers", "orders"]

    rels = model["relationships"]
    assert len(rels) == 1
    assert rels[0]["from"] == "orders"
    assert rels[0]["to"] == "customers"
    assert rels[0]["from_columns"] == ["customer_id"]
    assert rels[0]["to_columns"] == ["id"]


def test_round_trip_preserves_metric_names_and_expressions():
    osi_in = _wrap(_two_table_model())
    mv_yaml = convert_osi_to_databricks(osi_in)
    osi_out = yaml.safe_load(convert_databricks_to_osi(mv_yaml))
    metrics = osi_out["semantic_model"][0]["metrics"]
    assert [m["name"] for m in metrics] == ["total_revenue"]
    expr = metrics[0]["expression"]["dialects"][0]
    assert expr["expression"] == "SUM(orders.amount)"


@pytest.mark.skipif(not TPCDS_PATH.exists(), reason="tpcds example not found")
def test_tpcds_example_converts_without_error():
    osi_yaml = TPCDS_PATH.read_text()
    mv = yaml.safe_load(convert_osi_to_databricks(osi_yaml))

    # Primary should be store_sales — the table on the `from` side of all 4
    # relationships in the TPC-DS example.
    assert mv["source"] == "tpcds.public.store_sales"

    # All four joins should be present, one per relationship in the OSI model
    join_names = sorted(j["name"] for j in mv["joins"])
    assert join_names == [
        "store_sales_to_customer",
        "store_sales_to_date",
        "store_sales_to_item",
        "store_sales_to_store",
    ]

    measure_names = {m["name"] for m in mv["measures"]}
    assert {"total_sales", "total_profit", "customer_lifetime_value"} <= measure_names
