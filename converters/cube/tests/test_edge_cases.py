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

"""Coverage-driven tests for paths the fixtures and property tests do not reach.

The fixture and Hypothesis suites cover the common shapes well, but they generate
inside the round-trippable subset and so never exercise several load-bearing
branches: composite primary keys (central to the fan-out mapping), `count` over an
expression, the export side of the one_to_many flip, off-layout file grouping, the
JavaScript-style mapping form of a collection, and Jinja detection. Each of those
is pinned here, along with the error paths for malformed input.
"""

import pytest
from _util import by_name, expr_of, model_of, parse, parse_files, stash_of

from ossie_cube import (
    ConversionError,
    IssueType,
    convert_cube_to_ossie,
    convert_ossie_to_cube,
)


def _files(**named):
    return {f"model/cubes/{n}.yml": t for n, t in named.items()}


def _roundtrip(files):
    ossie, issues = convert_cube_to_ossie(files, strict_fanout=False)
    back, _ = convert_ossie_to_cube(ossie)
    return ossie, back, issues


# --- composite primary keys -----------------------------------------------------

_COMPOSITE = _files(order_lines=(
    "cubes:\n"
    "  - name: order_lines\n"
    "    sql_table: public.order_lines\n"
    "    dimensions:\n"
    "      - name: order_id\n"
    "        sql: order_id\n"
    "        type: number\n"
    "        primary_key: true\n"
    "      - name: line_no\n"
    "        sql: line_no\n"
    "        type: number\n"
    "        primary_key: true\n"
    "    measures:\n"
    "      - name: count\n"
    "        type: count\n"
))


def test_composite_primary_key_becomes_a_concatenated_distinct_count():
    """Cube concatenates a composite key with CAST + CONCAT in `primaryKeyCount`;
    the Ossie expression mirrors that so the count stays correct under fan-out and
    stays portable (both functions are REQUIRED in the expression language)."""
    ossie, _ = convert_cube_to_ossie(_COMPOSITE)
    model = model_of(ossie)
    assert by_name(model["datasets"])["order_lines"]["primary_key"] == [
        "order_id", "line_no"]
    assert expr_of(model["metrics"][0]) == (
        "COUNT(DISTINCT CONCAT(CAST(order_lines.order_id AS VARCHAR), "
        "CAST(order_lines.line_no AS VARCHAR)))")


def test_composite_key_count_converts_back_to_a_bare_count():
    _, back, _ = _roundtrip(_COMPOSITE)
    cube = parse(back["model/cubes/order_lines.yml"])["cubes"][0]
    assert cube["measures"] == [{"name": "count", "type": "count"}]
    assert [d["name"] for d in cube["dimensions"] if d.get("primary_key")] == [
        "order_id", "line_no"]


def test_composite_key_roundtrips():
    _, back, _ = _roundtrip(_COMPOSITE)
    assert parse_files(back) == parse_files(_COMPOSITE)


# --- count over an expression ---------------------------------------------------

_COUNT_SQL = _files(orders=(
    "cubes:\n"
    "  - name: orders\n"
    "    sql_table: public.orders\n"
    "    dimensions:\n"
    "      - name: id\n"
    "        sql: id\n"
    "        type: number\n"
    "        primary_key: true\n"
    "    measures:\n"
    "      - name: statuses\n"
    "        sql: \"{CUBE}.status\"\n"
    "        type: count\n"
))


def test_count_over_an_expression_keeps_its_operand():
    """`type: count` with `sql` is COUNT(x), not COUNT(*) -- Cube only routes
    through the primary key when no sql is given."""
    ossie, _ = convert_cube_to_ossie(_COUNT_SQL)
    assert expr_of(model_of(ossie)["metrics"][0]) == "COUNT(orders.status)"


def test_count_over_an_expression_roundtrips():
    _, back, _ = _roundtrip(_COUNT_SQL)
    assert parse_files(back) == parse_files(_COUNT_SQL)


def test_count_over_an_expression_is_fanout_unsafe():
    """Unlike a bare count, COUNT(x) over a fanned-out dataset over-counts."""
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{CUBE}.user_id = {users}.id\"\n"
        "        relationship: many_to_one\n"
        "    dimensions:\n"
        "      - name: user_id\n"
        "        sql: user_id\n"
        "        type: number\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "    dimensions:\n"
        "      - name: id\n"
        "        sql: id\n"
        "        type: number\n"
        "        primary_key: true\n"
        "    measures:\n"
        "      - name: emails\n"
        "        sql: \"{CUBE}.email\"\n"
        "        type: count\n"
    ))
    with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
        convert_cube_to_ossie(files)
    _, issues = convert_cube_to_ossie(files, strict_fanout=False)
    assert issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)


# --- join orientation, both ways ------------------------------------------------

_ONE_TO_MANY = _files(m=(
    "cubes:\n"
    "  - name: users\n"
    "    sql_table: public.users\n"
    "    joins:\n"
    "      - name: orders\n"
    "        sql: \"{CUBE}.id = {orders}.user_id\"\n"
    "        relationship: one_to_many\n"
    "    dimensions:\n"
    "      - name: id\n"
    "        sql: id\n"
    "        type: number\n"
    "        primary_key: true\n"
    "  - name: orders\n"
    "    sql_table: public.orders\n"
    "    dimensions:\n"
    "      - name: user_id\n"
    "        sql: user_id\n"
    "        type: number\n"
))


def test_one_to_many_is_flipped_back_onto_its_original_cube():
    """Ossie's `from` is always the many side, so import flips a one_to_many. Export
    has to flip it back -- onto `users`, not `orders`."""
    _, back, _ = _roundtrip(_ONE_TO_MANY)
    cubes = by_name(parse(back["model/cubes/m.yml"])["cubes"])
    assert cubes["users"]["joins"] == [{
        "name": "orders", "sql": "{CUBE}.id = {orders}.user_id",
        "relationship": "one_to_many"}]
    assert "joins" not in cubes["orders"]


def test_one_to_one_keeps_its_declared_orientation():
    files = _files(m=_ONE_TO_MANY["model/cubes/m.yml"].replace(
        "one_to_many", "one_to_one"))
    ossie, issues = convert_cube_to_ossie(files)
    rel = model_of(ossie)["relationships"][0]
    assert (rel["from"], rel["to"]) == ("users", "orders")
    assert any("one_to_one" in i.detail for i in issues.of_type(
        IssueType.PARKED_IN_META))
    _, back, _ = _roundtrip(files)
    assert parse_files(back) == parse_files(files)


@pytest.mark.parametrize("alias,emitted", [
    ("belongsTo", "belongs_to"),
    ("belongs_to", "belongs_to"),
    ("hasMany", "has_many"),
    ("hasOne", "has_one"),
])
def test_legacy_relationship_spellings_are_accepted_and_kept_semantically(
        alias, emitted):
    """Cube still accepts belongsTo/hasMany/hasOne. The *kind* of relationship is
    preserved rather than modernized to many_to_one, but the spelling is normalized
    to snake_case along with every other key -- the documented normalization."""
    files = _files(m=_ONE_TO_MANY["model/cubes/m.yml"].replace(
        "one_to_many", alias))
    _, back, _ = _roundtrip(files)
    joins = [c.get("joins") for c in parse(back["model/cubes/m.yml"])["cubes"]
             if c.get("joins")]
    assert joins[0][0]["relationship"] == emitted


def test_two_joins_between_one_pair_get_distinct_relationship_names():
    """Ossie relationship names are unique per model, so a second join between the
    same two cubes is suffixed rather than colliding."""
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{CUBE}.buyer_id = {users}.id\"\n"
        "        relationship: many_to_one\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "    joins:\n"
        "      - name: orders\n"
        "        sql: \"{CUBE}.id = {orders}.seller_id\"\n"
        "        relationship: one_to_many\n"
    ))
    ossie, _ = convert_cube_to_ossie(files)
    names = [r["name"] for r in model_of(ossie)["relationships"]]
    assert names == ["orders_to_users", "orders_to_users_2"]


def test_unconvertible_join_is_restored_at_its_original_position():
    """A non-equi join has no Ossie form, so it rides in the stash -- and export has
    to put it back among the converted joins, in order."""
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    joins:\n"
        "      - name: rates\n"
        "        sql: \"{CUBE}.day >= {rates}.valid_from\"\n"
        "        relationship: many_to_one\n"
        "      - name: users\n"
        "        sql: \"{CUBE}.user_id = {users}.id\"\n"
        "        relationship: many_to_one\n"
        "  - name: rates\n"
        "    sql_table: public.rates\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
    ))
    _, back, issues = _roundtrip(files)
    assert parse_files(back) == parse_files(files)
    orders = by_name(parse(back["model/cubes/m.yml"])["cubes"])["orders"]
    assert [j["name"] for j in orders["joins"]] == ["rates", "users"]
    assert issues.of_type(IssueType.PARKED_IN_META)


def test_join_clause_written_target_side_first_still_decomposes():
    """Either side of the equality may name either cube."""
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{users.id} = {CUBE}.user_id\"\n"
        "        relationship: many_to_one\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "    dimensions:\n"
        "      - name: id\n"
        "        sql: id\n"
        "        type: number\n"
    ))
    ossie, back, _ = _roundtrip(files)
    rel = model_of(ossie)["relationships"][0]
    assert (rel["from_columns"], rel["to_columns"]) == (["user_id"], ["id"])
    assert parse_files(back) == parse_files(files)


def test_join_clause_not_spanning_both_cubes_is_preserved():
    """A clause has to relate the two joined cubes. One comparing a cube to itself
    (or reaching a third cube) is a valid Cube join with no Ossie relationship form,
    so it is preserved verbatim instead of guessed at."""
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{CUBE}.a = {CUBE}.b\"\n"
        "        relationship: many_to_one\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
    ))
    ossie, back, issues = _roundtrip(files)
    assert "relationships" not in model_of(ossie)
    assert any("references cubes other than" in i.detail for i in issues)
    assert parse_files(back) == parse_files(files)


def test_join_clause_reaching_an_unrelated_cube_is_preserved():
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{CUBE}.user_id = {regions}.id\"\n"
        "        relationship: many_to_one\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "  - name: regions\n"
        "    sql_table: public.regions\n"
    ))
    ossie, back, issues = _roundtrip(files)
    assert "relationships" not in model_of(ossie)
    assert any("not between two member references" in i.detail for i in issues)
    assert parse_files(back) == parse_files(files)


def test_join_clause_that_is_not_a_single_equality_is_preserved():
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{CUBE}.a = {users}.b = 1\"\n"
        "        relationship: many_to_one\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
    ))
    ossie, back, issues = _roundtrip(files)
    assert "relationships" not in model_of(ossie)
    assert any("not a single equality" in i.detail for i in issues)
    assert parse_files(back) == parse_files(files)


def test_metric_without_a_usable_dialect_is_dropped_with_an_issue():
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: public.orders\n"
        "  metrics:\n"
        "  - name: m\n"
        "    expression:\n"
        "      dialects:\n"
        "      - dialect: MAQL\n"
        "        expression: SELECT SUM(x)\n"
    )
    files, issues = convert_ossie_to_cube(ossie)
    assert "measures" not in parse(files["model/cubes/orders.yml"])["cubes"][0]
    assert issues.of_type(IssueType.NO_USABLE_DIALECT)


# --- file layout ----------------------------------------------------------------

def test_off_layout_files_are_restored_with_their_grouping():
    """Import accepts any layout. Several cubes in one oddly-named file have to go
    back into that same file, not be split into the canonical per-cube layout."""
    files = {
        "schema/warehouse/everything.yaml": (
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "  - name: users\n"
            "    sql_table: public.users\n"
            "views:\n"
            "  - name: main\n"
            "    description: All of it\n"
        ),
    }
    ossie, back, _ = _roundtrip(files)
    assert set(back) == {"schema/warehouse/everything.yaml"}
    assert parse_files(back) == parse_files(files)
    stash = stash_of(model_of(ossie))
    assert stash["cube_files"]["orders"] == "schema/warehouse/everything.yaml"
    assert stash["view_files"]["main"] == "schema/warehouse/everything.yaml"


_MIXED_VIEW_FILE = (
    "views:\n"
    "  - name: sales\n"
    "    description: Sales overview\n"
    "    meta:\n"
    "      ai_context: Use for revenue questions.\n"
    "    cubes:\n"
    "      - join_path: orders\n"
    "        includes: '*'\n"
    "cubes:\n"
    "  - name: orders\n"
    "    sql_table: public.orders\n"
    "    dimensions:\n"
    "      - name: id\n"
    "        sql: id\n"
    "        type: number\n"
    "        primary_key: true\n"
    "    measures:\n"
    "      - name: revenue\n"
    "        sql: \"{CUBE}.amount\"\n"
    "        type: sum\n"
)


def test_a_view_file_may_also_define_cubes():
    """`cubes:` and `views:` are independent top-level keys, so one file can hold
    both -- a self-contained model. Note the view's own nested `cubes:` (its
    include list) is a different key at a different level and is not confused with
    cube definitions."""
    files = {"model/views/sales.yml": _MIXED_VIEW_FILE}
    ossie, _ = convert_cube_to_ossie(files)
    model = model_of(ossie)
    # The view supplied the model identity...
    assert model["name"] == "sales"
    assert model["description"] == "Sales overview"
    assert model["ai_context"]["instructions"] == "Use for revenue questions."
    # ...and the cube in the same file became the dataset.
    assert [d["name"] for d in model["datasets"]] == ["orders"]
    assert expr_of(model["metrics"][0]) == "SUM(orders.amount)"
    # The view's include list round-trips as curation, not as a dataset.
    assert stash_of(model)["views"]["sales"]["cubes"] == [
        {"join_path": "orders", "includes": "*"}]


def test_a_mixed_file_is_rebuilt_as_one_file():
    """Both halves have to go back into the single file they came from, rather than
    being split into the canonical per-cube and per-view layout."""
    files = {"model/views/sales.yml": _MIXED_VIEW_FILE}
    _, back, _ = _roundtrip(files)
    assert set(back) == {"model/views/sales.yml"}
    assert parse_files(back) == parse_files(files)
    rebuilt = parse(back["model/views/sales.yml"])
    assert [c["name"] for c in rebuilt["cubes"]] == ["orders"]
    assert [v["name"] for v in rebuilt["views"]] == ["sales"]


def test_a_cube_file_may_also_define_views():
    """The mirror image: the canonical cube path holding the view. The view's path is
    the off-layout one here, so it is the one that gets stashed."""
    files = {"model/cubes/orders.yml": _MIXED_VIEW_FILE}
    ossie, back, _ = _roundtrip(files)
    assert stash_of(model_of(ossie))["view_files"]["sales"] == (
        "model/cubes/orders.yml")
    assert "cube_files" not in stash_of(model_of(ossie))
    assert set(back) == {"model/cubes/orders.yml"}
    assert parse_files(back) == parse_files(files)


def test_a_single_monolithic_file_round_trips():
    """Neither path is canonical, so both are stashed and both return to the one
    file -- the shape you get from `-i model.yml`."""
    files = {"model.yml": _MIXED_VIEW_FILE}
    ossie, back, _ = _roundtrip(files)
    stash = stash_of(model_of(ossie))
    assert stash["cube_files"]["orders"] == "model.yml"
    assert stash["view_files"]["sales"] == "model.yml"
    assert set(back) == {"model.yml"}
    assert parse_files(back) == parse_files(files)


def test_non_model_yaml_is_preserved_verbatim():
    files = {
        "model/cubes/orders.yml": (
            "cubes:\n  - name: orders\n    sql_table: public.orders\n"),
        "model/notes.yaml": "just: some data\n",
    }
    ossie, back, issues = _roundtrip(files)
    assert back["model/notes.yaml"] == "just: some data\n"
    assert issues.of_type(IssueType.PARKED_IN_META)


# --- the JavaScript-style mapping form ------------------------------------------

def test_collections_may_be_mappings_keyed_by_name():
    """Cube's post-transpile schema keys dimensions/measures/joins by name, and a
    model converted from JavaScript can carry that shape. Both forms are accepted;
    export always emits the list form YAML models use."""
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    dimensions:\n"
        "      id:\n"
        "        sql: id\n"
        "        type: number\n"
        "        primary_key: true\n"
        "      status:\n"
        "        sql: status\n"
        "        type: string\n"
        "    measures:\n"
        "      count:\n"
        "        type: count\n"
    ))
    ossie, _ = convert_cube_to_ossie(files)
    model = model_of(ossie)
    fields = by_name(by_name(model["datasets"])["orders"]["fields"])
    assert set(fields) == {"id", "status"}
    assert expr_of(model["metrics"][0]) == "COUNT(DISTINCT orders.id)"

    back, _ = convert_ossie_to_cube(ossie)
    cube = parse(back["model/cubes/m.yml"])["cubes"][0]
    assert isinstance(cube["dimensions"], list)
    assert isinstance(cube["measures"], list)


def test_a_collection_of_the_wrong_shape_is_rejected():
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    dimensions: not-a-collection\n"
    ))
    with pytest.raises(ConversionError, match="expected a list or mapping"):
        convert_cube_to_ossie(files)


# --- Jinja ----------------------------------------------------------------------

def test_jinja_anywhere_disqualifies_the_whole_file():
    """Jinja is detected per *file*, not per member -- Cube's own CubeSchemaConverter
    uses the same file-level rule. So templating inside a single dimension's `sql`
    still costs the whole file, which is preserved verbatim rather than
    half-converted. There is deliberately no member-level Jinja path."""
    templated = (
        "cubes:\n"
        "  - name: templated\n"
        "    sql_table: public.orders\n"
        "    dimensions:\n"
        "      - name: dyn\n"
        "        sql: \"{{ 'x' }}\"\n"
        "        type: string\n"
    )
    files = {
        "model/cubes/templated.yml": templated,
        "model/cubes/plain.yml": (
            "cubes:\n  - name: plain\n    sql_table: public.plain\n"),
    }
    ossie, issues = convert_cube_to_ossie(files)
    model = model_of(ossie)
    assert [d["name"] for d in model["datasets"]] == ["plain"]
    assert stash_of(model)["extra_files"]["model/cubes/templated.yml"] == templated
    assert issues.of_type(IssueType.TEMPLATED_FILE_SKIPPED)

    # And it comes back byte-for-byte, since it was never parsed.
    back, _ = convert_ossie_to_cube(ossie)
    assert back["model/cubes/templated.yml"] == templated


# --- metadata corners -----------------------------------------------------------

def test_measure_title_survives_the_round_trip():
    files = _files(orders=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    measures:\n"
        "      - name: revenue\n"
        "        sql: \"{CUBE}.amount\"\n"
        "        type: sum\n"
        "        title: Total Revenue\n"
    ))
    ossie, back, _ = _roundtrip(files)
    assert stash_of(model_of(ossie)["metrics"][0])["title"] == "Total Revenue"
    assert parse_files(back) == parse_files(files)


def test_geo_dimension_extras_survive_the_split_and_merge():
    files = _files(users=(
        "cubes:\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "    dimensions:\n"
        "      - name: home\n"
        "        type: geo\n"
        "        title: Home Location\n"
        "        description: Where they live\n"
        "        latitude:\n"
        "          sql: \"{CUBE}.lat\"\n"
        "        longitude:\n"
        "          sql: \"{CUBE}.lon\"\n"
    ))
    _, back, issues = _roundtrip(files)
    assert parse_files(back) == parse_files(files)
    assert issues.of_type(IssueType.GEO_DIMENSION_SPLIT)


_GEO_MODEL = (
    "version: 0.2.0.dev0\n"
    "semantic_model:\n"
    "- name: shop\n"
    "  datasets:\n"
    "  - name: users\n"
    "    source: public.users\n"
    "    fields:\n"
    "    - name: home_latitude\n"
    "      expression:\n"
    "        dialects:\n"
    "        - dialect: ANSI_SQL\n"
    "          expression: lat\n"
    "      datatype: Float\n"
    "      custom_extensions:\n"
    "      - vendor_name: CUBE\n"
    "        data: '{\"_v\": 1, \"geo\": {\"of\": \"home\", \"part\": \"latitude\","
    " \"sql\": \"{CUBE}.lat\"}}'\n"
    "    - name: home_longitude\n"
    "      expression:\n"
    "        dialects:\n"
    "        - dialect: ANSI_SQL\n"
    "          expression: lon\n"
    "      datatype: Float\n"
    "      custom_extensions:\n"
    "      - vendor_name: CUBE\n"
    "        data: '{\"_v\": 1, \"geo\": {\"of\": \"home\", \"part\": \"longitude\","
    " \"sql\": \"{CUBE}.lon\"}}'\n"
    "  metrics:\n"
    "  - name: avg_lat\n"
    "    expression:\n"
    "      dialects:\n"
    "      - dialect: ANSI_SQL\n"
    "        expression: AVG(users.home_latitude)\n"
)


def test_a_metric_referencing_a_geo_half_inlines_its_sql():
    """A split geo half's name exists only in Ossie: Cube has neither a column nor a
    member called `home_latitude`, since the halves merge into the `home` dimension.
    So a reference to one is replaced by the half's own SQL, which is valid Cube."""
    files, _ = convert_ossie_to_cube(_GEO_MODEL)
    cube = parse(files["model/cubes/users.yml"])["cubes"][0]
    assert cube["measures"] == [
        {"name": "avg_lat", "sql": "{CUBE}.lat", "type": "avg"}]
    # And the dimension itself still merges back to a single geo member.
    assert cube["dimensions"] == [{
        "name": "home", "type": "geo",
        "latitude": {"sql": "{CUBE}.lat"},
        "longitude": {"sql": "{CUBE}.lon"}}]


def test_a_geo_half_reference_is_requalified_when_it_crosses_cubes():
    """`{CUBE}` means "the cube this is declared on", so inlining a snippet into
    another cube's SQL has to name the original cube explicitly."""
    model = _GEO_MODEL.replace(
        "  - name: users\n", "  - name: orders\n    source: public.orders\n"
        "    fields:\n"
        "    - name: amount\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: amount\n"
        "      datatype: Decimal\n"
        "  - name: users\n", 1
    ).replace("        expression: AVG(users.home_latitude)\n",
              "        expression: AVG(users.home_latitude) - MIN(orders.amount)\n")
    files, _ = convert_ossie_to_cube(model, base_cube="orders")
    measures = parse(files["model/cubes/orders.yml"])["cubes"][0]["measures"]
    # `{users}.lat` names the cube explicitly, since `{CUBE}` here would mean
    # `orders`. `{CUBE.amount}` stays a member reference because `amount` is a
    # declared field of the cube the measure lands on.
    assert measures[0]["sql"] == "AVG({users}.lat) - MIN({CUBE.amount})"
    assert measures[0]["type"] == "number"


def test_geo_half_references_normalize_to_the_underlying_column():
    """Documented normalization: after a round trip the metric names the column the
    geo half actually reads rather than the Ossie-only field name. Semantically the
    same reference, and it is what Cube can express."""
    files, _ = convert_ossie_to_cube(_GEO_MODEL)
    ossie2, _ = convert_cube_to_ossie(files)
    metric = model_of(ossie2)["metrics"][0]
    assert expr_of(metric) == "AVG(users.lat)"


def test_geo_dimension_missing_a_half_is_rejected():
    files = _files(users=(
        "cubes:\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "    dimensions:\n"
        "      - name: home\n"
        "        type: geo\n"
        "        latitude:\n"
        "          sql: lat\n"
    ))
    with pytest.raises(ConversionError, match="missing 'longitude.sql'"):
        convert_cube_to_ossie(files)


def test_ai_context_examples_reach_cube_as_prose_and_park_structurally():
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  ai_context:\n"
        "    instructions: Sales model.\n"
        "    examples:\n"
        "    - What were sales last month?\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: public.orders\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    meta = parse(files["model/views/shop.yml"])["views"][0]["meta"]
    assert meta["ai_context"] == (
        "Sales model.\nExample questions: What were sales last month?")
    assert meta["ossie"]["ai_context"]["examples"] == [
        "What were sales last month?"]
    # And the structured form is what comes back, not the flattened prose.
    ossie2, _ = convert_cube_to_ossie(files)
    assert model_of(ossie2)["ai_context"]["examples"] == [
        "What were sales last month?"]


def test_a_plain_string_ai_context_survives_as_a_string():
    """Ossie allows `ai_context` to be a bare string. Import reads Cube's prose back
    as {'instructions': ...}, so the original scalar has to be parked to survive."""
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: public.orders\n"
        "    ai_context: orders, purchases, sales\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    ossie2, _ = convert_cube_to_ossie(files)
    ds = by_name(model_of(ossie2)["datasets"])["orders"]
    assert ds["ai_context"] == "orders, purchases, sales"


# --- multiple views -------------------------------------------------------------

_TWO_VIEWS = {
    "model/cubes/orders.yml": (
        "cubes:\n  - name: orders\n    sql_table: public.orders\n"),
    "model/views/a.yml": "views:\n  - name: a\n    description: View A\n",
    "model/views/b.yml": "views:\n  - name: b\n    description: View B\n",
}


def test_several_views_need_an_explicit_choice():
    _, issues = convert_cube_to_ossie(_TWO_VIEWS)
    assert any("none chosen with --view" in i.detail
               for i in issues.of_type(IssueType.PARKED_IN_META))


def test_choosing_a_view_maps_its_metadata_onto_the_model():
    ossie, _ = convert_cube_to_ossie(_TWO_VIEWS, view="b")
    model = model_of(ossie)
    assert model["name"] == "b"
    assert model["description"] == "View B"
    # The unchosen view is still preserved whole.
    assert set(stash_of(model)["views"]) == {"a", "b"}


def test_both_views_are_restored_on_export():
    ossie, _ = convert_cube_to_ossie(_TWO_VIEWS, view="b")
    back, _ = convert_ossie_to_cube(ossie)
    assert parse_files(back) == parse_files(_TWO_VIEWS)


# --- malformed input ------------------------------------------------------------

def test_malformed_yaml_is_reported_cleanly():
    with pytest.raises(ConversionError, match="Invalid YAML"):
        convert_cube_to_ossie({"model/cubes/m.yml": "cubes: [oops\n"})


def test_empty_input_is_rejected():
    with pytest.raises(ConversionError, match="non-empty mapping"):
        convert_cube_to_ossie({})


def test_a_non_string_name_is_rejected_cleanly():
    files = _files(m="cubes:\n  - name: 42\n    sql_table: t\n")
    with pytest.raises(ConversionError, match="must be a string"):
        convert_cube_to_ossie(files)


def test_ossie_root_must_be_a_mapping():
    with pytest.raises(ConversionError, match="expected a mapping at the root"):
        convert_ossie_to_cube("- just\n- a\n- list\n")


def test_measure_without_a_type_is_rejected():
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: t\n"
        "    measures:\n"
        "      - name: m\n"
        "        sql: amount\n"
    ))
    with pytest.raises(ConversionError, match="missing required 'type'"):
        convert_cube_to_ossie(files)


def test_unknown_dimension_type_is_rejected():
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: t\n"
        "    dimensions:\n"
        "      - name: d\n"
        "        sql: d\n"
        "        type: quaternion\n"
    ))
    with pytest.raises(ConversionError, match="unknown type 'quaternion'"):
        convert_cube_to_ossie(files)


def test_unknown_ossie_datatype_is_rejected():
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: t\n"
        "    fields:\n"
        "    - name: f\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: f\n"
        "      datatype: Quaternion\n"
    )
    with pytest.raises(ConversionError, match="unknown datatype"):
        convert_ossie_to_cube(ossie)


def test_dataset_without_a_source_is_rejected_on_export():
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
    )
    with pytest.raises(ConversionError, match="missing/empty 'source'"):
        convert_ossie_to_cube(ossie)


def test_several_semantic_models_convert_the_first_with_an_issue():
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: first\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: t\n"
        "- name: second\n"
        "  datasets:\n"
        "  - name: users\n"
        "    source: t\n"
    )
    files, issues = convert_ossie_to_cube(ossie)
    assert set(files) == {"model/cubes/orders.yml", "model/views/first.yml"}
    assert any("converting only the first" in i.detail for i in issues)
