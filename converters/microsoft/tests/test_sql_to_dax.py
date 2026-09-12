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

"""Tests for the curated SQL-to-DAX translator.

The translator's contract is asymmetric on purpose: a missed translation is a
nuisance, but a wrong one is a model that deploys cleanly and reports wrong
numbers. So these tests care far more about what is *refused* than about breadth.
"""

import pytest

from ossie_microsoft._sql_to_dax import quote_column, quote_table, translate

COLUMNS = {
    "amount": ("Sales", "Amount"),
    "cust": ("Sales", "Customer Id"),
    "customer_name": ("TableName", "customer_name"),
    "customer_last_name": ("TableName", "customer_last_name"),
    "store_sales.ss_ext_sales_price": ("store_sales", "ss_ext_sales_price"),
    "store_sales.ss_net_profit": ("store_sales", "ss_net_profit"),
    "customer.c_customer_sk": ("customer", "c_customer_sk"),
    "store.s_number_employees": ("store", "s_number_employees"),
}


def _translate(sql, dialect="ANSI_SQL", table="Sales"):
    return translate(
        sql,
        dialect,
        lambda name: COLUMNS.get(name.casefold()),
        lambda: table,
    )


@pytest.mark.parametrize(
    ("sql", "dax"),
    [
        ("SUM(amount)", "SUM('Sales'[Amount])"),
        ("MIN(amount)", "MIN('Sales'[Amount])"),
        ("MAX(amount)", "MAX('Sales'[Amount])"),
        ("COUNT(amount)", "COUNTA('Sales'[Amount])"),
        ("AVG(amount)", "AVERAGE('Sales'[Amount])"),
        ("MEDIAN(amount)", "MEDIAN('Sales'[Amount])"),
        ("STDDEV(amount)", "STDEV.S('Sales'[Amount])"),
        ("STDDEV_SAMP(amount)", "STDEV.S('Sales'[Amount])"),
        ("STDDEV_POP(amount)", "STDEV.P('Sales'[Amount])"),
        ("VARIANCE(amount)", "VAR.S('Sales'[Amount])"),
        ("VAR_POP(amount)", "VAR.P('Sales'[Amount])"),
        ("COUNT(DISTINCT cust)", "DISTINCTCOUNTNOBLANK('Sales'[Customer Id])"),
        ("COUNT(*)", "COUNTROWS('Sales')"),
        ("sum(amount)", "SUM('Sales'[Amount])"),
        ("SUM(amount) AS total", "SUM('Sales'[Amount])"),
    ],
)
def test_a_curated_aggregate_is_translated(sql, dax):
    assert _translate(sql) == (dax, None)


def test_columns_and_literals_are_concatenated_with_table_qualified_dax():
    assert _translate("customer_name || ' ' || customer_last_name") == (
        "'TableName'[customer_name] & \" \" & 'TableName'[customer_last_name]",
        None,
    )


def test_a_dax_string_literal_escapes_double_quotes():
    assert _translate("customer_name || '\"' || customer_last_name") == (
        "'TableName'[customer_name] & \"\"\"\" & 'TableName'[customer_last_name]",
        None,
    )


@pytest.mark.parametrize(
    ("sql", "dax"),
    [
        (
            "SUM(store_sales.ss_ext_sales_price)",
            "SUM('store_sales'[ss_ext_sales_price])",
        ),
        (
            "SUM(store_sales.ss_ext_sales_price) / "
            "COUNT(DISTINCT customer.c_customer_sk)",
            "DIVIDE(SUM('store_sales'[ss_ext_sales_price]), "
            "DISTINCTCOUNTNOBLANK('customer'[c_customer_sk]))",
        ),
        (
            "SUM(store_sales.ss_ext_sales_price) / "
            "NULLIF(SUM(store.s_number_employees), 0)",
            "DIVIDE(SUM('store_sales'[ss_ext_sales_price]), "
            "SUM('store'[s_number_employees]))",
        ),
    ],
)
def test_tpcds_metric_shapes_are_translated(sql, dax):
    assert _translate(sql) == (dax, None)


@pytest.mark.parametrize(
    "sql",
    [
        "SUM(amount) + 0",
        "SUM(amount + amount)",
        "SUM(CASE WHEN amount THEN amount END)",
        "SUM(s.amount)",
        "SUM(db.s.amount)",
        "SUM(DISTINCT amount)",
        "AVG(DISTINCT amount)",
        "COUNT(DISTINCT amount, cust)",
        "COUNT(amount, cust)",
        "COUNT(DISTINCT *)",
        "PERCENTILE_CONT(amount, 0.5)",
        "SUM(amount) FILTER (WHERE amount > 1)",
        "COUNT(amount) OVER ()",
        "CASE WHEN amount THEN 1 END",
        "amount",
        "42",
        "SUM(unknown)",
        "",
        "SUM(",
    ],
)
def test_anything_outside_the_curated_set_is_refused(sql):
    dax, reason = _translate(sql)
    assert dax is None
    assert reason


def test_count_star_needs_an_unambiguous_table():
    assert _translate("COUNT(*)", table=None) == (
        None,
        "'COUNT(*)' needs exactly one dataset to count rows of",
    )


def test_count_maps_to_counta_not_count():
    """DAX `COUNT` documents TRUE/FALSE columns as unsupported, so it is not the
    equivalent of SQL `COUNT(x)` for an arbitrary column. `COUNTA` counts non-blank
    values of any type, which is what SQL means."""
    dax, _ = _translate("COUNT(amount)")
    assert dax.startswith("COUNTA(")


def test_a_multi_argument_count_is_refused_not_truncated():
    """Snowflake and Databricks `COUNT(a, b)` counts rows where both are non-NULL.
    sqlglot exposes only the first argument as `this`, so reading it alone would
    silently overcount."""
    dax, reason = _translate("COUNT(amount, cust)")
    assert dax is None
    assert "multiple arguments" in reason


def test_count_distinct_excludes_blank_like_sql_excludes_null():
    """SQL `COUNT(DISTINCT x)` excludes NULL, but DAX `DISTINCTCOUNT` counts BLANK as
    a distinct value, so it is off by one on any nullable column."""
    dax, _ = _translate("COUNT(DISTINCT cust)")
    assert dax.startswith("DISTINCTCOUNTNOBLANK(")


@pytest.mark.parametrize("dialect", ["SNOWFLAKE", "DATABRICKS", "BIGQUERY"])
def test_other_sql_dialects_are_read(dialect):
    assert _translate("SUM(amount)", dialect=dialect) == ("SUM('Sales'[Amount])", None)


@pytest.mark.parametrize("dialect", ["MDX", "TABLEAU", "MAQL", "DAX"])
def test_a_non_sql_dialect_is_not_parsed_as_sql(dialect):
    dax, reason = _translate("SUM(amount)", dialect=dialect)
    assert dax is None
    assert "not a SQL dialect" in reason


def test_an_unresolvable_column_names_itself_in_the_reason():
    _, reason = _translate("SUM(missing_col)")
    assert "missing_col" in reason


# --- identifier quoting ----------------------------------------------------
# Verified against the Power BI DAX grammar: a single-quoted table name is always
# accepted, so quoting unconditionally avoids a conditional rule that could misfire.


@pytest.mark.parametrize(
    ("name", "quoted"),
    [("Sales", "'Sales'"), ("My Sales", "'My Sales'"), ("It's", "'It''s'")],
)
def test_a_table_reference_is_always_single_quoted(name, quoted):
    assert quote_table(name) == quoted


@pytest.mark.parametrize(
    ("name", "quoted"),
    [("Amount", "[Amount]"), ("Customer Id", "[Customer Id]"), ("a]b", "[a]]b]")],
)
def test_a_column_reference_escapes_a_closing_bracket(name, quoted):
    assert quote_column(name) == quoted
