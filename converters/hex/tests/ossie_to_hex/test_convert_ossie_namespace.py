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

import importlib

import pytest
from ossie import OssieSemanticModel

from ossie_hex.ossie_to_hex.context import (
    ExportContext,
    MetricAssignment,
    RelationshipAssignment,
)
from ossie_hex.ossie_to_hex.convert_ossie_namespace import (
    finalize_ossie_namespace,
    initialize_ossie_namespace,
)
from tests.ossie_to_hex.utils import Quick

AMOUNT = Quick.field("amount", "Integer", [("ANSI_SQL", "amount")])
ORDERS = Quick.dataset(
    "orders", "public.orders", [("amount", "Integer", [("ANSI_SQL", "amount")])]
)
CUSTOMERS = Quick.dataset(
    "customers", "public.customers", [("id", "Integer", [("ANSI_SQL", "id")])]
)
TOTAL = Quick.metric("total", "Integer", [("ANSI_SQL", "SUM(orders.amount)")])
ORDERS_CUSTOMERS = Quick.relationship(
    "orders_customers", "orders", "customers", ["customer_id"], ["id"]
)


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


def test_initialize_disambiguates_dataset_ids_and_reports_collision(
    ctx: ExportContext,
) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            ORDERS.model_copy(update={"name": "Sales"}),
            ORDERS.model_copy(update={"name": "sales"}),
        ],
    )

    initialize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.datasets == {"Sales": "sales_2", "sales": "sales"}
    assert [(p.code, p.severity, p.cause_path) for p in ctx.problems] == [
        ("identifier-collision", "warning", ["datasets", "Sales"])
    ]


def test_initialize_stores_provisional_member_ids(ctx: ExportContext) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            ORDERS.model_copy(
                update={
                    "fields": [AMOUNT, AMOUNT.model_copy(update={"name": "Amount"})]
                }
            ),
            CUSTOMERS,
        ],
        metrics=[TOTAL.model_copy(update={"name": "Amount"})],
        relationships=[ORDERS_CUSTOMERS.model_copy(update={"name": "Amount"})],
    )

    initialize_ossie_namespace(source, ctx=ctx)

    assert (
        ctx.hex_ids.fields[("orders", "amount")]
        == ctx.hex_ids.fields[("orders", "Amount")]
        == "amount"
    )
    assert ctx.hex_ids.metrics == {"Amount": "amount"}
    assert ctx.hex_ids.relationships == {"Amount": "amount"}


@pytest.mark.parametrize(
    "names",
    [
        ["Order ID", "order_id", "order_id_2"],
        ["order_id_2", "order_id", "Order ID"],
    ],
)
def test_initialize_reserves_suffixes_regardless_of_input_order(
    ctx: ExportContext,
    names: list[str],
) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[ORDERS.model_copy(update={"name": name}) for name in names],
    )

    initialize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.datasets == {
        "Order ID": "order_id_3",
        "order_id": "order_id",
        "order_id_2": "order_id_2",
    }


def test_finalize_skips_occupied_suffixes(ctx: ExportContext) -> None:
    names = ["Order ID", "order_id", "order_id_2", "order_id_3", "order_id_4"]
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            ORDERS.model_copy(
                update={
                    "fields": [
                        AMOUNT.model_copy(update={"name": name}) for name in names
                    ]
                }
            )
        ],
    )
    initialize_ossie_namespace(source, ctx=ctx)

    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.for_field("orders", "Order ID") == "order_id_5"
    assert len(ctx.hex_ids.fields) == len(names)


def test_finalize_truncates_long_ids_to_make_room_for_suffix(
    ctx: ExportContext,
) -> None:
    base = "a" * 128
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            ORDERS.model_copy(
                update={
                    "fields": [
                        AMOUNT.model_copy(update={"name": base}),
                        AMOUNT.model_copy(update={"name": base + "x"}),
                    ]
                }
            )
        ],
    )
    initialize_ossie_namespace(source, ctx=ctx)

    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.fields == {
        ("orders", base): base,
        ("orders", base + "x"): "a" * 126 + "_2",
    }


def test_finalize_resolves_collisions_across_member_groups(ctx: ExportContext) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            ORDERS.model_copy(
                update={"fields": [AMOUNT.model_copy(update={"name": "Amount"})]}
            ),
            CUSTOMERS,
        ],
        metrics=[TOTAL.model_copy(update={"name": "amount"})],
        relationships=[ORDERS_CUSTOMERS.model_copy(update={"name": "Amount"})],
    )
    initialize_ossie_namespace(source, ctx=ctx)
    ctx.assignment.set_for_metric(MetricAssignment("amount", "orders", None))
    ctx.assignment.set_for_relationship(
        RelationshipAssignment("Amount", "from_to", "orders", "customers")
    )

    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.for_metric("amount") == "amount"
    assert ctx.hex_ids.for_field("orders", "Amount") == "amount_2"
    assert ctx.hex_ids.for_relationship("Amount") == "amount_3"


def test_finalize_allows_same_member_id_in_different_models(ctx: ExportContext) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[ORDERS, CUSTOMERS],
        metrics=[TOTAL.model_copy(update={"name": "Total"}), TOTAL],
    )
    initialize_ossie_namespace(source, ctx=ctx)
    ctx.assignment.set_for_metric(MetricAssignment("Total", "orders", None))
    ctx.assignment.set_for_metric(MetricAssignment("total", "customers", None))

    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.metrics == {"Total": "total", "total": "total"}


def test_finalize_avoids_collisions_in_every_relationship_placement(
    ctx: ExportContext,
) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            ORDERS.model_copy(
                update={"fields": [AMOUNT.model_copy(update={"name": "shared"})]}
            ),
            CUSTOMERS.model_copy(
                update={"fields": [AMOUNT.model_copy(update={"name": "shared_2"})]}
            ),
        ],
        relationships=[ORDERS_CUSTOMERS.model_copy(update={"name": "shared"})],
    )
    initialize_ossie_namespace(source, ctx=ctx)
    ctx.assignment.set_for_relationship(
        RelationshipAssignment("shared", "from_to", "orders", "customers")
    )
    ctx.assignment.for_relationship("shared").append(
        RelationshipAssignment("shared", "to_from", "customers", "orders")
    )

    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.for_relationship("shared") == "shared_3"


def test_finalize_retains_ids_for_unassigned_members(ctx: ExportContext) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[ORDERS, CUSTOMERS],
        metrics=[TOTAL.model_copy(update={"name": "amount"})],
        relationships=[ORDERS_CUSTOMERS.model_copy(update={"name": "amount"})],
    )
    initialize_ossie_namespace(source, ctx=ctx)

    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.metrics == {"amount": "amount"}
    assert ctx.hex_ids.relationships == {"amount": "amount"}


def test_namespace_handles_absent_member_collections(ctx: ExportContext) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[ORDERS.model_copy(update={"fields": None})],
    )

    initialize_ossie_namespace(source, ctx=ctx)
    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.datasets == {"orders": "orders"}
    assert not ctx.problems


def test_namespace_preserves_source_keys_and_metadata(ctx: ExportContext) -> None:
    source = OssieSemanticModel(
        name="sales",
        description="Keep metadata",
        datasets=[
            ORDERS.model_copy(
                update={
                    "fields": [AMOUNT, AMOUNT.model_copy(update={"name": "Amount"})],
                    "primary_key": ["Amount"],
                    "unique_keys": [["amount"]],
                }
            )
        ],
    )
    before = source.model_dump()

    initialize_ossie_namespace(source, ctx=ctx)
    finalize_ossie_namespace(source, ctx=ctx)

    assert source.model_dump() == before


def test_initialize_reports_parent_and_child_name_errors_and_continues(
    ctx: ExportContext,
) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            ORDERS.model_copy(
                update={
                    "name": "model",
                    "fields": [AMOUNT.model_copy(update={"name": "__hex_invalid"})],
                }
            ),
            ORDERS,
        ],
    )
    before = source.model_dump()

    initialize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.datasets == {"orders": "orders"}
    assert [p.cause_path for p in ctx.problems if p.severity == "error"] == [
        ["datasets", "model"],
        ["datasets", "model", "fields", "__hex_invalid"],
    ]
    assert source.model_dump() == before


def test_initialize_reports_duplicates_and_keeps_their_mappings(
    ctx: ExportContext,
) -> None:
    dataset = ORDERS.model_copy(update={"name": "dupe"})
    field = AMOUNT.model_copy(update={"name": "dupe"})
    metric = TOTAL.model_copy(update={"name": "dupe"})
    relationship = ORDERS_CUSTOMERS.model_copy(update={"name": "dupe"})
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            dataset,
            dataset,
            ORDERS.model_copy(update={"fields": [field, field]}),
            CUSTOMERS,
        ],
        metrics=[metric, metric],
        relationships=[relationship, relationship],
    )
    before = source.model_dump()

    initialize_ossie_namespace(source, ctx=ctx)

    assert (
        ctx.hex_ids.for_dataset("dupe"),
        ctx.hex_ids.for_field("orders", "dupe"),
        ctx.hex_ids.for_metric("dupe"),
        ctx.hex_ids.for_relationship("dupe"),
    ) == ("dupe",) * 4
    assert [p.code for p in ctx.problems] == ["duplicate-name"] * 4
    assert source.model_dump() == before


def test_finalize_assigns_one_suffixed_mapping_for_duplicate_names(
    ctx: ExportContext,
) -> None:
    duplicate = AMOUNT.model_copy(update={"name": "Amount"})
    source = OssieSemanticModel(
        name="sales",
        datasets=[ORDERS.model_copy(update={"fields": [duplicate, duplicate, AMOUNT]})],
    )
    initialize_ossie_namespace(source, ctx=ctx)

    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.fields == {
        ("orders", "Amount"): "amount_2",
        ("orders", "amount"): "amount",
    }
    assert [p.code for p in ctx.problems] == ["duplicate-name", "identifier-collision"]


def test_finalize_reports_allocation_failure_and_preserves_source(
    ctx: ExportContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = OssieSemanticModel(
        name="sales",
        datasets=[
            ORDERS.model_copy(
                update={
                    "fields": [AMOUNT, AMOUNT.model_copy(update={"name": "Amount"})]
                }
            )
        ],
    )
    before = source.model_dump()
    initialize_ossie_namespace(source, ctx=ctx)
    module = importlib.import_module("ossie_hex.ossie_to_hex.convert_ossie_namespace")
    monkeypatch.setattr(module, "convert_ossie_name", lambda name, *, ctx: None)

    finalize_ossie_namespace(source, ctx=ctx)

    assert ctx.hex_ids.fields == {("orders", "amount"): "amount"}
    assert [(p.code, p.severity, p.cause_path) for p in ctx.problems] == [
        (
            "identifier-allocation-failed",
            "error",
            ["datasets", "orders", "fields", "Amount"],
        )
    ]
    assert source.model_dump() == before
