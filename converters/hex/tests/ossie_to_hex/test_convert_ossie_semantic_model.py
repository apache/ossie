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
from ossie import (
    OssieAIContextObject,
    OssieCustomExtension,
    OssieDataset,
    OssieMetric,
    OssieRelationship,
    OssieSemanticModel,
)

from ossie_hex.hex import HexModel, HexRelationType, HexResource
from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.convert_ossie_semantic_model import (
    convert_ossie_semantic_model,
)
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


def _models(resources: list[HexResource]) -> list[HexModel]:
    return [resource for resource in resources if isinstance(resource, HexModel)]


def _model_by_id(resources: list[HexResource], id: str) -> HexModel | None:
    return next(
        resource
        for resource in resources
        if resource.id == id and isinstance(resource, HexModel)
    )


def _measure_ids(model: HexModel) -> list[str]:
    return [measure.id for measure in model.measures]


def _relation_ids(model: HexModel) -> list[str]:
    return [relation.id for relation in model.relations]


@pytest.fixture
def order_customer() -> OssieRelationship:
    return Quick.relationship(
        "order_customer", "orders", "customers", ["customer_id"], ["id"]
    )


@pytest.fixture
def sales_per_customer() -> OssieMetric:
    return Quick.metric(
        "sales_per_customer",
        "Decimal",
        [("ANSI_SQL", "SUM(orders.amount) / COUNT(DISTINCT customers.id)")],
    )


@pytest.fixture
def orders() -> OssieDataset:
    return Quick.dataset(
        "orders",
        "public.orders",
        [
            ("amount", "Integer", [("ANSI_SQL", "amount")]),
            ("customer_id", "String", [("ANSI_SQL", "customer_id")]),
        ],
    )


@pytest.fixture
def customers() -> OssieDataset:
    return Quick.dataset(
        "customers",
        "public.customers",
        [("id", "String", [("ANSI_SQL", "id")])],
    )


@pytest.fixture
def ctx() -> ExportContext:
    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    return ctx


def test_preserves_name(ctx: ExportContext) -> None:
    name = "sales"
    semantic_model = OssieSemanticModel(name=name, datasets=[])

    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert result.name == name
    assert problems_snapshot(ctx.problems) == snapshot("")


def test_warns_about_description(ctx: ExportContext) -> None:
    description = "Sales are important."
    semantic_model = OssieSemanticModel(
        name="sales", datasets=[], description=description
    )

    convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_ai_context(ctx: ExportContext) -> None:
    ai_context = OssieAIContextObject(synonyms=("bar", "baz"))
    semantic_model = OssieSemanticModel(
        name="sales", datasets=[], ai_context=ai_context
    )

    convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_custom_extensions(ctx: ExportContext) -> None:
    custom_extension = OssieCustomExtension(vendor_name="foo", data="bar")
    semantic_model = OssieSemanticModel(
        name="sales", datasets=[], custom_extensions=[custom_extension]
    )

    convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_assigns_metric_and_relationship(
    ctx: ExportContext,
    orders: OssieDataset,
    customers: OssieDataset,
    order_customer: OssieRelationship,
    sales_per_customer: OssieMetric,
) -> None:
    """Assigns a metric and relationship to the correct model.

    Does not
      - assign metric more than once
      - assign to a model not referenced by the metric
      - assign relationship more than once for a single metric
    """
    semantic_model = OssieSemanticModel(
        name="sales",
        datasets=[orders, customers],
        relationships=[order_customer],
        metrics=[sales_per_customer],
    )

    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert len(result.resources) == 2
    orders_model = _model_by_id(result.resources, "orders")
    customers_model = _model_by_id(result.resources, "customers")
    assert orders_model is not None
    assert customers_model is not None
    assert len(customers_model.measures) == 0
    assert len(orders_model.measures) == 1
    measure = orders_model.measures[0]
    assert measure.id == "sales_per_customer"
    assert measure.func_sql == snapshot(
        "SUM(${amount}) / COUNT(DISTINCT ${order_customer.id})"
    )

    assert len(customers_model.relations) == 0
    assert len(orders_model.relations) == 1
    relation = orders_model.relations[0]
    assert relation.id == "order_customer"
    assert relation.target == "customers"
    assert relation.type == HexRelationType.MANY_TO_ONE
    assert relation.join_sql == snapshot("customer_id = ${order_customer}.id")

    assert not ctx.problems


def test_assignment_prefers_from_to_direction(
    ctx: ExportContext,
    orders: OssieDataset,
    customers: OssieDataset,
    order_customer: OssieRelationship,
    sales_per_customer: OssieMetric,
) -> None:
    semantic_model = OssieSemanticModel(
        name="sales",
        datasets=[customers, orders],  # different dataset order
        relationships=[order_customer],
        metrics=[sales_per_customer],
    )

    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert len(result.resources) == 2
    customers_model = _model_by_id(result.resources, "customers")
    orders_model = _model_by_id(result.resources, "orders")
    assert customers_model is not None
    assert orders_model is not None
    assert _measure_ids(customers_model) == snapshot([])
    assert _measure_ids(orders_model) == snapshot(["sales_per_customer"])

    assert _relation_ids(customers_model) == snapshot([])
    assert _relation_ids(orders_model) == snapshot(["order_customer"])


def test_drops_metric_that_no_dataset_can_resolve(
    ctx: ExportContext,
    orders: OssieDataset,
    customers: OssieDataset,
    sales_per_customer: OssieMetric,
) -> None:
    semantic_model = OssieSemanticModel(
        name="sales",
        datasets=[orders, customers],
        relationships=[],
        metrics=[sales_per_customer],
    )

    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    models = _models(result.resources)
    assert all(model.measures == [] for model in models)
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Cannot assign metric. Unable to find a relationship between referenced datasets: orders, customers."
    )


def test_does_not_choose_between_parallel_relationships(
    ctx: ExportContext,
    orders: OssieDataset,
    customers: OssieDataset,
    sales_per_customer: OssieMetric,
) -> None:
    billing_customer = Quick.relationship(
        "billing_customer", "orders", "customers", ["billing_customer_id"], ["id"]
    )
    shipping_customer = Quick.relationship(
        "shipping_customer", "orders", "customers", ["shipping_customer_id"], ["id"]
    )
    semantic_model = OssieSemanticModel(
        name="sales",
        datasets=[orders, customers],
        relationships=[billing_customer, shipping_customer],
        metrics=[sales_per_customer],
    )
    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert len(result.resources) == 2
    orders_model = _model_by_id(result.resources, "orders")
    customers_model = _model_by_id(result.resources, "customers")
    assert orders_model is not None
    assert customers_model is not None
    orders_relation_ids = _relation_ids(orders_model)
    customers_relation_ids = _relation_ids(customers_model)
    assert orders_relation_ids == snapshot(["billing_customer", "shipping_customer"])
    assert customers_relation_ids == snapshot([])

    assert problems_snapshot(ctx.problems) == snapshot(
        "[WARNING] Ambiguous metric assignment. Multiple relationships found between referenced datasets: orders, customers. Possible relationships: billing_customer, shipping_customer."
    )


def test_drops_metric_without_a_dataset_reference(
    ctx: ExportContext,
    orders: OssieDataset,
) -> None:
    row_count = Quick.metric("row_count", "Integer", [("ANSI_SQL", "COUNT(*)")])
    semantic_model = OssieSemanticModel(
        name="sales",
        datasets=[orders],
        metrics=[row_count],
    )
    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    models = _models(result.resources)
    assert all(model.measures == [] for model in models)
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Referencing no datasets is not supported."
    )


def test_reports_metric_with_more_than_two_dataset_references(
    ctx: ExportContext,
    orders: OssieDataset,
    customers: OssieDataset,
) -> None:
    stores = Quick.dataset(
        "stores",
        "public.stores",
        [("id", "String", [("ANSI_SQL", "id")])],
    )
    metric = Quick.metric(
        "three_dataset_metric",
        "Integer",
        [("ANSI_SQL", "orders.amount + customers.id + stores.id")],
    )
    semantic_model = OssieSemanticModel(
        name="sales",
        datasets=[orders, customers, stores],
        metrics=[metric],
    )

    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    models = _models(result.resources)
    assert all(model.measures == [] for model in models)
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Referencing more than two datasets is not supported. Found 3: orders, customers, stores."
    )


def test_fully_processes_unassigned_metric(
    ctx: ExportContext,
    orders: OssieDataset,
) -> None:
    metric = Quick.metric("row_count", "Integer", [("ANSI_SQL", "COUNT(*)")])
    semantic_model = OssieSemanticModel(
        name="sales",
        datasets=[orders],
        metrics=[metric],
    )

    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    models = _models(result.resources)
    assert all(model.measures == [] for model in models)
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Referencing no datasets is not supported."
    )


def test_assigns_relationship_not_used_by_a_metric(
    ctx: ExportContext,
    orders: OssieDataset,
    customers: OssieDataset,
    order_customer: OssieRelationship,
) -> None:
    semantic_model = OssieSemanticModel(
        name="sales",
        datasets=[orders, customers],
        relationships=[order_customer],
        metrics=[],
    )

    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    models = _models(result.resources)
    assert _relation_ids(models[0]) == snapshot(["order_customer"])
    assert _relation_ids(models[1]) == snapshot([])
    assert not ctx.problems
