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

from ossie_hex._common import load_yaml
from ossie_hex.hex_models import HexDialect, parse_hex_resource
from ossie_hex.hex_project import write_hex_project
from ossie_hex.hex_to_ossie import convert_hex_to_ossie
from ossie_hex.ossie_to_hex import convert_ossie_to_hex


def _resources_by_id(project_dir: str | Path) -> dict[str, dict]:
    """Every Hex resource under a directory, keyed by id."""
    resources = {}
    for path in sorted(Path(project_dir).rglob("*.yml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if doc:
                parse_hex_resource(doc)
                resources[doc["id"]] = doc
    return resources


def test_hex_roundtrip_reproduces_source_project(
    hex_project_path: str,
    tmp_path: Path,
) -> None:
    ossie_yaml, _import_warnings = convert_hex_to_ossie(
        hex_project_path,
        dialect=HexDialect.DUCKDB.value,
        model_name="roundtrip",
    )
    files, _export_warnings = convert_ossie_to_hex(
        ossie_yaml, dialect=OSIDialect.ANSI_SQL.value
    )

    out_dir = tmp_path / "roundtrip"
    write_hex_project(out_dir, files)

    # Emitted YAML differs from the source in list indentation and quote style,
    # so compare parsed resources rather than raw text.
    assert _resources_by_id(out_dir) == _resources_by_id(hex_project_path)


def test_hex_roundtrip_emits_expected_yaml(minimal_hex_path: str) -> None:
    ossie_yaml, _ = convert_hex_to_ossie(
        minimal_hex_path,
        dialect=HexDialect.DUCKDB.value,
        model_name="minimal_hex",
    )
    files, warnings = convert_ossie_to_hex(
        ossie_yaml, dialect=OSIDialect.ANSI_SQL.value
    )

    assert warnings == []
    assert files == {
        "customers.yml": """\
id: customers
base_sql_table: analytics.public.customers
dimensions:
- id: customer_id
  type: string
  unique: true
- id: email
  type: string
- id: created_at
  type: timestamp_tz
""",
        "order_overview.yml": """\
id: order_overview
type: view
base: orders
contents:
- dimensions:
  - '...'
  measures:
  - order_count
  - total_amount
""",
        "orders.yml": """\
id: orders
base_sql_table: analytics.public.orders
dimensions:
- id: order_id
  type: string
  unique: true
  visibility: internal
- id: customer_id
  type: string
- id: order_date
  type: date
- id: amount
  type: number
  expr_sql: amount_usd
- id: is_cancelled
  type: boolean
  expr_sql: status = 'cancelled'
measures:
- id: order_count
  func: count
- id: total_amount
  func: sum
  of: amount
- id: cancelled_orders
  func: count
  filters:
  - is_cancelled
relations:
- id: customers
  type: many_to_one
  join_sql: ${customer_id} = ${customers.customer_id}
description: Order fact table.
""",
    }


def test_named_joins_roundtrip(named_joins_hex_path: str) -> None:
    ossie_yaml, _warnings = convert_hex_to_ossie(
        named_joins_hex_path, dialect=HexDialect.DUCKDB.value
    )
    doc = load_yaml(ossie_yaml)
    rels = doc["semantic_model"][0].get("relationships") or []
    assert len(rels) == 2
    names = {r["name"] for r in rels}
    assert names == {"sender", "receiver"}

    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL.value)
    # Multi-doc source file should round-trip as a single path.
    assert "messages.yml" in files
    docs = list(yaml.safe_load_all(files["messages.yml"]))
    docs = [d for d in docs if d]
    ids = {d["id"] for d in docs}
    assert ids == {"messages", "users"}
    messages = next(d for d in docs if d["id"] == "messages")
    rel_ids = {r["id"] for r in messages.get("relations", [])}
    assert rel_ids == {"sender", "receiver"}
    for rel in messages["relations"]:
        assert rel["target"] == "users"
        assert rel["id"] in rel["join_sql"] or "users" in rel["join_sql"]


def test_tpcds_export(tpcds_ossie_yaml: str, tmp_path: Path) -> None:
    files, _warnings = convert_ossie_to_hex(
        tpcds_ossie_yaml,
        dialect=OSIDialect.ANSI_SQL.value,
        base_model="store_sales",
    )
    assert files
    out = tmp_path / "tpcds_hex"
    write_hex_project(out, files)
    # Every file validates as a Hex resource.
    for path in out.rglob("*.yml"):
        parse_hex_resource(yaml.safe_load(path.read_text()))

    # Re-import should validate as Ossie.
    ossie_yaml, _ = convert_hex_to_ossie(
        str(out), dialect=HexDialect.DUCKDB.value, model_name="tpcds"
    )
    from ossie import OSIDocument

    OSIDocument.model_validate(yaml.safe_load(ossie_yaml))
