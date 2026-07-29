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

from pathlib import Path

import pytest

from ossie_hex._common import ConversionError, load_yaml
from ossie_hex.hex_models import HexDialect, HexModel, HexView, parse_hex_resource
from ossie_hex.hex_project import (
    load_hex_project,
    resource_to_yaml,
    write_hex_project,
)


def test_resource_to_yaml_omits_model_defaults() -> None:
    resource_data = {
        "id": "orders",
        "type": "model",
        "base_sql_table": "orders",
        "description": "",
        "visibility": "public",
        "dimensions": [
            {
                "id": "order_id",
                "type": "number",
                "description": "",
                "visibility": "public",
                "unique": False,
            }
        ],
        "measures": [
            {
                "id": "order_count",
                "func": "count",
                "type": "number",
                "filters": [],
                "description": "",
                "visibility": "public",
            }
        ],
        "relations": [
            {
                "id": "users",
                "target": "users",
                "type": "many_to_one",
                "join_sql": "${user_id} = ${users.id}",
                "visibility": "public",
            }
        ],
    }
    expected = {
        "id": "orders",
        "base_sql_table": "orders",
        "dimensions": [{"id": "order_id", "type": "number"}],
        "measures": [{"id": "order_count", "func": "count"}],
        "relations": [
            {
                "id": "users",
                "type": "many_to_one",
                "join_sql": "${user_id} = ${users.id}",
            }
        ],
    }

    assert load_yaml(resource_to_yaml(resource_data)) == expected
    assert (
        load_yaml(resource_to_yaml(HexModel.model_validate(resource_data))) == expected
    )


def test_resource_to_yaml_preserves_view_type() -> None:
    resource = HexView(id="orders_view", base="orders", contents=[])

    data = load_yaml(resource_to_yaml(resource))

    assert data == {
        "id": "orders_view",
        "type": "view",
        "base": "orders",
        "contents": [],
    }
    assert parse_hex_resource(data) == resource


def test_load_multi_document_yaml(named_joins_hex_path: str) -> None:
    project = load_hex_project(
        named_joins_hex_path, dialect=HexDialect.DUCKDB.value, name="chat"
    )

    assert {resource.id for resource in project.resources} == {"messages", "users"}


def test_load_hex_project_rejects_duplicate_ids(tmp_path: Path) -> None:
    (tmp_path / "a.yml").write_text("id: orders\nbase_sql_table: a.orders\n")
    (tmp_path / "b.yml").write_text("id: orders\nbase_sql_table: b.orders\n")

    with pytest.raises(ConversionError, match="Duplicate Hex resource id 'orders'"):
        load_hex_project(tmp_path, dialect=HexDialect.DUCKDB.value)


def test_load_hex_project_rejects_non_mapping_document(tmp_path: Path) -> None:
    (tmp_path / "invalid.yml").write_text("- orders\n")

    with pytest.raises(ConversionError, match="expected a mapping"):
        load_hex_project(tmp_path, dialect=HexDialect.DUCKDB.value)


def test_load_empty_project_errors(tmp_path: Path) -> None:
    with pytest.raises(ConversionError, match="No Hex YAML"):
        load_hex_project(tmp_path, dialect=HexDialect.DUCKDB.value)


def test_write_hex_project_creates_nested_directories(tmp_path: Path) -> None:
    write_hex_project(tmp_path, {"models/orders.yml": "id: orders\n"})

    assert (tmp_path / "models" / "orders.yml").read_text() == "id: orders\n"
