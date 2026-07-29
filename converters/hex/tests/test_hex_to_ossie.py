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
from typing import Any

import yaml
from ossie import OSIDataType, OSIDocument, OSIVendor

from ossie_hex.hex_models import HexDialect
from ossie_hex.hex_to_ossie import convert_hex_to_ossie


def test_import_minimal_hex_project(minimal_hex_path: str) -> None:
    yaml_text, warnings = convert_hex_to_ossie(
        minimal_hex_path,
        dialect=HexDialect.DUCKDB.value,
        model_name="demo",
    )
    doc = OSIDocument.model_validate(yaml.safe_load(yaml_text))
    assert doc.version == "0.2.0.dev0"
    assert doc.vendors == [OSIVendor.HEX]

    model = doc.semantic_model[0]
    assert model.name == "demo"
    assert {d.name for d in model.datasets} == {"orders", "customers"}

    orders = next(d for d in model.datasets if d.name == "orders")
    assert orders.source == "analytics.public.orders"
    assert orders.primary_key == ["order_id"]

    # Hex measures compile down to plain Ossie SQL, including the filtered form.
    assert {
        m.name: m.expression.dialects[0].expression for m in model.metrics or []
    } == {
        "order_count": "COUNT(*)",
        "total_amount": "SUM(orders.amount)",
        "cancelled_orders": "COUNT(CASE WHEN orders.is_cancelled THEN 1 END)",
    }

    assert {f.name: f.datatype for f in orders.fields or []} == {
        "order_id": OSIDataType.STRING,
        "customer_id": OSIDataType.STRING,
        "order_date": OSIDataType.DATE,
        "amount": OSIDataType.DECIMAL,
        "is_cancelled": OSIDataType.BOOLEAN,
    }

    by_name = {f.name: f for f in orders.fields or []}
    dimensions = [f.dimension for f in by_name.values()]
    assert all(d is None for d in dimensions)
    assert all(f.is_time_dimension() is False for f in by_name.values())

    assert {r.name for r in model.relationships or []} == {"customers"}

    # View preserved via warning + custom extension.
    assert any("view" in w.message for w in warnings)


def _hex_extension(node: dict[str, Any]) -> dict[str, Any] | None:
    """The HEX custom-extension payload attached to an Ossie node."""
    extensions = node.get("custom_extensions")
    if not extensions:
        return None
    return json.loads(extensions[0]["data"])


def test_hex_extension_carries_only_non_ossie_data(minimal_hex_path: str) -> None:
    yaml_text, _ = convert_hex_to_ossie(
        minimal_hex_path,
        dialect=HexDialect.DUCKDB.value,
        model_name="demo",
    )
    model = yaml.safe_load(yaml_text)["semantic_model"][0]
    orders = next(ds for ds in model["datasets"] if ds["name"] == "orders")

    # Stashes carry only what Ossie cannot express. Asserting the whole payload
    # keeps derived defaults (a Hex `name`, an empty `description`) from
    # creeping in, since those resurface as noise when converting back.
    assert _hex_extension(model) == {
        "extension_version": 1,
        "hex_dialect": HexDialect.DUCKDB.value,
        "resource_order": [
            {"id": "customers", "source_file": "customers.yml"},
            {"id": "order_overview", "source_file": "order_overview.yml"},
            {"id": "orders", "source_file": "orders.yml"},
        ],
        "views": [
            {
                "resource": {
                    "id": "order_overview",
                    "type": "view",
                    "base": "orders",
                    "contents": [
                        {
                            "dimensions": ["..."],
                            "measures": ["order_count", "total_amount"],
                        }
                    ],
                }
            }
        ],
    }

    assert _hex_extension(orders) == {"display_name": "Orders", "source_kind": "table"}

    assert [_hex_extension(field) for field in orders["fields"]] == [
        {"visibility": "internal"},
        None,
        None,
        {"expr_sql": "amount_usd"},
        {"expr_sql": "status = 'cancelled'"},
    ]

    assert [_hex_extension(metric) for metric in model["metrics"]] == [
        {
            "model_id": "orders",
            "measure_id": "order_count",
            "display_name": "Order count",
            "func": "count",
        },
        {
            "model_id": "orders",
            "measure_id": "total_amount",
            "display_name": "Total amount",
            "func": "sum",
            "of": "amount",
        },
        {
            "model_id": "orders",
            "measure_id": "cancelled_orders",
            "display_name": "Cancelled orders",
            "func": "count",
            "filters": ["is_cancelled"],
        },
    ]

    assert _hex_extension(model["relationships"][0]) == {
        "join_sql": "${customer_id} = ${customers.customer_id}",
        "relation_type": "many_to_one",
        "target": "customers",
        "source_model_id": "orders",
        "relation_id": "customers",
    }


def test_query_backed_model(query_hex_path: str) -> None:
    yaml_text, _ = convert_hex_to_ossie(query_hex_path, dialect=HexDialect.DUCKDB.value)
    doc = OSIDocument.model_validate(yaml.safe_load(yaml_text))
    events = doc.semantic_model[0].datasets[0]
    assert "SELECT" in events.source.upper()
    payload = next(f for f in (events.fields or []) if f.name == "payload")
    assert payload.datatype == OSIDataType.OPAQUE
