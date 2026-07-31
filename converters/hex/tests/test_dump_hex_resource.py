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

from ossie_hex.hex_types import HexModel, HexView, parse_hex_resource
from ossie_hex.ossie_to_hex.dump_hex_resource import hex_resource_to_yaml
from ossie_hex.util.yaml import load_yaml


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

    assert load_yaml(hex_resource_to_yaml(resource_data)) == expected
    assert (
        load_yaml(hex_resource_to_yaml(HexModel.model_validate(resource_data)))
        == expected
    )


def test_resource_to_yaml_preserves_view_type() -> None:
    resource = HexView(id="orders_view", base="orders", contents=[])

    data = load_yaml(hex_resource_to_yaml(resource))

    assert data == {
        "id": "orders_view",
        "type": "view",
        "base": "orders",
        "contents": [],
    }
    assert parse_hex_resource(data) == resource
