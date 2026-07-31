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
    OSIDialect,
    OSIDocument,
    OSIVendor,
)


def test_to_osi_yaml_uses_alias(document_data: dict) -> None:
    document_data["semantic_model"][0]["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OSIDocument.model_validate(document_data)
    output = document.to_osi_yaml()
    assert "from: orders" in output
    assert "from_dataset" not in output


def test_to_osi_yaml_includes_dialects_and_vendors(document_data: dict) -> None:
    document_data["dialects"] = [OSIDialect.ANSI_SQL, OSIDialect.DATABRICKS]
    document_data["vendors"] = [OSIVendor.DATABRICKS]
    document = OSIDocument.model_validate(document_data)
    output = document.to_osi_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["dialects"] == ["ANSI_SQL", "DATABRICKS"]
    assert parsed["vendors"] == ["DATABRICKS"]


def test_to_osi_json_includes_dialects_and_vendors(document_data: dict) -> None:
    document_data["dialects"] = [OSIDialect.SNOWFLAKE]
    document_data["vendors"] = [OSIVendor.SALESFORCE, OSIVendor.COMMON]
    document = OSIDocument.model_validate(document_data)
    output = document.to_osi_json()
    parsed = json.loads(output)
    assert parsed["dialects"] == ["SNOWFLAKE"]
    assert parsed["vendors"] == ["SALESFORCE", "COMMON"]


def test_to_osi_yaml_excludes_none(document_data: dict) -> None:
    document_data["dialects"] = [OSIDialect.ANSI_SQL]
    document = OSIDocument.model_validate(document_data)
    output = document.to_osi_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["dialects"] == ["ANSI_SQL"]
    assert "vendors" not in parsed
    semantic_model = parsed["semantic_model"][0]
    assert semantic_model["name"] == "typed_model"
    assert "description" not in semantic_model


def test_to_osi_json_uses_alias(document_data: dict) -> None:
    document_data["semantic_model"][0]["relationships"] = [
        {
            "name": "order_customer",
            "from": "orders",
            "to": "customers",
            "from_columns": ["customer_id"],
            "to_columns": ["id"],
        }
    ]
    document = OSIDocument.model_validate(document_data)
    output = document.to_osi_json()
    parsed = json.loads(output)
    assert parsed["semantic_model"][0]["relationships"][0]["from"] == "orders"


def test_to_osi_json_excludes_none(document_data: dict) -> None:
    document = OSIDocument.model_validate(document_data)
    output = document.to_osi_json()
    parsed = json.loads(output)
    semantic_model = parsed["semantic_model"][0]
    assert "description" not in semantic_model
    assert "relationships" not in semantic_model
    assert "vendors" not in semantic_model


def test_to_osi_yaml_validates_as_yaml(document_data: dict) -> None:
    document = OSIDocument.model_validate(document_data)
    output = document.to_osi_yaml()
    parsed = yaml.safe_load(output)
    assert parsed["semantic_model"][0]["name"] == "typed_model"


def test_to_osi_json_roundtrip(document_data: dict) -> None:
    document_data["dialects"] = [OSIDialect.DATABRICKS]
    document_data["semantic_model"][0]["datasets"][0]["fields"][0]["expression"] = {
        "dialects": [{"dialect": OSIDialect.DATABRICKS, "expression": "order_id"}]
    }
    document = OSIDocument.model_validate(document_data)
    json_str = document.to_osi_json()
    parsed = json.loads(json_str)
    document2 = OSIDocument(**parsed)
    assert document2.to_osi_json() == json_str
