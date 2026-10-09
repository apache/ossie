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

"""Exported metric expressions must compute what OrionBelt computes.

Other Ossie consumers read only the expression, not our extension, so:

* every column reference resolves to ``<dataset>.<field>`` of the document;
* measure filters, totals, synthesized counts and metric-on-metric references
  are spelled out in the SQL;
* what has no faithful expression is left out, warned about, and restored by
  the reverse conversion.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

import ossie_orionbelt.converter as conv
from ossie_orionbelt._portable import sql_ident

_MODEL: dict[str, Any] = {
    "version": 1.0,
    "dataObjects": {
        "Order Lines": {
            "code": "FCT_ORDER_LINES",
            "database": "DW",
            "schema": "SALES",
            "columns": {
                "Line ID": {"code": "LINE_ID", "abstractType": "string", "primaryKey": True},
                "Amount": {"code": "AMT", "abstractType": "float"},
                "Status": {"code": "STATUS", "abstractType": "string"},
                "Line Date": {"code": "LINE_DT", "abstractType": "date"},
                "Customer": {"code": "CUST_ID", "abstractType": "string"},
            },
            "joins": [
                {
                    "joinType": "many-to-one",
                    "joinTo": "Customers",
                    "columnsFrom": ["Customer"],
                    "columnsTo": ["Customer ID"],
                }
            ],
        },
        "Customers": {
            "code": "DIM_CUSTOMERS",
            "database": "DW",
            "schema": "SALES",
            "columns": {
                "Customer ID": {"code": "ID", "abstractType": "string", "primaryKey": True},
                "Country": {"code": "COUNTRY", "abstractType": "string"},
            },
        },
    },
    "dimensions": {
        "Line Date": {
            "dataObject": "Order Lines",
            "column": "Line Date",
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
        "Sales": {
            "columns": [{"dataObject": "Order Lines", "column": "Amount"}],
            "resultType": "float",
            "aggregation": "sum",
        },
        "US Sales": {
            "columns": [{"dataObject": "Order Lines", "column": "Amount"}],
            "resultType": "float",
            "aggregation": "sum",
            "filters": [
                {
                    "column": {"dataObject": "Customers", "column": "Country"},
                    "operator": "equals",
                    "values": [{"dataType": "string", "valueString": "US"}],
                },
                {
                    "logic": "or",
                    "negated": True,
                    "filters": [
                        {
                            "column": {"dataObject": "Order Lines", "column": "Status"},
                            "operator": "inlist",
                            "values": [
                                {"dataType": "string", "valueString": "void"},
                                {"dataType": "string", "valueString": "it's test"},
                            ],
                        },
                        {
                            "column": {"dataObject": "Order Lines", "column": "Status"},
                            "operator": "contains",
                            "values": [{"dataType": "string", "valueString": "50%"}],
                        },
                    ],
                },
            ],
        },
        "Total Sales": {
            "columns": [{"dataObject": "Order Lines", "column": "Amount"}],
            "resultType": "float",
            "aggregation": "sum",
            "total": True,
        },
        "Average Line": {
            "columns": [{"dataObject": "Order Lines", "column": "Amount"}],
            "resultType": "float",
            "aggregation": "avg",
            "total": True,
            "defaultValue": 0,
        },
        "Customers Reached": {
            "columns": [{"dataObject": "Order Lines", "column": "Customer"}],
            "resultType": "int",
            "aggregation": "count_distinct",
        },
        "Sales At Fixed Grain": {
            "columns": [{"dataObject": "Order Lines", "column": "Amount"}],
            "resultType": "float",
            "aggregation": "sum",
            "grain": {"mode": "FIXED", "include": ["Customer Country"]},
        },
    },
    "metrics": {
        "Sales per Line": {"expression": "{[Sales]} / {[Order Lines Count]}"},
        "US Share": {"expression": "{[US Sales]} / {[Total Sales]}"},
        "US Share Pct": {"expression": "{[US Share]} * 100"},
        "Running Sales": {
            "type": "cumulative",
            "measure": "Sales",
            "timeDimension": "Line Date",
            "partitionBy": ["Customer Country"],
        },
        "Sales Rank": {
            "type": "window",
            "windowFunction": "rank",
            "measure": "Sales",
            "partitionBy": ["Customer Country"],
        },
        "Sales MoM": {
            "type": "period_over_period",
            "expression": "{[Sales]}",
            "periodOverPeriod": {
                "timeDimension": "Line Date",
                "grain": "month",
                "offsetGrain": "month",
                "comparison": "difference",
            },
        },
        "Fixed Grain Share": {"expression": "{[Sales]} / {[Sales At Fixed Grain]}"},
    },
}


def _export(obml: dict[str, Any] = _MODEL) -> tuple[dict[str, Any], list[str]]:
    converter = conv.OBMLtoOssie(copy.deepcopy(obml))
    return converter.convert(), converter.warnings


def _sql(ossie: dict[str, Any]) -> dict[str, str]:
    return {
        m["name"]: m["expression"]["dialects"][0]["expression"] for m in ossie.get("metrics", [])
    }


class TestReferencesResolve:
    def test_every_column_reference_is_a_dataset_field(self) -> None:
        sqlglot = pytest.importorskip("sqlglot")
        ossie, _ = _export()
        fields = {ds["name"]: {f["name"] for f in ds["fields"]} for ds in ossie["datasets"]}
        for name, sql in _sql(ossie).items():
            assert "{[" not in sql, name
            for column in sqlglot.parse_one(sql).find_all(sqlglot.exp.Column):
                assert column.table in fields, (name, column.sql())
                assert column.name in fields[column.table], (name, column.sql())

    def test_dataset_name_with_space_is_quoted(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["Sales"] == f"SUM({AMT})"

    def test_sql_ident_always_quotes(self) -> None:
        assert sql_ident("Orders") == '"Orders"'
        assert sql_ident("Order Lines") == '"Order Lines"'
        assert sql_ident('a"b') == '"a""b"'

    def test_reserved_word_data_object_parses(self) -> None:
        obml = copy.deepcopy(_MODEL)
        obml["dataObjects"]["Order"] = obml["dataObjects"].pop("Order Lines")
        obml["measures"] = {
            "Sales": {
                "columns": [{"dataObject": "Order", "column": "Amount"}],
                "resultType": "float",
                "aggregation": "sum",
            }
        }
        obml["dimensions"] = {}
        obml["metrics"] = {}
        ossie, _ = _export(obml)
        assert _sql(ossie)["Sales"] == 'SUM("Order"."AMT")'
        sqlglot = pytest.importorskip("sqlglot")
        sqlglot.parse_one(_sql(ossie)["Sales"])


# Column references as the export writes them
LINES = '"Order Lines"'
AMT = f'{LINES}."AMT"'
STATUS = f'{LINES}."STATUS"'
COUNTRY = '"Customers"."COUNTRY"'
# "Line Date" declares resultType: date, so the truncation is cast back to DATE.
MONTH = f"CAST(DATE_TRUNC('month', {LINES}.\"LINE_DT\") AS DATE)"


class TestSemanticsSpelledOut:
    def test_measure_filters_become_case_when(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["US Sales"] == (
            f"SUM(CASE WHEN {COUNTRY} = 'US' AND NOT ("
            f"({STATUS} IN ('void', 'it''s test')) OR "
            f"({STATUS} LIKE '%50\\%%' ESCAPE '\\')"
            f") THEN {AMT} END)"
        )

    def test_total_becomes_grand_total_window(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["Total Sales"] == f"SUM(SUM({AMT})) OVER ()"

    def test_avg_total_is_exact_and_default_applies(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["Average Line"] == (
            f"COALESCE((SUM(SUM({AMT})) OVER () / SUM(COUNT({AMT})) OVER ()), 0)"
        )

    def test_count_distinct(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["Customers Reached"] == f'COUNT(DISTINCT {LINES}."CUST_ID")'

    def test_synthesized_count_is_inlined(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["Sales per Line"] == f'SUM({AMT}) / COUNT({LINES}."LINE_ID")'

    def test_metric_on_metric_is_inlined(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["US Share Pct"].startswith("(SUM(CASE WHEN")
        assert _sql(ossie)["US Share Pct"].endswith(") * 100")

    def test_cumulative_orders_by_the_truncated_time_dimension(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["Running Sales"] == (
            f"SUM(SUM({AMT})) OVER (PARTITION BY {COUNTRY} "
            f"ORDER BY {MONTH} ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
        )

    def test_window_metric(self) -> None:
        ossie, _ = _export()
        assert _sql(ossie)["Sales Rank"] == (
            f"RANK() OVER (PARTITION BY {COUNTRY} ORDER BY SUM({AMT}) DESC)"
        )

    def test_databricks_delegation_is_tagged_databricks(self) -> None:
        obml = copy.deepcopy(_MODEL)
        obml["measures"] = {"Delegated": {"aggregation": "measure"}}
        obml["metrics"] = {}
        ossie, _ = _export(obml)
        dialect = ossie["metrics"][0]["expression"]["dialects"][0]
        assert dialect == {"dialect": "DATABRICKS", "expression": 'MEASURE("Delegated")'}


class TestExportedSqlRuns:
    """Every exported metric runs in the query OrionBelt would group it by.

    The datasets are aliased to their Ossie names, and the query groups by the
    time dimension at its grain and the partition dimension, as OrionBelt does.
    """

    @staticmethod
    def _connection() -> Any:
        duckdb = pytest.importorskip("duckdb")
        con = duckdb.connect()
        con.execute(
            "CREATE TABLE fct (LINE_ID VARCHAR, AMT DOUBLE, STATUS VARCHAR, "
            "LINE_DT DATE, CUST_ID VARCHAR)"
        )
        con.execute("CREATE TABLE cust (ID VARCHAR, COUNTRY VARCHAR)")
        con.execute(
            "INSERT INTO fct VALUES ('1', 10, 'ok', DATE '2024-01-05', 'a'), "
            "('2', 20, 'void', DATE '2024-02-05', 'b'), ('3', 5, '50% off', DATE '2024-02-06', 'a')"
        )
        con.execute("INSERT INTO cust VALUES ('a', 'US'), ('b', 'DE')")
        return con

    def test_every_exported_metric_executes(self) -> None:
        con = self._connection()
        ossie, _ = _export()
        for name, sql in _sql(ossie).items():
            (
                con.execute(
                    f"SELECT {MONTH}, {COUNTRY}, {sql} "
                    f'FROM fct AS {LINES} LEFT JOIN cust AS "Customers" '
                    f'ON {LINES}."CUST_ID" = "Customers"."ID" '
                    f"GROUP BY {MONTH}, {COUNTRY}"
                ).fetchall(),
                name,
            )

    def test_window_over_a_window_is_left_out(self) -> None:
        obml = copy.deepcopy(_MODEL)
        obml["metrics"] = {
            "Running Total": {
                "type": "cumulative",
                "measure": "Total Sales",
                "timeDimension": "Line Date",
            },
            "Rank of Running": {
                "type": "window",
                "windowFunction": "rank",
                "measure": "Running Sales",
            },
            "Running Sales": copy.deepcopy(_MODEL["metrics"]["Running Sales"]),
        }
        ossie, warnings = _export(obml)
        assert set(_sql(ossie)) & {"Running Total", "Rank of Running"} == set()
        assert "Running Sales" in _sql(ossie)
        for name in ("Running Total", "Rank of Running"):
            assert any(name in w and "nested window" in w for w in warnings)
        back = conv.OssietoOBML(ossie).convert()
        assert back["metrics"] == obml["metrics"]


class TestReferenceCycles:
    def test_indirect_cycle_is_left_out_not_a_crash(self) -> None:
        obml = copy.deepcopy(_MODEL)
        obml["metrics"] = {
            "A": {"expression": "{[B]} + 1"},
            "B": {"expression": "{[A]} * 2"},
            "Running A": {"type": "cumulative", "measure": "A", "timeDimension": "Line Date"},
        }
        ossie, warnings = _export(obml)
        assert set(_sql(ossie)) & {"A", "B", "Running A"} == set()
        for name in ("A", "B", "Running A"):
            assert any(f"'{name}'" in w and "not exported" in w for w in warnings), name
        assert conv.OssietoOBML(ossie).convert()["metrics"] == obml["metrics"]


class TestNotPortableIsLeftOut:
    @pytest.mark.parametrize(
        ("name", "reason"),
        [
            ("Sales MoM", "date spine"),
            ("Sales At Fixed Grain", "grain"),
            ("Fixed Grain Share", "grain"),
        ],
    )
    def test_left_out_and_warned(self, name: str, reason: str) -> None:
        ossie, warnings = _export()
        assert name not in _sql(ossie)
        assert any(name in w and reason in w for w in warnings)

    def test_unknown_measure_reference_is_left_out(self) -> None:
        obml = copy.deepcopy(_MODEL)
        obml["metrics"] = {"Broken": {"expression": "{[Nope]} * 2"}}
        ossie, warnings = _export(obml)
        assert "Broken" not in _sql(ossie)
        assert any("Broken" in w and "Nope" in w for w in warnings)

    def test_count_without_single_primary_key_is_left_out(self) -> None:
        obml = copy.deepcopy(_MODEL)
        del obml["dataObjects"]["Order Lines"]["columns"]["Line ID"]["primaryKey"]
        ossie, warnings = _export(obml)
        assert "Sales per Line" not in _sql(ossie)
        assert any("Sales per Line" in w and "primary key" in w for w in warnings)


class TestRoundTrip:
    def test_measures_metrics_and_column_names_survive(self) -> None:
        ossie, _ = _export()
        back = conv.OssietoOBML(ossie).convert()
        assert back["measures"] == _MODEL["measures"]
        assert back["metrics"] == _MODEL["metrics"]
        for do_name, do_obj in _MODEL["dataObjects"].items():
            assert list(back["dataObjects"][do_name]["columns"]) == list(do_obj["columns"])
        assert back["dataObjects"]["Order Lines"]["joins"][0]["columnsFrom"] == ["Customer"]
        assert back["dataObjects"]["Order Lines"]["joins"][0]["columnsTo"] == ["Customer ID"]
        # The import names a dimension after its field; the column is the OBML one.
        assert back["dimensions"]["COUNTRY"]["column"] == "Country"

    def test_left_out_entities_ride_in_the_model_extension(self) -> None:
        ossie, _ = _export()
        ext = next(e for e in ossie["custom_extensions"] if e["vendor_name"] == "ORIONBELT")
        kept = json.loads(ext["data"])["obml_unexported"]
        assert set(kept["metrics"]) == {"Sales MoM", "Fixed Grain Share"}
        assert set(kept["measures"]) == {"Sales At Fixed Grain"}
