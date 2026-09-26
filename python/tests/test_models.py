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
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ossie import (
    OssieDataType,
    OssieDialect,
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieSemanticModel,
)


def _expression_data(value: str = "value") -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": value}]}


def _expression(value: str = "value") -> OssieExpression:
    return OssieExpression.model_validate(_expression_data(value))


def _document() -> dict:
    return {
        "version": "0.2.0.dev0",
        "name": "typed_model",
        "datasets": [
            {
                "name": "events",
                "source": "catalog.schema.events",
                "fields": [
                    {
                        "name": "occurred_at",
                        "expression": _expression_data("occurred_at"),
                        "dimension": {},
                        "datatype": "DateTimeTz",
                    }
                ],
            }
        ],
        "metrics": [
            {
                "name": "revenue",
                "expression": _expression_data("SUM(events.revenue)"),
                "datatype": "Decimal",
            }
        ],
    }


def test_dialect_enum_matches_core_schema() -> None:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    schema = json.loads(schema_path.read_text())

    assert {member.value for member in OssieDialect} == set(
        schema["$defs"]["Dialect"]["enum"]
    )


def test_ossie_sql_2026_dialect_survives_serialization() -> None:
    data = _document()
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


def test_field_and_metric_datatypes_survive_serialization() -> None:
    document = OssieDocument.model_validate(_document())

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


def test_document_serialization_preserves_flat_model_and_metadata() -> None:
    data = _document()
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
    "serialize", ["model_dump", "model_dump_json", "to_ossie_yaml", "to_ossie_json"]
)
def test_document_serialization_puts_version_first(serialize: str) -> None:
    data = _document()
    document = OssieDocument.model_validate(data)

    serialized = getattr(document, serialize)()
    if isinstance(serialized, str):
        serialized = yaml.safe_load(serialized)

    assert next(iter(serialized)) == "version"
    assert serialized["version"] == data["version"]


@pytest.mark.parametrize(
    "serialize", ["model_dump", "model_dump_json", "to_ossie_yaml", "to_ossie_json"]
)
@pytest.mark.parametrize(
    "options",
    [
        {"exclude": {"version"}},
        {"include": {"name", "datasets"}},
        {"exclude_defaults": True},
        {"exclude_unset": True},
    ],
)
def test_document_serialization_can_omit_version(serialize: str, options: dict) -> None:
    data = _document()
    del data["version"]
    document = OssieDocument.model_validate(data)

    serialized = getattr(document, serialize)(**options)
    if isinstance(serialized, str):
        serialized = yaml.safe_load(serialized)

    assert "version" not in serialized
    assert serialized["name"] == data["name"]


def test_document_serialization_schema_preserves_model_fields() -> None:
    schema = OssieDocument.model_json_schema(mode="serialization")

    assert schema["properties"]["version"]["type"] == "string"
    assert schema["properties"]["datasets"]["type"] == "array"
    # Early Pydantic 2.x versions also require defaulted fields in serialization schemas.
    assert {"name", "datasets"} <= set(schema["required"])
    assert schema["additionalProperties"] is False


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
def test_document_rejects_legacy_wrapper(legacy_value: object, include_root_model: bool) -> None:
    data = _document() if include_root_model else {"version": "0.2.0.dev0"}
    data["semantic_model"] = legacy_value

    with pytest.raises(ValidationError) as error:
        OssieDocument.model_validate(data)

    assert any(
        item["loc"] == ("semantic_model",) and item["type"] == "extra_forbidden"
        for item in error.value.errors()
    )


@pytest.mark.parametrize("property_name", ["name", "datasets"])
def test_document_requires_root_model_properties(property_name: str) -> None:
    data = _document()
    del data[property_name]

    with pytest.raises(ValidationError):
        OssieDocument.model_validate(data)


@pytest.mark.parametrize("property_name", ["dialects", "vendors"])
def test_document_rejects_removed_root_metadata(property_name: str) -> None:
    data = _document()
    data[property_name] = []
    with pytest.raises(ValidationError):
        OssieDocument.model_validate(data)


def test_embedded_semantic_model_has_no_document_metadata() -> None:
    data = _document()
    del data["version"]

    embedded = OssieSemanticModel.model_validate(data)

    assert embedded.model_dump(by_alias=True, exclude_none=True, mode="json") == data


def test_invalid_datatype_is_rejected() -> None:
    document = _document()
    field = document["datasets"][0]["fields"][0]
    field["datatype"] = "timestamp"

    with pytest.raises(ValidationError):
        OssieDocument.model_validate(document)


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
    dimension: OssieDimension | None,
    datatype: OssieDataType | None,
    expected: bool,
) -> None:
    field = OssieField(
        name="value",
        expression=_expression(),
        dimension=dimension,
        datatype=datatype,
    )

    assert field.is_time_dimension() is expected
