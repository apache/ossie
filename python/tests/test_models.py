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
    OssieField,
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


def test_document_ai_context_matches_core_schema() -> None:
    schema_path = Path(__file__).parents[2] / "core-spec" / "ossie-schema.json"
    schema = json.loads(schema_path.read_text())

    assert schema["properties"]["ai_context"]["$ref"] == "#/$defs/AIContext"

    # Document-wide context is optional, so existing documents keep validating
    # unchanged, and the root stays closed to anything else.
    assert "ai_context" not in schema["required"]
    assert schema["additionalProperties"] is False


def test_document_ai_context_agrees_with_the_ontology_root() -> None:
    """Both document roots resolve ai_context against the same definition.

    The ontology specification already puts ai_context on its root and $refs this
    specification's AIContext. If that reference is ever repointed at a different
    definition, the two document types would silently disagree about what
    document-wide context means.
    """
    core = json.loads(
        (Path(__file__).parents[2] / "core-spec" / "ossie-schema.json").read_text()
    )
    ontology = json.loads(
        (Path(__file__).parents[2] / "ontology" / "ontology.json").read_text()
    )

    assert ontology["properties"]["ai_context"]["$ref"].endswith(
        "ossie-schema.json#/$defs/AIContext"
    )
    assert core["properties"]["ai_context"]["$ref"] == "#/$defs/AIContext"


def test_document_ai_context_survives_serialization() -> None:
    """Both forms of AIContext round-trip, and an absent one does not serialize."""
    document = _document()
    document["ai_context"] = {
        "instructions": "Fiscal year starts in July.",
        "synonyms": ["revenue = net_sales"],
    }

    parsed = OssieDocument.model_validate(document)
    assert parsed.ai_context.instructions == "Fiscal year starts in July."
    for serialized in (
        json.loads(parsed.to_ossie_json()),
        yaml.safe_load(parsed.to_ossie_yaml()),
    ):
        assert serialized["ai_context"]["instructions"] == "Fiscal year starts in July."

    document["ai_context"] = "Fiscal year starts in July."
    assert OssieDocument.model_validate(document).ai_context == "Fiscal year starts in July."

    absent = OssieDocument.model_validate(_document())
    assert absent.ai_context is None
    assert "ai_context" not in yaml.safe_load(absent.to_ossie_yaml())
    assert "ai_context" not in json.loads(absent.to_ossie_json())


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
