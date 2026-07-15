"""Tests for OSI ↔ OBML roundtrip of data types, settings, owner, and column properties.

Validates that recently added OBML properties survive the OBML → OSI → OBML
roundtrip via custom_extensions preservation.
"""

from __future__ import annotations

from typing import Any

import ossie_orionbelt.converter as conv

_OBML_FULL: dict[str, Any] = {
    "version": 1.0,
    "description": "Test model with all properties",
    "owner": "analytics-team",
    "settings": {
        "defaultNumericDataType": "NUMERIC(18,4)",
        "defaultTimezone": "UTC",
        "overrideDatabaseTimezone": True,
        "defaultDialect": "snowflake",
    },
    "dataObjects": {
        "Orders": {
            "code": "ORDERS",
            "database": "WAREHOUSE",
            "schema": "PUBLIC",
            "owner": "data-eng",
            "comment": "Main orders fact table",
            "columns": {
                "Order ID": {
                    "code": "ORDER_ID",
                    "abstractType": "string",
                    "owner": "data-eng",
                    "comment": "Primary key",
                },
                "Amount": {
                    "code": "AMOUNT",
                    "abstractType": "float",
                    "sqlType": "NUMERIC",
                    "sqlPrecision": 18,
                    "sqlScale": 4,
                    "numClass": "decimal",
                },
                "Order Date": {
                    "code": "ORDER_DATE",
                    "abstractType": "date",
                },
            },
        },
    },
    "dimensions": {
        "Order Date": {
            "dataObject": "Orders",
            "column": "Order Date",
            "resultType": "date",
            "timeGrain": "month",
            "description": "Order date dimension",
            "owner": "analytics",
        },
    },
    "measures": {
        "Revenue": {
            "resultType": "float",
            "aggregation": "sum",
            "expression": "{[Orders].[Amount]}",
            "dataType": "NUMERIC(18,2)",
            "owner": "finance-team",
        },
    },
    "metrics": {
        "Revenue Growth": {
            "expression": "{[Revenue]} / 100",
            "dataType": "NUMERIC(10,4)",
            "owner": "finance-team",
            "format": "#,##0.00%",
        },
    },
}


def _roundtrip(obml: dict) -> dict:
    """OBML → OSI → OBML roundtrip."""
    fwd = conv.OBMLtoOSI(obml)
    osi = fwd.convert()
    back = conv.OSItoOBML(osi)
    return back.convert()


class TestModelSettings:
    def test_settings_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert "settings" in result
        settings = result["settings"]
        assert settings["defaultNumericDataType"] == "NUMERIC(18,4)"
        assert settings["defaultTimezone"] == "UTC"
        assert settings["overrideDatabaseTimezone"] is True
        assert settings["defaultDialect"] == "snowflake"

    def test_no_settings_no_key(self):
        obml = {**_OBML_FULL, "settings": None}
        del obml["settings"]
        result = _roundtrip(obml)
        assert "settings" not in result


class TestModelOwner:
    def test_model_owner_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result.get("owner") == "analytics-team"

    def test_no_owner_no_key(self):
        obml = {**_OBML_FULL}
        del obml["owner"]
        result = _roundtrip(obml)
        assert "owner" not in result


class TestMeasureDataType:
    def test_measure_data_type_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["measures"]["Revenue"].get("dataType") == "NUMERIC(18,2)"

    def test_measure_owner_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["measures"]["Revenue"].get("owner") == "finance-team"


class TestMetricDataType:
    def test_metric_data_type_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["metrics"]["Revenue Growth"].get("dataType") == "NUMERIC(10,4)"

    def test_metric_owner_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["metrics"]["Revenue Growth"].get("owner") == "finance-team"

    def test_metric_format_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["metrics"]["Revenue Growth"].get("format") == "#,##0.00%"


class TestRefreshPolicy:
    """OBML refresh: block round-trips through OSI custom_extensions."""

    def test_static_refresh_roundtrip(self):
        obml = {
            **_OBML_FULL,
            "dataObjects": {
                **_OBML_FULL["dataObjects"],
                "Orders": {
                    **_OBML_FULL["dataObjects"]["Orders"],
                    "refresh": {"mode": "static"},
                },
            },
        }
        result = _roundtrip(obml)
        assert result["dataObjects"]["Orders"].get("refresh") == {"mode": "static"}

    def test_interval_refresh_roundtrip(self):
        obml = {
            **_OBML_FULL,
            "dataObjects": {
                **_OBML_FULL["dataObjects"],
                "Orders": {
                    **_OBML_FULL["dataObjects"]["Orders"],
                    "refresh": {
                        "mode": "interval",
                        "interval": "1h",
                        "anchor": "00:00",
                        "timezone": "UTC",
                    },
                },
            },
        }
        result = _roundtrip(obml)
        rt = result["dataObjects"]["Orders"]["refresh"]
        assert rt["mode"] == "interval"
        assert rt["interval"] == "1h"
        assert rt["anchor"] == "00:00"
        assert rt["timezone"] == "UTC"

    def test_heartbeat_refresh_roundtrip(self):
        obml = {
            **_OBML_FULL,
            "dataObjects": {
                **_OBML_FULL["dataObjects"],
                "Orders": {
                    **_OBML_FULL["dataObjects"]["Orders"],
                    "refresh": {"mode": "heartbeat", "maxStaleness": "5m"},
                },
            },
        }
        result = _roundtrip(obml)
        rt = result["dataObjects"]["Orders"]["refresh"]
        assert rt["mode"] == "heartbeat"
        assert rt["maxStaleness"] == "5m"

    def test_no_refresh_no_key(self):
        result = _roundtrip(_OBML_FULL)
        assert "refresh" not in result["dataObjects"]["Orders"]


class TestColumnProperties:
    """Column properties roundtrip.

    Note: OSI uses column *code* as field name, so after roundtrip the OBML
    column keys become the physical codes (AMOUNT, ORDER_ID) rather than
    the original display names.
    """

    def test_sql_type_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        amount = result["dataObjects"]["Orders"]["columns"]["AMOUNT"]
        assert amount.get("sqlType") == "NUMERIC"

    def test_sql_precision_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        amount = result["dataObjects"]["Orders"]["columns"]["AMOUNT"]
        assert amount.get("sqlPrecision") == 18

    def test_sql_scale_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        amount = result["dataObjects"]["Orders"]["columns"]["AMOUNT"]
        assert amount.get("sqlScale") == 4

    def test_num_class_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        amount = result["dataObjects"]["Orders"]["columns"]["AMOUNT"]
        assert amount.get("numClass") == "decimal"

    def test_column_owner_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        order_id = result["dataObjects"]["Orders"]["columns"]["ORDER_ID"]
        assert order_id.get("owner") == "data-eng"

    def test_column_comment_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        order_id = result["dataObjects"]["Orders"]["columns"]["ORDER_ID"]
        assert order_id.get("comment") == "Primary key"


class TestDataObjectProperties:
    def test_data_object_owner_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["dataObjects"]["Orders"].get("owner") == "data-eng"

    def test_data_object_comment_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["dataObjects"]["Orders"].get("comment") == "Main orders fact table"


class TestDimensionProperties:
    """Dimension properties roundtrip.

    Note: OSI uses column *code* as the field name, so after roundtrip the
    dimension key becomes the physical code (ORDER_DATE).
    """

    def test_dimension_result_type_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["dimensions"]["ORDER_DATE"].get("resultType") == "date"

    def test_dimension_description_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["dimensions"]["ORDER_DATE"].get("description") == "Order date dimension"

    def test_dimension_owner_roundtrip(self):
        result = _roundtrip(_OBML_FULL)
        assert result["dimensions"]["ORDER_DATE"].get("owner") == "analytics"


class TestCumulativeMetricDataType:
    def test_cumulative_data_type_roundtrip(self):
        obml = {
            **_OBML_FULL,
            "metrics": {
                "Running Revenue": {
                    "type": "cumulative",
                    "measure": "Revenue",
                    "timeDimension": "Order Date",
                    "dataType": "NUMERIC(18,2)",
                    "owner": "finance-team",
                },
            },
        }
        result = _roundtrip(obml)
        m = result["metrics"]["Running Revenue"]
        assert m.get("dataType") == "NUMERIC(18,2)"
        assert m.get("owner") == "finance-team"


class TestPoPMetricDataType:
    def test_pop_data_type_roundtrip(self):
        obml = {
            **_OBML_FULL,
            "metrics": {
                "YoY Growth": {
                    "type": "period_over_period",
                    "expression": "{[Revenue]}",
                    "periodOverPeriod": {
                        "timeDimension": "Order Date",
                        "grain": "month",
                        "offsetGrain": "year",
                    },
                    "dataType": "NUMERIC(10,4)",
                    "owner": "finance-team",
                },
            },
        }
        result = _roundtrip(obml)
        m = result["metrics"]["YoY Growth"]
        assert m.get("dataType") == "NUMERIC(10,4)"
        assert m.get("owner") == "finance-team"


class TestCountSynthesisKnobs:
    """Count-synthesis knobs roundtrip via OBML-vendor custom_extensions.

    The synthesized ``<object>.count`` measures are derived (regenerated on
    load), so they are never emitted to OSI; only the knobs must survive.
    """

    _OBML_COUNTS = {
        "version": 1.0,
        "exposeCounts": False,
        "countLabelPattern": "# {object}",
        "dataObjects": {
            "Sales": {
                "code": "SALES",
                "database": "W",
                "schema": "P",
                "countable": True,
                "countLabel": "Sales headcount",
                "columns": {"Amount": {"code": "AMT", "abstractType": "float"}},
            },
            "Returns": {
                "code": "RET",
                "database": "W",
                "schema": "P",
                "countable": False,
                "columns": {"Qty": {"code": "Q", "abstractType": "int"}},
            },
        },
        "measures": {
            "Revenue": {
                "aggregation": "sum",
                "columns": [{"dataObject": "Sales", "column": "Amount"}],
            }
        },
    }

    def test_model_level_knobs_roundtrip(self):
        result = _roundtrip(self._OBML_COUNTS)
        assert result.get("exposeCounts") is False
        assert result.get("countLabelPattern") == "# {object}"

    def test_data_object_knobs_roundtrip(self):
        result = _roundtrip(self._OBML_COUNTS)
        sales = result["dataObjects"]["Sales"]
        returns = result["dataObjects"]["Returns"]
        assert sales.get("countable") is True
        assert sales.get("countLabel") == "Sales headcount"
        assert returns.get("countable") is False

    def test_no_synthesized_count_measures_emitted(self):
        osi = conv.OBMLtoOSI(self._OBML_COUNTS).convert()
        # No ``.count`` measure leaks into the OSI datasets/metrics.
        blob = str(osi)
        assert "Sales.count" not in blob
        result = _roundtrip(self._OBML_COUNTS)
        assert set(result.get("measures", {})) == {"Revenue"}

    def test_defaults_absent_when_unset(self):
        obml = {
            "version": 1.0,
            "dataObjects": {
                "Sales": {
                    "code": "SALES",
                    "database": "W",
                    "schema": "P",
                    "columns": {"Amount": {"code": "AMT", "abstractType": "float"}},
                }
            },
        }
        result = _roundtrip(obml)
        # Unset knobs are not re-materialized (pydantic defaults apply on load).
        assert "exposeCounts" not in result
        assert "countLabelPattern" not in result
        assert "countable" not in result["dataObjects"]["Sales"]
