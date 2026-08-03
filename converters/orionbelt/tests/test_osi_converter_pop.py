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

"""Tests for OSI ↔ OBML period-over-period metric conversion.

Validates that PoP metrics survive the OBML → OSI → OBML roundtrip
via custom_extensions preservation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import ossie_orionbelt.converter as conv

# ---------------------------------------------------------------------------
# Test OBML model with period-over-period metrics
# ---------------------------------------------------------------------------

_OBML_WITH_POP: dict[str, Any] = {
    "version": 1.0,
    "dataObjects": {
        "Orders": {
            "code": "ORDERS",
            "database": "WAREHOUSE",
            "schema": "PUBLIC",
            "columns": {
                "Order ID": {"code": "ORDER_ID", "abstractType": "string"},
                "Order Date": {"code": "ORDER_DATE", "abstractType": "date"},
                "Amount": {"code": "AMOUNT", "abstractType": "float"},
                "Quantity": {"code": "QUANTITY", "abstractType": "int"},
            },
            "joins": [
                {
                    "joinTo": "Customers",
                    "columnsFrom": ["Customer ID"],
                    "columnsTo": ["Customer ID"],
                    "joinType": "left",
                    "cardinality": "many_to_one",
                }
            ],
        },
        "Customers": {
            "code": "CUSTOMERS",
            "database": "WAREHOUSE",
            "schema": "PUBLIC",
            "columns": {
                "Customer ID": {"code": "CUSTOMER_ID", "abstractType": "string"},
                "Country": {"code": "COUNTRY", "abstractType": "string"},
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
        "Customer Country": {
            "dataObject": "Customers",
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
    },
    "metrics": {
        "Revenue YoY Growth": {
            "type": "period_over_period",
            "expression": "{[Revenue]}",
            "periodOverPeriod": {
                "timeDimension": "Order Date",
                "grain": "month",
                "offsetGrain": "year",
                "comparison": "percentChange",
            },
        },
        "Revenue MoM Diff": {
            "type": "period_over_period",
            "expression": "{[Revenue]}",
            "periodOverPeriod": {
                "timeDimension": "Order Date",
                "grain": "month",
                "offset": -1,
                "offsetGrain": "month",
                "comparison": "difference",
            },
        },
        "Revenue Prev Year": {
            "type": "period_over_period",
            "expression": "{[Revenue]}",
            "periodOverPeriod": {
                "timeDimension": "Order Date",
                "grain": "month",
                "offsetGrain": "year",
                "comparison": "previousValue",
            },
            "description": "Last year's revenue for the same month",
            "format": "$#,##0.00",
        },
        "Revenue YoY Ratio": {
            "type": "period_over_period",
            "expression": "{[Revenue]}",
            "periodOverPeriod": {
                "timeDimension": "Order Date",
                "grain": "quarter",
                "offsetGrain": "year",
                "comparison": "ratio",
            },
        },
        "Derived Metric": {
            "expression": "{[Revenue]} / 100",
        },
    },
}


# ---------------------------------------------------------------------------
# OBML → OSI
# ---------------------------------------------------------------------------


class TestOBMLtoOSIPoP:
    """OBML → OSI: PoP metrics are serialized into custom_extensions."""

    def _convert(self) -> tuple[dict, list[str]]:
        converter = conv.OBMLtoOSI(_OBML_WITH_POP)
        result = converter.convert()
        return result, converter.warnings

    def _find_metric(self, osi: dict, name: str) -> dict | None:
        for m in osi["semantic_model"][0].get("metrics", []):
            if m["name"] == name:
                return m
        return None

    def test_yoy_growth_exported(self) -> None:
        osi, _ = self._convert()
        m = self._find_metric(osi, "Revenue YoY Growth")
        assert m is not None

        # Has an approximate SQL expression
        expr = m["expression"]["dialects"][0]["expression"]
        assert "NULLIF" in expr
        assert "- 1" in expr  # percentChange: x / NULLIF(prev, 0) - 1

        # Has custom_extensions with PoP config
        ext = self._get_pop_ext(m)
        assert ext["obml_metric_type"] == "period_over_period"
        assert ext["obml_pop_expression"] == "{[Revenue]}"
        assert ext["obml_pop_time_dimension"] == "Order Date"
        assert ext["obml_pop_grain"] == "month"
        assert ext["obml_pop_offset_grain"] == "year"
        assert ext["obml_pop_comparison"] == "percentChange"

    def test_mom_diff_exported(self) -> None:
        osi, _ = self._convert()
        m = self._find_metric(osi, "Revenue MoM Diff")
        assert m is not None

        expr = m["expression"]["dialects"][0]["expression"]
        assert "- prev.value" in expr  # difference

        ext = self._get_pop_ext(m)
        assert ext["obml_pop_offset"] == -1
        assert ext["obml_pop_offset_grain"] == "month"
        assert ext["obml_pop_comparison"] == "difference"

    def test_prev_year_exported(self) -> None:
        osi, _ = self._convert()
        m = self._find_metric(osi, "Revenue Prev Year")
        assert m is not None

        expr = m["expression"]["dialects"][0]["expression"]
        assert "prev.value" in expr  # previousValue

        ext = self._get_pop_ext(m)
        assert ext["obml_pop_comparison"] == "previousValue"
        assert ext["obml_format"] == "$#,##0.00"

    def test_ratio_exported(self) -> None:
        osi, _ = self._convert()
        m = self._find_metric(osi, "Revenue YoY Ratio")
        assert m is not None

        expr = m["expression"]["dialects"][0]["expression"]
        assert "NULLIF" in expr
        assert "- 1" not in expr  # ratio, not percentChange

        ext = self._get_pop_ext(m)
        assert ext["obml_pop_grain"] == "quarter"
        assert ext["obml_pop_comparison"] == "ratio"

    def test_derived_metric_still_exported(self) -> None:
        """Derived metrics are not affected by PoP handling."""
        osi, _ = self._convert()
        m = self._find_metric(osi, "Derived Metric")
        assert m is not None
        expr = m["expression"]["dialects"][0]["expression"]
        assert "{[" not in expr

    def test_description_preserved(self) -> None:
        osi, _ = self._convert()
        m = self._find_metric(osi, "Revenue Prev Year")
        assert m["description"] == "Last year's revenue for the same month"

    @staticmethod
    def _get_pop_ext(metric: dict) -> dict:
        for ext in metric.get("custom_extensions", []):
            if ext.get("vendor_name") == "ORIONBELT":
                data = json.loads(ext["data"])
                if data.get("obml_metric_type") == "period_over_period":
                    return data
        pytest.fail("No period_over_period custom_extension found")


# ---------------------------------------------------------------------------
# OSI → OBML
# ---------------------------------------------------------------------------


class TestOSItoOBMLPoP:
    """OSI → OBML: PoP metrics are reconstructed from custom_extensions."""

    def _roundtrip_osi(self) -> dict:
        """OBML → OSI → OBML roundtrip."""
        converter1 = conv.OBMLtoOSI(_OBML_WITH_POP)
        osi = converter1.convert()
        converter2 = conv.OSItoOBML(osi)
        return converter2.convert()

    def test_yoy_growth_reconstructed(self) -> None:
        obml = self._roundtrip_osi()
        m = obml["metrics"]["Revenue YoY Growth"]
        assert m["type"] == "period_over_period"
        assert m["expression"] == "{[Revenue]}"
        pop = m["periodOverPeriod"]
        assert pop["timeDimension"] == "Order Date"
        assert pop["grain"] == "month"
        assert pop["offsetGrain"] == "year"
        # Default offset -1 is omitted
        assert "offset" not in pop
        # Default comparison percentChange is omitted
        assert "comparison" not in pop

    def test_mom_diff_reconstructed(self) -> None:
        obml = self._roundtrip_osi()
        m = obml["metrics"]["Revenue MoM Diff"]
        assert m["type"] == "period_over_period"
        pop = m["periodOverPeriod"]
        assert pop["offsetGrain"] == "month"
        assert pop["comparison"] == "difference"
        # offset -1 is default, should be omitted
        assert "offset" not in pop

    def test_prev_year_reconstructed(self) -> None:
        obml = self._roundtrip_osi()
        m = obml["metrics"]["Revenue Prev Year"]
        assert m["type"] == "period_over_period"
        pop = m["periodOverPeriod"]
        assert pop["comparison"] == "previousValue"
        assert m.get("format") == "$#,##0.00"
        assert m.get("description") == "Last year's revenue for the same month"

    def test_ratio_reconstructed(self) -> None:
        obml = self._roundtrip_osi()
        m = obml["metrics"]["Revenue YoY Ratio"]
        pop = m["periodOverPeriod"]
        assert pop["grain"] == "quarter"
        assert pop["comparison"] == "ratio"

    def test_derived_metric_still_works(self) -> None:
        """Derived metrics unaffected by PoP roundtrip."""
        obml = self._roundtrip_osi()
        m = obml["metrics"]["Derived Metric"]
        assert "expression" in m
        assert "type" not in m  # Derived is the default, not stored

    def test_no_warnings_for_pop(self) -> None:
        """PoP metrics should not generate warnings on import."""
        converter1 = conv.OBMLtoOSI(_OBML_WITH_POP)
        osi = converter1.convert()
        converter2 = conv.OSItoOBML(osi)
        converter2.convert()
        pop_warnings = [
            w
            for w in converter2.warnings
            if "YoY" in w or "MoM" in w or "Prev Year" in w or "Ratio" in w
        ]
        assert pop_warnings == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestPoPEdgeCases:
    """Edge cases for PoP metric conversion."""

    def test_pop_without_config_skipped(self) -> None:
        """PoP metric missing periodOverPeriod is skipped with warning."""
        obml: dict[str, Any] = {
            "version": 1.0,
            "dataObjects": {
                "T": {
                    "code": "T",
                    "database": "DB",
                    "schema": "S",
                    "columns": {"c": {"code": "c", "abstractType": "int"}},
                }
            },
            "metrics": {
                "Bad PoP": {
                    "type": "period_over_period",
                    "expression": "{[Revenue]}",
                },
            },
        }
        converter = conv.OBMLtoOSI(obml)
        osi = converter.convert()
        metrics = osi["semantic_model"][0].get("metrics", [])
        assert all(m["name"] != "Bad PoP" for m in metrics)
        assert any("Bad PoP" in w for w in converter.warnings)

    def test_pop_without_expression_skipped(self) -> None:
        """PoP metric missing expression is skipped with warning."""
        obml: dict[str, Any] = {
            "version": 1.0,
            "dataObjects": {
                "T": {
                    "code": "T",
                    "database": "DB",
                    "schema": "S",
                    "columns": {"c": {"code": "c", "abstractType": "int"}},
                }
            },
            "metrics": {
                "Bad PoP": {
                    "type": "period_over_period",
                    "periodOverPeriod": {
                        "timeDimension": "Order Date",
                        "grain": "month",
                        "offsetGrain": "year",
                    },
                },
            },
        }
        converter = conv.OBMLtoOSI(obml)
        osi = converter.convert()
        metrics = osi["semantic_model"][0].get("metrics", [])
        assert all(m["name"] != "Bad PoP" for m in metrics)
        assert any("Bad PoP" in w for w in converter.warnings)

    def test_pop_with_synonyms_roundtrip(self) -> None:
        """Synonyms survive the roundtrip."""
        obml: dict[str, Any] = {
            "version": 1.0,
            "dataObjects": {
                "Orders": {
                    "code": "ORDERS",
                    "database": "DB",
                    "schema": "S",
                    "columns": {
                        "Amount": {"code": "AMOUNT", "abstractType": "float"},
                        "Date": {"code": "DT", "abstractType": "date"},
                    },
                },
            },
            "measures": {
                "Rev": {
                    "columns": [{"dataObject": "Orders", "column": "Amount"}],
                    "resultType": "float",
                    "aggregation": "sum",
                },
            },
            "metrics": {
                "YoY Growth": {
                    "type": "period_over_period",
                    "expression": "{[Rev]}",
                    "periodOverPeriod": {
                        "timeDimension": "Date",
                        "grain": "month",
                        "offsetGrain": "year",
                    },
                    "synonyms": ["year-over-year", "annual growth"],
                },
            },
        }
        converter1 = conv.OBMLtoOSI(obml)
        osi = converter1.convert()

        # Check synonyms in OSI ai_context
        osi_metric = next(
            m for m in osi["semantic_model"][0]["metrics"] if m["name"] == "YoY Growth"
        )
        assert "year-over-year" in osi_metric.get("ai_context", {}).get("synonyms", [])

        # Roundtrip back
        converter2 = conv.OSItoOBML(osi)
        obml2 = converter2.convert()
        m = obml2["metrics"]["YoY Growth"]
        assert "year-over-year" in m.get("synonyms", [])
        assert "annual growth" in m.get("synonyms", [])

    def test_mixed_model_all_metric_types(self) -> None:
        """Model with measures, derived, cumulative, and PoP metrics all convert."""
        converter = conv.OBMLtoOSI(_OBML_WITH_POP)
        osi = converter.convert()
        metric_names = [m["name"] for m in osi["semantic_model"][0]["metrics"]]

        # Revenue (measure → OSI metric) plus all 4 PoP metrics + derived
        assert "Revenue" in metric_names
        assert "Revenue YoY Growth" in metric_names
        assert "Revenue MoM Diff" in metric_names
        assert "Revenue Prev Year" in metric_names
        assert "Revenue YoY Ratio" in metric_names
        assert "Derived Metric" in metric_names
