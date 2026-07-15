"""Tests for OSI ↔ OBML roundtrip of measure grain and filterContext properties.

Validates that grain override and filterContext configurations survive the
OBML → OSI → OBML roundtrip via custom_extensions preservation.
"""

from __future__ import annotations

from typing import Any

import ossie_orionbelt.converter as conv

_BASE_OBML: dict[str, Any] = {
    "version": 1.0,
    "dataObjects": {
        "Orders": {
            "code": "ORDERS",
            "database": "WAREHOUSE",
            "schema": "PUBLIC",
            "columns": {
                "Order ID": {"code": "ORDER_ID", "abstractType": "string"},
                "Amount": {"code": "AMOUNT", "abstractType": "float"},
                "Order Date": {"code": "ORDER_DATE", "abstractType": "date"},
                "Region": {"code": "REGION", "abstractType": "string"},
            },
        },
    },
    "dimensions": {
        "Order Date": {
            "dataObject": "Orders",
            "column": "Order Date",
            "resultType": "date",
        },
        "Region": {
            "dataObject": "Orders",
            "column": "Region",
            "resultType": "string",
        },
    },
    "measures": {
        "Revenue": {
            "resultType": "float",
            "aggregation": "sum",
            "expression": "{[Orders].[Amount]}",
        },
    },
}


def _with_measure(overrides: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of _BASE_OBML with Revenue measure overrides applied."""
    obml: dict[str, Any] = {
        "version": _BASE_OBML["version"],
        "dataObjects": _BASE_OBML["dataObjects"],
        "dimensions": _BASE_OBML["dimensions"],
        "measures": {
            "Revenue": {**_BASE_OBML["measures"]["Revenue"], **overrides},
        },
    }
    return obml


def _roundtrip(obml: dict[str, Any]) -> dict[str, Any]:
    """OBML → OSI → OBML roundtrip."""
    fwd = conv.OBMLtoOSI(obml)
    osi = fwd.convert()
    back = conv.OSItoOBML(osi)
    return back.convert()


class TestGrainOverrideRoundtrip:
    def test_grain_fixed_empty(self):
        obml = _with_measure({"grain": {"mode": "FIXED"}})
        result = _roundtrip(obml)
        grain = result["measures"]["Revenue"]["grain"]
        assert grain["mode"] == "FIXED"

    def test_grain_fixed_with_include(self):
        obml = _with_measure({"grain": {"mode": "FIXED", "include": ["Region"]}})
        result = _roundtrip(obml)
        grain = result["measures"]["Revenue"]["grain"]
        assert grain["mode"] == "FIXED"
        assert grain["include"] == ["Region"]

    def test_grain_relative_exclude(self):
        obml = _with_measure({"grain": {"mode": "RELATIVE", "exclude": ["Region"]}})
        result = _roundtrip(obml)
        grain = result["measures"]["Revenue"]["grain"]
        assert grain["mode"] == "RELATIVE"
        assert grain["exclude"] == ["Region"]

    def test_grain_relative_keep_only(self):
        obml = _with_measure({"grain": {"mode": "RELATIVE", "keepOnly": ["Order Date"]}})
        result = _roundtrip(obml)
        grain = result["measures"]["Revenue"]["grain"]
        assert grain["mode"] == "RELATIVE"
        assert grain["keepOnly"] == ["Order Date"]

    def test_no_grain_no_key(self):
        obml = _with_measure({})
        result = _roundtrip(obml)
        assert "grain" not in result["measures"]["Revenue"]


class TestFilterContextRoundtrip:
    def test_filter_context_fixed(self):
        obml = _with_measure({"filterContext": {"mode": "FIXED"}})
        result = _roundtrip(obml)
        fc = result["measures"]["Revenue"]["filterContext"]
        assert fc["mode"] == "FIXED"

    def test_filter_context_relative_exclude(self):
        obml = _with_measure(
            {
                "filterContext": {"mode": "RELATIVE", "exclude": ["Region"]},
            }
        )
        result = _roundtrip(obml)
        fc = result["measures"]["Revenue"]["filterContext"]
        assert fc["mode"] == "RELATIVE"
        assert fc["exclude"] == ["Region"]

    def test_filter_context_relative_keep_only(self):
        obml = _with_measure(
            {
                "filterContext": {"mode": "RELATIVE", "keepOnly": ["Order Date"]},
            }
        )
        result = _roundtrip(obml)
        fc = result["measures"]["Revenue"]["filterContext"]
        assert fc["mode"] == "RELATIVE"
        assert fc["keepOnly"] == ["Order Date"]

    def test_filter_context_with_include(self):
        obml = _with_measure(
            {
                "filterContext": {
                    "mode": "FIXED",
                    "include": [
                        {"field": "Region", "op": "=", "value": "EMEA"},
                        {"field": "Order Date", "op": ">=", "value": "2024-01-01"},
                    ],
                },
            }
        )
        result = _roundtrip(obml)
        fc = result["measures"]["Revenue"]["filterContext"]
        assert fc["mode"] == "FIXED"
        assert len(fc["include"]) == 2
        assert fc["include"][0]["field"] == "Region"
        assert fc["include"][0]["op"] == "="
        assert fc["include"][0]["value"] == "EMEA"
        assert fc["include"][1]["field"] == "Order Date"

    def test_no_filter_context_no_key(self):
        obml = _with_measure({})
        result = _roundtrip(obml)
        assert "filterContext" not in result["measures"]["Revenue"]


class TestCombinedOverridesRoundtrip:
    def test_grain_and_filter_context_together(self):
        obml = _with_measure(
            {
                "grain": {"mode": "FIXED", "include": ["Order Date"]},
                "filterContext": {
                    "mode": "FIXED",
                    "include": [
                        {"field": "Region", "op": "=", "value": "APAC"},
                    ],
                },
            }
        )
        result = _roundtrip(obml)
        grain = result["measures"]["Revenue"]["grain"]
        fc = result["measures"]["Revenue"]["filterContext"]
        assert grain["mode"] == "FIXED"
        assert grain["include"] == ["Order Date"]
        assert fc["mode"] == "FIXED"
        assert fc["include"][0]["value"] == "APAC"

    def test_grain_and_filter_context_with_total(self):
        obml = _with_measure(
            {
                "total": True,
                "grain": {"mode": "FIXED", "include": ["Region"]},
                "filterContext": {"mode": "RELATIVE", "exclude": ["Order Date"]},
            }
        )
        result = _roundtrip(obml)
        assert result["measures"]["Revenue"]["total"] is True
        assert result["measures"]["Revenue"]["grain"]["mode"] == "FIXED"
        assert result["measures"]["Revenue"]["filterContext"]["exclude"] == ["Order Date"]

    def test_all_extras_preserved(self):
        obml = _with_measure(
            {
                "total": True,
                "allowFanOut": True,
                "dataType": "NUMERIC(18,2)",
                "owner": "finance",
                "grain": {"mode": "RELATIVE", "keepOnly": ["Order Date"]},
                "filterContext": {"mode": "FIXED"},
            }
        )
        result = _roundtrip(obml)
        m = result["measures"]["Revenue"]
        assert m["total"] is True
        assert m["allowFanOut"] is True
        assert m["dataType"] == "NUMERIC(18,2)"
        assert m["owner"] == "finance"
        assert m["grain"]["keepOnly"] == ["Order Date"]
        assert m["filterContext"]["mode"] == "FIXED"


class TestMeasureAggregationRoundtrip:
    """``aggregation: measure`` delegates resolution to the engine
    (Databricks Metric View). OSI has no first-class concept for
    engine-delegated aggregation, so the OBML → OSI direction emits a
    ``MEASURE("<label>")`` placeholder expression and stashes
    ``obml_aggregation: measure`` under custom_extensions for the
    reverse direction. See issue #92.
    """

    def _delegated_obml(self) -> dict[str, Any]:
        """Minimal OBML model with a single MEASURE-aggregation measure.
        Cannot share the _BASE_OBML Revenue (which has an expression);
        MEASURE forbids columns / expression / filters / total.
        """
        return {
            "version": 1.0,
            "dataObjects": {
                "MetricView": {
                    "code": "sales_metric_view",
                    "database": "warehouse",
                    "schema": "silver",
                    "columns": {
                        "Region": {"code": "region", "abstractType": "string"},
                    },
                },
            },
            "dimensions": {
                "Region": {
                    "dataObject": "MetricView",
                    "column": "Region",
                    "resultType": "string",
                },
            },
            "measures": {
                "Total Revenue": {
                    "aggregation": "measure",
                    "resultType": "float",
                    "dataType": "decimal(18, 2)",
                },
            },
        }

    @staticmethod
    def _osi_metrics(osi: dict[str, Any]) -> list[dict[str, Any]]:
        return osi["semantic_model"][0].get("metrics", [])

    def test_obml_to_osi_emits_measure_placeholder(self):
        obml = self._delegated_obml()
        osi = conv.OBMLtoOSI(obml).convert()
        metrics = {m["name"]: m for m in self._osi_metrics(osi)}
        assert "Total Revenue" in metrics
        expr = metrics["Total Revenue"]["expression"]["dialects"][0]["expression"]
        assert expr == 'MEASURE("Total Revenue")'

    def test_obml_to_osi_stashes_aggregation_marker(self):
        obml = self._delegated_obml()
        osi = conv.OBMLtoOSI(obml).convert()
        metric = next(m for m in self._osi_metrics(osi) if m["name"] == "Total Revenue")
        exts = metric.get("custom_extensions", [])
        assert exts, "expected custom_extensions to carry the obml_aggregation marker"
        import json as _json

        common = [_json.loads(e["data"]) for e in exts if e.get("vendor_name") == "ORIONBELT"]
        assert any(e.get("obml_aggregation") == "measure" for e in common), (
            "obml_aggregation marker missing from COMMON extras"
        )

    def test_roundtrip_preserves_measure_aggregation(self):
        obml = self._delegated_obml()
        result = _roundtrip(obml)
        m = result["measures"]["Total Revenue"]
        assert m["aggregation"] == "measure"
        # Round-tripping must not invent a source column, expression,
        # or filter — those would all break re-validation of the OBML.
        assert "columns" not in m or m["columns"] == []
        assert "expression" not in m
        assert "filters" not in m

    def test_roundtrip_preserves_data_type(self):
        obml = self._delegated_obml()
        result = _roundtrip(obml)
        assert result["measures"]["Total Revenue"]["dataType"] == "decimal(18, 2)"
