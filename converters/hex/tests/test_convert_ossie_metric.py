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

from typing import Any

import pytest
import yaml
from ossie import OSIDialect

from ossie_hex.ossie_to_hex import convert_ossie_to_hex
from tests.utils import one_metric_ossie, two_dataset_ossie


def _measure_for(metric_expression: str) -> dict[str, Any]:
    """Convert one metric on an `orders` model whose only field is `amount`."""
    files, _ = convert_ossie_to_hex(
        one_metric_ossie(metric_expression),
        dialect=OSIDialect.ANSI_SQL,
        base_model="orders",
    )
    return yaml.safe_load(files["orders.yml"])["measures"][0]


def test_a_metric_becomes_func_sql() -> None:
    """An Ossie metric is SQL, and `func_sql` is the Hex measure that holds SQL.

    Hex `func`/`of`/`filters` compile into that same expression on import, so the
    export path does not try to recover a structured aggregate from it.
    """
    assert _measure_for("SUM(orders.amount)") == {
        "id": "total",
        "func_sql": "SUM(${amount})",
    }


@pytest.mark.parametrize(
    "expression",
    [
        "COUNT(*)",
        "COUNT(DISTINCT amount)",
        "SUM(amount * 2)",
        "SUM(CASE WHEN x THEN 1 END)",
        "SUM(a) / COUNT(*)",
        "AVG(price) OVER (PARTITION BY x)",
    ],
)
def test_a_metric_expression_carries_across_verbatim(expression: str) -> None:
    """Every shape of expression takes the same path, however complex.

    These are the cases an aggregate parser had to recognise and decline, since
    a Hex `of` names a single dimension and cannot hold a computed argument, a
    window, or an expression spanning two aggregates. Only a reference needs
    rewriting, and none of these carry one.
    """
    assert _measure_for(expression) == {"id": "total", "func_sql": expression}


def test_a_metric_datatype_becomes_the_measure_type() -> None:
    """MAX over a date column is a date, and `func_sql` can say so.

    Nothing here came from Hex, so there is no stash and the metric's own
    datatype is all the importer has to go on. This is the type a `func` measure
    could not have held, since Hex pins those to `number`.
    """
    ossie = """
version: "0.2.0.dev0"
semantic_model:
  - name: m
    datasets:
      - name: orders
        source: s.orders
        fields:
          - name: order_date
            datatype: Date
            expression:
              dialects: [{dialect: ANSI_SQL, expression: order_date}]
            dimension: {}
    metrics:
      - name: latest_order
        datatype: Date
        expression:
          dialects: [{dialect: ANSI_SQL, expression: "MAX(orders.order_date)"}]
"""
    files, _ = convert_ossie_to_hex(
        ossie, dialect=OSIDialect.ANSI_SQL, base_model="orders"
    )

    assert yaml.safe_load(files["orders.yml"])["measures"][0] == {
        "id": "latest_order",
        "func_sql": "MAX(${order_date})",
        "type": "date",
    }



def test_a_qualifier_inside_a_string_literal_is_not_a_reference() -> None:
    # The same text twice: rewritten as a reference, left alone as a literal.
    measure = _measure_for(
        "COUNT(CASE WHEN orders.amount > 0 THEN 'orders.amount' END)"
    )

    assert measure["func_sql"] == (
        "COUNT(CASE WHEN ${amount} > 0 THEN 'orders.amount' END)"
    )


def test_a_metric_named_like_a_qualified_one_is_taken_at_face_value() -> None:
    """Only the HEX payload can say a name was qualified to dodge a collision.

    Export renames a colliding measure to ``<model>__<measure>``, but nothing
    stops an Ossie author from writing that name and meaning it. With no
    payload to say otherwise, the name is the measure ID, prefix and all.
    """
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
              dialects: [{dialect: ANSI_SQL, expression: amount}]
            dimension: {}
    metrics:
      - name: orders__revenue
        datatype: Decimal
        expression:
          dialects: [{dialect: ANSI_SQL, expression: "SUM(orders.amount)"}]
"""

    files, _ = convert_ossie_to_hex(ossie, dialect=OSIDialect.ANSI_SQL)
    measure = yaml.safe_load(files["orders.yml"])["measures"][0]

    assert measure == {"id": "orders__revenue", "func_sql": "SUM(${amount})"}


def _cross_dataset_measure(
    metric_expression: str, *, related: bool
) -> tuple[dict[str, Any], list[str]]:
    files, warnings = convert_ossie_to_hex(
        two_dataset_ossie(metric_expression, related=related),
        dialect=OSIDialect.ANSI_SQL,
        base_model="orders",
    )
    measure = yaml.safe_load(files["orders.yml"])["measures"][0]
    return measure, [str(w) for w in warnings]


def test_every_reference_in_a_raw_sql_measure_is_rewritten() -> None:
    measure, warnings = _cross_dataset_measure(
        "SUM(orders.amount) / COUNT(DISTINCT customers.id)", related=True
    )

    assert measure["func_sql"] == "SUM(${amount}) / COUNT(DISTINCT ${buyer.id})"
    assert warnings == []


def test_a_reference_to_an_unrelated_model_is_left_verbatim() -> None:
    """Better to leave SQL a human can fix than invent a ref Hex cannot follow."""
    measure, warnings = _cross_dataset_measure(
        "SUM(orders.amount) / COUNT(DISTINCT customers.id)", related=False
    )

    assert measure["func_sql"] == "SUM(${amount}) / COUNT(DISTINCT customers.id)"
    assert warnings == [
        (
            "metric 'value_per_customer' references customers, which 'orders' has "
            "no relation to; the SQL was kept verbatim and needs review"
        )
    ]
