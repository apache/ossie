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

"""Exported metrics also carry ``OSSIE_SQL_2026`` where it has a form.

Each Ossie metric keeps its ``ANSI_SQL`` expression and gains one in Apache
Ossie's own expression language, rendered in that language's forms and checked
node by node against the subset ``core-spec/expression_language.md`` defines.
A metric with no such form keeps ``ANSI_SQL`` only.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

import ossie_orionbelt.converter as conv
from ossie_orionbelt._ossie_sql import _FUNCTIONS, ossie_sql, subset_violations


def _filter(operator: str, value: Any, data_type: str = "string") -> dict[str, Any]:
    key = {"string": "valueString", "timestamp": "valueDate"}[data_type]
    return {
        "column": {"dataObject": "Orders", "column": "Status"},
        "operator": operator,
        "values": [{"dataType": data_type, key: value}],
    }


def _measure(aggregation: str = "sum", **extra: Any) -> dict[str, Any]:
    return {
        "columns": [{"dataObject": "Orders", "column": "Amount"}],
        "resultType": "float",
        "aggregation": aggregation,
        **extra,
    }


_OBML: dict[str, Any] = {
    "version": 1.0,
    "dataObjects": {
        "Orders": {
            "code": "orders",
            "columns": {
                "Amount": {"code": "amount", "abstractType": "float"},
                "Status": {"code": "status", "abstractType": "string"},
            },
        }
    },
    "measures": {
        "Revenue": _measure(),
        "Discounted": _measure(filters=[_filter("contains", "50%")]),
        "Prefixed": _measure(filters=[_filter("starts_with", "web_")]),
        "Not Void": _measure(filters=[_filter("notcontains", "void")]),
        "Pattern": _measure(filters=[_filter("like", "VOID%")]),
        "Escaped": _measure(filters=[_filter("like", "VOID\\_%")]),
        "Since": _measure(filters=[_filter("gte", "2024-01-01 00:00:00", "timestamp")]),
        "Typical": _measure("mode"),
        "Hashed": {
            "expression": "MD5({[Orders].[Status]})",
            "resultType": "int",
            "aggregation": "count",
        },
    },
    "metrics": {"Share": {"expression": "{[Discounted]} / {[Revenue]} * 100"}},
}


def _dialects() -> dict[str, dict[str, str]]:
    ossie = conv.OBMLtoOssie(copy.deepcopy(_OBML), "sales").convert()
    return {
        m["name"]: {d["dialect"]: d["expression"] for d in m["expression"]["dialects"]}
        for m in ossie["metrics"]
    }


class TestBothDialects:
    def test_plain_measure_carries_both(self) -> None:
        revenue = _dialects()["Revenue"]
        assert list(revenue) == ["ANSI_SQL", "OSSIE_SQL_2026"]
        assert revenue["OSSIE_SQL_2026"] == revenue["ANSI_SQL"] == "SUM(Orders.Amount)"

    def test_substring_filters_use_the_language_functions(self) -> None:
        d = _dialects()
        assert "ESCAPE" in d["Discounted"]["ANSI_SQL"]
        assert d["Discounted"]["OSSIE_SQL_2026"] == (
            "SUM(CASE WHEN CONTAINS(Orders.Status, '50%') THEN Orders.Amount END)"
        )
        assert "STARTSWITH(Orders.Status, 'web_')" in d["Prefixed"]["OSSIE_SQL_2026"]
        assert "NOT CONTAINS(Orders.Status, 'void')" in d["Not Void"]["OSSIE_SQL_2026"]

    def test_like_is_kept(self) -> None:
        d = _dialects()["Pattern"]
        assert d["ANSI_SQL"] == d["OSSIE_SQL_2026"]
        assert "Orders.Status LIKE 'VOID%'" in d["OSSIE_SQL_2026"]

    def test_timestamp_literal_is_a_cast(self) -> None:
        d = _dialects()["Since"]
        assert "TIMESTAMP '2024-01-01 00:00:00'" in d["ANSI_SQL"]
        assert "CAST('2024-01-01 00:00:00' AS TIMESTAMP)" in d["OSSIE_SQL_2026"]

    def test_metric_over_measures_carries_both(self) -> None:
        share = _dialects()["Share"]
        assert share["OSSIE_SQL_2026"] == (
            "SUM(CASE WHEN CONTAINS(Orders.Status, '50%') THEN Orders.Amount END) "
            "/ SUM(Orders.Amount) * 100"
        )

    def test_every_ossie_expression_is_inside_the_subset(self) -> None:
        for name, dialects in _dialects().items():
            if "OSSIE_SQL_2026" in dialects:
                assert subset_violations(dialects["OSSIE_SQL_2026"]) == [], name


class TestAnsiOnly:
    @pytest.mark.parametrize(
        "name",
        [
            "Escaped",  # needs ESCAPE, which the language does not have
            "Typical",  # MODE is not in the language
            "Hashed",  # an authored expression outside the subset
        ],
    )
    def test_no_ossie_form_keeps_ansi_only(self, name: str) -> None:
        assert list(_dialects()[name]) == ["ANSI_SQL"]

    def test_untokenizable_expression_keeps_ansi_only(self) -> None:
        # An unterminated string does not tokenize; the export must not fail.
        obml = copy.deepcopy(_OBML)
        obml["measures"] = {
            "Broken": {
                "expression": "{[Orders].[Status]} || 'x",
                "resultType": "int",
                "aggregation": "count",
            }
        }
        obml["metrics"] = {}
        ossie = conv.OBMLtoOssie(obml, "sales").convert()
        assert [d["dialect"] for d in ossie["metrics"][0]["expression"]["dialects"]] == ["ANSI_SQL"]


def _hello_world(metric_sql: str) -> dict[str, Any]:
    """The hello-world model of apache/ossie#576, tagged OSSIE_SQL_2026."""

    def expr(sql: str) -> dict[str, Any]:
        return {"dialects": [{"dialect": "OSSIE_SQL_2026", "expression": sql}]}

    return {
        "version": "0.2.0.dev0",
        "name": "hello_world",
        "datasets": [
            {
                "name": "sales",
                "source": "hello.public.sales",
                "fields": [
                    {"name": "amount", "datatype": "Decimal", "expression": expr("amount")},
                    {"name": "status", "datatype": "String", "expression": expr("status")},
                ],
            }
        ],
        "metrics": [
            {"name": "total_amount", "datatype": "Decimal", "expression": expr(metric_sql)}
        ],
    }


class TestRoundTrip:
    def test_import_reads_the_ansi_form(self) -> None:
        ossie = conv.OBMLtoOssie(copy.deepcopy(_OBML), "sales").convert()
        back = conv.OssietoOBML(ossie).convert()
        assert (
            back["measures"]["Discounted"]["filters"] == _OBML["measures"]["Discounted"]["filters"]
        )

    def test_ossie_first_scalar_function_keeps_its_tag(self) -> None:
        # ROUND is REQUIRED by the spec; dropping the tag would retag it again.
        doc = _hello_world("ROUND(SUM(sales.amount), 2)")
        back = conv.OBMLtoOssie(conv.OssietoOBML(doc).convert()).convert()
        dialects = {
            d["dialect"]: d["expression"]
            for d in next(m for m in back["metrics"] if m["name"] == "total_amount")["expression"][
                "dialects"
            ]
        }
        assert subset_violations(dialects["OSSIE_SQL_2026"]) == []
        assert "ROUND(" in dialects["OSSIE_SQL_2026"]

    @pytest.mark.parametrize(
        ("written", "exported"),
        [
            ("SUM(CHARINDEX('a', sales.amount))", "SUM(CHARINDEX('a', sales.amount))"),
            ("SUM(POSITION('a' IN sales.amount))", "SUM(POSITION('a' IN sales.amount))"),
            ("VAR_POP(sales.amount)", "VAR_POP(sales.amount)"),
            ("SUM(CAST(sales.amount AS STRING))", "SUM(CAST(sales.amount AS STRING))"),
            ("SUM(CASE WHEN STARTSWITH(sales.status, 'w') THEN sales.amount END)", None),
            ("SUM(CASE WHEN ENDSWITH(sales.status, 'w') THEN sales.amount END)", None),
        ],
    )
    def test_imported_functions_keep_the_tag(self, written: str, exported: str | None) -> None:
        back = conv.OBMLtoOssie(conv.OssietoOBML(_hello_world(written)).convert()).convert()
        metric = next(m for m in back["metrics"] if m["name"] == "total_amount")
        dialects = {d["dialect"]: d["expression"] for d in metric["expression"]["dialects"]}
        assert dialects["OSSIE_SQL_2026"] == (exported or written)

    @pytest.mark.parametrize(
        ("sql", "respelled"),
        [
            ("CAST(t.x AS TIMESTAMPNTZ(3))", "CAST(t.x AS TIMESTAMP_NTZ(3))"),
            ("CAST(t.x AS TEXT(2))", "CAST(t.x AS STRING(2))"),
            ("CAST(t.x AS TEXT)", "CAST(t.x AS STRING)"),
        ],
    )
    def test_respelled_types_keep_their_parameters(self, sql: str, respelled: str) -> None:
        assert ossie_sql(sql) == respelled

    def test_ossie_first_metric_keeps_its_tag(self) -> None:
        doc = _hello_world("SUM(sales.amount)")
        back = conv.OBMLtoOssie(conv.OssietoOBML(doc).convert()).convert()
        dialects = {
            d["dialect"]: d["expression"] for d in back["metrics"][0]["expression"]["dialects"]
        }
        assert dialects["OSSIE_SQL_2026"] == "SUM(sales.amount)"


class TestSubsetChecker:
    @pytest.mark.parametrize(
        "sql",
        [
            "SUM(t.x)",
            "COUNT(*)",
            "COUNT(DISTINCT t.x)",
            "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY t.x)",
            "COALESCE(SUM(t.x), 0)",
            "CAST(DATE_TRUNC('month', t.d) AS DATE)",
            "LAG(SUM(t.x), 1, 0) OVER (ORDER BY t.d ASC)",
            "SUM(SUM(t.x)) OVER (PARTITION BY t.r ORDER BY t.d"
            " ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)",
            "SUM(CASE WHEN t.a IN ('x', 'y') AND NOT (t.b IS NULL) THEN t.c END)",
        ],
    )
    def test_allowed(self, sql: str) -> None:
        assert subset_violations(sql) == []

    @pytest.mark.parametrize("name", sorted(_FUNCTIONS))
    def test_every_spec_function_sample_is_allowed(self, name: str) -> None:
        # The samples define both the names and the node types; each must pass.
        assert subset_violations(_FUNCTIONS[name]) == []

    @pytest.mark.parametrize(
        "sql",
        [
            "ROUND(SUM(t.x), 2) + ABS(t.y) + CEIL(t.z) + TRUNCATE(t.x, 1) + LOG10(t.x)",
            "DATEADD(day, 7, t.d) + DATEDIFF(month, t.a, t.b) + EXTRACT(YEAR FROM t.d)",
            "IFF(t.c, 1, 0) + ZEROIFNULL(t.x) + NVL2(t.a, 1, 2) + CHARINDEX('a', t.s)",
            "CAST(t.x AS STRING) + TRY_CAST(t.y AS DECIMAL(10, 2)) + TIMESTAMP_NTZ '2024-01-01'",
            "t.a || t.b",
            "t.a IS NOT DISTINCT FROM t.b",
            "CASE WHEN t.a LIKE 'x%' THEN 'y' ELSE 'z' END",
        ],
    )
    def test_required_scalar_functions_and_syntax(self, sql: str) -> None:
        assert subset_violations(sql) == []

    @pytest.mark.parametrize(
        "sql",
        [
            # A string or a quoted name is not a keyword.
            "SUM(CASE WHEN t.status = 'AS' THEN t.amount END)",
            "SUM(CASE WHEN t.status = 'ESCAPE' THEN t.amount END)",
            'SUM(t."AS") + SUM(t."Escape") + SUM(t."Date")',
            # A parenthesized argument is still one argument.
            "TO_DATE((MAX(t.s)))",
            # A leading TRUNCATE is a function, not a statement.
            "TRUNCATE(t.x, 2) + CEILING(t.x)",
        ],
    )
    def test_no_false_violations(self, sql: str) -> None:
        assert subset_violations(sql) == []

    def test_a_clause_after_the_expression_is_refused(self) -> None:
        assert subset_violations("SUM(t.x) FROM t") == ["the expression does not parse"]

    @pytest.mark.parametrize("name", sorted(_FUNCTIONS))
    def test_written_back_samples_stay_in_the_language(self, name: str) -> None:
        # The import keeps sqlglot's spelling (VAR_POP as VARIANCE_POP); the
        # export respells it through ossie_sql, or an imported metric loses its
        # OSSIE_SQL_2026 form.
        import sqlglot

        written = sqlglot.parse_one(f"SELECT {_FUNCTIONS[name]}").expressions[0].sql()
        assert subset_violations(ossie_sql(written)) == []

    @pytest.mark.parametrize(
        ("sql", "reason"),
        [
            ("MODE(t.x)", "Mode"),
            ("LISTAGG(t.x, ',')", "LISTAGG"),
            ("ANY_VALUE(t.x)", "AnyValue"),
            ("t.a LIKE 'x' ESCAPE '\\'", "Escape"),
            ("AVG(DISTINCT t.x)", "DISTINCT"),
            ("SUM(t.x) + (SELECT 1)", "Subquery"),
            ("CAST(t.x AS INTERVAL)", "INTERVAL"),
            # Written forms sqlglot would normalize into allowed nodes.
            ("SUM(SAFE_CAST(t.x AS INT))", "SAFE_CAST"),
            ("CAST(t.x AS INT64)", "INT64"),
            ("STR_POSITION(t.s, 'a')", "STR_POSITION"),
            # Constructs the spec does not list.
            ("TIMESTAMP '2024-01-01'", "TIMESTAMP"),
            ("INTERVAL '1' DAY", "INTERVAL"),
            ("t.a::int", "::"),
            ("t.a[1]", "["),
            ("CURRENT_USER", "CurrentUser"),
            ("TO_DATE(t.s, 'YYYY')", "EXPERIMENTAL"),
            ("EXTRACT(DECADE FROM t.d)", "EXTRACT"),
            ("DATE_TRUNC('decade', t.d)", "DATE_TRUNC"),
            ("SUM(", "does not parse"),
        ],
    )
    def test_rejected(self, sql: str, reason: str) -> None:
        assert any(reason in v for v in subset_violations(sql))
