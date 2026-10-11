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
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMA_PATH = REPO_ROOT / "core-spec" / "ossie-schema.json"
TPCDS_PATH = REPO_ROOT / "examples" / "tpcds_semantic_model.yaml"

ORDERS = "sap.capire.bookshop.Orders"
BOOKS = "sap.capire.bookshop.Books"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES_DIR / name).read_text())


def load_tpcds() -> dict[str, Any]:
    return yaml.safe_load(TPCDS_PATH.read_text())


def validate_against_schema(document: Any) -> None:
    """Validate an OssieDocument against the core-spec JSON Schema."""
    import jsonschema

    schema = json.loads(SCHEMA_PATH.read_text())
    jsonschema.validate(json.loads(document.to_ossie_json()), schema)


def by_name(items: Any) -> dict[str, Any]:
    return {i.name: i for i in items or []}


def sap_ext(obj: Any) -> dict[str, Any]:
    for extension in obj.custom_extensions or []:
        if extension.vendor_name == "SAP_CSN":
            return json.loads(extension.data)
    return {}


def sql_of(obj: Any) -> str:
    return obj.expression.dialects[0].expression
