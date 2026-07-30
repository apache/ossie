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
from ossie import OSIDialect, OSIDialectExpression, OSIExpression

from ossie_hex._common import ConversionError, load_yaml
from ossie_hex.ossie_to_hex import (
    convert_ossie_to_hex,
    normalize_to_hex_id,
    pick_expression,
)


def test_coerce_hex_id_prefixes_number_with_underscore() -> None:
    assert normalize_to_hex_id('"123 Orders"', "dataset", set()) == "_123_orders"


def test_coerce_hex_id_replaces_empty_result() -> None:
    assert normalize_to_hex_id('"!"', "dataset", set()) == "_1"


def test_normalize_to_hex_id_preserves_valid_id() -> None:
    assert normalize_to_hex_id("order_items", "dataset", set()) == "order_items"


def test_normalize_to_hex_id_rejects_collisions() -> None:
    with pytest.raises(ConversionError, match="collides"):
        normalize_to_hex_id("Orders", "dataset", {"orders"})


def test_pick_expression_prefers_requested_then_ansi_dialect() -> None:
    expression = OSIExpression(
        dialects=[
            OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="ansi_expr"),
            OSIDialectExpression(
                dialect=OSIDialect.SNOWFLAKE, expression="snowflake_expr"
            ),
        ]
    )

    assert (
        pick_expression(expression, preferred=OSIDialect.SNOWFLAKE) == "snowflake_expr"
    )
    assert pick_expression(expression, preferred=OSIDialect.BIGQUERY) == "ansi_expr"


def test_pick_expression_falls_back_to_first_or_none() -> None:
    expression = OSIExpression(
        dialects=[
            OSIDialectExpression(
                dialect=OSIDialect.SNOWFLAKE, expression="snowflake_expr"
            )
        ]
    )

    assert (
        pick_expression(expression, preferred=OSIDialect.BIGQUERY) == "snowflake_expr"
    )
    assert pick_expression(None) is None


def test_export_uses_requested_osi_expression_dialect() -> None:
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: amount
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: ansi_amount
                - dialect: SNOWFLAKE
                  expression: snowflake_amount
            dimension: {}
"""
    files, _ = convert_ossie_to_hex(ossie, dialect="snowflake")

    dimension = load_yaml(files["orders.yml"])["dimensions"][0]
    assert dimension["expr_sql"] == "${snowflake_amount}"


def test_export_ambiguous_metrics_require_base_model() -> None:
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: a
        source: s.a
        fields:
          - name: x
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: x
            dimension: {}
            datatype: Integer
      - name: b
        source: s.b
        fields:
          - name: y
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: y
            dimension: {}
            datatype: Integer
    metrics:
      - name: weird
        datatype: Integer
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "1 + 1"
"""
    with pytest.raises(ConversionError, match="Could not assign metric"):
        convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)

    files, warnings = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL.value, base_model="a"
    )

    # The unassignable metric lands on --base-model, not on the other dataset.
    # Single-character Ossie names are padded, since Hex IDs need two characters.
    assert (
        files["a_.yml"]
        == """\
id: a_
base_sql_table: s.a
dimensions:
- id: x_
  type: number
measures:
- id: weird
  func_sql: 1 + 1
"""
    )
    assert (
        files["b_.yml"]
        == """\
id: b_
base_sql_table: s.b
dimensions:
- id: y_
  type: number
"""
    )
    assert warnings == []


def test_export_ignores_non_hex_custom_extensions() -> None:
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    custom_extensions:
      - vendor_name: DBT
        data: '{}'
    datasets:
      - name: orders
        source: analytics.orders
        custom_extensions:
          - vendor_name: SNOWFLAKE
            data: '{}'
        fields:
          - name: order_id
            expression:
              dialects:
                - dialect: ANSI_SQL
                  expression: order_id
            dimension: {}
            datatype: Decimal
            custom_extensions:
              - vendor_name: DBT
                data: '{}'
    metrics:
      - name: order_count
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: COUNT(*)
        datatype: Decimal
        custom_extensions:
          - vendor_name: DBT
            data: '{}'
"""
    files, warnings = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL.value)

    assert files
    assert warnings == []
