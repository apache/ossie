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

from ossie_hex.hex_to_ossie import convert_hex_to_ossie
from ossie_hex.hex_types import HexDialect
from tests.utils import hex_extension


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
    payloads = {rel["name"]: hex_extension(rel) for rel in model["relationships"]}

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
