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
from inline_snapshot import snapshot

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.load_ossie_semantic_model import load_ossie_semantic_model
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


orders = Quick.dataset(
    "orders",
    "public.orders",
    [("amount", "Decimal", [("ANSI_SQL", "amount")])],
)

customers = Quick.dataset(
    "customers",
    "public.customers",
    [("id", "String", [("ANSI_SQL", "id")])],
)

total_amount = Quick.metric(
    "total_amount",
    "Integer",
    [("ANSI_SQL", "orders.amount")],
)

orders_to_customers = Quick.relationship(
    "orders_to_customers",
    "orders",
    "customers",
    ["customer_id"],
    ["id"],
)


def test_keeps_valid_members(ctx: ExportContext) -> None:
    datasets = [orders, customers]
    metrics = [total_amount]
    relationships = [orders_to_customers]
    foo = Quick.semantic_model("foo", datasets, metrics, relationships)
    result = load_ossie_semantic_model(foo, ctx=ctx)
    assert result.name == foo.name
    assert len(result.datasets) == 2
    assert len(result.relationships or []) == 1
    assert len(result.metrics or []) == 1
    assert not ctx.problems


def test_removes_invalid_relationship(ctx: ExportContext) -> None:
    datasets = [orders, customers]
    metrics = []
    relationships = [orders_to_customers]
    bad = Quick.relationship("bad", "orders", "missing", ["customer_id"], ["id"])
    foo = Quick.semantic_model("foo", datasets, metrics, [bad, *relationships])
    result = load_ossie_semantic_model(foo, ctx=ctx)
    assert result.relationships is not None
    assert len(result.relationships) == 1
    assert result.relationships[0].name == "orders_to_customers"
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Could not resolve dataset name: 'missing'."
    )


def test_removes_invalid_metric(ctx: ExportContext) -> None:
    datasets = [orders, customers]
    metrics = [total_amount]
    relationships = []
    bad = Quick.metric(
        "bad",
        "Integer",
        [("ANSI_SQL", "orders.missing")],
    )
    foo = Quick.semantic_model("foo", datasets, [bad, *metrics], relationships)
    result = load_ossie_semantic_model(foo, ctx=ctx)
    assert result.metrics is not None
    assert len(result.metrics) == 1
    assert result.metrics[0].name == "total_amount"
    assert problems_snapshot(ctx.problems) == snapshot("""\
[ERROR] Field expression references field not in semantic model: orders.missing

[ERROR] Expression must have at least one valid dialect\
""")
