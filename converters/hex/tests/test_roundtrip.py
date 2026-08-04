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

import yaml
from ossie import OSIDialect

from ossie_hex.cli.hex_project_io import read_hex_project, write_hex_project
from ossie_hex.hex_to_ossie import convert_hex_to_ossie
from ossie_hex.hex_types import parse_hex_resource
from ossie_hex.ossie_to_hex import convert_ossie_to_hex
from ossie_hex.util.yaml import load_yaml

# A resource addresses each of these by `id`, never by position, so two projects
# that differ only in the order they list them are the same model. A view's
# `contents` is left alone: that order is the presentation Hex shows a user.
_KEYED_BY_ID = ("dimensions", "measures", "relations")


def _key_collections_by_id(resource: dict) -> dict:
    normalized = dict(resource)
    for key in _KEYED_BY_ID:
        items = normalized.get(key)
        if items is not None:
            normalized[key] = {item["id"]: item for item in items}
    return normalized


def _resources_by_id(project_dir: str | Path) -> dict[str, dict]:
    """Every Hex resource under a directory, keyed by id, order-insensitively."""
    resources = {}
    for path in sorted(Path(project_dir).rglob("*.yml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if doc:
                parse_hex_resource(doc)
                resources[doc["id"]] = _key_collections_by_id(doc)
    return resources


def test_hex_roundtrip_reaches_a_fixed_point(
    hex_project_path: str,
    tmp_path: Path,
) -> None:
    """Hex → Ossie → Hex may rewrite `func` measures as equivalent `func_sql`.

    A second pass must leave that canonical form alone. Comparing authoring
    syntax to the source would reject a faithful SQL representation of the same
    aggregate.
    """
    files = read_hex_project(hex_project_path)
    ossie_yaml, _import_warnings = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    files, _export_warnings = convert_ossie_to_hex(
        ossie_yaml, dialect=OSIDialect.ANSI_SQL
    )

    out_dir = tmp_path / "roundtrip"
    write_hex_project(out_dir, files)

    ossie_yaml_again, _ = convert_hex_to_ossie(
        read_hex_project(out_dir),
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    files_again, _ = convert_ossie_to_hex(ossie_yaml_again, dialect=OSIDialect.ANSI_SQL)
    again_dir = tmp_path / "roundtrip_again"
    write_hex_project(again_dir, files_again)

    assert _resources_by_id(again_dir) == _resources_by_id(out_dir)


def test_compiled_metric_sql_survives_a_roundtrip(minimal_hex_path: str) -> None:
    """The SQL compiled from `func`/`of`/`filters` is what the next import sees."""
    files = read_hex_project(minimal_hex_path)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    first_metrics = {
        metric["name"]: metric["expression"]["dialects"][0]["expression"]
        for metric in load_yaml(ossie_yaml)["semantic_model"][0]["metrics"]
    }

    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    second_metrics = {
        metric["name"]: metric["expression"]["dialects"][0]["expression"]
        for metric in load_yaml(ossie_yaml)["semantic_model"][0]["metrics"]
    }

    assert second_metrics == first_metrics
    assert first_metrics == {
        "order_count": "COUNT(orders.*)",
        "total_amount": "SUM(orders.amount)",
        "cancelled_orders": "COUNT(CASE WHEN orders.is_cancelled THEN 1 END)",
    }


def test_hex_roundtrip_emits_expected_yaml(minimal_hex_path: str) -> None:
    files = read_hex_project(minimal_hex_path)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="minimal_hex",
    )
    files, warnings = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)

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
  func_sql: COUNT(orders.*)
- id: total_amount
  func_sql: SUM(${amount})
- id: cancelled_orders
  func_sql: COUNT(CASE WHEN ${is_cancelled} THEN 1 END)
relations:
- id: customers
  type: many_to_one
  join_sql: ${customer_id} = ${customers.customer_id}
description: Order fact table.
""",
    }


def test_named_joins_roundtrip(named_joins_hex_path: str) -> None:
    files = read_hex_project(named_joins_hex_path)
    ossie_yaml, _warnings = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    doc = load_yaml(ossie_yaml)
    rels = doc["semantic_model"][0].get("relationships") or []
    assert len(rels) == 2
    names = {r["name"] for r in rels}
    assert names == {"sender", "receiver"}

    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)
    # The two models shared one multi-doc file on the way in and come back as a
    # file each, since it's functionally equivalent.
    assert set(files) == {"messages.yml", "users.yml"}
    messages = yaml.safe_load(files["messages.yml"])
    rel_ids = {r["id"] for r in messages.get("relations", [])}
    assert rel_ids == {"sender", "receiver"}
    for rel in messages["relations"]:
        assert rel["target"] == "users"
        assert rel["id"] in rel["join_sql"] or "users" in rel["join_sql"]


def test_a_non_numeric_func_sql_measure_keeps_its_type(tmp_path: Path) -> None:
    """A date-typed measure has no `func` form to be rebuilt into.

    Hex pins a `func` measure to `number`, so the type is what would be lost if
    the round trip tried to reconstitute `func: max` from the SQL.
    """
    (tmp_path / "orders.yml").write_text(
        """id: orders
base_sql_table: s.orders
dimensions:
- id: order_date
  type: date
measures:
- id: latest_order
  func_sql: MAX(${order_date})
  type: date
""",
        encoding="utf-8",
    )

    files = read_hex_project(tmp_path)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)
    measure = yaml.safe_load(files["orders.yml"])["measures"][0]

    assert measure == {
        "id": "latest_order",
        "func_sql": "MAX(${order_date})",
        "type": "date",
    }


def test_measure_ids_ride_along_in_the_metric_name(tmp_path: Path) -> None:
    """A measure ID is recorded only where the metric name is not already it.

    Ossie metric names are unique per document while Hex measure IDs are unique
    only within a model, so the second ``revenue`` is exported qualified and
    the payload has to carry the ID that rename replaced. The measure authored
    as ``orders__revenue`` is the control: it collides with nothing, so its
    name is its ID and reading the name back is enough.
    """
    (tmp_path / "orders.yml").write_text(
        """id: orders
base_sql_table: s.orders
dimensions:
- id: amount
  type: number
measures:
- id: revenue
  func: sum
  of: amount
- id: orders__revenue
  func: sum
  of: amount
""",
        encoding="utf-8",
    )
    (tmp_path / "sales.yml").write_text(
        """id: sales
base_sql_table: s.sales
dimensions:
- id: amount
  type: number
measures:
- id: revenue
  func: sum
  of: amount
""",
        encoding="utf-8",
    )

    files = read_hex_project(tmp_path)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    metrics = load_yaml(ossie_yaml)["semantic_model"][0]["metrics"]
    payloads = {
        metric["name"]: json.loads(metric["custom_extensions"][0]["data"])
        for metric in metrics
    }

    assert set(payloads) == {"revenue", "orders__revenue", "sales__revenue"}
    assert "measure_id" not in payloads["revenue"]
    assert "measure_id" not in payloads["orders__revenue"]
    assert payloads["sales__revenue"]["measure_id"] == "revenue"

    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)
    orders = yaml.safe_load(files["orders.yml"])
    sales = yaml.safe_load(files["sales.yml"])

    assert [m["id"] for m in orders["measures"]] == ["revenue", "orders__revenue"]
    assert [m["id"] for m in sales["measures"]] == ["revenue"]


def test_relation_cardinality_survives_the_roundtrip(tmp_path: Path) -> None:
    """An inverted relation is the one Ossie cannot describe on its own.

    Ossie stores a one-to-many with ``from`` and ``to`` swapped, which is
    indistinguishable from a many-to-one pointing the other way once the join is
    decomposed into column pairs. The many-to-one beside it is the control.
    """
    (tmp_path / "orders.yml").write_text(
        """id: orders
base_sql_table: s.orders
dimensions:
- id: id
  type: string
- id: customer_id
  type: string
relations:
- id: customers
  type: many_to_one
  join_sql: ${customer_id} = ${customers.id}
- id: sales
  type: one_to_many
  join_sql: ${id} = ${sales.order_id}
""",
        encoding="utf-8",
    )
    (tmp_path / "customers.yml").write_text(
        """id: customers
base_sql_table: s.customers
dimensions:
- id: id
  type: string
""",
        encoding="utf-8",
    )
    (tmp_path / "sales.yml").write_text(
        """id: sales
base_sql_table: s.sales
dimensions:
- id: order_id
  type: string
""",
        encoding="utf-8",
    )

    files = read_hex_project(tmp_path)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)
    relations = yaml.safe_load(files["orders.yml"])["relations"]

    assert relations == [
        {
            "id": "customers",
            "type": "many_to_one",
            "join_sql": "${customer_id} = ${customers.id}",
        },
        {
            "id": "sales",
            "type": "one_to_many",
            "join_sql": "${id} = ${sales.order_id}",
        },
    ]


def test_a_decomposable_join_comes_back_canonicalized(tmp_path: Path) -> None:
    """The join is rebuilt from the column pairs, not carried through verbatim.

    Ossie records which columns are joined but not how the equality was written,
    so a join authored with the target on the left comes back with its operands
    the other way round, and one qualified by the target model comes back
    qualified by the relation. Both say what was authored; neither is the same
    text. Nothing about that is worth a payload, so the relations carry none.
    """
    (tmp_path / "orders.yml").write_text(
        """id: orders
base_sql_table: s.orders
dimensions:
- id: region_id
  type: string
- id: customer_id
  type: string
relations:
- id: regions
  type: many_to_one
  join_sql: ${regions.id} = ${region_id}
- id: buyer
  target: customers
  type: many_to_one
  join_sql: ${customer_id} = ${customers.id}
""",
        encoding="utf-8",
    )
    (tmp_path / "regions.yml").write_text(
        """id: regions
base_sql_table: s.regions
dimensions:
- id: id
  type: string
""",
        encoding="utf-8",
    )
    (tmp_path / "customers.yml").write_text(
        """id: customers
base_sql_table: s.customers
dimensions:
- id: id
  type: string
""",
        encoding="utf-8",
    )

    files = read_hex_project(tmp_path)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    relationships = load_yaml(ossie_yaml)["semantic_model"][0]["relationships"]
    assert [rel.get("custom_extensions") for rel in relationships] == [None, None]

    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)

    assert yaml.safe_load(files["orders.yml"])["relations"] == [
        {
            "id": "regions",
            "type": "many_to_one",
            "join_sql": "${region_id} = ${regions.id}",
        },
        {
            "id": "buyer",
            "target": "customers",
            "type": "many_to_one",
            "join_sql": "${customer_id} = ${buyer.id}",
        },
    ]


def test_an_undecomposable_join_survives_the_roundtrip(tmp_path: Path) -> None:
    """A join with no column pairs leaves no Ossie relationship to annotate.

    Ossie describes a join as the columns on either side of it, which a range
    predicate does not have, so the relation is kept whole on the model it
    belongs to instead of being rebuilt from a relationship on the way back.
    """
    (tmp_path / "orders.yml").write_text(
        """id: orders
base_sql_table: s.orders
dimensions:
- id: amount
  type: number
relations:
- id: tier
  target: price_tiers
  type: one_to_many
  visibility: internal
  join_sql: ${amount} > ${tier.floor}
""",
        encoding="utf-8",
    )
    (tmp_path / "price_tiers.yml").write_text(
        """id: price_tiers
base_sql_table: s.price_tiers
dimensions:
- id: floor
  type: number
""",
        encoding="utf-8",
    )

    files = read_hex_project(tmp_path)
    ossie_yaml, warnings = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    assert load_yaml(ossie_yaml)["semantic_model"][0].get("relationships") is None
    assert [str(w) for w in warnings] == [
        (
            "relation 'orders.tier' join_sql could not be decomposed into "
            "column pairs; preserved in custom_extensions[HEX]"
        )
    ]

    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)

    assert yaml.safe_load(files["orders.yml"])["relations"] == [
        {
            "id": "tier",
            "target": "price_tiers",
            "type": "one_to_many",
            "visibility": "internal",
            "join_sql": "${amount} > ${tier.floor}",
        }
    ]


def test_null_typed_dimension_survives_the_roundtrip(tmp_path: Path) -> None:
    """`null` is the one Hex type with no Ossie datatype to carry it home.

    Its neighbour is the control: an ordinary type comes back from ``datatype``
    alone, so only the null one depends on the extension payload.
    """
    (tmp_path / "events.yml").write_text(
        """id: events
base_sql_table: s.events
dimensions:
- id: nothing
  type: 'null'
- id: label
  type: string
""",
        encoding="utf-8",
    )

    files = read_hex_project(tmp_path)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    files, _ = convert_ossie_to_hex(ossie_yaml, dialect=OSIDialect.ANSI_SQL)
    dimensions = yaml.safe_load(files["events.yml"])["dimensions"]

    assert [dimension["type"] for dimension in dimensions] == ["null", "string"]


def test_dimension_references_survive_the_roundtrip(tmp_path: Path) -> None:
    """A reference to this model's own dimension rebuilds itself; two shapes do not.

    Qualifying ``${amount}`` as ``orders.amount`` on the way out is what lets the
    export recognise it as a dimension again. A relation cannot be recovered that
    way: Ossie records a join between two datasets, not which of the model's
    relations an expression read through, so ``${buyer.name}`` comes back only
    because the extension recorded it.

    Neither does a bare column that a dimension of the same name reads
    differently. ``label`` the column and ``label`` the dimension are not the same
    thing here, and reading the identifier as the dimension would silently move
    ``raw_label`` onto ``order_label``.
    """
    (tmp_path / "orders.yml").write_text(
        """id: orders
base_sql_table: s.orders
dimensions:
- id: customer_id
  type: string
- id: amount
  type: number
- id: doubled
  type: number
  expr_sql: ${amount} * 2
- id: label
  type: string
  expr_sql: order_label
- id: raw_label
  type: string
  expr_sql: label
- id: buyer_name
  type: string
  expr_sql: UPPER(${buyer.name})
relations:
- id: buyer
  target: customers
  type: many_to_one
  join_sql: ${customer_id} = ${buyer.id}
""",
        encoding="utf-8",
    )
    (tmp_path / "customers.yml").write_text(
        """id: customers
base_sql_table: s.customers
dimensions:
- id: id
  type: string
- id: name
  type: string
""",
        encoding="utf-8",
    )

    files = read_hex_project(tmp_path)
    ossie_yaml, _ = convert_hex_to_ossie(
        files,
        dialect=OSIDialect.ANSI_SQL,
        model_name="roundtrip",
    )
    files, _ = convert_ossie_to_hex(
        ossie_yaml, dialect=OSIDialect.ANSI_SQL, base_model="orders"
    )
    expressions = {
        dimension["id"]: dimension.get("expr_sql")
        for dimension in yaml.safe_load(files["orders.yml"])["dimensions"]
    }

    assert expressions["doubled"] == "${amount} * 2"
    assert expressions["buyer_name"] == "UPPER(${buyer.name})"
    assert expressions["label"] == "order_label"
    assert expressions["raw_label"] == "label"


def test_tpcds_export(tpcds_ossie_yaml: str, tmp_path: Path) -> None:
    files, _warnings = convert_ossie_to_hex(
        tpcds_ossie_yaml,
        dialect=OSIDialect.ANSI_SQL,
        base_model="store_sales",
    )
    assert files
    out = tmp_path / "tpcds_hex"
    write_hex_project(out, files)
    # Every file validates as a Hex resource.
    for path in out.rglob("*.yml"):
        parse_hex_resource(yaml.safe_load(path.read_text()))

    # Re-import should validate as Ossie.
    files_2 = read_hex_project(out)
    ossie_yaml, _ = convert_hex_to_ossie(
        files_2, dialect=OSIDialect.ANSI_SQL, model_name="tpcds"
    )
    from ossie import OSIDocument

    OSIDocument.model_validate(yaml.safe_load(ossie_yaml))
