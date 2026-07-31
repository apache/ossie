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

import yaml
from ossie import OSIDialect

from ossie_hex.hex_to_ossie import convert_hex_to_ossie
from tests.utils import hex_extension


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

    yaml_text, _ = convert_hex_to_ossie(
        str(tmp_path), dialect=OSIDialect.ANSI_SQL.value
    )
    datasets = yaml.safe_load(yaml_text)["semantic_model"][0]["datasets"]
    orders = next(ds for ds in datasets if ds["name"] == "orders")

    assert {field["name"]: hex_extension(field) for field in orders["fields"]} == {
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

    yaml_text, _ = convert_hex_to_ossie(
        str(tmp_path), dialect=OSIDialect.ANSI_SQL.value
    )
    fields = yaml.safe_load(yaml_text)["semantic_model"][0]["datasets"][0]["fields"]
    nothing, label = fields

    assert nothing["datatype"] == "Opaque"
    assert hex_extension(nothing) == {"type": "null"}

    assert label["datatype"] == "String"
    assert hex_extension(label) is None
