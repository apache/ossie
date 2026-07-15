"""OSI ↔ OBML roundtrip for v2.6 trend-analysis fields.

Validates that:
- ``partitionBy`` survives OBML → OSI → OBML on cumulative metrics
- ``MetricType.WINDOW`` round-trips (function, offset, buckets, partitionBy,
  orderDirection, defaultValue)
- Statistical aggregations (two-column ``CORR`` / ``COVAR_*``) preserve
  column order through the SQL serialization
"""

from __future__ import annotations

import json
from typing import Any

import ossie_orionbelt.converter as conv

_OBML_V26: dict[str, Any] = {
    "version": 1.0,
    "dataObjects": {
        "Orders": {
            "code": "ORDERS",
            "database": "WAREHOUSE",
            "schema": "PUBLIC",
            "columns": {
                "Order Date": {"code": "ORDER_DATE", "abstractType": "date"},
                "Country": {"code": "COUNTRY", "abstractType": "string"},
                "Amount": {"code": "AMOUNT", "abstractType": "float"},
                "Spend": {"code": "SPEND", "abstractType": "float"},
            },
        },
    },
    "dimensions": {
        "Order Date": {
            "dataObject": "Orders",
            "column": "Order Date",
            "resultType": "date",
            "timeGrain": "month",
        },
        "Country": {
            "dataObject": "Orders",
            "column": "Country",
            "resultType": "string",
        },
    },
    "measures": {
        "Revenue": {
            "columns": [{"dataObject": "Orders", "column": "Amount"}],
            "resultType": "float",
            "aggregation": "sum",
        },
        "Spend Stddev": {
            "columns": [{"dataObject": "Orders", "column": "Spend"}],
            "resultType": "float",
            "aggregation": "stddev_pop",
        },
        "Revenue Spend Corr": {
            "columns": [
                {"dataObject": "Orders", "column": "Amount"},
                {"dataObject": "Orders", "column": "Spend"},
            ],
            "resultType": "float",
            "aggregation": "corr",
        },
    },
    "metrics": {
        "Corr Doubled": {
            "type": "derived",
            "expression": "2 * {[Revenue Spend Corr]}",
        },
        "Revenue MA3 by Country": {
            "type": "cumulative",
            "measure": "Revenue",
            "timeDimension": "Order Date",
            "cumulativeType": "avg",
            "window": 3,
            "partitionBy": ["Country"],
        },
        "Revenue Rank": {
            "type": "window",
            "windowFunction": "dense_rank",
            "measure": "Revenue",
            "orderDirection": "desc",
            "partitionBy": ["Country"],
        },
        "Revenue Prior Month": {
            "type": "window",
            "windowFunction": "lag",
            "measure": "Revenue",
            "offset": 1,
            "timeDimension": "Order Date",
            "partitionBy": ["Country"],
            "defaultValue": 0,
        },
        "Revenue Quartile": {
            "type": "window",
            "windowFunction": "ntile",
            "measure": "Revenue",
            "buckets": 4,
            "partitionBy": ["Country"],
        },
    },
}


def _osi_metric(osi: dict, name: str) -> dict:
    for m in osi["semantic_model"][0].get("metrics", []):
        if m["name"] == name:
            return m
    raise AssertionError(f"missing OSI metric {name!r}")


def _extras(osi_metric: dict) -> dict:
    for ext in osi_metric.get("custom_extensions", []):
        if ext.get("vendor_name") == "ORIONBELT":
            return json.loads(ext["data"])
    return {}


class TestObmlToOsiV26:
    def setup_method(self) -> None:
        self.osi = conv.OBMLtoOSI(_OBML_V26).convert()

    def test_partition_by_preserved_on_cumulative(self) -> None:
        m = _osi_metric(self.osi, "Revenue MA3 by Country")
        extras = _extras(m)
        assert extras["obml_metric_type"] == "cumulative"
        assert extras["obml_partition_by"] == ["Country"]

    def test_window_dense_rank_preserved(self) -> None:
        m = _osi_metric(self.osi, "Revenue Rank")
        extras = _extras(m)
        assert extras["obml_metric_type"] == "window"
        assert extras["obml_window_function"] == "dense_rank"
        assert extras["obml_window_measure"] == "Revenue"
        assert extras["obml_partition_by"] == ["Country"]
        assert extras["obml_order_direction"] == "desc"

    def test_window_lag_preserved(self) -> None:
        m = _osi_metric(self.osi, "Revenue Prior Month")
        extras = _extras(m)
        assert extras["obml_window_function"] == "lag"
        assert extras["obml_window_offset"] == 1
        assert extras["obml_window_time_dimension"] == "Order Date"
        assert extras["obml_window_default_value"] == 0

    def test_window_ntile_preserved(self) -> None:
        m = _osi_metric(self.osi, "Revenue Quartile")
        extras = _extras(m)
        assert extras["obml_window_function"] == "ntile"
        assert extras["obml_window_buckets"] == 4

    def test_corr_measure_emits_two_columns(self) -> None:
        # Find Revenue Spend Corr in OSI metrics
        m = _osi_metric(self.osi, "Revenue Spend Corr")
        expr = m["expression"]["dialects"][0]["expression"]
        # CORR(orders.amount, orders.spend) — column order preserved
        assert expr.upper().startswith("CORR(")
        assert "AMOUNT" in expr.upper()
        assert "SPEND" in expr.upper()
        assert expr.upper().index("AMOUNT") < expr.upper().index("SPEND")

    def test_derived_metric_referencing_multi_col_measure_emits_all_columns(
        self,
    ) -> None:
        """Regression: a derived metric that references a two-column
        stat-aggregate measure must inline ALL columns when serialized
        to OSI SQL. Before the fix, ``_measure_to_sql`` only emitted
        ``columns[0]`` so the derived expression carried garbage
        ``CORR(orders.amount)``.
        """
        m = _osi_metric(self.osi, "Corr Doubled")
        expr = m["expression"]["dialects"][0]["expression"]
        # Both columns from the referenced measure must appear,
        # in declaration order, inside the derived expression.
        assert "CORR(" in expr.upper()
        assert "AMOUNT" in expr.upper()
        assert "SPEND" in expr.upper()
        assert expr.upper().index("AMOUNT") < expr.upper().index("SPEND")


class TestOsiToObmlV26:
    def setup_method(self) -> None:
        self.osi = conv.OBMLtoOSI(_OBML_V26).convert()
        self.obml = conv.OSItoOBML(self.osi).convert()

    def test_cumulative_partition_by_roundtrip(self) -> None:
        m = self.obml["metrics"]["Revenue MA3 by Country"]
        assert m["type"] == "cumulative"
        assert m["partitionBy"] == ["Country"]
        assert m["window"] == 3
        assert m["cumulativeType"] == "avg"

    def test_window_dense_rank_roundtrip(self) -> None:
        m = self.obml["metrics"]["Revenue Rank"]
        assert m["type"] == "window"
        assert m["windowFunction"] == "dense_rank"
        assert m["measure"] == "Revenue"
        assert m["partitionBy"] == ["Country"]

    def test_window_lag_roundtrip(self) -> None:
        m = self.obml["metrics"]["Revenue Prior Month"]
        assert m["type"] == "window"
        assert m["windowFunction"] == "lag"
        assert m["offset"] == 1
        assert m["timeDimension"] == "Order Date"
        assert m["defaultValue"] == 0

    def test_window_ntile_roundtrip(self) -> None:
        m = self.obml["metrics"]["Revenue Quartile"]
        assert m["windowFunction"] == "ntile"
        assert m["buckets"] == 4
