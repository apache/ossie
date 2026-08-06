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
import yaml
from _cube_gate import (
    assert_cube_compiles,
    assert_ossie_is_valid,
    cube_gate,
    validator_gate,
)
from _util import (REPO_ROOT, canon, load_fixture, load_fixture_dir, parse,
                   parse_files)

from ossie_cube import IssueType, convert_cube_to_ossie, convert_ossie_to_cube
from ossie_cube._common import OSSIE_VERSION

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


_INLINE_COMPOSITE = {
    "model/cubes/orders.yml": (
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: shop.public.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{CUBE}.user_id = {users}.id\"\n"
        "        relationship: many_to_one\n"
        "    dimensions:\n"
        "      - name: id\n        sql: id\n        type: number\n"
        "        primary_key: true\n"
        "      - name: user_id\n        sql: user_id\n        type: number\n"
        "    measures:\n"
        "      - name: spread\n"
        "        sql: \"MAX({CUBE}.amount) - MIN({CUBE}.amount)\"\n"
        "        type: number\n"
        "      - name: value_per_user\n"
        "        sql: \"SUM({CUBE}.amount) / COUNT(DISTINCT {users.id})\"\n"
        "        type: number\n"
    ),
    "model/cubes/users.yml": (
        "cubes:\n"
        "  - name: users\n"
        "    sql_table: shop.public.users\n"
        "    dimensions:\n"
        "      - name: id\n        sql: id\n        type: number\n"
        "        primary_key: true\n"
    ),
}


def test_an_inline_composite_measure_travels_with_no_extension():
    """Both composite shapes carry nothing in custom_extensions: the expression is
    the whole record. This is the review principle -- everything parseable from
    the expression stays out of the stash -- applied to the last measure shape
    that used to keep a rendered copy of its own SQL."""
    from _util import by_name, model_of

    ossie, _ = convert_cube_to_ossie(_INLINE_COMPOSITE)
    metrics = by_name(model_of(ossie)["metrics"])
    assert "custom_extensions" not in metrics["spread"]
    assert "custom_extensions" not in metrics["value_per_user"]


def test_a_single_cube_composite_round_trips_verbatim():
    """All of `spread`'s aggregates read the cube it is declared on, so hidden
    parts would buy nothing -- Cube's fan-out correction keys on the same cube
    either way -- and the measure comes back exactly as written."""
    ossie, _ = convert_cube_to_ossie(_INLINE_COMPOSITE)
    files2, _ = convert_ossie_to_cube(ossie)
    measures = {m["name"]: m
                for m in parse(files2["model/cubes/orders.yml"])["cubes"][0]["measures"]}
    assert measures["spread"] == {
        "name": "spread", "sql": "MAX({CUBE}.amount) - MIN({CUBE}.amount)",
        "type": "number"}


def test_a_cross_cube_composite_normalizes_to_the_decomposed_fixed_point():
    """`value_per_user` spans two cubes, so the round trip hands back the
    decomposed form -- one hidden measure per aggregate, each on its own cube --
    which is the fan-out-correct shape, not a loss. The normalization converges:
    the second cycle reproduces both the Cube files and the Ossie document
    exactly, and the committed tpcds fixture is this fixed point."""
    ossie, _ = convert_cube_to_ossie(_INLINE_COMPOSITE)
    files2, _ = convert_ossie_to_cube(ossie)
    orders = {m["name"]: m
              for m in parse(files2["model/cubes/orders.yml"])["cubes"][0]["measures"]}
    users = {m["name"]: m
             for m in parse(files2["model/cubes/users.yml"])["cubes"][0]["measures"]}
    assert orders["value_per_user"]["sql"] == (
        "{CUBE.value_per_user_part_1} / {users.value_per_user_part_2}")
    assert orders["value_per_user_part_1"]["public"] is False
    assert users["value_per_user_part_2"]["type"] == "count"

    # One step to the fixed point: the next cycle changes nothing on either side.
    ossie2, _ = convert_cube_to_ossie(files2)
    assert canon(parse(ossie2))["semantic_model"][0]["metrics"] == \
        canon(parse(ossie))["semantic_model"][0]["metrics"]
    files3, _ = convert_ossie_to_cube(ossie2)
    assert parse_files(files3) == parse_files(files2)


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


def test_hand_authored_ossie_survives_the_round_trip_identically():
    """The whole document, not chosen fields: a hand-authored model must come back
    *exactly* as written -- in particular with no stash it never had. The view a
    prior export generated is derivable by construction, so import predicts it
    (with export's own builder) and skips recording it."""
    source = load_fixture("hand_authored_ossie.yaml")
    files, _ = convert_ossie_to_cube(source)
    ossie2, _ = convert_cube_to_ossie(files)
    assert parse(ossie2) == parse(source)


def test_an_edited_generated_view_is_stashed_verbatim_again():
    """The skip is exact-match only: once a user curates the generated view in
    Cube, it is no longer derivable and rides in the stash like any other view."""
    files, _ = convert_ossie_to_cube(load_fixture("hand_authored_ossie.yaml"))
    files = dict(files)
    files["model/views/ecommerce.yml"] = files["model/views/ecommerce.yml"].replace(
        "includes: '*'", "includes: [id]", 1)
    ossie2, _ = convert_cube_to_ossie(files)
    from _util import stash_of
    stash = stash_of(parse(ossie2)["semantic_model"][0])
    assert stash["views"]["ecommerce"]["cubes"][0]["includes"] == ["id"]
    assert stash["mapped_view"] == "ecommerce"


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


@validator_gate
def test_a_model_from_another_converter_survives_the_round_trip_exactly():
    """`Ossie -> Cube -> Ossie` on a document written by another converter.

    Everything this fixture exercises is a place where the *forward* direction has to
    make a Cube-shaped choice, and each of those choices was one-way until provenance was
    recorded: a warehouse dialect became `ANSI_SQL`, a `unique_keys` promoted to satisfy
    Cube's join requirement came back as a declared `primary_key`, the dimension the
    promotion synthesized came back as a field, and a fact came back as a dimension
    because Cube has only the one kind.
    """
    src = load_fixture("databricks_ossie.yaml")
    files, _ = convert_ossie_to_cube(src)
    back, _ = convert_cube_to_ossie(files)
    assert_ossie_is_valid(back, "databricks_ossie.yaml round trip")

    before = parse(src)["semantic_model"][0]
    after = parse(back)["semantic_model"][0]

    def shape(model):
        return {
            "datasets": {ds["name"]: {
                "primary_key": ds.get("primary_key"),
                "unique_keys": ds.get("unique_keys"),
                "fields": {f["name"]: (f["expression"]["dialects"][0]["dialect"],
                                       "dimension" in f, f.get("datatype"))
                           for f in ds.get("fields", [])}}
                for ds in model["datasets"]},
            "metrics": {m["name"]: m["expression"]["dialects"][0]["dialect"]
                        for m in model.get("metrics", [])},
        }

    assert shape(after) == shape(before)


@cube_gate
def test_the_compile_gate_does_not_silently_drop_a_same_named_file():
    """A meta-test: the gate has to actually see every file it is handed.

    Cube keys model files by their path relative to the model root, and the gate passed
    basenames instead -- so `cubes/orders.yml` and `views/orders.yml` collided and one was
    dropped without a word. An invalid model then reported COMPILED OK, which is how the
    cube/view namespace collision above went unnoticed. The converter emits exactly this
    pair of names, so this is the arrangement that has to fail loudly.
    """
    files = {
        "model/cubes/orders.yml":
            "cubes:\n- name: orders\n  sql_table: public.orders\n"
            "  dimensions:\n  - name: id\n    sql: id\n    type: number\n"
            "    primary_key: true\n",
        # Same basename, different directory, and invalid: it includes a member no cube
        # defines. If the gate drops this file, it reports success.
        "model/views/orders.yml":
            "views:\n- name: orders_view\n  cubes:\n  - join_path: orders\n"
            "    includes:\n    - id\n    - no_such_member\n",
    }
    with pytest.raises(AssertionError, match="Cube refused the model"):
        assert_cube_compiles(files, "same-basename files")


def test_a_model_named_after_one_of_its_datasets_does_not_collide_in_cube():
    """Cube keeps cubes and views in one namespace, so a model named `orders` over a
    dataset named `orders` cannot emit both under that name -- Cube rejected the whole
    model with `Cannot read properties of undefined (reading 'toString')`.

    This is the shape every Databricks metric view over a same-named table produces, so
    the view is renamed rather than the model refused, and the model's own name is
    recorded so the trip back does not adopt the renamed view's.
    """
    src = load_fixture("databricks_ossie.yaml")
    assert parse(src)["semantic_model"][0]["name"] == "orders"

    files, issues = convert_ossie_to_cube(src)
    assert set(files) == {"model/cubes/orders.yml", "model/cubes/customer.yml",
                          "model/views/orders_view.yml"}
    view = parse(files["model/views/orders_view.yml"])["views"][0]
    assert view["name"] == "orders_view"
    assert view["meta"]["ossie"]["model_name"] == "orders"
    assert any("one namespace" in i.detail
               for i in issues.of_type(IssueType.PARKED_IN_META))

    # And the name comes back, rather than becoming `orders_view`.
    back, _ = convert_cube_to_ossie(files)
    assert parse(back)["semantic_model"][0]["name"] == "orders"


def test_a_renamed_view_stays_renamed_on_the_second_export():
    """The rename has to survive a second export too. It is not re-derived from the
    stash -- `meta.ossie` is stripped when a view is stashed -- so the recorded model
    name is what keeps cycle two from emitting a colliding view again."""
    files, _ = convert_ossie_to_cube(load_fixture("databricks_ossie.yaml"))
    second, _ = convert_ossie_to_cube(convert_cube_to_ossie(files)[0])
    assert set(second) == set(files)
    assert parse(second["model/views/orders_view.yml"])["views"][0][
        "name"] == "orders_view"


@cube_gate
def test_a_renamed_view_compiles_on_both_cycles():
    files, _ = convert_ossie_to_cube(load_fixture("databricks_ossie.yaml"))
    assert_cube_compiles(files, "model named after its own dataset")
    second, _ = convert_ossie_to_cube(convert_cube_to_ossie(files)[0])
    assert_cube_compiles(second, "model named after its own dataset (cycle 2)")


_SALES_MODEL = (
    f"version: {OSSIE_VERSION}\n"
    "semantic_model:\n"
    "- name: Sales Model\n"
    "  datasets:\n"
    "  - name: orders\n"
    "    source: shop.public.orders\n"
    "    primary_key:\n    - id\n"
    "    fields:\n"
    "    - name: id\n      dimension: {}\n      datatype: Integer\n"
    "      expression:\n        dialects:\n"
    "        - dialect: ANSI_SQL\n          expression: id\n"
)


def test_a_model_name_needing_sanitizing_is_preserved():
    """`Sales Model` is a legal Ossie name and cannot be a Cube identifier, so the view is
    `sales_model` -- and the model came back named `sales_model` too.

    The record was scoped to cube/view collisions, which is the rarer cause; plain
    sanitization is the common one and went unrecorded. Three cycles because the value has
    to survive being read back out of the stash, not just written once.
    """
    files, issues = convert_ossie_to_cube(_SALES_MODEL)
    view = parse(files["model/views/sales_model.yml"])["views"][0]
    assert view["name"] == "sales_model"
    assert view["meta"]["ossie"]["model_name"] == "Sales Model"
    assert any("preserved under meta.ossie.model_name" in i.detail
               for i in issues.of_type(IssueType.PARKED_IN_META))

    ossie = _SALES_MODEL
    for cycle in range(3):
        ossie, _ = convert_cube_to_ossie(convert_ossie_to_cube(ossie)[0])
        assert parse(ossie)["semantic_model"][0]["name"] == "Sales Model", (
            f"lost on cycle {cycle + 1}")


def test_a_name_override_that_sanitizes_to_the_view_name_is_preserved():
    """`--name 'Sales Model'` over a Cube model whose view is already `sales_model`.

    Both sides sanitize to `sales_model`, so comparing the *sanitized* forms saw no
    difference and recorded nothing -- the override was silently undone on the way back.
    The comparison is against the raw name for exactly this case.
    """
    cube = {
        "model/cubes/orders.yml":
            "cubes:\n- name: orders\n  sql_table: shop.public.orders\n"
            "  dimensions:\n  - name: id\n    sql: id\n    type: number\n"
            "    primary_key: true\n",
        "model/views/sales_model.yml":
            "views:\n- name: sales_model\n  cubes:\n  - join_path: orders\n"
            "    includes: '*'\n",
    }
    ossie, _ = convert_cube_to_ossie(cube, model_name="Sales Model")
    assert parse(ossie)["semantic_model"][0]["name"] == "Sales Model"
    for cycle in range(3):
        ossie, _ = convert_cube_to_ossie(convert_ossie_to_cube(ossie)[0])
        assert parse(ossie)["semantic_model"][0]["name"] == "Sales Model", (
            f"lost on cycle {cycle + 1}")


_CUBE_ONLY = {
    "model/cubes/orders.yml":
        "cubes:\n- name: orders\n  sql_table: shop.public.orders\n"
        "  dimensions:\n  - name: id\n    sql: id\n    type: number\n"
        "    primary_key: true\n",
}
_TWO_VIEWS = {
    **_CUBE_ONLY,
    "model/views/a.yml":
        "views:\n- name: view_a\n  cubes:\n  - join_path: orders\n"
        "    includes: '*'\n",
    "model/views/b.yml":
        "views:\n- name: view_b\n  cubes:\n  - join_path: orders\n"
        "    includes: '*'\n",
}


def _with_model_metadata(cube_files):
    """Import, then add the model-level metadata a user would edit in on the Ossie side."""
    ossie, _ = convert_cube_to_ossie(cube_files, model_name="Sales Model")
    doc = parse(ossie)
    model = doc["semantic_model"][0]
    model["description"] = "Sales overview with a {brace}"
    model["ai_context"] = {"instructions": "Prefer completed orders"}
    return json.loads(json.dumps(doc)), model


def _dump(doc):
    return yaml.safe_dump(doc, sort_keys=False)


@pytest.mark.parametrize("label,cube_files", [
    ("no views at all", _CUBE_ONLY),
    ("two views, none selected", _TWO_VIEWS),
])
def test_model_metadata_survives_when_no_view_can_carry_it(label, cube_files):
    """Model-level metadata has no Cube field; it rides on the view representing the model.

    A Cube model need not contain a view, and one with several views need not say which is
    the model -- and in both cases export emitted no view at all, so the name, description
    and AI context were dropped without a word. `--name 'Sales Model'` came back as the
    synthesized `cube_model`. They ride on a deterministic cube instead now.

    Three cycles, since the value has to survive being read back out of a cube's stash;
    and a literal brace, because Cube compiles every string in a model as an f-string.
    """
    doc, _ = _with_model_metadata(cube_files)
    ossie = _dump(doc)
    for cycle in range(3):
        files, issues = convert_ossie_to_cube(ossie)
        ossie, _ = convert_cube_to_ossie(files)
        model = parse(ossie)["semantic_model"][0]
        assert model["name"] == "Sales Model", f"{label}: lost on cycle {cycle + 1}"
        assert model["description"] == "Sales overview with a {brace}"
        assert model["ai_context"]["instructions"] == "Prefer completed orders"

    # Reported, not silent -- the whole complaint about the old behaviour.
    assert any(i.element_name == "cube 'orders'" and "no view to carry" in i.detail
               for i in issues.of_type(IssueType.PARKED_IN_META))


def test_the_carrier_is_the_alphabetically_first_cube():
    """Deterministic, and independent of dataset order and of the relationship graph, so
    every export picks the same cube. Import does not depend on the choice."""
    cube_files = {
        "model/cubes/zeta.yml":
            "cubes:\n- name: zeta\n  sql_table: s.p.zeta\n  dimensions:\n"
            "  - name: id\n    sql: id\n    type: number\n    primary_key: true\n",
        "model/cubes/alpha.yml":
            "cubes:\n- name: alpha\n  sql_table: s.p.alpha\n  dimensions:\n"
            "  - name: id\n    sql: id\n    type: number\n    primary_key: true\n",
    }
    doc, _ = _with_model_metadata(cube_files)
    files, _ = convert_ossie_to_cube(_dump(doc))
    carried = {
        name: parse(text)["cubes"][0].get("meta", {}).get("ossie", {}).get("model")
        for name, text in files.items()}
    assert carried["model/cubes/alpha.yml"]["name"] == "Sales Model"
    assert carried["model/cubes/zeta.yml"] is None


def test_a_cube_only_model_without_metadata_gains_nothing():
    """The record appears only when there is something unrecoverable to keep, so a Cube
    model that never had model-level metadata still round-trips byte-identical instead of
    acquiring a `meta.ossie` key it never had. Every feature fixture is cube-only, so this
    is what keeps their structural round trips honest."""
    files, _ = convert_ossie_to_cube(convert_cube_to_ossie(_CUBE_ONLY)[0])
    assert parse_files(files) == parse_files(_CUBE_ONLY)


@cube_gate
def test_a_model_carried_on_a_cube_still_compiles():
    """The carrier is new YAML in the emitted model, and it holds a literal brace."""
    doc, _ = _with_model_metadata(_CUBE_ONLY)
    files, _ = convert_ossie_to_cube(_dump(doc))
    assert_cube_compiles(files, "model metadata carried on a cube")


def test_a_model_name_already_matching_its_view_records_nothing():
    """The record only appears when the names actually differ, so an ordinary model keeps
    a clean Cube document with no `meta.ossie` on its view at all."""
    files, _ = convert_ossie_to_cube(_SALES_MODEL.replace("Sales Model", "sales_model"))
    view = parse(files["model/views/sales_model.yml"])["views"][0]
    assert "meta" not in view


def test_a_model_from_another_converter_is_stable_after_one_cycle():
    """`Ossie -> Cube -> Ossie` twice, compared byte-for-byte.

    The one-cycle comparison above cannot see a value that survives the first trip and
    is dropped on the second: the `DATABRICKS` label on a metric restored verbatim from
    the stash came back as `ANSI_SQL` on cycle two, because the verbatim path hands back
    stashed Cube SQL instead of picking a dialect and so had no label to re-park.
    """
    first, _ = convert_cube_to_ossie(
        convert_ossie_to_cube(load_fixture("databricks_ossie.yaml"))[0])
    second, _ = convert_cube_to_ossie(convert_ossie_to_cube(first)[0])
    assert second == first


_SHADOWED_KEY_COLUMN = (
    f"version: {OSSIE_VERSION}\n"
    "semantic_model:\n"
    "- name: shop\n"
    "  datasets:\n"
    "  - name: orders\n"
    "    source: shop.public.orders\n"
    "    primary_key:\n    - id\n"
    "    fields:\n"
    "    - name: id\n      expression:\n        dialects:\n"
    "        - dialect: ANSI_SQL\n          expression: LOWER(email)\n"
    "      datatype: String\n"
)


def _two_export_cycles(ossie):
    first, _ = convert_ossie_to_cube(ossie)
    second, _ = convert_ossie_to_cube(convert_cube_to_ossie(first)[0])
    return first, second


def test_two_export_cycles_produce_the_same_cube_model():
    """`Ossie -> Cube -> Ossie -> Cube`, on the collision that made the first differ from
    the second: a key column `id` alongside a computed field also named `id`.

    Comparing only the Ossie ends cannot see this class. A record meant to be read one way
    that the next export reads another leaves both Ossie documents identical while the Cube
    model changes -- here the key moved off the column and onto `LOWER(email)`, so Cube
    deduplicated on a different value and returned different counts.

    Ungated: comparing two exports needs no Cube installation, and putting the whole test
    behind the optional gate meant the regression it exists for went unchecked on every
    machine without a built Cube checkout -- including CI.
    """
    first, second = _two_export_cycles(_SHADOWED_KEY_COLUMN)
    assert parse_files(second) == parse_files(first)

    keys = [d for d in parse_files(first)["model/cubes/orders.yml"]["cubes"][0][
        "dimensions"] if d.get("primary_key")]
    assert [(d["name"], d["sql"]) for d in keys] == [("id_pk", "id")]


@cube_gate
def test_the_second_export_cycle_still_compiles():
    """The half of the above that genuinely needs Cube."""
    _, second = _two_export_cycles(_SHADOWED_KEY_COLUMN)
    assert_cube_compiles(second, "second export cycle")
