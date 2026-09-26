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

"""Tests for Ossie ↔ OBML period-over-period metric conversion.

A PoP metric compares against a period shifted on a date spine, which no single
Ossie expression reproduces when periods are missing. The export leaves it out of
the Ossie metrics and keeps it whole in the model-level extension; the reverse
conversion restores it. Documents written before that carry the configuration in
per-metric ``obml_pop_*`` keys, which still import.
"""

from __future__ import annotations

import json
from typing import Any

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
# OBML → Ossie
# ---------------------------------------------------------------------------


class TestOBMLtoOssiePoP:
    """OBML → Ossie: PoP metrics are left out and kept in the model extension."""

    _POP_NAMES = (
        "Revenue YoY Growth",
        "Revenue MoM Diff",
        "Revenue Prev Year",
        "Revenue YoY Ratio",
    )

    def _convert(self) -> tuple[dict, list[str]]:
        converter = conv.OBMLtoOssie(_OBML_WITH_POP)
        result = converter.convert()
        return result, converter.warnings

    @staticmethod
    def _unexported(ossie: dict) -> dict:
        for ext in ossie["custom_extensions"]:
            if ext["vendor_name"] == "ORIONBELT":
                return json.loads(ext["data"]).get("obml_unexported", {})
        return {}

    def test_pop_metrics_are_not_ossie_metrics(self) -> None:
        ossie, _ = self._convert()
        names = {m["name"] for m in ossie.get("metrics", [])}
        assert names.isdisjoint(self._POP_NAMES)

    def test_pop_definitions_kept_in_model_extension(self) -> None:
        ossie, _ = self._convert()
        kept = self._unexported(ossie)["metrics"]
        for name in self._POP_NAMES:
            assert kept[name] == _OBML_WITH_POP["metrics"][name]

    def test_each_left_out_metric_is_warned(self) -> None:
        _, warnings = self._convert()
        for name in self._POP_NAMES:
            assert any(name in w and "not exported" in w for w in warnings)

    def test_derived_metric_still_exported(self) -> None:
        """Derived metrics are not affected by PoP handling."""
        ossie, _ = self._convert()
        m = next(m for m in ossie["metrics"] if m["name"] == "Derived Metric")
        expr = m["expression"]["dialects"][0]["expression"]
        assert "{[" not in expr


# ---------------------------------------------------------------------------
# Ossie → OBML
# ---------------------------------------------------------------------------


class TestOssietoOBMLPoP:
    """Ossie → OBML: PoP metrics are reconstructed from custom_extensions."""

    def _roundtrip_ossie(self) -> dict:
        """OBML → Ossie → OBML roundtrip."""
        converter1 = conv.OBMLtoOssie(_OBML_WITH_POP)
        ossie = converter1.convert()
        converter2 = conv.OssietoOBML(ossie)
        return converter2.convert()

    def test_yoy_growth_reconstructed(self) -> None:
        obml = self._roundtrip_ossie()
        name = "Revenue YoY Growth"
        assert obml["metrics"][name] == _OBML_WITH_POP["metrics"][name]

    def test_mom_diff_reconstructed(self) -> None:
        obml = self._roundtrip_ossie()
        name = "Revenue MoM Diff"
        assert obml["metrics"][name] == _OBML_WITH_POP["metrics"][name]

    def test_legacy_per_metric_extension_still_imports(self) -> None:
        """Documents written before PoP metrics were left out still convert."""
        legacy = {
            "obml_metric_type": "period_over_period",
            "obml_pop_expression": "{[Revenue]}",
            "obml_pop_time_dimension": "Order Date",
            "obml_pop_grain": "month",
            "obml_pop_offset": -1,
            "obml_pop_offset_grain": "year",
            "obml_pop_comparison": "percentChange",
        }
        ossie = conv.OBMLtoOssie(_OBML_WITH_POP).convert()
        ossie["metrics"].append(
            {
                "name": "Legacy YoY",
                "expression": {
                    "dialects": [
                        {"dialect": "ANSI_SQL", "expression": "SUM(Orders.AMOUNT) - prev.value"}
                    ]
                },
                "custom_extensions": [{"vendor_name": "ORIONBELT", "data": json.dumps(legacy)}],
            }
        )
        m = conv.OssietoOBML(ossie).convert()["metrics"]["Legacy YoY"]
        assert m["type"] == "period_over_period"
        pop = m["periodOverPeriod"]
        assert pop["offsetGrain"] == "year"
        # Defaults are omitted on the legacy path
        assert "offset" not in pop
        assert "comparison" not in pop

    def test_prev_year_reconstructed(self) -> None:
        obml = self._roundtrip_ossie()
        m = obml["metrics"]["Revenue Prev Year"]
        assert m["type"] == "period_over_period"
        pop = m["periodOverPeriod"]
        assert pop["comparison"] == "previousValue"
        assert m.get("format") == "$#,##0.00"
        assert m.get("description") == "Last year's revenue for the same month"

    def test_ratio_reconstructed(self) -> None:
        obml = self._roundtrip_ossie()
        m = obml["metrics"]["Revenue YoY Ratio"]
        pop = m["periodOverPeriod"]
        assert pop["grain"] == "quarter"
        assert pop["comparison"] == "ratio"

    def test_derived_metric_still_works(self) -> None:
        """Derived metrics unaffected by PoP roundtrip."""
        obml = self._roundtrip_ossie()
        m = obml["metrics"]["Derived Metric"]
        assert "expression" in m
        assert "type" not in m  # Derived is the default, not stored

    def test_no_warnings_for_pop(self) -> None:
        """PoP metrics should not generate warnings on import."""
        converter1 = conv.OBMLtoOssie(_OBML_WITH_POP)
        ossie = converter1.convert()
        converter2 = conv.OssietoOBML(ossie)
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
        converter = conv.OBMLtoOssie(obml)
        ossie = converter.convert()
        metrics = ossie.get("metrics", [])
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
        converter = conv.OBMLtoOssie(obml)
        ossie = converter.convert()
        metrics = ossie.get("metrics", [])
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
        converter1 = conv.OBMLtoOssie(obml)
        ossie = converter1.convert()

        # Left out of the Ossie metrics, so no ai_context to check
        assert all(m["name"] != "YoY Growth" for m in ossie["metrics"])

        # Roundtrip back
        converter2 = conv.OssietoOBML(ossie)
        obml2 = converter2.convert()
        m = obml2["metrics"]["YoY Growth"]
        assert "year-over-year" in m.get("synonyms", [])
        assert "annual growth" in m.get("synonyms", [])

    def test_mixed_model_all_metric_types(self) -> None:
        """Model with measures, derived, cumulative, and PoP metrics all convert."""
        converter = conv.OBMLtoOssie(_OBML_WITH_POP)
        ossie = converter.convert()
        metric_names = [m["name"] for m in ossie["metrics"]]

        # Revenue (measure → Ossie metric) plus the derived metric; PoP is left out
        assert "Revenue" in metric_names
        assert "Derived Metric" in metric_names
        assert "Revenue YoY Growth" not in metric_names
        roundtrip = conv.OssietoOBML(ossie).convert()
        assert roundtrip["metrics"] == _OBML_WITH_POP["metrics"]
