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

    assert _hex_extension(orders) == {
        "display_name": "Orders",
        "source_kind": "table",
    }

    # A plain typed column has nothing Ossie is missing, so it gets no extension.
    # Both expressions here are raw SQL over the source table, which is exactly
    # what the Ossie expression holds, so the visibility Ossie has no field for
    # is all that is left to record.
    assert [_hex_extension(field) for field in orders["fields"]] == [
        {"visibility": "internal"},
        None,
        None,
        None,
        None,
    ]

    # No `measure_id`: nothing on this model collides, so each metric is named
    # for its measure and the export reads the ID back off the metric name.
    assert [_hex_extension(metric) for metric in model["metrics"]] == [
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
    assert _hex_extension(model["relationships"][0]) is None


def test_relationship_payload_is_kept_only_for_what_the_columns_cannot_say(
    tmp_path: Path,
) -> None:
    """Cardinality and visibility are all the Ossie column pairs cannot say.

    The export reads a payload-free relationship as many-to-one from the base
    dataset, so that shape needs nothing recorded. The relation's ID, its target,
    and the model holding it are readable from the relationship in every case,
    and the join is rebuilt from the column pairs.
    """
    (tmp_path / "orders.yml").write_text(
        """
id: orders
base_sql_table: s.orders
dimensions:
- id: id
  type: string
- id: customer_id
  type: string
- id: region_id
  type: string
relations:
- id: customers
  type: many_to_one
  join_sql: ${customer_id} = ${customers.id}
- id: sales
  type: one_to_many
  join_sql: ${id} = ${sales.order_id}
- id: shipment
  type: one_to_one
  join_sql: ${id} = ${shipment.order_id}
- id: hidden
  target: customers
  type: many_to_one
  visibility: internal
  join_sql: ${customer_id} = ${hidden.id}
- id: regions
  type: many_to_one
  join_sql: ${regions.id} = ${region_id}
""",
        encoding="utf-8",
    )

    yaml_text, _ = convert_hex_to_ossie(str(tmp_path), dialect=HexDialect.DUCKDB.value)
    model = yaml.safe_load(yaml_text)["semantic_model"][0]
    payloads = {rel["name"]: _hex_extension(rel) for rel in model["relationships"]}

    assert payloads["customers"] is None

    # An inverted relation is stored with `from` and `to` swapped, which reads
    # back as an ordinary many-to-one pointing the other way. The cardinality is
    # what tells the export to turn the relationship inside out again, so the
    # target and the join follow from it.
    assert payloads["sales"] == {"relation_type": "one_to_many"}

    # One-to-one keeps Ossie's orientation but not its cardinality.
    assert payloads["shipment"] == {"relation_type": "one_to_one"}

    # Ossie has no field for visibility.
    assert payloads["hidden"] == {"visibility": "internal"}

    # Decomposing to column pairs loses which side was written first, but the
    # flipped equality it comes back as says the same thing.
    assert payloads["regions"] is None


def test_expr_sql_is_only_preserved_when_ossie_cannot_rebuild_it(
    tmp_path: Path,
) -> None:
    """The Ossie expression already holds the SQL; the payload is for what it loses.

    Raw SQL over the source table comes back verbatim however it is written, and
    a ``${dim}`` ref is qualified as ``model.dim`` on the way out and read back
    off that, so repeating any of it in the payload is noise. What the rewrite
    cannot return is a reference it would land somewhere else: a bare column that
    a dimension of the same name reads differently, and a relation-qualified ref
    whose relation the import has no way to place.
    """
    (tmp_path / "orders.yml").write_text(
        """
id: orders
base_sql_table: s.orders
dimensions:
- id: is_delivery
  type: boolean
  expr_sql: delivery = 'Yes'
- id: amount
  type: number
  expr_sql: amount_usd
- id: status
  type: string
- id: status_upper
  type: string
  expr_sql: UPPER(${status})
- id: qualified_amount
  type: number
  expr_sql: orders.amount_usd
- id: doubled_amount
  type: number
  expr_sql: orders.amount_usd * 2
- id: label
  type: string
  expr_sql: order_label
- id: raw_label
  type: string
  expr_sql: label
- id: shouty_label
  type: string
  expr_sql: UPPER(orders.label)
- id: buyer_name
  type: string
  expr_sql: ${buyer.name}
relations:
- id: buyer
  target: customers
  type: many_to_one
  join_sql: ${customer_id} = ${buyer.id}
""",
        encoding="utf-8",
    )
    (tmp_path / "customers.yml").write_text(
        """
id: customers
base_sql_table: s.customers
dimensions:
- id: id
  type: string
- id: name
  type: string
""",
        encoding="utf-8",
    )

    yaml_text, _ = convert_hex_to_ossie(str(tmp_path), dialect=HexDialect.DUCKDB.value)
    datasets = yaml.safe_load(yaml_text)["semantic_model"][0]["datasets"]
    orders = next(ds for ds in datasets if ds["name"] == "orders")

    assert {field["name"]: _hex_extension(field) for field in orders["fields"]} == {
        "is_delivery": None,
        "amount": None,
        "status": None,
        "status_upper": None,
        "qualified_amount": None,
        "doubled_amount": None,
        "label": None,
        # `label` the column and `label` the dimension are different things, and
        # the dimension reads `order_label`, so the reference the rewrite would
        # return in place of the bare column names the wrong one. Same for the
        # qualified form of it inside a larger expression.
        "raw_label": {"expr_sql": "label"},
        "shouty_label": {"expr_sql": "UPPER(orders.label)"},
        # Hex reaches another model through a relation ID, which is not a dataset
        # name, so `buyer.name` is not something the import can resolve.
        "buyer_name": {"expr_sql": "${buyer.name}"},
    }


def test_a_relation_qualified_reference_is_not_qualified_again(
    tmp_path: Path,
) -> None:
    """Ossie identifiers are ``dataset.field``, so there is no third part.

    A Hex ``of`` or filter naming a relation already says where it reads from,
    and prefixing the owning model onto it produces a name the spec cannot
    place. The bare references beside them are the control: those do need it.
    """
    (tmp_path / "sales.yml").write_text(
        """
id: sales
base_sql_table: s.sales
dimensions:
- id: value
  type: number
- id: order_id
  type: string
relations:
- id: orders
  type: many_to_one
  join_sql: ${order_id} = ${orders.id}
measures:
- id: delivered_revenue
  func: sum
  of: value
  filters:
  - orders.is_delivery
- id: total_delivery_fee
  func: sum
  of: orders.delivery_fee
""",
        encoding="utf-8",
    )
    (tmp_path / "orders.yml").write_text(
        """
id: orders
base_sql_table: s.orders
dimensions:
- id: id
  type: string
- id: is_delivery
  type: boolean
- id: delivery_fee
  type: number
""",
        encoding="utf-8",
    )

    yaml_text, _ = convert_hex_to_ossie(str(tmp_path), dialect=HexDialect.DUCKDB.value)
    metrics = yaml.safe_load(yaml_text)["semantic_model"][0]["metrics"]
    expressions = {
        metric["name"]: metric["expression"]["dialects"][0]["expression"]
        for metric in metrics
    }

    assert expressions == {
        "delivered_revenue": "SUM(CASE WHEN orders.is_delivery THEN sales.value END)",
        "total_delivery_fee": "SUM(orders.delivery_fee)",
    }


def test_hex_measure_with_func_calc_is_preserved(
    formula_measure_hex_path: str,
) -> None:
    """A Hex formula names other measures, which an Ossie metric cannot do."""
    yaml_text, warnings = convert_hex_to_ossie(
        formula_measure_hex_path, dialect=HexDialect.DUCKDB.value
    )
    model = yaml.safe_load(yaml_text)["semantic_model"][0]

    assert [metric["name"] for metric in model["metrics"]] == [
        "revenue",
        "order_count",
    ]

    payload = _hex_extension(model["datasets"][0])
    assert payload is not None
    (preserved,) = payload["measures"]
    assert preserved["id"] == "revenue_per_order"
    assert preserved["func_calc"] == "revenue / order_count"
    assert any("revenue_per_order" in w.message for w in warnings)


def test_independently_unique_dimensions_stay_separate_keys(tmp_path: Path) -> None:
    """Hex marks each dimension unique alone, so they are not a composite key."""
    (tmp_path / "users.yml").write_text(
        """
id: users
base_sql_table: s.users
dimensions:
- id: user_id
  type: string
  unique: true
- id: email
  type: string
  unique: true
- id: username
  type: string
  unique: true
""",
        encoding="utf-8",
    )

    yaml_text, _ = convert_hex_to_ossie(str(tmp_path), dialect=HexDialect.DUCKDB.value)
    dataset = OSIDocument.model_validate(yaml.safe_load(yaml_text)).semantic_model[0]

    assert dataset.datasets[0].primary_key == ["user_id"]
    assert dataset.datasets[0].unique_keys == [["email"], ["username"]]


def test_preserves_lossy_types(tmp_path: Path) -> None:
    """Some Hex types have no Ossie datatype, so the custom extension must hold them."""

    # "label" is a control
    (tmp_path / "events.yml").write_text(
        """
id: events
base_sql_table: s.events
dimensions:
- id: nothing
  type: 'null'
- id: label
  type: string
""",
        encoding="utf-8",
    )

    yaml_text, _ = convert_hex_to_ossie(str(tmp_path), dialect=HexDialect.DUCKDB.value)
    fields = yaml.safe_load(yaml_text)["semantic_model"][0]["datasets"][0]["fields"]
    nothing, label = fields

    assert nothing["datatype"] == "Opaque"
    assert _hex_extension(nothing) == {"type": "null"}

    assert label["datatype"] == "String"
    assert _hex_extension(label) is None


def test_query_backed_model(query_hex_path: str) -> None:
    yaml_text, _ = convert_hex_to_ossie(query_hex_path, dialect=HexDialect.DUCKDB.value)
    doc = OSIDocument.model_validate(yaml.safe_load(yaml_text))
    events = doc.semantic_model[0].datasets[0]
    assert "SELECT" in events.source.upper()
    payload = next(f for f in (events.fields or []) if f.name == "payload")
    assert payload.datatype == OSIDataType.OPAQUE
