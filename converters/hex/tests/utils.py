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

from __future__ import annotations

import json
from typing import Any


def hex_extension(node: dict[str, Any]) -> dict[str, Any] | None:
    """The HEX custom-extension payload attached to an Ossie node."""
    extensions = node.get("custom_extensions")
    if not extensions:
        return None
    payload = json.loads(extensions[0]["data"])
    assert isinstance(payload, dict)
    return payload


def field_ossie(*, datatype: str | None, is_time: bool | None) -> str:
    """One dataset holding a single `created_at` field with optional metadata."""
    datatype_line = f"            datatype: {datatype}\n" if datatype else ""
    dimension_block = (
        "            dimension: {}\n"
        if is_time is None
        else f"            dimension:\n              is_time: {str(is_time).lower()}\n"
    )
    return f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: created_at
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: created_at}}]
{datatype_line}{dimension_block}"""


def one_metric_ossie(metric_expression: str, *, datatype: str = "Decimal") -> str:
    """An `orders` dataset whose only field is `amount`, carrying one metric."""
    return f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: amount
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: amount}}]
            dimension: {{}}
    metrics:
      - name: total
        datatype: {datatype}
        expression:
          dialects: [{{dialect: ANSI_SQL, expression: "{metric_expression}"}}]
"""


def two_dataset_ossie(metric_expression: str, *, related: bool) -> str:
    """`orders` and `customers`, optionally joined, carrying one cross-model metric."""
    relationships = (
        """
    relationships:
      - name: buyer
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [id]
"""
        if related
        else ""
    )
    return f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: customer_id
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: customer_id}}]
            dimension: {{}}
            datatype: String
          - name: amount
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: amount}}]
            dimension: {{}}
            datatype: Decimal
      - name: customers
        source: s.customers
        fields:
          - name: id
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: id}}]
            dimension: {{}}
            datatype: String
{relationships}
    metrics:
      - name: value_per_customer
        datatype: Decimal
        expression:
          dialects:
            - dialect: ANSI_SQL
              expression: "{metric_expression}"
"""


def labelled_field_ossie(field_expression: str, *, related: bool) -> str:
    """An `orders.label` field, with `customers` optionally reachable as `buyer`."""
    relationships = (
        """
    relationships:
      - name: buyer
        from: orders
        to: customers
        from_columns: [customer_id]
        to_columns: [name]
"""
        if related
        else ""
    )
    return f"""
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: customer_id
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: customer_id}}]
            dimension: {{}}
          - name: label
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: "{field_expression}"}}]
            dimension: {{}}
      - name: customers
        source: s.customers
        fields:
          - name: name
            expression:
              dialects: [{{dialect: ANSI_SQL, expression: name}}]
            dimension: {{}}
{relationships}
"""
