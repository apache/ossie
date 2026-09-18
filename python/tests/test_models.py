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
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ossie import (
    OssieAIContextObject,
    OssieDataType,
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieRelationship,
)


# ---------------------------------------------------------------------------
# Data type tests
# ---------------------------------------------------------------------------


def test_data_type_enum_matches_core_schema() -> None:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    schema = json.loads(schema_path.read_text())

    assert [member.value for member in OssieDataType] == schema["$defs"]["DataType"][
        "enum"
    ]
    assert schema["$defs"]["Field"]["properties"]["datatype"] == {
        "$ref": "#/$defs/DataType"
    }
    assert schema["$defs"]["Metric"]["properties"]["datatype"] == {
        "$ref": "#/$defs/DataType"
    }


def test_field_and_metric_datatypes_survive_serialization(document_data: dict) -> None:
    document = OssieDocument.model_validate(document_data)
    field = document.semantic_model[0].datasets[0].fields[0]
    metric = document.semantic_model[0].metrics[0]
    assert field.datatype is OssieDataType.DATE_TIME_TZ
    assert metric.datatype is OssieDataType.DECIMAL

    as_json = json.loads(document.to_ossie_json())
    as_yaml = yaml.safe_load(document.to_ossie_yaml())
    for serialized in (as_json, as_yaml):
        model = serialized["semantic_model"][0]
        assert model["datasets"][0]["fields"][0]["datatype"] == "DateTimeTz"
        assert model["metrics"][0]["datatype"] == "Decimal"


def test_invalid_datatype_is_rejected(document_data: dict) -> None:
    field = document_data["semantic_model"][0]["datasets"][0]["fields"][0]
    field["datatype"] = "timestamp"

    with pytest.raises(ValidationError):
        OssieDocument.model_validate(document_data)


@pytest.mark.parametrize(
    ("dimension", "datatype", "expected"),
    [
        (None, OssieDataType.DATE, False),
        (OssieDimension(), OssieDataType.DATE, True),
        (OssieDimension(is_time=False), OssieDataType.DATE_TIME_TZ, False),
        (OssieDimension(is_time=True), OssieDataType.STRING, True),
        (OssieDimension(), OssieDataType.STRING, False),
        (OssieDimension(), None, False),
    ],
)
def test_effective_time_dimension_role(
    make_expression: Callable[[str], OssieExpression],
    dimension: OssieDimension | None,
    datatype: OssieDataType | None,
    expected: bool,
) -> None:
    field = OssieField(
        name="value",
        expression=make_expression(),
        dimension=dimension,
        datatype=datatype,
    )

    assert field.is_time_dimension() is expected


# ---------------------------------------------------------------------------
# Model behavior
# ---------------------------------------------------------------------------


def test_ai_context_object_allows_extra() -> None:
    ai_ctx = OssieAIContextObject(custom_field="custom_value")
    assert ai_ctx.custom_field == "custom_value"


def test_ai_context_accepts_string(document_data: dict) -> None:
    document_data["semantic_model"][0]["ai_context"] = "Plain text context"
    document = OssieDocument.model_validate(document_data)
    assert document.semantic_model[0].ai_context == "Plain text context"


def test_ai_context_accepts_object(document_data: dict) -> None:
    document_data["semantic_model"][0]["ai_context"] = {
        "instructions": "Use this model for analytics"
    }
    document = OssieDocument.model_validate(document_data)
    ai_ctx = document.semantic_model[0].ai_context
    assert isinstance(ai_ctx, OssieAIContextObject)
    assert ai_ctx.instructions == "Use this model for analytics"


def test_relationship_with_alias() -> None:
    relationship = OssieRelationship(
        name="order_customer",
        **{"from": "orders"},
        to="customers",
        from_columns=["customer_id"],
        to_columns=["id"],
    )
    assert relationship.from_dataset == "orders"
    assert relationship.to == "customers"


def test_relationship_with_python_name() -> None:
    relationship = OssieRelationship(
        name="order_customer",
        from_dataset="orders",
        to="customers",
        from_columns=["customer_id"],
        to_columns=["id"],
    )
    assert relationship.from_dataset == "orders"
