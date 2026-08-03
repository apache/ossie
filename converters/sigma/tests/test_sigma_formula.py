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

from ossie_sigma.sigma_formula import (
    ColumnRef,
    FormulaParseError,
    is_plain_column_ref,
    parse_formula,
    sql_to_sigma_formula,
    to_ansi_sql,
)


@pytest.mark.parametrize(
    ("formula", "dataset_alias", "expected_sql"),
    [
        ("[Amount]", "Orders", '"Amount"'),
        ("[Orders/Amount]", "Orders", '"Amount"'),
        ("[Orders/Amount]", None, '"Orders"."Amount"'),
        ("Sum([Amount])", "Orders", 'SUM("Amount")'),
        ("CountDistinct([Order Id])", "Orders", 'COUNT(DISTINCT "Order Id")'),
        ('If([Status] = "closed", 1, 0)', "Orders", "CASE WHEN (\"Status\" = 'closed') THEN 1 ELSE 0 END"),
        ("IfNull([X], 0)", "Orders", 'COALESCE("X", 0)'),
        ("IsNull([X])", "Orders", '("X" IS NULL)'),
        ("IsNotNull([X])", "Orders", '("X" IS NOT NULL)'),
        ('[A] & " " & [B]', "T", "((\"A\" || ' ') || \"B\")"),
        ("Left([Name], 3)", "T", 'SUBSTRING("Name" FROM 1 FOR 3)'),
        ("Mid([Name], 2, 3)", "T", 'SUBSTRING("Name" FROM 2 FOR 3)'),
        ("Year([Created At])", "T", 'EXTRACT(YEAR FROM "Created At")'),
        ("Upper(Trim([Name]))", "T", 'UPPER(TRIM("Name"))'),
        ("[Qty] * [Price] + 1", "T", '(("Qty" * "Price") + 1)'),
        ("Median([Amount])", "T", 'PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY "Amount")'),
        ('SumIf([Status] = "won", [Amount])', "T", "SUM(CASE WHEN (\"Status\" = 'won') THEN \"Amount\" ELSE 0 END)"),
        ("2 ^ 3", "T", "POWER(2, 3)"),
        ("-[X]", "T", '-("X")'),
        ("NOT [X]", "T", 'NOT ("X")'),
    ],
)
def test_translatable_formulas(formula, dataset_alias, expected_sql):
    node = parse_formula(formula)
    assert to_ansi_sql(node, dataset_alias=dataset_alias) == expected_sql


@pytest.mark.parametrize(
    "formula",
    [
        "RunningSum([Amount])",
        "Rank([Amount])",
        "SomeUnknownFunction([X])",
    ],
)
def test_untranslatable_functions_return_none(formula):
    node = parse_formula(formula)
    assert to_ansi_sql(node) is None


@pytest.mark.parametrize(
    "formula",
    [
        "",
        "[Unterminated",
        "Sum([X]",
        "@#$%",
    ],
)
def test_unparseable_formulas_raise(formula):
    with pytest.raises(FormulaParseError):
        parse_formula(formula)


def test_is_plain_column_ref():
    assert is_plain_column_ref("[Orders/Amount]") == ColumnRef("Orders", "Amount")
    assert is_plain_column_ref("[Amount]") == ColumnRef(None, "Amount")
    assert is_plain_column_ref("Sum([Amount])") is None
    assert is_plain_column_ref("not a formula @@@") is None


@pytest.mark.parametrize(
    ("sql", "dataset_alias", "expected"),
    [
        ('"Amount"', "Orders", "[Amount]"),
        ('"Orders"."Amount"', None, "[Orders/Amount]"),
        ("SUM(ss_ext_sales_price)", "store_sales", "Sum([ss_ext_sales_price])"),
        ("COUNT(DISTINCT customer_id)", "customer", "CountDistinct([customer_id])"),
        ("CASE WHEN status = 'won' THEN 1 ELSE 0 END", "deals", 'If((["status"] = "won"), 1, 0)'.replace('["status"]', "[status]")),
    ],
)
def test_reverse_translation_basic(sql, dataset_alias, expected):
    result = sql_to_sigma_formula(sql, dataset_alias=dataset_alias)
    assert result == expected


def test_reverse_translation_gives_up_on_count_star():
    assert sql_to_sigma_formula("COUNT(*)") is None


def test_reverse_translation_gives_up_on_unparseable():
    assert sql_to_sigma_formula("not valid sql {{{") is None
