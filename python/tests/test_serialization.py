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

import yaml

from ossie import (
    OssieDialect,
    OssieDocument,
)


def test_to_ossie_yaml_uses_alias(document_data: dict) -> None:
    document_data["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OssieDocument.model_validate(document_data)
    output = document.to_ossie_yaml()
    assert "from: orders" in output
    assert "from_dataset" not in output


def test_to_ossie_json_uses_alias(document_data: dict) -> None:
    document_data["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OssieDocument.model_validate(document_data)
    output = document.to_ossie_json()
    parsed = json.loads(output)
    assert parsed["relationships"][0]["from"] == "orders"


def test_to_ossie_yaml_includes_all_dialects(document_data: dict) -> None:
    document_data["datasets"][0]["fields"][0]["expression"] = {
        "dialects": [
            {"dialect": OssieDialect.ANSI_SQL, "expression": "occurred_at"},
            {"dialect": OssieDialect.SNOWFLAKE, "expression": "OCCURRED_AT"},
        ]
    }
    document = OssieDocument.model_validate(document_data)
    parsed = yaml.safe_load(document.to_ossie_yaml())
    assert parsed["datasets"][0]["fields"][0]["expression"]["dialects"] == [
        {"dialect": "ANSI_SQL", "expression": "occurred_at"},
        {"dialect": "SNOWFLAKE", "expression": "OCCURRED_AT"},
    ]


def test_to_ossie_yaml_includes_custom_extension_vendor(document_data: dict) -> None:
    document_data["custom_extensions"] = [
        {"vendor_name": "DATABRICKS", "data": '{"id":"model-1"}'}
    ]
    document = OssieDocument.model_validate(document_data)
    parsed = yaml.safe_load(document.to_ossie_yaml())
    assert parsed["custom_extensions"] == [
        {"vendor_name": "DATABRICKS", "data": '{"id":"model-1"}'}
    ]


def test_to_ossie_yaml_excludes_none(document_data: dict) -> None:
    document = OssieDocument.model_validate(document_data)
    output = document.to_ossie_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["name"] == "typed_model"
    assert "description" not in parsed
    assert "relationships" not in parsed
    assert "ai_context" not in parsed


def test_to_ossie_json_excludes_none(document_data: dict) -> None:
    document = OssieDocument.model_validate(document_data)
    output = document.to_ossie_json()
    parsed = json.loads(output)
    assert parsed["name"] == "typed_model"
    assert "description" not in parsed
    assert "relationships" not in parsed
    assert "ai_context" not in parsed


def test_to_ossie_yaml_validates_as_yaml(document_data: dict) -> None:
    document = OssieDocument.model_validate(document_data)
    output = document.to_ossie_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["name"] == "typed_model"


def test_to_ossie_json_roundtrip(document_data: dict) -> None:
    document_data["datasets"][0]["fields"][0]["expression"] = {
        "dialects": [{"dialect": OssieDialect.DATABRICKS, "expression": "order_id"}]
    }
    document = OssieDocument.model_validate(document_data)
    json_str = document.to_ossie_json()
    parsed = json.loads(json_str)
    document2 = OssieDocument(**parsed)
    assert document2.to_ossie_json() == json_str


def test_to_ossie_yaml_accepts_by_alias_override(document_data: dict) -> None:
    document_data["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OssieDocument.model_validate(document_data)
    parsed = yaml.safe_load(document.to_ossie_yaml(by_alias=False))
    assert parsed["relationships"][0]["from_dataset"] == "orders"
    assert "from" not in parsed["relationships"][0]


def test_to_ossie_json_accepts_by_alias_override(document_data: dict) -> None:
    document_data["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OssieDocument.model_validate(document_data)
    parsed = json.loads(document.to_ossie_json(by_alias=False))
    assert parsed["relationships"][0]["from_dataset"] == "orders"
    assert "from" not in parsed["relationships"][0]


def test_to_ossie_yaml_accepts_exclude_none_override(document_data: dict) -> None:
    document = OssieDocument.model_validate(document_data)
    parsed = yaml.safe_load(document.to_ossie_yaml(exclude_none=False))
    assert parsed["description"] is None
    assert parsed["relationships"] is None


def test_to_ossie_json_accepts_exclude_none_override(document_data: dict) -> None:
    document = OssieDocument.model_validate(document_data)
    parsed = json.loads(document.to_ossie_json(exclude_none=False))
    assert parsed["description"] is None
    assert parsed["relationships"] is None
