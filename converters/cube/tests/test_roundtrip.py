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
from _cube_gate import (
    assert_cube_compiles,
    assert_ossie_is_valid,
    cube_gate,
    validator_gate,
)
from _util import (REPO_ROOT, canon, load_fixture, load_fixture_dir, parse,
                   parse_files)

from ossie_cube import convert_cube_to_ossie, convert_ossie_to_cube

FIXTURES = ["fixtureA_cube", "tpcds_cube"]


@pytest.mark.parametrize("fixture", FIXTURES)
def test_cube_roundtrip_is_lossless(fixture):
    """Cube -> Ossie -> Cube reproduces the original model, structurally.

    Compared parsed rather than byte-for-byte: YAML comments (including the
    license headers on the fixtures) are not part of the data model, and key order
    within a mapping is not semantic.
    """
    files = load_fixture_dir(fixture)
    ossie, _ = convert_cube_to_ossie(files)
    files2, _ = convert_ossie_to_cube(ossie)
    assert parse_files(files2) == parse_files(files)


@pytest.mark.parametrize("cube_dir,ossie_file", [
    ("fixtureA_cube", "fixtureA_ossie.yaml"),
    ("tpcds_cube", "tpcds_ossie.yaml"),
])
def test_import_matches_the_committed_ossie_fixture(cube_dir, ossie_file):
    """Whole-document snapshot, so an unintended change anywhere in the output shows
    up as a readable diff rather than slipping past field-level assertions.

    Regenerate with `ossie-cube import -i tests/fixtures/<cube_dir>` when a change
    to the output is intended.
    """
    ossie, _ = convert_cube_to_ossie(load_fixture_dir(cube_dir))
    assert canon(parse(ossie)) == canon(parse(load_fixture(ossie_file)))


@pytest.mark.parametrize("cube_dir,ossie_file", [
    ("fixtureA_cube", "fixtureA_ossie.yaml"),
    ("tpcds_cube", "tpcds_ossie.yaml"),
])
def test_export_of_the_ossie_fixture_matches_the_cube_fixture(cube_dir, ossie_file):
    """The same snapshot in the other direction: the committed Ossie fixture has to
    export back to the committed Cube fixture."""
    files, _ = convert_ossie_to_cube(load_fixture(ossie_file))
    assert parse_files(files) == parse_files(load_fixture_dir(cube_dir))


@pytest.mark.parametrize("fixture", FIXTURES)
def test_imported_ossie_validates_against_core_spec_schema(fixture):
    jsonschema = pytest.importorskip("jsonschema")
    with open(REPO_ROOT / "core-spec" / "osi-schema.json") as fh:
        schema = json.load(fh)
    ossie, _ = convert_cube_to_ossie(load_fixture_dir(fixture))
    jsonschema.validate(parse(ossie), schema)


@validator_gate
@pytest.mark.parametrize("fixture", FIXTURES)
def test_imported_ossie_passes_the_repo_validator(fixture):
    """More than the schema: unique names across the document, relationship references
    that resolve, and every expression parseable as SQL."""
    ossie, _ = convert_cube_to_ossie(load_fixture_dir(fixture))
    assert_ossie_is_valid(ossie, fixture)


@cube_gate
@pytest.mark.parametrize("fixture", FIXTURES)
def test_the_fixture_and_its_round_trip_both_compile_in_cube(fixture):
    """The question a YAML comparison cannot ask. Both directions are checked, because
    the committed fixture being valid Cube is itself an assertion worth holding: the
    tpcds one was not, and nothing noticed until Cube was asked."""
    files = load_fixture_dir(fixture)
    assert_cube_compiles(files, f"{fixture} (as committed)")
    ossie, _ = convert_cube_to_ossie(files)
    back, _ = convert_ossie_to_cube(ossie)
    assert_cube_compiles(back, f"{fixture} (after a round trip)")


@validator_gate
def test_a_model_from_another_converter_is_valid_ossie():
    assert_ossie_is_valid(load_fixture("databricks_ossie.yaml"), "databricks_ossie.yaml")


@cube_gate
def test_a_model_from_another_converter_exports_to_a_model_cube_accepts():
    """The Databricks path end to end. Nothing here was written for Cube: the dialect is
    `DATABRICKS` throughout and the primary key comes from `unique_keys`, because a
    metric view has no primary-key concept and Cube demands one for a join."""
    files, _ = convert_ossie_to_cube(load_fixture("databricks_ossie.yaml"))
    assert_cube_compiles(files, "databricks_ossie.yaml")


@cube_gate
def test_a_hand_authored_ossie_model_exports_to_a_model_cube_accepts():
    """Nothing here came from Cube, so nothing is restored from a stash -- every key is
    one the exporter chose. That makes it the case most likely to produce something Cube
    rejects."""
    files, _ = convert_ossie_to_cube(load_fixture("hand_authored_ossie.yaml"))
    assert_cube_compiles(files, "hand_authored_ossie.yaml")


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
    ossie = load_fixture("hand_authored_ossie.yaml")
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
        # Both cubes carry an `id`, which a view cannot include twice.
        {"join_path": "orders.customers", "includes": "*", "prefix": True},
    ]


def test_hand_authored_ossie_survives_the_round_trip():
    files, _ = convert_ossie_to_cube(load_fixture("hand_authored_ossie.yaml"))
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
    files, _ = convert_ossie_to_cube(load_fixture("hand_authored_ossie.yaml"))
    orders = parse(files["model/cubes/orders.yml"])["cubes"][0]
    parked = orders["meta"]["ossie"]
    assert parked["unique_keys"] == [["order_number"]]
    assert parked["custom_extensions"][0]["vendor_name"] == "SNOWFLAKE"

    ossie2, _ = convert_cube_to_ossie(files)
    ds = {d["name"]: d for d in parse(ossie2)["semantic_model"][0]["datasets"]}
    assert ds["orders"]["unique_keys"] == [["order_number"]]
    vendors = {e["vendor_name"] for e in ds["orders"]["custom_extensions"]}
    assert "SNOWFLAKE" in vendors
