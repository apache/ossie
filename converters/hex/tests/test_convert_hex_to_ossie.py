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

import yaml
from ossie import OSIDataType, OSIDialect, OSIDocument, OSIVendor

from ossie_hex.cli.hex_project_io import read_hex_project
from ossie_hex.hex_to_ossie import convert_hex_to_ossie
from tests.utils import hex_extension


def test_import_minimal_hex_project(minimal_hex_path: str) -> None:
    files = read_hex_project(minimal_hex_path)
    yaml_text, warnings = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.SNOWFLAKE,
        model_name="demo",
    )
    doc = OSIDocument.model_validate(yaml.safe_load(yaml_text))
    assert doc.version == "0.2.0.dev0"
    assert doc.vendors == [OSIVendor.HEX]
    assert doc.dialects == [OSIDialect.SNOWFLAKE]

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

    # The requested dialect is what the converted SQL is claimed to be written in.
    assert {
        entry.dialect
        for holder in [*(model.metrics or []), *(orders.fields or [])]
        for entry in holder.expression.dialects
    } == {OSIDialect.SNOWFLAKE}

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


def test_hex_extension_carries_only_non_ossie_data(minimal_hex_path: str) -> None:
    files = read_hex_project(minimal_hex_path)
    yaml_text, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="demo",
    )
    model = yaml.safe_load(yaml_text)["semantic_model"][0]
    orders = next(ds for ds in model["datasets"] if ds["name"] == "orders")

    # Stashes carry only what Ossie cannot express. Asserting the whole payload
    # keeps derived defaults (a Hex `name`, an empty `description`) from
    # creeping in, since those resurface as noise when converting back.
    assert hex_extension(model) == {
        "extension_version": 1,
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

    assert hex_extension(orders) == {
        "display_name": "Orders",
        "source_kind": "table",
    }

    # A plain typed column has nothing Ossie is missing, so it gets no extension.
    # Both expressions here are raw SQL over the source table, which is exactly
    # what the Ossie expression holds, so the visibility Ossie has no field for
    # is all that is left to record.
    assert [hex_extension(field) for field in orders["fields"]] == [
        {"visibility": "internal"},
        None,
        None,
        None,
        None,
    ]

    # No `measure_id`: nothing on this model collides, so each metric is named
    # for its measure and the export reads the ID back off the metric name.
    assert [hex_extension(metric) for metric in model["metrics"]] == [
        {
            "model_id": "orders",
            "display_name": "Order count",
            "func": "count",
        },
        {
            "model_id": "orders",
            "display_name": "Total amount",
            "func": "sum",
            "of": "amount",
        },
        {
            "model_id": "orders",
            "display_name": "Cancelled orders",
            "func": "count",
            "filters": ["is_cancelled"],
        },
    ]

    # A many-to-one join is what the Ossie column pairs already describe, so the
    # whole payload would be a restatement of `from`, `to`, and the two column
    # lists sitting beside it.
    assert hex_extension(model["relationships"][0]) is None


def test_query_backed_model(query_hex_path: str) -> None:
    files = read_hex_project(query_hex_path)
    yaml_text, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="demo",
    )
    doc = OSIDocument.model_validate(yaml.safe_load(yaml_text))
    events = doc.semantic_model[0].datasets[0]
    assert "SELECT" in events.source.upper()
    payload = next(f for f in (events.fields or []) if f.name == "payload")
    assert payload.datatype == OSIDataType.OPAQUE
