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

from ossie_hex.expression_rewrite import (
    hex_refs_to_ossie,
    ossie_expr_to_hex_refs,
    parse_equi_join,
    synthesize_join_sql,
)


def test_hex_refs_to_ossie() -> None:
    assert hex_refs_to_ossie("${order_id}") == "order_id"
    assert hex_refs_to_ossie("${order_id}", model="orders") == "orders.order_id"
    assert hex_refs_to_ossie("${customers.id}") == "customers.id"


def test_hex_refs_to_ossie_rewrites_multiple_references() -> None:
    assert (
        hex_refs_to_ossie("${subtotal} + ${tax}", model="orders")
        == "orders.subtotal + orders.tax"
    )


@pytest.mark.parametrize(
    ("expression", "model", "expected"),
    [
        ("order_id", None, "${order_id}"),
        ("orders.order_id", "orders", "${order_id}"),
        ("customers.id", "orders", "${customers.id}"),
        ("SUM(orders.amount)", "orders", "SUM(orders.amount)"),
    ],
)
def test_ossie_expr_to_hex_refs(
    expression: str,
    model: str | None,
    expected: str,
) -> None:
    assert ossie_expr_to_hex_refs(expression, model=model) == expected


def test_parse_equi_join_simple() -> None:
    parsed = parse_equi_join(
        "${customer_id} = ${customers.customer_id}",
        relation_id="customers",
        target="customers",
    )
    assert parsed == (["customer_id"], ["customer_id"])


def test_parse_equi_join_named() -> None:
    parsed = parse_equi_join(
        "${sender_id} = ${sender.id}",
        relation_id="sender",
        target="users",
    )
    assert parsed == (["sender_id"], ["id"])


def test_parse_equi_join_reversed_and_multi_column() -> None:
    parsed = parse_equi_join(
        "${customers.tenant_id} = ${tenant_id} AND ${customers.id} = ${customer_id}",
        relation_id="customers",
        target="customers",
    )
    assert parsed == (["tenant_id", "customer_id"], ["tenant_id", "id"])


@pytest.mark.parametrize(
    "join_sql",
    [
        "${a} > ${b.id}",
        "${customers.id} = ${customers.parent_id}",
        "${a} = 1",
    ],
)
def test_parse_equi_join_rejects_non_decomposable_sql(join_sql: str) -> None:
    assert (
        parse_equi_join(
            join_sql,
            relation_id="customers",
            target="customers",
        )
        is None
    )


def test_synthesize_join_sql() -> None:
    sql = synthesize_join_sql(
        from_columns=["customer_id"],
        to_columns=["customer_id"],
        relation_id="customers",
    )
    assert sql == "${customer_id} = ${customers.customer_id}"


def test_synthesize_join_sql_multiple_columns() -> None:
    sql = synthesize_join_sql(
        from_columns=["tenant_id", "customer_id"],
        to_columns=["tenant_id", "id"],
        relation_id="customers",
    )
    assert sql == (
        "${tenant_id} = ${customers.tenant_id} AND ${customer_id} = ${customers.id}"
    )


def test_synthesize_join_sql_rejects_mismatched_columns() -> None:
    with pytest.raises(ValueError, match="must have equal length"):
        synthesize_join_sql(
            from_columns=["tenant_id"],
            to_columns=[],
            relation_id="customers",
        )
