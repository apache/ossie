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

"""Fixture-based round-trip tests.

- Cube -> Ossie -> Cube must be lossless (the stash carries everything).
- Ossie -> Cube -> Ossie must be identical up to the documented normalizations.
- Every Ossie document the importer emits must validate against the core-spec
  JSON schema (skipped when jsonschema is not installed).
"""

import json

import pytest
from _util import REPO_ROOT, load_fixture_dir, parse, parse_files

from ossie_cube import convert_cube_to_ossie, convert_ossie_to_cube

FIXTURES = ["fixtureA_cube", "tpcds_cube"]


@pytest.mark.parametrize("fixture", FIXTURES)
def test_cube_roundtrip_is_lossless(fixture):
    """Cube -> Ossie -> Cube reproduces the original model, structurally.

    Compared parsed rather than byte-for-byte: YAML comments (including the
    licence headers on the fixtures) are not part of the data model, and key order
    within a mapping is not semantic.
    """
    files = load_fixture_dir(fixture)
    ossie, _ = convert_cube_to_ossie(files)
    files2, _ = convert_ossie_to_cube(ossie)
    assert parse_files(files2) == parse_files(files)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_imported_ossie_validates_against_core_spec_schema(fixture):
    jsonschema = pytest.importorskip("jsonschema")
    with open(REPO_ROOT / "core-spec" / "osi-schema.json") as fh:
        schema = json.load(fh)
    ossie, _ = convert_cube_to_ossie(load_fixture_dir(fixture))
    jsonschema.validate(parse(ossie), schema)


@pytest.mark.parametrize("fixture", FIXTURES)
def test_ossie_roundtrip_is_lossless(fixture):
    """Ossie -> Cube -> Ossie reproduces the model too.

    Cube has a `meta` field at every level, so the export direction parks what
    Cube has no slot for under `meta.ossie` instead of dropping it -- which makes
    this direction lossless as well, unlike converters whose target format has
    nowhere to put the leftovers.
    """
    ossie, _ = convert_cube_to_ossie(load_fixture_dir(fixture))
    files, _ = convert_ossie_to_cube(ossie)
    ossie2, _ = convert_cube_to_ossie(files)
    assert parse(ossie2) == parse(ossie)


def test_hand_authored_ossie_gets_a_generated_view():
    """A model with no stashed views is not from Cube, so export has to invent the
    view -- the model boundary Cube users work with."""
    ossie = _HAND_AUTHORED
    files, _ = convert_ossie_to_cube(ossie)
    assert set(files) == {
        "model/cubes/orders.yml", "model/cubes/customers.yml",
        "model/views/ecommerce.yml",
    }
    view = parse(files["model/views/ecommerce.yml"])["views"][0]
    assert view["name"] == "ecommerce"
    assert view["description"] == "Orders and customers"
    # Rooted at the FK sink, with the joined cube addressed by its join path.
    assert view["cubes"] == [
        {"join_path": "orders", "includes": "*"},
        {"join_path": "orders.customers", "includes": "*"},
    ]


def test_hand_authored_ossie_survives_the_round_trip():
    files, _ = convert_ossie_to_cube(_HAND_AUTHORED)
    ossie2, _ = convert_cube_to_ossie(files)
    model = parse(ossie2)["semantic_model"][0]
    assert model["name"] == "ecommerce"
    assert model["description"] == "Orders and customers"
    assert [d["name"] for d in model["datasets"]] == ["orders", "customers"]
    assert model["relationships"][0]["from_columns"] == ["customer_id"]
    metrics = {m["name"]: m for m in model["metrics"]}
    assert metrics["total_revenue"]["expression"]["dialects"][0]["expression"] == (
        "SUM(orders.amount)")


def test_ossie_only_constructs_are_parked_not_dropped():
    """`unique_keys` and a foreign vendor's extensions have no Cube field, so they
    ride under `meta.ossie` and come back intact."""
    files, _ = convert_ossie_to_cube(_HAND_AUTHORED)
    orders = parse(files["model/cubes/orders.yml"])["cubes"][0]
    parked = orders["meta"]["ossie"]
    assert parked["unique_keys"] == [["order_number"]]
    assert parked["custom_extensions"][0]["vendor_name"] == "SNOWFLAKE"

    ossie2, _ = convert_cube_to_ossie(files)
    ds = {d["name"]: d for d in parse(ossie2)["semantic_model"][0]["datasets"]}
    assert ds["orders"]["unique_keys"] == [["order_number"]]
    vendors = {e["vendor_name"] for e in ds["orders"]["custom_extensions"]}
    assert "SNOWFLAKE" in vendors


_HAND_AUTHORED = """
version: 0.2.0.dev0
semantic_model:
- name: ecommerce
  description: Orders and customers
  ai_context:
    instructions: Use for sales analysis.
    synonyms:
    - sales
    - purchases
  datasets:
  - name: orders
    source: sales.public.orders
    primary_key:
    - id
    unique_keys:
    - - order_number
    fields:
    - name: id
      expression:
        dialects:
        - dialect: ANSI_SQL
          expression: id
      datatype: Integer
    - name: customer_id
      expression:
        dialects:
        - dialect: ANSI_SQL
          expression: customer_id
      datatype: Integer
    - name: ordered_at
      expression:
        dialects:
        - dialect: ANSI_SQL
          expression: ordered_at
      datatype: Date
    custom_extensions:
    - vendor_name: SNOWFLAKE
      data: '{"warehouse": "ANALYTICS_WH"}'
  - name: customers
    source: sales.public.customers
    primary_key:
    - id
    fields:
    - name: id
      expression:
        dialects:
        - dialect: ANSI_SQL
          expression: id
      datatype: Integer
    - name: email
      expression:
        dialects:
        - dialect: ANSI_SQL
          expression: LOWER(email)
      datatype: String
  relationships:
  - name: orders_to_customers
    from: orders
    to: customers
    from_columns:
    - customer_id
    to_columns:
    - id
  metrics:
  - name: total_revenue
    expression:
      dialects:
      - dialect: ANSI_SQL
        expression: SUM(orders.amount)
    description: Total revenue
    datatype: Decimal
"""
