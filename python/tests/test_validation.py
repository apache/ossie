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

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from ossie import (
    OssieAIContextObject,
    OssieCustomExtension,
    OssieDataset,
    OssieDialect,
    OssieDialectExpression,
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieMetric,
    OssieRelationship,
    OssieSemanticModel,
)


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------


def test_dataset_missing_required() -> None:
    with pytest.raises(ValidationError):
        OssieDataset()


def test_field_missing_name(make_expression: Callable[[str], OssieExpression]) -> None:
    with pytest.raises(ValidationError):
        OssieField(expression=make_expression("order_id"))


def test_field_missing_expression() -> None:
    with pytest.raises(ValidationError):
        OssieField(name="order_id")


def test_relationship_missing_name() -> None:
    with pytest.raises(ValidationError):
        OssieRelationship(
            **{"from": "orders"},
            to="customers",
            from_columns=["customer_id"],
            to_columns=["id"],
        )


def test_relationship_missing_from() -> None:
    with pytest.raises(ValidationError):
        OssieRelationship(
            name="order_customer",
            to="customers",
            from_columns=["customer_id"],
            to_columns=["id"],
        )


def test_relationship_missing_to() -> None:
    with pytest.raises(ValidationError):
        OssieRelationship(
            name="order_customer",
            **{"from": "orders"},
            from_columns=["customer_id"],
            to_columns=["id"],
        )


def test_relationship_missing_from_columns() -> None:
    with pytest.raises(ValidationError):
        OssieRelationship(
            name="order_customer",
            **{"from": "orders"},
            to="customers",
            to_columns=["id"],
        )


def test_relationship_missing_to_columns() -> None:
    with pytest.raises(ValidationError):
        OssieRelationship(
            name="order_customer",
            **{"from": "orders"},
            to="customers",
            from_columns=["customer_id"],
        )


def test_metric_missing_name(make_expression: Callable[[str], OssieExpression]) -> None:
    with pytest.raises(ValidationError):
        OssieMetric(expression=make_expression("order_id"))


def test_metric_missing_expression() -> None:
    with pytest.raises(ValidationError):
        OssieMetric(name="total_sales")


def test_document_missing_semantic_model() -> None:
    with pytest.raises(ValidationError):
        OssieDocument()


def test_semantic_model_missing_name() -> None:
    with pytest.raises(ValidationError):
        OssieSemanticModel(
            datasets=[OssieDataset(name="orders", source="database.schema.orders")]
        )


def test_semantic_model_missing_datasets() -> None:
    with pytest.raises(ValidationError):
        OssieSemanticModel(name="sales_model")


def test_custom_extension_missing_vendor_name() -> None:
    with pytest.raises(ValidationError):
        OssieCustomExtension(data="{}")


def test_custom_extension_missing_data() -> None:
    with pytest.raises(ValidationError):
        OssieCustomExtension(vendor_name="ASF")


def test_dialect_expression_missing_dialect() -> None:
    with pytest.raises(ValidationError):
        OssieDialectExpression(expression="order_id")


def test_dialect_expression_missing_expression() -> None:
    with pytest.raises(ValidationError):
        OssieDialectExpression(dialect=OssieDialect.ANSI_SQL)


def test_expression_missing_dialects() -> None:
    with pytest.raises(ValidationError):
        OssieExpression()


def test_invalid_dialect_in_expression() -> None:
    with pytest.raises(ValidationError):
        OssieDialectExpression(dialect="INVALID", expression="order_id")


# ---------------------------------------------------------------------------
# Frozen models
# ---------------------------------------------------------------------------


def test_frozen_dataset() -> None:
    dataset = OssieDataset(name="orders", source="database.schema.orders")
    with pytest.raises(ValidationError):
        dataset.name = "other"


def test_frozen_field(make_expression: Callable[[str], OssieExpression]) -> None:
    field = OssieField(
        name="order_id",
        expression=make_expression("order_id"),
    )
    with pytest.raises(ValidationError):
        field.name = "other"


def test_frozen_document(document_data: dict) -> None:
    document = OssieDocument.model_validate(document_data)
    with pytest.raises(ValidationError):
        document.version = "new.version"


def test_frozen_relationship() -> None:
    relationship = OssieRelationship(
        name="order_customer",
        **{"from": "orders"},
        to="customers",
        from_columns=["customer_id"],
        to_columns=["id"],
    )
    with pytest.raises(ValidationError):
        relationship.name = "other"


def test_frozen_ai_context_object() -> None:
    ai_ctx = OssieAIContextObject(instructions="Use this field for filtering")
    with pytest.raises(ValidationError):
        ai_ctx.instructions = "Updated instructions"


def test_frozen_custom_extension() -> None:
    custom_ext = OssieCustomExtension(vendor_name="ASF", data="{}")
    with pytest.raises(ValidationError):
        custom_ext.vendor_name = "other"


def test_frozen_dialect_expression() -> None:
    dialect_expr = OssieDialectExpression(
        dialect=OssieDialect.ANSI_SQL, expression="order_id"
    )
    with pytest.raises(ValidationError):
        dialect_expr.expression = "other_column"


def test_frozen_expression(make_expression: Callable[[str], OssieExpression]) -> None:
    expr = make_expression("order_id")
    with pytest.raises(ValidationError):
        expr.dialects = []


def test_frozen_dimension() -> None:
    dim = OssieDimension(is_time=True)
    with pytest.raises(ValidationError):
        dim.is_time = False


def test_frozen_metric(make_expression: Callable[[str], OssieExpression]) -> None:
    expr = make_expression("order_id")
    m = OssieMetric(name="total_sales", expression=expr)
    with pytest.raises(ValidationError):
        m.name = "other"


def test_frozen_semantic_model() -> None:
    semantic_model = OssieSemanticModel(
        name="sales_model",
        datasets=[OssieDataset(name="orders", source="database.schema.orders")],
    )
    with pytest.raises(ValidationError):
        semantic_model.name = "other"
