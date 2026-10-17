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

import pytest

from ossie_sap_csn.cxn import UnsupportedExpression, cxn_to_sql, sql_to_cxn


def _bare(table, column):
    return [column] if table is None else [table, column]


@pytest.mark.parametrize(
    "cxn, sql",
    [
        ({"ref": ["a"]}, "a"),
        ({"val": "it's"}, "'it''s'"),
        ({"val": 3}, "3"),
        ({"val": None}, "NULL"),
        ({"val": True}, "TRUE"),
        ({"xpr": [{"ref": ["a"]}, "*", {"val": 2}]}, "a * 2"),
        ({"xpr": [{"xpr": [{"ref": ["a"]}, "+", {"ref": ["b"]}]}, "*", {"ref": ["c"]}]}, "(a + b) * c"),
        ({"xpr": [{"ref": ["a"]}, "is", "null"]}, "a IS NULL"),
        ({"xpr": ["not", {"ref": ["a"]}]}, "NOT a"),
        ({"xpr": [{"ref": ["a"]}, "in", {"list": [{"val": 1}, {"val": 2}]}]}, "a IN (1, 2)"),
        (
            {"xpr": ["case", "when", {"xpr": [{"ref": ["a"]}, ">", {"val": 0}]}, "then", {"val": "pos"}, "else", {"val": "neg"}, "end"]},
            "CASE WHEN (a > 0) THEN 'pos' ELSE 'neg' END",
        ),
        ({"func": "upper", "args": [{"ref": ["a"]}]}, "UPPER(a)"),
        ({"func": "count", "args": ["*"]}, "COUNT(*)"),
        ({"func": "count", "args": [{"xpr": ["distinct", {"ref": ["a"]}]}]}, "COUNT(DISTINCT a)"),
        ({"ref": ["a"], "cast": {"type": "cds.Integer"}}, "CAST(a AS INT)"),
        ({"ref": ["assoc", "name"]}, "Target.name"),
    ],
)
def test_cxn_to_sql(cxn, sql):
    assert cxn_to_sql(cxn, lambda path: f"Target.{path[1]}") == sql


@pytest.mark.parametrize(
    "sql",
    [
        "a",
        "a * 2",
        "(a + b) * c",
        "a IS NULL",
        "NOT a",
        "a IN (1, 2)",
        "a BETWEEN 1 AND 5",
        "a LIKE 'x%'",
        "CASE WHEN a > 0 THEN 'pos' ELSE 'neg' END",
        "UPPER(a)",
        "COALESCE(a, b, 0)",
        "COUNT(DISTINCT a)",
        "CAST(a AS INT)",
        "a || 'x'",
    ],
)
def test_sql_survives_a_round_trip_through_cxn(sql):
    cxn = sql_to_cxn(sql, _bare)
    again = cxn_to_sql(cxn)
    assert sql_to_cxn(again, _bare) == cxn


@pytest.mark.parametrize(
    "sql",
    [
        "SUM(a) OVER (PARTITION BY b)",
        "(SELECT 1)",
        "CAST(a AS GEOGRAPHY)",
        "a IS NOT DISTINCT FROM b",
    ],
)
def test_untranslatable_sql_is_refused_not_guessed(sql):
    with pytest.raises(UnsupportedExpression):
        sql_to_cxn(sql, _bare)


@pytest.mark.parametrize(
    "cxn",
    [
        {"ref": ["$session", "user"]},
        {"ref": [{"id": "a", "where": []}]},
        {"func": "f", "args": {"x": {"ref": ["a"]}}},
        {"SELECT": {}},
        {"ref": ["a", "b", "c"]},
    ],
)
def test_untranslatable_cxn_is_refused_not_guessed(cxn):
    with pytest.raises(UnsupportedExpression):
        cxn_to_sql(cxn)


@pytest.mark.parametrize("name, rendered", [("from", '"from"'), ("plus", "plus"), ("my col", '"my col"'), ('a"b', '"a""b"')])
def test_identifiers_that_are_not_plain_words_are_quoted(name, rendered):
    assert cxn_to_sql({"ref": [name]}) == rendered


def test_quoted_identifier_round_trips_through_sql():
    sql = cxn_to_sql({"xpr": [{"ref": ["select"]}, "+", {"val": 1}]})
    assert sql == '"select" + 1'
    assert sql_to_cxn(sql, _bare) == {"xpr": [{"ref": ["select"]}, "+", {"val": 1}]}


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_literals_are_refused(value):
    with pytest.raises(UnsupportedExpression):
        cxn_to_sql({"val": value})


def test_parse_error_text_carries_no_ansi_escapes():
    with pytest.raises(UnsupportedExpression) as info:
        sql_to_cxn("this is not sql !!!", _bare)
    assert "\x1b" not in str(info.value)
