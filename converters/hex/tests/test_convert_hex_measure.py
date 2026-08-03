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

from ossie_hex.cli.hex_project_io import read_hex_project
from ossie_hex.hex_to_ossie import convert_hex_to_ossie
from tests.utils import hex_extension


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

    files = read_hex_project(tmp_path)
    yaml_text, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="demo",
    )
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
    files = read_hex_project(formula_measure_hex_path)
    yaml_text, warnings = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="demo",
    )
    model = yaml.safe_load(yaml_text)["semantic_model"][0]

    assert [metric["name"] for metric in model["metrics"]] == [
        "revenue",
        "order_count",
    ]

    payload = hex_extension(model["datasets"][0])
    assert payload is not None
    (preserved,) = payload["measures"]
    assert preserved["id"] == "revenue_per_order"
    assert preserved["func_calc"] == "revenue / order_count"
    assert any("revenue_per_order" in w.message for w in warnings)
