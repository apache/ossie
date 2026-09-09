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
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieFileSource,
    OssieField,
    OssieSQLQuerySource,
    OssieTableSource,
)


def _expression_data(value: str = "value") -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": value}]}


def _expression(value: str = "value") -> OssieExpression:
    return OssieExpression.model_validate(_expression_data(value))


def _document() -> dict:
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
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
        ],
    }


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


def test_invalid_datatype_is_rejected() -> None:
    document = _document()
    field = document["semantic_model"][0]["datasets"][0]["fields"][0]
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


def test_legacy_dataset_source_remains_a_string() -> None:
    document = OssieDocument.model_validate(_document())
    assert document.semantic_model[0].datasets[0].source == "catalog.schema.events"


@pytest.mark.parametrize(
    ("source_data", "source_type"),
    [
        (
            {
                "kind": "file",
                "format": "parquet",
                "locations": ["s3://analytics/events/*.parquet"],
            },
            OssieFileSource,
        ),
        (
            {
                "kind": "HIVE_CATALOG",
                "format": "table",
                "identifier": "analytics.sales.orders",
            },
            OssieTableSource,
        ),
        (
            {
                "kind": "SNOWFLAKE_CATALOG",
                "format": "SQL_QUERY",
                "query": "SELECT * FROM analytics.sales.orders WHERE order_total > 10",
            },
            OssieSQLQuerySource,
        ),
    ],
)
def test_structured_dataset_sources_survive_serialization(
    source_data: dict, source_type: type
) -> None:
    data = _document()
    data["semantic_model"][0]["datasets"][0]["source"] = source_data

    document = OssieDocument.model_validate(data)
    source = document.semantic_model[0].datasets[0].source
    assert isinstance(source, source_type)

    for serialized in (
        json.loads(document.to_ossie_json()),
        yaml.safe_load(document.to_ossie_yaml()),
    ):
        assert serialized["semantic_model"][0]["datasets"][0]["source"] == source_data


def test_catalog_kind_is_open_ended() -> None:
    data = _document()
    data["semantic_model"][0]["datasets"][0]["source"] = {
        "kind": "POLARIS_CATALOG",
        "format": "table",
        "identifier": "analytics.sales.orders",
    }

    document = OssieDocument.model_validate(data)
    source = document.semantic_model[0].datasets[0].source
    assert isinstance(source, OssieTableSource)
    assert source.kind == "POLARIS_CATALOG"


def test_structured_source_definitions_match_core_schema() -> None:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    schema = json.loads(schema_path.read_text())

    assert schema["$defs"]["Source"]["oneOf"] == [
        {"type": "string"},
        {"$ref": "#/$defs/FileSource"},
        {"$ref": "#/$defs/TableSource"},
        {"$ref": "#/$defs/SQLQuerySource"},
    ]
    assert schema["$defs"]["FileSource"]["required"] == ["kind", "format", "locations"]
    assert schema["$defs"]["TableSource"]["required"] == ["kind", "format", "identifier"]
    assert schema["$defs"]["SQLQuerySource"]["required"] == ["kind", "format", "query"]


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "file", "format": "parquet", "locations": []},
        {"kind": "file", "format": "", "locations": ["s3://bucket/events.parquet"]},
        {"kind": "file", "format": "parquet", "locations": [""]},
        {"kind": "HIVE_CATALOG", "format": "table", "identifier": ""},
        {"kind": "HIVE_CATALOG", "format": "table", "query": "SELECT 1"},
        {"kind": "SNOWFLAKE_CATALOG", "format": "SQL_QUERY", "query": ""},
        {"kind": "SNOWFLAKE_CATALOG", "format": "SQL_QUERY", "identifier": "db.s.t"},
        {"kind": "", "format": "table", "identifier": "db.s.t"},
        {"kind": "HIVE_CATALOG", "format": "view", "identifier": "db.s.v"},
        {
            "kind": "file",
            "format": "parquet",
            "locations": ["s3://bucket/events.parquet"],
            "unknown": True,
        },
    ],
)
def test_invalid_structured_dataset_source_is_rejected(source: dict) -> None:
    data = _document()
    data["semantic_model"][0]["datasets"][0]["source"] = source

    with pytest.raises(ValidationError):
        OssieDocument.model_validate(data)
