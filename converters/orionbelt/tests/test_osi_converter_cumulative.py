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

"""Tests for OSI ↔ OBML cumulative metric conversion.

Validates that cumulative metrics survive the OBML → OSI → OBML roundtrip
via custom_extensions preservation.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import ossie_orionbelt.converter as conv

# ---------------------------------------------------------------------------
# Test OBML model with cumulative metrics
# ---------------------------------------------------------------------------

_OBML_WITH_CUMULATIVES: dict[str, Any] = {
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
            },
        },
    },
    "dimensions": {
        "Order Date": {
            "dataObject": "Orders",
            "column": "Order Date",
            "resultType": "date",
            "timeGrain": "day",
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
        "Running Revenue": {
            "type": "cumulative",
            "measure": "Revenue",
            "timeDimension": "Order Date",
        },
        "Rolling 7d Revenue": {
            "type": "cumulative",
            "measure": "Revenue",
            "timeDimension": "Order Date",
            "cumulativeType": "sum",
            "window": 7,
        },
        "MTD Revenue": {
            "type": "cumulative",
            "measure": "Revenue",
            "timeDimension": "Order Date",
            "cumulativeType": "avg",
            "grainToDate": "month",
            "description": "Month-to-date average revenue",
            "format": "$#,##0.00",
        },
        "Derived Metric": {
            "expression": "{[Revenue]} / 100",
        },
    },
}


# ---------------------------------------------------------------------------
# OBML → OSI
# ---------------------------------------------------------------------------


class TestOBMLtoOSICumulative:
    """OBML → OSI: cumulative metrics are serialized into custom_extensions."""

    def _convert(self) -> tuple[dict, list[str]]:
        converter = conv.OBMLtoOSI(_OBML_WITH_CUMULATIVES)
        result = converter.convert()
        return result, converter.warnings

    def _find_metric(self, osi: dict, name: str) -> dict | None:
        for m in osi["semantic_model"][0].get("metrics", []):
            if m["name"] == name:
                return m
        return None

    def test_running_total_exported(self) -> None:
        osi, warnings = self._convert()
        m = self._find_metric(osi, "Running Revenue")
        assert m is not None

        # Has an approximate SQL expression
        expr = m["expression"]["dialects"][0]["expression"]
        assert "OVER" in expr
        assert "UNBOUNDED PRECEDING" in expr

        # Has custom_extensions with cumulative config
        ext = self._get_cumulative_ext(m)
        assert ext["obml_metric_type"] == "cumulative"
        assert ext["obml_cumulative_measure"] == "Revenue"
        assert ext["obml_cumulative_time_dimension"] == "Order Date"
        assert ext["obml_cumulative_type"] == "sum"
        assert "obml_cumulative_window" not in ext
        assert "obml_cumulative_grain_to_date" not in ext

    def test_rolling_window_exported(self) -> None:
        osi, _ = self._convert()
        m = self._find_metric(osi, "Rolling 7d Revenue")
        assert m is not None

        expr = m["expression"]["dialects"][0]["expression"]
        assert "6 PRECEDING" in expr

        ext = self._get_cumulative_ext(m)
        assert ext["obml_cumulative_window"] == 7
        assert "obml_cumulative_grain_to_date" not in ext

    def test_grain_to_date_exported(self) -> None:
        osi, _ = self._convert()
        m = self._find_metric(osi, "MTD Revenue")
        assert m is not None

        expr = m["expression"]["dialects"][0]["expression"]
        assert "PARTITION BY" in expr
        assert "month" in expr

        ext = self._get_cumulative_ext(m)
        assert ext["obml_cumulative_type"] == "avg"
        assert ext["obml_cumulative_grain_to_date"] == "month"
        assert ext["obml_format"] == "$#,##0.00"
        assert "obml_cumulative_window" not in ext

    def test_derived_metric_still_exported(self) -> None:
        """Derived metrics are not affected by cumulative handling."""
        osi, _ = self._convert()
        m = self._find_metric(osi, "Derived Metric")
        assert m is not None
        expr = m["expression"]["dialects"][0]["expression"]
        # Should contain the expanded SQL, not OBML refs
        assert "{[" not in expr

    def test_description_preserved(self) -> None:
        osi, _ = self._convert()
        m = self._find_metric(osi, "MTD Revenue")
        assert m["description"] == "Month-to-date average revenue"

    @staticmethod
    def _get_cumulative_ext(metric: dict) -> dict:
        for ext in metric.get("custom_extensions", []):
            if ext.get("vendor_name") == "ORIONBELT":
                data = json.loads(ext["data"])
                if data.get("obml_metric_type") == "cumulative":
                    return data
        pytest.fail("No cumulative custom_extension found")


# ---------------------------------------------------------------------------
# OSI → OBML
# ---------------------------------------------------------------------------


class TestOSItoOBMLCumulative:
    """OSI → OBML: cumulative metrics are reconstructed from custom_extensions."""

    def _roundtrip_osi(self) -> dict:
        """OBML → OSI → OBML roundtrip."""
        converter1 = conv.OBMLtoOSI(_OBML_WITH_CUMULATIVES)
        osi = converter1.convert()
        converter2 = conv.OSItoOBML(osi)
        return converter2.convert()

    def test_running_total_reconstructed(self) -> None:
        obml = self._roundtrip_osi()
        m = obml["metrics"]["Running Revenue"]
        assert m["type"] == "cumulative"
        assert m["measure"] == "Revenue"
        assert m["timeDimension"] == "Order Date"
        # Default cumulativeType "sum" is omitted (default)
        assert "cumulativeType" not in m
        assert "window" not in m
        assert "grainToDate" not in m

    def test_rolling_window_reconstructed(self) -> None:
        obml = self._roundtrip_osi()
        m = obml["metrics"]["Rolling 7d Revenue"]
        assert m["type"] == "cumulative"
        assert m["measure"] == "Revenue"
        assert m["timeDimension"] == "Order Date"
        assert m["window"] == 7

    def test_grain_to_date_reconstructed(self) -> None:
        obml = self._roundtrip_osi()
        m = obml["metrics"]["MTD Revenue"]
        assert m["type"] == "cumulative"
        assert m["measure"] == "Revenue"
        assert m["timeDimension"] == "Order Date"
        assert m["cumulativeType"] == "avg"
        assert m["grainToDate"] == "month"
        assert m["format"] == "$#,##0.00"
        assert m["description"] == "Month-to-date average revenue"

    def test_derived_metric_still_works(self) -> None:
        """Derived metrics unaffected by cumulative roundtrip."""
        obml = self._roundtrip_osi()
        m = obml["metrics"]["Derived Metric"]
        assert "expression" in m
        assert "type" not in m  # Derived is the default, not stored

    def test_no_warnings_for_cumulative(self) -> None:
        """Cumulative metrics should not generate warnings on import."""
        converter1 = conv.OBMLtoOSI(_OBML_WITH_CUMULATIVES)
        osi = converter1.convert()
        converter2 = conv.OSItoOBML(osi)
        converter2.convert()
        # No "skipped" or "unparseable" warnings for cumulative metrics
        cum_warnings = [
            w for w in converter2.warnings if "Running Revenue" in w or "Rolling" in w or "MTD" in w
        ]
        assert cum_warnings == []


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestCumulativeEdgeCases:
    """Edge cases for cumulative metric conversion."""

    def test_cumulative_without_measure_skipped(self) -> None:
        """Cumulative metric missing 'measure' field is skipped with warning."""
        obml = {
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
                "Bad Cumulative": {
                    "type": "cumulative",
                    "timeDimension": "Order Date",
                },
            },
        }
        converter = conv.OBMLtoOSI(obml)
        osi = converter.convert()
        # Should be skipped with a warning
        metrics = osi["semantic_model"][0].get("metrics", [])
        assert all(m["name"] != "Bad Cumulative" for m in metrics)
        assert any("Bad Cumulative" in w for w in converter.warnings)

    def test_cumulative_with_synonyms_roundtrip(self) -> None:
        """Synonyms survive the roundtrip."""
        obml = {
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
                "Running Rev": {
                    "type": "cumulative",
                    "measure": "Rev",
                    "timeDimension": "Date",
                    "synonyms": ["cumulative revenue", "total so far"],
                },
            },
        }
        converter1 = conv.OBMLtoOSI(obml)
        osi = converter1.convert()

        # Check synonyms in OSI ai_context
        osi_metric = osi["semantic_model"][0]["metrics"][-1]
        assert "cumulative revenue" in osi_metric.get("ai_context", {}).get("synonyms", [])

        # Roundtrip back
        converter2 = conv.OSItoOBML(osi)
        obml2 = converter2.convert()
        m = obml2["metrics"]["Running Rev"]
        assert "cumulative revenue" in m.get("synonyms", [])
        assert "total so far" in m.get("synonyms", [])

    def test_mixed_model_all_metric_types(self) -> None:
        """Model with measures, derived metrics, and cumulative metrics all convert."""
        converter = conv.OBMLtoOSI(_OBML_WITH_CUMULATIVES)
        osi = converter.convert()
        metric_names = [m["name"] for m in osi["semantic_model"][0]["metrics"]]

        # All four should be present: Revenue (measure), Running Revenue,
        # Rolling 7d Revenue, MTD Revenue, Derived Metric
        assert "Revenue" in metric_names  # measure → OSI metric
        assert "Running Revenue" in metric_names
        assert "Rolling 7d Revenue" in metric_names
        assert "MTD Revenue" in metric_names
        assert "Derived Metric" in metric_names
