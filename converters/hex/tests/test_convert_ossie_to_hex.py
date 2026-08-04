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
import yaml
from inline_snapshot import snapshot as inline_snapshot
from ossie import OSIDialect
from syrupy.assertion import SnapshotAssertion

from ossie_hex.ossie_to_hex import convert_ossie_to_hex
from ossie_hex.util.errors import ConversionError
from ossie_hex.util.yaml import load_yaml


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
    files, _ = convert_ossie_to_hex(ossie, dialect=OSIDialect.SNOWFLAKE)

    # `snowflake_amount` is a column of `s.orders`, not a dimension of the model,
    # so it stays raw SQL. Wrapping it would name a dimension that is not there.
    dimension = load_yaml(files["orders.yml"])["dimensions"][0]
    assert dimension["expr_sql"] == "snowflake_amount"


def test_export_ambiguous_metrics_require_base_model(
    snapshot: SnapshotAssertion,
) -> None:
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
        convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL)

    files, warnings = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL, base_model="a"
    )

    # The unassignable metric lands on --base-model, not on the other dataset.
    # Single-character Ossie names are padded, since Hex IDs need two characters.
    assert files == snapshot
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
    files, warnings = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL)

    assert files
    assert warnings == []


def test_export_maps_dataset_names_that_are_not_hex_ids() -> None:
    """Ossie names are free-form, so every ref must go through the ID mapping."""
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: Order Items
        source: s.order_items
        fields:
          - name: CustomerID
            expression:
              dialects: [{dialect: ANSI_SQL, expression: CustomerID}]
            dimension: {}
          - name: Amount
            expression:
              dialects: [{dialect: ANSI_SQL, expression: Amount}]
            dimension: {}
      - name: Customers
        source: s.customers
        primary_key: [CustomerID]
        fields:
          - name: CustomerID
            expression:
              dialects: [{dialect: ANSI_SQL, expression: CustomerID}]
            dimension: {}
    relationships:
      - name: items_to_customers
        from: Order Items
        to: Customers
        from_columns: [CustomerID]
        to_columns: [CustomerID]
    metrics:
      - name: total
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "SUM(Order Items.Amount)"
"""
    files, _ = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL, base_model="Order Items"
    )
    items = yaml.safe_load(files["order_items.yml"])

    assert items["id"] == "order_items"
    # `target` must name the coerced Hex model, not the original Ossie name.
    assert items["relations"] == inline_snapshot(
        [
            {
                "id": "items_to_customers",
                "target": "customers",
                "type": "many_to_one",
                "join_sql": "${customerid} = ${items_to_customers.customerid}",
            }
        ]
    )
