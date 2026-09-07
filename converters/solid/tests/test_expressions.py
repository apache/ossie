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

"""Metric expression qualification.

The property that matters most here is that the converter never rewrites SQL it was only
asked to qualify: the splice must leave every byte outside the inserted qualifier alone.
"""

import pytest

from ossie_solid.expressions import (
    AMBIGUOUS,
    QUALIFIED,
    UNCHANGED,
    UNPARSED,
    UNQUALIFIED,
    column_reference,
    qualify_metric,
    referenced_datasets,
    unqualify_metric,
)

COLUMNS = ["amount", "status", "order_id", "region", "ss_sales_price", "ss_list_price"]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("SUM(amount)", "SUM(orders.amount)"),
        ("SUM(amount) / 12.0", "SUM(orders.amount) / 12.0"),
        ("COUNT(DISTINCT order_id)", "COUNT(DISTINCT orders.order_id)"),
        ("SUM(SUM(amount)) OVER ()", "SUM(SUM(orders.amount)) OVER ()"),
        (
            "SUM(CASE WHEN status = 'shipped' THEN amount ELSE 0 END)",
            "SUM(CASE WHEN orders.status = 'shipped' THEN orders.amount ELSE 0 END)",
        ),
        (
            "1 - (ss_sales_price / NULLIF(ss_list_price, 0))",
            "1 - (orders.ss_sales_price / NULLIF(orders.ss_list_price, 0))",
        ),
    ],
)
def test_bare_columns_are_prefixed_with_the_owning_dataset(expression, expected):
    assert qualify_metric(expression, "SNOWFLAKE", "orders", COLUMNS) == (
        expected,
        QUALIFIED,
    )


def test_a_column_name_inside_a_string_literal_is_not_touched():
    result, status = qualify_metric(
        "COUNT(CASE WHEN status = 'amount' THEN 1 END)", "SNOWFLAKE", "orders", COLUMNS
    )
    assert result == "COUNT(CASE WHEN orders.status = 'amount' THEN 1 END)"
    assert status == QUALIFIED


def test_an_already_qualified_column_is_left_alone():
    assert qualify_metric("SUM(o.amount)", "SNOWFLAKE", "orders", COLUMNS) == (
        "SUM(o.amount)",
        UNCHANGED,
    )


def test_a_function_whose_name_matches_a_column_is_not_qualified():
    assert qualify_metric("status(amount)", "SNOWFLAKE", "orders", COLUMNS)[0] == (
        "status(orders.amount)"
    )


def test_a_column_the_dataset_does_not_own_is_left_bare():
    assert qualify_metric("SUM(other_col)", "SNOWFLAKE", "orders", COLUMNS) == (
        "SUM(other_col)",
        UNCHANGED,
    )


def test_column_matching_is_case_insensitive():
    result, status = qualify_metric("SUM(AMOUNT)", "SNOWFLAKE", "orders", COLUMNS)
    assert result == "SUM(orders.AMOUNT)"
    assert status == QUALIFIED


def test_an_expression_with_no_columns_is_returned_verbatim():
    assert qualify_metric("COUNT(*)", "SNOWFLAKE", "orders", COLUMNS) == (
        "COUNT(*)",
        UNCHANGED,
    )


def test_a_name_used_in_a_non_column_position_blocks_the_edit():
    # `year` is a column here *and* the datepart keyword inside EXTRACT, so the token
    # scan and the parse disagree and the expression is left exactly as written.
    result, status = qualify_metric(
        "SUM(EXTRACT(year FROM closed_at))", "SNOWFLAKE", "orders", ["year", "closed_at"]
    )
    assert result == "SUM(EXTRACT(year FROM closed_at))"
    assert status == AMBIGUOUS


@pytest.mark.parametrize(
    ("expression", "dialect"),
    [
        # sqlglot canonicalizes both of these when it re-renders a parsed tree; a splice
        # must not.
        ("ROUND(CAST(amount AS FLOAT), 2)", "SNOWFLAKE"),
        ("SUM(amount)   +   1", "SNOWFLAKE"),
        ("COUNTIF(status = 'active')", "BIGQUERY"),
        ("SUM(amount) /* trailing comment */", "SNOWFLAKE"),
    ],
)
def test_everything_outside_the_inserted_qualifier_is_preserved_byte_for_byte(
    expression, dialect
):
    result, status = qualify_metric(expression, dialect, "orders", COLUMNS)
    assert status == QUALIFIED, "otherwise this asserts nothing"
    assert result.replace("orders.", "") == expression


def test_an_unparseable_expression_is_returned_verbatim():
    assert qualify_metric("SUM(amount", "SNOWFLAKE", "orders", COLUMNS) == (
        "SUM(amount",
        UNPARSED,
    )


def test_a_quoted_identifier_is_qualified_with_its_quotes_intact():
    result, status = qualify_metric(
        'SUM("odd name")', "SNOWFLAKE", "orders", ["odd name"]
    )
    assert result == 'SUM(orders."odd name")'
    assert status == QUALIFIED


# --- the inverse -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("SUM(orders.amount)", "SUM(amount)"),
        (
            "SUM(CASE WHEN orders.status = 'x' THEN orders.amount END)",
            "SUM(CASE WHEN status = 'x' THEN amount END)",
        ),
    ],
)
def test_a_dataset_qualifier_is_stripped_back_out(expression, expected):
    assert unqualify_metric(expression, "SNOWFLAKE", ["orders"]) == (
        expected,
        UNQUALIFIED,
    )


def test_a_qualifier_that_is_not_a_dataset_is_preserved():
    assert unqualify_metric("SUM(p.amount)", "SNOWFLAKE", ["orders"]) == (
        "SUM(p.amount)",
        UNCHANGED,
    )


def test_a_schema_qualified_reference_is_left_alone():
    assert unqualify_metric("SUM(db.orders.amount)", "SNOWFLAKE", ["orders"]) == (
        "SUM(db.orders.amount)",
        UNCHANGED,
    )


@pytest.mark.parametrize(
    ("expression", "dialect"),
    [
        ("SUM(orders.amount) / NULLIF(COUNT(orders.order_id), 0)", "SNOWFLAKE"),
        ("ROUND(CAST(orders.amount AS FLOAT), 2)", "SNOWFLAKE"),
        ("COUNTIF(orders.status = 'active')", "BIGQUERY"),
    ],
)
def test_qualifying_and_unqualifying_returns_the_original_text(expression, dialect):
    bare, _ = unqualify_metric(expression, dialect, ["orders"])
    again, _ = qualify_metric(bare, dialect, "orders", COLUMNS)
    assert again == expression


# --- helpers -----------------------------------------------------------------------


def test_the_datasets_a_metric_references_are_reported_in_declaration_order():
    assert referenced_datasets(
        "SUM(orders.amount) / COUNT(customers.id)",
        "SNOWFLAKE",
        ["customers", "orders", "items"],
    ) == ["customers", "orders"]


def test_no_datasets_are_reported_for_a_bare_expression():
    assert referenced_datasets("SUM(amount)", "SNOWFLAKE", ["orders"]) == []


@pytest.mark.parametrize(
    ("name", "dialect", "expected"),
    [
        ("amount", "SNOWFLAKE", "amount"),
        ("odd name", "SNOWFLAKE", '"odd name"'),
        ("odd name", "DATABRICKS", "`odd name`"),
        ("select", "SNOWFLAKE", '"select"'),
    ],
)
def test_a_column_name_renders_as_a_valid_reference(name, dialect, expected):
    assert column_reference(name, dialect) == expected
