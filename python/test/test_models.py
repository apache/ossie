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

import json

import pytest
import yaml
from pydantic import ValidationError

from ossie.models import (
    OSIAIContextObject,
    OSICustomExtension,
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDimension,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIMetric,
    OSIRelationship,
    OSISemanticModel,
    OSIVendor,
)


# ---------------------------------------------------------------------------
# Model specific behavior
# ---------------------------------------------------------------------------


def test_ai_context_object_allows_extra():
    ai_ctx = OSIAIContextObject(custom_field="custom_value")
    assert ai_ctx.custom_field == "custom_value"


def test_ai_context_accepts_string():
    doc = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
                ai_context="Plain text context",
            )
        ]
    )
    assert doc.semantic_model[0].ai_context == "Plain text context"


def test_ai_context_accepts_object():
    doc = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
                ai_context={"instructions": "Use this model for analytics"},
            )
        ]
    )
    ai_ctx = doc.semantic_model[0].ai_context
    assert isinstance(ai_ctx, OSIAIContextObject)
    assert ai_ctx.instructions == "Use this model for analytics"


def test_relationship_with_alias():
    relationship = OSIRelationship(
        name="order_customer",
        **{"from": "orders"},
        to="customers",
        from_columns=["customer_id"],
        to_columns=["id"],
    )
    assert relationship.from_dataset == "orders"
    assert relationship.to == "customers"


def test_relationship_with_python_name():
    relationship = OSIRelationship(
        name="order_customer",
        from_dataset="orders",
        to="customers",
        from_columns=["customer_id"],
        to_columns=["id"],
    )
    assert relationship.from_dataset == "orders"


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


def test_dataset_missing_required():
    with pytest.raises(ValidationError):
        OSIDataset()


def test_field_missing_name():
    with pytest.raises(ValidationError):
        OSIField(
            expression=OSIExpression(
                dialects=[OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="order_id")]
            )
        )


def test_field_missing_expression():
    with pytest.raises(ValidationError):
        OSIField(name="order_id")


def test_relationship_missing_name():
    with pytest.raises(ValidationError):
        OSIRelationship(**{"from": "orders"}, to="customers", from_columns=["customer_id"], to_columns=["id"])


def test_relationship_missing_from():
    with pytest.raises(ValidationError):
        OSIRelationship(name="order_customer", to="customers", from_columns=["customer_id"], to_columns=["id"])


def test_relationship_missing_to():
    with pytest.raises(ValidationError):
        OSIRelationship(
            name="order_customer", **{"from": "orders"}, from_columns=["customer_id"], to_columns=["id"]
        )


def test_relationship_missing_from_columns():
    with pytest.raises(ValidationError):
        OSIRelationship(name="order_customer", **{"from": "orders"}, to="customers", to_columns=["id"])


def test_relationship_missing_to_columns():
    with pytest.raises(ValidationError):
        OSIRelationship(name="order_customer", **{"from": "orders"}, to="customers", from_columns=["customer_id"])


def test_metric_missing_name():
    with pytest.raises(ValidationError):
        OSIMetric(
            expression=OSIExpression(
                dialects=[OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="order_id")]
            )
        )


def test_metric_missing_expression():
    with pytest.raises(ValidationError):
        OSIMetric(name="total_sales")


def test_document_missing_semantic_model():
    with pytest.raises(ValidationError):
        OSIDocument()


def test_semantic_model_missing_name():
    with pytest.raises(ValidationError):
        OSISemanticModel(datasets=[OSIDataset(name="orders", source="database.schema.orders")])


def test_semantic_model_missing_datasets():
    with pytest.raises(ValidationError):
        OSISemanticModel(name="sales_model")


def test_custom_extension_missing_vendor_name():
    with pytest.raises(ValidationError):
        OSICustomExtension(data="{}")


def test_custom_extension_missing_data():
    with pytest.raises(ValidationError):
        OSICustomExtension(vendor_name="ASF")


def test_dialect_expression_missing_dialect():
    with pytest.raises(ValidationError):
        OSIDialectExpression(expression="order_id")


def test_dialect_expression_missing_expression():
    with pytest.raises(ValidationError):
        OSIDialectExpression(dialect=OSIDialect.ANSI_SQL)


def test_expression_missing_dialects():
    with pytest.raises(ValidationError):
        OSIExpression()


def test_invalid_dialect_in_expression():
    with pytest.raises(ValidationError):
        OSIDialectExpression(dialect="INVALID", expression="order_id")


# ---------------------------------------------------------------------------
# Frozen models
# ---------------------------------------------------------------------------


def test_frozen_dataset():
    dataset = OSIDataset(name="orders", source="database.schema.orders")
    with pytest.raises(ValidationError):
        dataset.name = "other"


def test_frozen_field():
    field = OSIField(
        name="order_id",
        expression=OSIExpression(
            dialects=[OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="order_id")]
        ),
    )
    with pytest.raises(ValidationError):
        field.name = "other"


def test_frozen_document():
    doc = OSIDocument(
        semantic_model=[OSISemanticModel(name="sales_model", datasets=[OSIDataset(name="orders", source="database.schema.orders")])]
    )
    with pytest.raises(ValidationError):
        doc.version = "new.version"


def test_frozen_relationship():
    relationship = OSIRelationship(
        name="order_customer",
        **{"from": "orders"},
        to="customers",
        from_columns=["customer_id"],
        to_columns=["id"],
    )
    with pytest.raises(ValidationError):
        relationship.name = "other"


def test_frozen_ai_context_object():
    ai_ctx = OSIAIContextObject(instructions="Use this field for filtering")
    with pytest.raises(ValidationError):
        ai_ctx.instructions = "Updated instructions"


def test_frozen_custom_extension():
    custom_ext = OSICustomExtension(vendor_name="ASF", data="{}")
    with pytest.raises(ValidationError):
        custom_ext.vendor_name = "other"


def test_frozen_dialect_expression():
    dialect_expr = OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="order_id")
    with pytest.raises(ValidationError):
        dialect_expr.expression = "other_column"


def test_frozen_expression():
    expr = OSIExpression(
        dialects=[OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="order_id")]
    )
    with pytest.raises(ValidationError):
        expr.dialects = []


def test_frozen_dimension():
    dim = OSIDimension(is_time=True)
    with pytest.raises(ValidationError):
        dim.is_time = False


def test_frozen_metric():
    expr = OSIExpression(
        dialects=[OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression="order_id")]
    )
    m = OSIMetric(name="total_sales", expression=expr)
    with pytest.raises(ValidationError):
        m.name = "other"


def test_frozen_semantic_model():
    semantic_model = OSISemanticModel(name="sales_model", datasets=[OSIDataset(name="orders", source="database.schema.orders")])
    with pytest.raises(ValidationError):
        semantic_model.name = "other"


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_to_osi_yaml_uses_alias():
    doc = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
                relationships=[
                    OSIRelationship(
                        name="order_customer",
                        **{"from": "orders"},
                        to="customers",
                        from_columns=["customer_id"],
                        to_columns=["id"],
                    )
                ],
            )
        ]
    )
    output = doc.to_osi_yaml()
    assert "from: orders" in output
    assert "from_dataset" not in output


def test_to_osi_yaml_includes_dialects_and_vendors():
    doc = OSIDocument(
        dialects=[OSIDialect.ANSI_SQL, OSIDialect.DATABRICKS],
        vendors=[OSIVendor.DATABRICKS],
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
            )
        ]
    )
    output = doc.to_osi_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["dialects"] == ["ANSI_SQL", "DATABRICKS"]
    assert parsed["vendors"] == ["DATABRICKS"]


def test_to_osi_json_includes_dialects_and_vendors():
    doc = OSIDocument(
        dialects=[OSIDialect.SNOWFLAKE],
        vendors=[OSIVendor.SALESFORCE, OSIVendor.COMMON],
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
            )
        ]
    )
    output = doc.to_osi_json()
    parsed = json.loads(output)
    assert parsed["dialects"] == ["SNOWFLAKE"]
    assert parsed["vendors"] == ["SALESFORCE", "COMMON"]


def test_to_osi_yaml_excludes_none():
    doc = OSIDocument(
        dialects=[OSIDialect.ANSI_SQL],
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
            )
        ]
    )
    output = doc.to_osi_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["dialects"] == ["ANSI_SQL"]
    assert "vendors" not in parsed
    semantic_model = parsed["semantic_model"][0]
    assert semantic_model["name"] == "sales_model"
    assert "description" not in semantic_model


def test_to_osi_json_uses_alias():
    doc = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
                relationships=[
                    OSIRelationship(
                        name="order_customer",
                        **{"from": "orders"},
                        to="customers",
                        from_columns=["customer_id"],
                        to_columns=["id"],
                    )
                ],
            )
        ]
    )
    output = doc.to_osi_json()
    parsed = json.loads(output)
    assert parsed["semantic_model"][0]["relationships"][0]["from"] == "orders"


def test_to_osi_json_excludes_none():
    doc = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
            )
        ]
    )
    output = doc.to_osi_json()
    parsed = json.loads(output)
    semantic_model = parsed["semantic_model"][0]
    assert "description" not in semantic_model
    assert "relationships" not in semantic_model
    assert "metrics" not in semantic_model


def test_to_osi_yaml_validates_as_yaml():
    doc = OSIDocument(
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[OSIDataset(name="orders", source="database.schema.orders")],
            )
        ]
    )
    out = doc.to_osi_yaml()
    parsed = yaml.safe_load(out)
    assert parsed["semantic_model"][0]["name"] == "sales_model"


def test_to_osi_json_roundtrip():
    doc = OSIDocument(
        dialects=[OSIDialect.DATABRICKS],
        semantic_model=[
            OSISemanticModel(
                name="sales_model",
                datasets=[
                    OSIDataset(
                        name="orders",
                        source="database.schema.orders",
                        fields=[
                            OSIField(
                                name="order_id",
                                expression=OSIExpression(
                                    dialects=[
                                        OSIDialectExpression(
                                            dialect=OSIDialect.DATABRICKS, expression="order_id"
                                        )
                                    ]
                                ),
                            )
                        ],
                    )
                ],
            )
        ]
    )
    json_str = doc.to_osi_json()
    parsed = json.loads(json_str)
    doc2 = OSIDocument(**parsed)
    assert doc2.to_osi_json() == json_str
