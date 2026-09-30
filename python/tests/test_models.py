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
    OssieDialect,
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieRelationship,
    OssieSemanticModel,
)


# ---------------------------------------------------------------------------
# Data type tests
# ---------------------------------------------------------------------------


def test_dialect_enum_matches_core_schema() -> None:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    schema = json.loads(schema_path.read_text())

    assert {member.value for member in OssieDialect} == set(
        schema["$defs"]["Dialect"]["enum"]
    )


@pytest.mark.parametrize(
    "dialect",
    ["ANSI_SQL", OssieDialect.ANSI_SQL],
    ids=["string", "enum"],
)
def test_dialect_accepts_string_and_enum_input(dialect: str | OssieDialect) -> None:
    expression = OssieExpression.model_validate(
        {"dialects": [{"dialect": dialect, "expression": "value"}]}
    )

    assert expression.dialects[0].dialect is OssieDialect.ANSI_SQL


def test_dialect_rejects_unknown_string() -> None:
    with pytest.raises(ValidationError):
        OssieExpression.model_validate(
            {"dialects": [{"dialect": "NOT_A_DIALECT", "expression": "value"}]}
        )


def test_ossie_sql_2026_dialect_survives_serialization(document_data: dict) -> None:
    data = document_data
    field = data["datasets"][0]["fields"][0]
    metric = data["metrics"][0]
    for item in (field, metric):
        item["expression"]["dialects"][0]["dialect"] = "OSSIE_SQL_2026"

    document = OssieDocument.model_validate(data)

    for item in (document.datasets[0].fields[0], document.metrics[0]):
        assert item.expression.dialects[0].dialect is OssieDialect.OSSIE_SQL_2026

    as_json = json.loads(document.to_ossie_json())
    as_yaml = yaml.safe_load(document.to_ossie_yaml())
    for serialized in (as_json, as_yaml):
        assert serialized == data
        assert OssieDocument.model_validate(serialized) == document


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
    field = document.datasets[0].fields[0]
    metric = document.metrics[0]

    assert field.datatype is OssieDataType.DATE_TIME_TZ
    assert metric.datatype is OssieDataType.DECIMAL

    as_json = json.loads(document.to_ossie_json())
    as_yaml = yaml.safe_load(document.to_ossie_yaml())
    for serialized in (as_json, as_yaml):
        model = serialized
        assert model["datasets"][0]["fields"][0]["datatype"] == "DateTimeTz"
        assert model["metrics"][0]["datatype"] == "Decimal"


def test_document_serialization_preserves_flat_model_and_metadata(
    document_data: dict,
) -> None:
    data = document_data
    data.update(
        description="A portable model",
        ai_context="Use the event timestamp",
        custom_extensions=[{"vendor_name": "SIGMA", "data": '{"id":"model-1"}'}],
        relationships=[
            {
                "name": "event_link",
                "from": "events",
                "to": "events",
                "from_columns": ["id"],
                "to_columns": ["id"],
            }
        ],
    )
    document = OssieDocument.model_validate(data)

    for serialized in (json.loads(document.to_ossie_json()), yaml.safe_load(document.to_ossie_yaml())):
        assert serialized == data
        assert "semantic_model" not in serialized
        assert OssieDocument.model_validate(serialized) == document


@pytest.mark.parametrize(
    "legacy_value",
    [
        None,
        [],
        {"name": "legacy", "datasets": []},
        [{"name": "legacy", "datasets": []}],
        [{"name": "first", "datasets": []}, {"name": "second", "datasets": []}],
    ],
)
@pytest.mark.parametrize("include_root_model", [False, True])
def test_document_rejects_legacy_wrapper(
    document_data: dict, legacy_value: object, include_root_model: bool
) -> None:
    data = document_data if include_root_model else {"version": "0.2.0.dev0"}
    data["semantic_model"] = legacy_value

    with pytest.raises(ValidationError) as error:
        OssieDocument.model_validate(data)

    assert any(
        item["loc"] == ("semantic_model",) and item["type"] == "extra_forbidden"
        for item in error.value.errors()
    )


@pytest.mark.parametrize("property_name", ["name", "datasets"])
def test_document_requires_root_model_properties(
    document_data: dict, property_name: str
) -> None:
    data = document_data
    del data[property_name]

    with pytest.raises(ValidationError):
        OssieDocument.model_validate(data)


@pytest.mark.parametrize("property_name", ["dialects", "vendors"])
def test_document_rejects_removed_root_metadata(
    document_data: dict, property_name: str
) -> None:
    data = document_data
    data[property_name] = []
    with pytest.raises(ValidationError):
        OssieDocument.model_validate(data)


def test_embedded_semantic_model_has_no_document_metadata(document_data: dict) -> None:
    data = document_data
    del data["version"]

    embedded = OssieSemanticModel.model_validate(data)

    assert embedded.model_dump(by_alias=True, exclude_none=True, mode="json") == data


def test_document_defaults_version_when_omitted(document_data: dict) -> None:
    del document_data["version"]

    document = OssieDocument.model_validate(document_data)

    assert document.version == "0.2.0.dev0"
    assert json.loads(document.to_ossie_json())["version"] == "0.2.0.dev0"


def test_document_is_a_semantic_model(document_data: dict) -> None:
    document = OssieDocument.model_validate(document_data)

    assert isinstance(document, OssieSemanticModel)
    semantic_model = OssieSemanticModel.model_validate(document_data)
    assert document.model_dump(exclude={"version"}) == semantic_model.model_dump()


def test_only_document_rejects_extra_fields(document_data: dict) -> None:
    document_data["vendor_extension"] = "unknown"

    embedded = OssieSemanticModel.model_validate(document_data)
    assert not hasattr(embedded, "vendor_extension")
    assert "vendor_extension" not in embedded.model_dump()

    with pytest.raises(ValidationError) as error:
        OssieDocument.model_validate(document_data)

    assert any(
        item["loc"] == ("vendor_extension",) and item["type"] == "extra_forbidden"
        for item in error.value.errors()
    )


def test_invalid_datatype_is_rejected(document_data: dict) -> None:
    field = document_data["datasets"][0]["fields"][0]
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
    document_data["ai_context"] = "Plain text context"
    document = OssieDocument.model_validate(document_data)
    assert document.ai_context == "Plain text context"


def test_ai_context_accepts_object(document_data: dict) -> None:
    document_data["ai_context"] = {
        "instructions": "Use this model for analytics"
    }
    document = OssieDocument.model_validate(document_data)
    ai_ctx = document.ai_context
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


def test_expression_dialects_min_length() -> None:
    with pytest.raises(ValidationError):
        OssieExpression(dialects=[])


def test_relationship_columns_min_length() -> None:
    with pytest.raises(ValidationError):
        OssieRelationship(name="rel", from_dataset="a", to="b", from_columns=[], to_columns=["id"])
    with pytest.raises(ValidationError):
        OssieRelationship(name="rel", from_dataset="a", to="b", from_columns=["id"], to_columns=[])


def test_semantic_model_datasets_min_length() -> None:
    with pytest.raises(ValidationError):
        OssieSemanticModel(name="model", datasets=[])
