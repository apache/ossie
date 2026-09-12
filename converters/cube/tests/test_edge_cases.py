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
from ossie_cube._common import dump_yaml


def _files(**named):
    return {f"model/cubes/{n}.yml": t for n, t in named.items()}


def _roundtrip(files):
    ossie, issues = convert_cube_to_ossie(files)
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
    _, issues = convert_cube_to_ossie(files)
    assert issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)
    with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
        convert_cube_to_ossie(files, strict_fanout=True)


_MULTI_STAGE = _files(orders=(
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
    "        sql: amount\n"
    "        type: sum\n"
    "      - name: rolling\n"
    "        sql: amount\n"
    "        type: sum\n"
    "        multi_stage: true\n"
    "        rolling_window:\n"
    "          trailing: 3 month\n"
    "      - name: cnt\n"
    "        type: count\n"
))


def test_a_multi_stage_measure_is_not_an_ossie_metric():
    """It renders as a window function over another grain, which an Ossie expression
    has no form for -- so it gets no `metrics` entry, and that is reported."""
    ossie, issues = convert_cube_to_ossie(_MULTI_STAGE)
    assert [m["name"] for m in model_of(ossie)["metrics"]] == ["revenue", "cnt"]
    parked = issues.of_type(IssueType.MULTI_STAGE_MEASURE_PARKED)
    assert [i.element_name for i in parked] == ["orders.rolling"]


def test_a_multi_stage_measure_survives_the_round_trip_in_place():
    """It used to be lost outright: no metric, and `measures` is a natively-mapped key
    so `cube_extras` did not carry it either -- while the issue claimed it had been
    preserved. Now it rides on the dataset's stash with its position, like an
    unconvertible join, and comes back interleaved with the rebuilt measures."""
    ossie, _ = convert_cube_to_ossie(_MULTI_STAGE)
    stashed = stash_of(by_name(model_of(ossie)["datasets"])["orders"])
    assert stashed["extra_measures"] == [
        {"index": 1, "measure": {
            "name": "rolling", "sql": "amount", "type": "sum",
            "multi_stage": True, "rolling_window": {"trailing": "3 month"}}}]

    back, _ = convert_ossie_to_cube(ossie)
    assert parse_files(back) == parse_files(_MULTI_STAGE)
    # Order matters: it goes back between the two ordinary measures.
    names = [m["name"] for m in parse(
        back["model/cubes/orders.yml"])["cubes"][0]["measures"]]
    assert names == ["revenue", "rolling", "cnt"]


def test_count_star_is_not_emitted_as_a_bare_cube_count():
    """A bare Cube `type: count` is this converter's form for
    `COUNT(DISTINCT <pk>)`. Emitting one for `COUNT(*)` round-tripped back as a
    different expression, and on a dataset with no primary key produced a measure
    the importer refuses -- export generating what its own import rejects."""
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: public.orders\n"
        "  metrics:\n"
        "  - name: n\n"
        "    expression:\n"
        "      dialects:\n"
        "      - dialect: ANSI_SQL\n"
        "        expression: COUNT(*)\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    measure = parse(files["model/cubes/orders.yml"])["cubes"][0]["measures"][0]
    assert measure == {"name": "n", "sql": "COUNT(*)", "type": "number"}

    # And it survives the trip back, without a primary key anywhere in sight.
    ossie2, _ = convert_cube_to_ossie(files)
    assert expr_of(model_of(ossie2)["metrics"][0]) == "COUNT(*)"


def test_field_and_metric_foreign_extensions_survive_the_round_trip():
    """Foreign-vendor extensions are parked under `meta.ossie` at every level, but
    only datasets were reading them back -- so field- and metric-level ones were
    parked and then silently dropped on re-import."""
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: public.orders\n"
        "    fields:\n"
        "    - name: status\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: status\n"
        "      datatype: String\n"
        "      custom_extensions:\n"
        "      - vendor_name: SNOWFLAKE\n"
        "        data: '{\"collation\": \"en\"}'\n"
        "    - name: amount\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: amount\n"
        "      datatype: Decimal\n"
        "  metrics:\n"
        "  - name: total\n"
        "    expression:\n"
        "      dialects:\n"
        "      - dialect: ANSI_SQL\n"
        "        expression: SUM(orders.amount)\n"
        "    custom_extensions:\n"
        "    - vendor_name: DBT\n"
        "      data: '{\"model\": \"fct_orders\"}'\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    ossie2, _ = convert_cube_to_ossie(files)
    model = model_of(ossie2)

    field = by_name(by_name(model["datasets"])["orders"]["fields"])["status"]
    exts = {e["vendor_name"]: e["data"] for e in field["custom_extensions"]}
    assert exts["SNOWFLAKE"] == '{"collation": "en"}'
    # A plain scalar field needs no CUBE stash at all any more, so the foreign
    # extension is the only entry -- which is the point of the reduction.
    assert list(exts) == ["SNOWFLAKE"]

    metric = by_name(model["metrics"])["total"]
    mexts = {e["vendor_name"]: e["data"] for e in metric["custom_extensions"]}
    assert mexts["DBT"] == '{"model": "fct_orders"}'
    # A single-dataset aggregate needs no CUBE stash at all -- the owning cube is
    # derivable from the expression -- so the foreign extension is the only entry.
    assert list(mexts) == ["DBT"]


@pytest.mark.parametrize("sql_table,parts,warns", [
    ("orders", 1, True),
    ("public.orders", 2, True),
    ("tpcds.public.orders", 3, False),
    ('"My.Catalog".public.orders', 3, False),   # dots inside quotes are not parts
    ("'My.Schema'.orders", 2, True),           # nor are dots inside single quotes
    ("a.b.c.d", 4, False),
])
def test_a_source_that_other_converters_reject_is_reported(sql_table, parts, warns):
    """Cube is happy with a one- or two-part `sql_table`, but the Databricks,
    Snowflake and NVIDIA GSF converters all reject a source shorter than
    `catalog.schema.table` -- so a model that converts cleanly here still cannot
    travel. Reported at the point the Ossie document is produced, rather than being
    discovered three hops later."""
    files = _files(orders=(
        "cubes:\n"
        "  - name: orders\n"
        # Single-quoted so a value containing double quotes stays one YAML scalar.
        f"    sql_table: '{sql_table.replace(chr(39), chr(39) * 2)}'\n"))
    _, issues = convert_cube_to_ossie(files)
    reported = issues.of_type(IssueType.SOURCE_NOT_FULLY_QUALIFIED)
    assert bool(reported) is warns
    if warns:
        assert f"{parts} part(s)" in reported[0].detail


def test_a_sql_defined_cube_is_not_reported_as_unqualified():
    """A `sql:` cube is a query, not a table path, and every converter accepts one."""
    files = _files(orders=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql: SELECT * FROM public.orders\n"))
    _, issues = convert_cube_to_ossie(files)
    assert not issues.of_type(IssueType.SOURCE_NOT_FULLY_QUALIFIED)


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


@pytest.mark.parametrize("declared", ["one_to_one", "hasOne", "has_one"])
def test_a_one_to_one_join_does_not_make_its_target_fanned_out(declared):
    """A one-to-one join multiplies neither side, so a `sum` across it is safe. It was
    being treated like any other relationship, whose `to` side *is* fanned out, and a
    valid measure was refused under strict mode."""
    files = _files(m=(
        "cubes:\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "    joins:\n"
        "      - name: profiles\n"
        "        sql: \"{CUBE}.id = {profiles}.user_id\"\n"
        f"        relationship: {declared}\n"
        "    dimensions:\n"
        "      - name: id\n"
        "        sql: id\n"
        "        type: number\n"
        "        primary_key: true\n"
        "  - name: profiles\n"
        "    sql_table: public.profiles\n"
        "    dimensions:\n"
        "      - name: user_id\n"
        "        sql: user_id\n"
        "        type: number\n"
        "        primary_key: true\n"
        "    measures:\n"
        "      - name: score_total\n"
        "        sql: \"{CUBE}.score\"\n"
        "        type: sum\n"
    ))
    # Strict mode is the default; this must simply convert.
    ossie, issues = convert_cube_to_ossie(files)
    assert not issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)
    assert expr_of(by_name(model_of(ossie)["metrics"])["score_total"]) == (
        "SUM(profiles.score)")


def test_a_many_to_one_join_still_makes_its_target_fanned_out():
    """The counterpart: excluding one-to-one must not weaken the ordinary case."""
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
        "      - name: ltv\n"
        "        sql: \"{CUBE}.ltv\"\n"
        "        type: sum\n"
    ))
    with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
        convert_cube_to_ossie(files, strict_fanout=True)


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
    assert any("does not resolve to two physical columns" in i.detail
               for i in issues)
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
    # The view is exactly the shape export generates (every cube, includes "*",
    # no curation), so it is *not* stashed -- export regenerates it. Only the
    # non-canonical file layout is recorded.
    assert "views" not in stash_of(model)
    assert stash_of(model)["cube_files"] == {"orders": "model/views/sales.yml"}


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


def _two_cube_geo_model(expression):
    """`_GEO_MODEL` plus an `orders.amount` field, and `expression` as the metric."""
    return _GEO_MODEL.replace(
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
              f"        expression: {expression}\n")


def test_a_geo_half_reference_is_requalified_when_it_crosses_cubes():
    """`{CUBE}` means "the cube this is declared on", so inlining a snippet into
    another cube's SQL has to name the original cube explicitly.

    One aggregate reading two datasets cannot be decomposed, so it lands on the base
    cube and the `users` half travels there with it.
    """
    model = _two_cube_geo_model("AVG(users.home_latitude - orders.amount)")
    files, _ = convert_ossie_to_cube(model, base_cube="orders")
    cube = parse(files["model/cubes/orders.yml"])["cubes"][0]
    assert cube["measures"] == [
        {"name": "avg_lat", "sql": "{users}.lat - {CUBE}.amount", "type": "avg"}]


def test_a_decomposed_part_lands_on_the_cube_its_operand_reads():
    """Two aggregates over two datasets: each part is declared on the cube it reads,
    which is what lets Cube correct row multiplication for each independently. So the
    geo half needs no requalification -- its part lives on `users` already."""
    model = _two_cube_geo_model(
        "AVG(users.home_latitude) - MIN(orders.amount)")
    files, _ = convert_ossie_to_cube(model, base_cube="orders")
    on_users = by_name(parse(files["model/cubes/users.yml"])["cubes"][0]["measures"])
    on_orders = by_name(parse(files["model/cubes/orders.yml"])["cubes"][0]["measures"])
    assert on_users["avg_lat_part_1"]["sql"] == "{CUBE}.lat"
    assert on_users["avg_lat_part_1"]["public"] is False
    assert on_orders["avg_lat_part_2"]["sql"] == "{CUBE}.amount"
    # The public measure stays on the base cube, naming the foreign part by its cube
    # and its own with `{CUBE.x}`.
    assert on_orders["avg_lat"]["sql"] == (
        "{users.avg_lat_part_1} - {CUBE.avg_lat_part_2}")
    assert "public" not in on_orders["avg_lat"]


def test_geo_half_references_normalize_to_the_underlying_column():
    """Documented normalization: after a round trip the metric names the column the
    geo half actually reads rather than the Ossie-only field name. Semantically the
    same reference, and it is what Cube can express."""
    files, _ = convert_ossie_to_cube(_GEO_MODEL)
    ossie2, _ = convert_cube_to_ossie(files)
    metric = model_of(ossie2)["metrics"][0]
    assert expr_of(metric) == "AVG(users.lat)"


def _geo_stash(part, of="home"):
    return ('{"_v": 1, "geo": {"of": "' + of + '", "part": "' + part
            + '", "sql": "{CUBE}.' + part[:3] + '"}}')


def _ossie_fields(*specs):
    """Build an Ossie model from (field name, expression, geo part or None) specs."""
    out = ("version: 0.2.0.dev0\n"
           "semantic_model:\n"
           "- name: shop\n"
           "  datasets:\n"
           "  - name: users\n"
           "    source: public.users\n"
           "    fields:\n")
    for fname, expr, part in specs:
        out += (f"    - name: {fname}\n"
                "      expression:\n"
                "        dialects:\n"
                "        - dialect: ANSI_SQL\n"
                f"          expression: {expr}\n"
                "      datatype: String\n")
        if part:
            out += ("      custom_extensions:\n"
                    "      - vendor_name: CUBE\n"
                    f"        data: '{_geo_stash(part)}'\n")
    return out


def _ossie_pk(primary_key, *specs):
    """An Ossie model with a primary_key and (name, expression, geo part) fields."""
    out = ("version: 0.2.0.dev0\n"
           "semantic_model:\n"
           "- name: shop\n"
           "  datasets:\n"
           "  - name: orders\n"
           "    source: public.orders\n"
           "    primary_key:\n")
    for col in primary_key:
        out += f"    - {col}\n"
    out += "    fields:\n"
    for fname, expr, part in specs:
        out += (f"    - name: {fname}\n"
                "      expression:\n"
                "        dialects:\n"
                "        - dialect: ANSI_SQL\n"
                f"          expression: {expr}\n"
                "      datatype: String\n")
        if part:
            out += ("      custom_extensions:\n"
                    "      - vendor_name: CUBE\n"
                    f"        data: '{_geo_stash(part, of=fname.rsplit('_', 1)[0])}'\n")
    return out


def _dims(files):
    return parse(files["model/cubes/orders.yml"])["cubes"][0]["dimensions"]


def test_a_computed_dimension_does_not_cover_a_primary_key():
    """`primary_key: true` in Cube declares that dimension's own sql to be the key.
    Marking a computed dimension would declare `LOWER(email)` as the key when Ossie
    named the `id` column -- so a name match alone must not count as coverage."""
    files, issues = convert_ossie_to_cube(
        _ossie_pk(["id"], ("id", "LOWER(email)", None)))
    dims = by_name(_dims(files))
    assert "primary_key" not in dims["id"]
    assert dims["id"]["sql"] == "LOWER({CUBE}.email)"
    # A private scalar dimension carries the key instead, under a free name.
    assert dims["id_pk"] == {
        "name": "id_pk", "sql": "id", "type": "string", "primary_key": True,
        "public": False, "meta": {"ossie": {"synthetic_key": True}}}
    assert issues.of_type(IssueType.APPROXIMATED)


def test_a_merged_geo_dimension_does_not_cover_a_primary_key():
    """A geo dimension has two sql expressions and no single one, so it cannot be
    the key even though its name matches."""
    files, _ = convert_ossie_to_cube(_ossie_pk(
        ["location"],
        ("location_latitude", "lat", "latitude"),
        ("location_longitude", "lon", "longitude")))
    dims = by_name(_dims(files))
    assert dims["location"]["type"] == "geo"
    assert "primary_key" not in dims["location"]
    assert dims["location_pk"] == {
        "name": "location_pk", "sql": "location", "type": "string",
        "primary_key": True, "public": False,
        "meta": {"ossie": {"synthetic_key": True}}}


def test_a_scalar_dimension_backed_by_the_key_column_covers_it():
    """The legitimate case: a differently-named dimension whose sql *is* the key
    column. It stays the key, and nothing is synthesized alongside it."""
    files, issues = convert_ossie_to_cube(
        _ossie_pk(["id"], ("order_id", "id", None)))
    dims = _dims(files)
    assert len(dims) == 1
    assert dims[0]["name"] == "order_id"
    assert dims[0]["primary_key"] is True
    assert not issues.of_type(IssueType.APPROXIMATED)


def test_a_scalar_dimension_named_as_the_key_covers_it():
    """Import records the *dimension name* in `primary_key`, not the column, so a
    scalar dimension matching by name has to keep covering it -- otherwise
    `Cube -> Ossie -> Cube` would synthesize a bogus duplicate key."""
    src = _files(orders=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    dimensions:\n"
        "      - name: order_id\n"
        "        sql: id\n"
        "        type: number\n"
        "        primary_key: true\n"
    ))
    ossie, _ = convert_cube_to_ossie(src)
    assert by_name(model_of(ossie)["datasets"])["orders"]["primary_key"] == [
        "order_id"]
    back, _ = convert_ossie_to_cube(ossie)
    assert parse_files(back) == parse_files(src)


def test_a_synthesized_key_name_avoids_every_existing_member():
    """Suffixing has to keep going while names are taken, and the result must still
    be a single non-public scalar dimension."""
    files, _ = convert_ossie_to_cube(_ossie_pk(
        ["id"],
        ("id", "LOWER(email)", None),
        ("id_pk", "UPPER(email)", None),
        ("id_pk_2", "TRIM(email)", None)))
    dims = by_name(_dims(files))
    keys = [n for n, d in dims.items() if d.get("primary_key")]
    assert keys == ["id_pk_3"]
    assert dims["id_pk_3"] == {
        "name": "id_pk_3", "sql": "id", "type": "string", "primary_key": True,
        "public": False, "meta": {"ossie": {"synthetic_key": True}}}
    # Nothing was overwritten.
    assert dims["id"]["sql"] == "LOWER({CUBE}.email)"
    assert dims["id_pk"]["sql"] == "UPPER({CUBE}.email)"
    assert dims["id_pk_2"]["sql"] == "TRIM({CUBE}.email)"


def test_geo_halves_may_appear_in_any_order_without_clobbering_a_dimension():
    """The geo dimension is assembled from two fields that need not be adjacent and
    may come in either order. Holding its place with a list index computed mid-loop
    overwrote whatever real dimension already sat at that index -- here `city`
    vanished entirely."""
    model = _ossie_fields(
        ("home_longitude", "lon", "longitude"),
        ("city", "city", None),
        ("home_latitude", "lat", "latitude"),
    )
    files, _ = convert_ossie_to_cube(model)
    dims = parse(files["model/cubes/users.yml"])["cubes"][0]["dimensions"]
    assert [d["name"] for d in dims] == ["home", "city"]
    assert by_name(dims)["home"] == {
        "name": "home", "type": "geo",
        "latitude": {"sql": "{CUBE}.lat"},
        "longitude": {"sql": "{CUBE}.lon"}}
    assert by_name(dims)["city"]["sql"] == "city"


def test_a_geo_base_colliding_with_a_field_is_rejected_in_either_order():
    """The base is the merged dimension's name, so it cannot also be an ordinary
    dimension -- that would emit two members of the same name. Whether the ordinary
    field comes first must not decide whether this is caught."""
    for specs in (
        (("home", "home", None), ("home_latitude", "lat", "latitude"),
         ("home_longitude", "lon", "longitude")),
        (("home_latitude", "lat", "latitude"),
         ("home_longitude", "lon", "longitude"), ("home", "home", None)),
    ):
        with pytest.raises(ConversionError, match="collides"):
            convert_ossie_to_cube(_ossie_fields(*specs))


def test_two_fields_claiming_the_same_geo_half_are_rejected():
    model = _ossie_fields(
        ("a_lat", "lat", "latitude"),
        ("b_lat", "lat2", "latitude"),
        ("home_longitude", "lon", "longitude"),
    )
    with pytest.raises(ConversionError, match="both claim the latitude"):
        convert_ossie_to_cube(model)


def test_a_geo_dimension_missing_a_half_is_rejected_on_export():
    model = _ossie_fields(("home_latitude", "lat", "latitude"))
    with pytest.raises(ConversionError, match="missing its longitude half"):
        convert_ossie_to_cube(model)


def test_an_unknown_geo_part_is_rejected():
    model = _ossie_fields(("home_altitude", "alt", "altitude"))
    with pytest.raises(ConversionError, match="geo part 'altitude'"):
        convert_ossie_to_cube(model)


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


def test_foreign_extensions_with_no_mapped_view_are_refused_not_dropped():
    """Model-level foreign-vendor extensions ride on the view that represents the
    model. With several views and none mapped there is no such view, and picking one
    arbitrarily would not survive a re-import -- only the mapped view's parked
    extensions are read back. So this is refused rather than silently losing them."""
    ossie, _ = convert_cube_to_ossie(_TWO_VIEWS)
    doc = parse(ossie)
    doc["semantic_model"][0].setdefault("custom_extensions", []).append(
        {"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "ANALYTICS_WH"}'})
    with pytest.raises(ConversionError, match="SNOWFLAKE"):
        convert_ossie_to_cube(dump_yaml(doc))


def test_foreign_extensions_survive_once_a_view_is_mapped():
    """The fix the error message points at: choose the view the model maps to, and
    the extensions have a home again."""
    ossie, _ = convert_cube_to_ossie(_TWO_VIEWS, view="b")
    doc = parse(ossie)
    doc["semantic_model"][0].setdefault("custom_extensions", []).append(
        {"vendor_name": "SNOWFLAKE", "data": '{"warehouse": "ANALYTICS_WH"}'})
    files, _ = convert_ossie_to_cube(dump_yaml(doc))
    parked = parse(files["model/views/b.yml"])["views"][0]["meta"]["ossie"]
    assert parked["custom_extensions"][0]["vendor_name"] == "SNOWFLAKE"
    # And they come back as Ossie extensions, not just stashed text.
    ossie2, _ = convert_cube_to_ossie(files, view="b")
    vendors = {e["vendor_name"] for e in model_of(ossie2)["custom_extensions"]}
    assert "SNOWFLAKE" in vendors


_TWO_VIEWS_ONE_FILE = {
    "model/all.yml": (
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "views:\n"
        "  - name: alpha\n"
        "    description: A\n"
        "    cubes:\n"
        "      - join_path: orders\n"
        "        includes: '*'\n"
        "  - name: beta\n"
        "    description: B\n"
    ),
}


def test_several_views_in_one_file_all_survive():
    """Views were keyed one-per-path on export, so two sharing a file meant the second
    overwrote the first. The lost one here is `alpha` -- the *mapped* view, which is
    where the model's own description and AI context live."""
    ossie, _ = convert_cube_to_ossie(_TWO_VIEWS_ONE_FILE, view="alpha")
    back, _ = convert_ossie_to_cube(ossie)
    assert set(back) == {"model/all.yml"}
    rebuilt = parse(back["model/all.yml"])
    # Declaration order preserved, both present.
    assert [v["name"] for v in rebuilt["views"]] == ["alpha", "beta"]
    assert [c["name"] for c in rebuilt["cubes"]] == ["orders"]
    assert parse_files(back) == parse_files(_TWO_VIEWS_ONE_FILE)


def test_the_mapped_view_in_a_shared_file_still_carries_model_metadata():
    """The mapped view is the model's home for description and AI context, so it has
    to be the one updated -- not whichever view happens to be written last."""
    ossie, _ = convert_cube_to_ossie(_TWO_VIEWS_ONE_FILE, view="alpha")
    model = model_of(ossie)
    assert model["name"] == "alpha"
    assert model["description"] == "A"

    model["description"] = "edited"
    files, _ = convert_ossie_to_cube(dump_yaml({
        "version": "0.2.0.dev0", "semantic_model": [model]}))
    views = by_name(parse(files["model/all.yml"])["views"])
    assert views["alpha"]["description"] == "edited"
    assert views["beta"]["description"] == "B"


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
    # The other models are not preserved anywhere, so this is a drop.
    dropped = issues.of_type(IssueType.DROPPED_NO_CUBE_EQUIVALENT)
    assert any("only the first is converted" in i.detail for i in dropped)


# --- string literals ------------------------------------------------------------
#
# The two directions are deliberately asymmetric, so both are pinned here. A Cube
# YAML `sql` is compiled as a Python f-string (`f"<sql>"` in YamlCompiler), which
# interpolates `{...}` anywhere in the value -- SQL's own quotes mean nothing to it.
# So on import a reference inside a literal is a real reference, while on export a
# rewrite must stop at the quotes or it would destroy the literal's text.

@pytest.mark.parametrize("sql,expected", [
    ("a = 'x'", [("a = ", False), ("'x'", True)]),
    ("'x' = a", [("'x'", True), (" = a", False)]),
    ("'it''s'", [("'it'", True), ("'s'", True)]),
    # A double-quoted run is an *identifier*, not a literal, so it stays parseable --
    # `DOTTED_REF_RE` matches it as one identifier part.
    ('"col" = `c`', [('"col" = ', False), ("`c`", True)]),
    ("a = 'unterminated", [("a = ", False), ("'unterminated", True)]),
    ("plain", [("plain", False)]),
])
def test_quoted_runs_splits_sql_into_code_and_quoted_text(sql, expected):
    from ossie_cube._common import quoted_runs
    assert quoted_runs(sql) == expected


@pytest.mark.parametrize("expr,expected", [
    ("SUM(orders.amount)", {"orders"}),
    ("SUM(orders.amount) / COUNT(users.id)", {"orders", "users"}),
    ("SUM(orders.amount) || ' per users.id unit'", {"orders"}),
    ("'orders.amount'", set()),
    ("SUM(ghost.amount)", set()),
])
def test_referenced_datasets_ignores_quoted_text(expr, expected):
    from ossie_cube._common import referenced_datasets
    assert referenced_datasets(expr, {"orders", "users"}) == expected


def test_a_reference_inside_a_literal_is_still_translated_on_import():
    """Not an oversight: Cube would have interpolated it, so dropping it would lose a
    reference the model really does resolve."""
    files = _files(orders=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: a.b.orders\n"
        "    dimensions:\n"
        "      - name: note\n"
        "        sql: \"CONCAT({CUBE}.status, ' {CUBE}.status ')\"\n"
        "        type: string\n"
    ))
    ossie, _ = convert_cube_to_ossie(files)
    field = by_name(by_name(model_of(ossie)["datasets"])["orders"]["fields"])["note"]
    assert expr_of(field) == "CONCAT(status, ' status ')"


# --- aggregate span scanning -----------------------------------------------------
#
# The scanner decides whether a metric is decomposed into one measure per aggregate,
# so its rejection paths matter as much as its matches: a false positive splices a
# measure reference into text that was never a call.

@pytest.mark.parametrize("expr,expected", [
    # Two aggregates -- the case decomposition exists for.
    ("SUM(a.x) / COUNT(b.y)", ["SUM(a.x)", "COUNT(b.y)"]),
    # Only the outermost of a nested pair.
    ("SUM(a.x) / NULLIF(SUM(b.y), 0)", ["SUM(a.x)", "SUM(b.y)"]),
    # A closing paren inside a literal does not end the call.
    ("SUM(a.x || ')') / COUNT(b.y)", ["SUM(a.x || ')')", "COUNT(b.y)"]),
    # Part of a longer identifier, not a call.
    ("MY_SUM(a.x) / 2", []),
    ("SUMMARY(a.x) - MIN(b.y)", ["MIN(b.y)"]),
    # A name with no argument list at all.
    ("a.count / b.total", []),
    # Whitespace between the name and its parens is still a call.
    ("SUM (a.x) - MIN (b.y)", ["SUM (a.x)", "MIN (b.y)"]),
    # Unbalanced parens: not a span, and not a crash.
    ("SUM(a.x / MIN(b.y)", []),
    # A single aggregate needs no decomposition.
    ("SUM(a.x)", []),
    # Unparseable input falls back to one opaque measure.
    ("SUM(a.x) /// COUNT(", []),
])
def test_aggregate_spans_only_matches_real_calls(expr, expected):
    from ossie_cube.expressions import aggregate_spans
    assert [expr[s:e] for s, e in aggregate_spans(expr)] == expected


@pytest.mark.parametrize("expr,expected", [
    ("SUM(a.x)", False),
    # One self-contained term: the space is inside the parens, so inlining it into a
    # larger expression needs no parentheses.
    ("COUNT(DISTINCT a.x)", False),
    ("SUM(a.x) / 2", True),
    ("CASE WHEN a.x THEN 1 END", True),   # a top-level space is structure
    ("'a + b'", False),                   # operators inside a literal are text
    ("'a b'", False),
    ('"a b"', False),
])
def test_has_top_level_operator_ignores_quoted_text(expr, expected):
    from ossie_cube.expressions import has_top_level_operator
    assert has_top_level_operator(expr) is expected


# --- review findings: model features that were silently mistranslated ------------

def test_a_case_dimension_becomes_a_real_case_expression():
    """A `case` dimension carries conditions instead of `sql`, so there is no column
    to name. Emitting the dimension's own name claimed a physical column that does not
    exist; Ossie expresses this natively."""
    files = _files(products=(
        "cubes:\n  - name: products\n    sql_table: a.b.products\n    dimensions:\n"
        "      - name: id\n        sql: id\n        type: number\n"
        "        primary_key: true\n"
        "      - name: size\n        type: string\n        case:\n          when:\n"
        "            - sql: \"{CUBE}.size_value = 'xl'\"\n              label: xl\n"
        "            - sql: \"{CUBE}.size_value = 'xxl'\"\n"
        "              label: \"it's big\"\n"
        "          else:\n            label: Unknown\n"))
    ossie, _ = convert_cube_to_ossie(files)
    size = by_name(by_name(model_of(ossie)["datasets"])["products"]["fields"])["size"]
    # A string label becomes a SQL literal, with quotes doubled as SQL requires.
    assert expr_of(size) == (
        "CASE WHEN size_value = 'xl' THEN 'xl' "
        "WHEN size_value = 'xxl' THEN 'it''s big' ELSE 'Unknown' END")


def test_a_case_dimension_restores_without_a_redundant_sql():
    """Cube rejects a dimension declaring both `case` and `sql` ("does not match any
    of the allowed types"), so the generated sql is dropped when `case` comes back."""
    files = _files(products=(
        "cubes:\n  - name: products\n    sql_table: a.b.products\n    dimensions:\n"
        "      - name: size\n        type: string\n        case:\n          when:\n"
        "            - sql: \"{CUBE}.v = 'xl'\"\n              label: xl\n"))
    _, back, _ = _roundtrip(files)
    dim = by_name(parse(back["model/cubes/products.yml"])["cubes"][0]["dimensions"])
    assert "sql" not in dim["size"]
    assert dim["size"]["case"]["when"][0]["label"] == "xl"


def test_a_case_label_may_be_an_expression():
    files = _files(products=(
        "cubes:\n  - name: products\n    sql_table: a.b.products\n    dimensions:\n"
        "      - name: size\n        type: string\n        case:\n          when:\n"
        "            - sql: \"{CUBE}.v = 'xl'\"\n"
        "              label:\n                sql: \"{CUBE}.english_size\"\n"))
    ossie, _ = convert_cube_to_ossie(files)
    size = by_name(by_name(model_of(ossie)["datasets"])["products"]["fields"])["size"]
    assert expr_of(size) == "CASE WHEN v = 'xl' THEN english_size END"


def test_a_sub_query_dimension_is_parked_whole():
    """`sub_query: true` means the sql references a *measure* through a correlated
    subquery, which an Ossie field expression has no form for. It used to be
    emitted anyway as the flattened reference (`users.count`) -- text that reads
    as a column no dataset has and computes nothing anywhere -- so it is parked
    like a switch dimension instead, and the referenced measure still reaches the
    model as an ordinary metric."""
    files = _files(products=(
        "cubes:\n  - name: products\n    sql_table: a.b.products\n    dimensions:\n"
        "      - name: users_count\n        sql: \"{users.count}\"\n"
        "        type: number\n        sub_query: true\n"
        "  - name: users\n    sql_table: a.b.users\n    dimensions:\n"
        "      - name: id\n        sql: id\n        type: number\n"
        "        primary_key: true\n"
        "    measures:\n      - name: count\n        type: count\n"))
    ossie, issues = convert_cube_to_ossie(files)
    assert any("sub_query" in i.detail
               for i in issues.of_type(IssueType.PARKED_IN_META))
    products = by_name(model_of(ossie)["datasets"])["products"]
    assert not products.get("fields")
    assert stash_of(products)["extra_dimensions"][0]["dimension"]["name"] == (
        "users_count")
    # The aggregate itself is still in the model, as the hoisted metric.
    assert expr_of(by_name(model_of(ossie)["metrics"])["count"]) == (
        "COUNT(DISTINCT users.id)")


def test_duplicate_member_names_in_one_cube_are_rejected():
    """Cube refuses this too ("orders cube: d defined more than once"). Converting it
    anyway emitted two Ossie fields of one name -- which the spec's own validator
    rejects for a duplicate field name."""
    files = _files(o=(
        "cubes:\n  - name: o\n    sql_table: a.b.t\n    dimensions:\n"
        "      - name: d\n        sql: a\n        type: string\n"
        "      - name: d\n        sql: b\n        type: string\n"))
    with pytest.raises(ConversionError, match="defined more than once"):
        convert_cube_to_ossie(files)


def test_a_dimension_and_a_measure_sharing_a_name_are_rejected_on_import():
    files = _files(o=(
        "cubes:\n  - name: o\n    sql_table: a.b.t\n    dimensions:\n"
        "      - name: revenue\n        sql: amount\n        type: number\n"
        "    measures:\n      - name: revenue\n        sql: amount\n        type: sum\n"))
    with pytest.raises(ConversionError, match="defined more than once"):
        convert_cube_to_ossie(files)


def test_an_empty_dimension_sql_is_reported():
    """Cube compiles `sql: ''` without complaint, so it is not refused -- but the Ossie
    expression is empty and no consumer can evaluate it."""
    files = _files(o=(
        "cubes:\n  - name: o\n    sql_table: a.b.t\n    dimensions:\n"
        "      - name: d\n        sql: ''\n        type: string\n"))
    _, issues = convert_cube_to_ossie(files)
    assert any("empty" in i.detail for i in issues.of_type(IssueType.APPROXIMATED))


def test_a_switch_dimension_keeps_its_type():
    """`switch` maps to String like an ordinary dimension and String maps back to
    `string`, so the type has to be recorded or the dimension returns as a plain
    string one carrying an orphaned `case` block."""
    files = _files(o=(
        "cubes:\n  - name: o\n    sql_table: a.b.t\n    dimensions:\n"
        "      - name: kind\n        sql: kind\n        type: switch\n"))
    _, back, _ = _roundtrip(files)
    dim = by_name(parse(back["model/cubes/o.yml"])["cubes"][0]["dimensions"])
    assert dim["kind"]["type"] == "switch"
    # And the recording is not itself emitted as a Cube key.
    assert "dim_type" not in dim["kind"]


def test_a_computed_primary_key_stays_on_its_own_dimension():
    """A Cube key can be an expression, and then the only name Ossie can carry is the
    dimension's. Re-export used to synthesize a dimension reading a column of that
    name -- which does not exist -- and move `primary_key: true` onto it, changing
    what Cube counts."""
    files = _files(orders=(
        "cubes:\n  - name: orders\n    sql_table: a.b.orders\n    dimensions:\n"
        "      - name: order_key\n"
        "        sql: \"CONCAT({CUBE}.tenant_id, {CUBE}.id)\"\n"
        "        type: string\n        primary_key: true\n"
        "    measures:\n      - name: count\n        type: count\n"))
    ossie, back, _ = _roundtrip(files)
    dims = parse(back["model/cubes/orders.yml"])["cubes"][0]["dimensions"]
    assert len(dims) == 1
    assert dims[0]["name"] == "order_key"
    assert dims[0]["primary_key"] is True
    # The original spelling, exactly: qualification makes the regenerated form
    # match the `{CUBE}`-referenced sql the model was written with.
    assert dims[0]["sql"] == "CONCAT({CUBE}.tenant_id, {CUBE}.id)"
    # Import records which entries are dimension names rather than columns, because
    # the Ossie document alone cannot tell them apart afterwards.
    assert stash_of(by_name(model_of(ossie)["datasets"])["orders"])[
        "computed_primary_key"] == ["order_key"]


# --- brace escaping -------------------------------------------------------------
#
# Cube compiles every string in a model as a Python f-string, so an unescaped `{`
# anywhere -- a description, an AI context, a parked JSON blob -- makes the model fail
# to compile. `\{` is Cube's escape for a literal brace.

def test_a_brace_in_free_text_is_escaped():
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  description: 'sales in {region}'\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: a.b.orders\n"
        "    description: 'holds {json} notes'\n"
        "    fields:\n"
        "    - name: id\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: id\n"
        "      datatype: Integer\n"
        "      description: 'the {id}'\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    cube = parse(files["model/cubes/orders.yml"])["cubes"][0]
    assert cube["description"] == "holds \\{json\\} notes"
    assert cube["dimensions"][0]["description"] == "the \\{id\\}"
    view = parse(files["model/views/shop.yml"])["views"][0]
    assert view["description"] == "sales in \\{region\\}"
    # And reading it back returns the original text, not the escaped spelling.
    ossie2, _ = convert_cube_to_ossie(files)
    model = model_of(ossie2)
    assert model["description"] == "sales in {region}"
    assert by_name(model["datasets"])["orders"]["description"] == "holds {json} notes"


def test_a_parked_foreign_extension_is_escaped_and_restored():
    """The headline multi-vendor case: a foreign vendor's `data` is JSON, so it always
    contains braces. Parking it unescaped made every such model fail to compile."""
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: a.b.orders\n"
        "    fields:\n"
        "    - name: id\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: id\n"
        "      datatype: Integer\n"
        "    custom_extensions:\n"
        "    - vendor_name: DBT\n"
        "      data: '{\"project\": \"x\"}'\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    parked = parse(files["model/cubes/orders.yml"])["cubes"][0][
        "meta"]["ossie"]["custom_extensions"]
    assert parked[0]["data"] == '\\{"project": "x"\\}'
    ossie2, _ = convert_cube_to_ossie(files)
    restored = by_name(model_of(ossie2)["datasets"])["orders"]["custom_extensions"]
    assert {"vendor_name": "DBT", "data": '{"project": "x"}'} in restored


def test_a_case_label_is_unescaped_on_the_way_into_an_expression():
    """A Cube label is escaped text; the Ossie CASE expression wants a plain SQL
    literal. Leaving the backslashes in put them inside the literal, so a consumer
    would compare against `large \\{special\\}` rather than `large {special}`."""
    files = _files(products=(
        "cubes:\n  - name: products\n    sql_table: a.b.products\n    dimensions:\n"
        "      - name: size\n        type: string\n        case:\n          when:\n"
        "            - sql: \"{CUBE}.v = 'x'\"\n"
        "              label: 'large \\{special\\}'\n"))
    ossie, _ = convert_cube_to_ossie(files)
    size = by_name(by_name(model_of(ossie)["datasets"])["products"]["fields"])["size"]
    assert expr_of(size) == "CASE WHEN v = 'x' THEN 'large {special}' END"
    # The stashed `case` block still restores the Cube spelling exactly.
    _, back, _ = _roundtrip(files)
    dim = by_name(parse(back["model/cubes/products.yml"])["cubes"][0]["dimensions"])
    assert dim["size"]["case"]["when"][0]["label"] == "large \\{special\\}"


# --- review round four -----------------------------------------------------------

_JOIN_MEMBERS = (
    "cubes:\n"
    "  - name: orders\n"
    "    sql_table: a.b.orders\n"
    "    joins:\n"
    "      - name: users\n"
    "        sql: \"{JOINSQL}\"\n"
    "        relationship: many_to_one\n"
    "    dimensions:\n"
    "      - name: id\n        sql: id\n        type: number\n        primary_key: true\n"
    "      - name: user_key\n        sql: user_id\n        type: number\n"
    "      - name: tenant_user_id\n"
    "        sql: \"CONCAT({CUBE}.tenant, {CUBE}.user_id)\"\n        type: string\n"
    "  - name: users\n"
    "    sql_table: a.b.users\n"
    "    dimensions:\n"
    "      - name: id\n        sql: id\n        type: number\n        primary_key: true\n"
)


def _join_model(join_sql):
    return _files(m=_JOIN_MEMBERS.replace("{JOINSQL}", join_sql))


@pytest.mark.parametrize("join_sql,expected", [
    # A raw column passes straight through.
    ("{CUBE}.user_id = {users}.id", ["user_id"]),
    # A *member* reference names a dimension, not a column -- so it resolves to the
    # column that dimension reads. `user_key` reads `user_id`.
    ("{CUBE.user_key} = {users.id}", ["user_id"]),
    ("{user_key} = {users.id}", ["user_id"]),
])
def test_a_join_member_resolves_to_the_column_it_reads(join_sql, expected):
    """Ossie's from_columns/to_columns are physical columns. Emitting the *member* name
    gave downstream converters a column that need not exist -- `user_key` is a dimension,
    the column is `user_id`."""
    ossie, back, _ = _roundtrip(_join_model(join_sql))
    rel = model_of(ossie)["relationships"][0]
    assert rel["from_columns"] == expected
    assert rel["to_columns"] == ["id"]
    # The original spelling is stashed, so Cube gets its own form back.
    assert parse_files(back) == parse_files(_join_model(join_sql))


def test_a_join_on_a_computed_member_is_parked_whole():
    """`tenant_user_id` is `CONCAT(...)`, so there is no column for Ossie to name. The
    join has no Ossie form and is preserved rather than described wrongly."""
    ossie, back, issues = _roundtrip(
        _join_model("{CUBE.tenant_user_id} = {users.id}"))
    assert "relationships" not in model_of(ossie)
    assert any("does not resolve to two physical columns" in i.detail
               for i in issues)
    assert parse_files(back) == parse_files(
        _join_model("{CUBE.tenant_user_id} = {users.id}"))


@pytest.mark.parametrize("reference", [
    # Ossie regular identifiers are case-insensitive (core-spec: "Regular identifiers
    # are upper cased"), so all of these address the same computed field.
    "orders.amount",
    "orders.AMOUNT",
    "ORDERS.amount",
    "Orders.Amount",
])
def test_identifiers_match_case_insensitively(reference):
    """Matching exactly emitted `{CUBE}.AMOUNT` -- a raw column that bypasses the
    member's own expression, so the metric silently summed the wrong thing."""
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: a.b.orders\n"
        "    fields:\n"
        "    - name: amount\n"
        "      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: amount * 2\n"
        "      datatype: Decimal\n"
        "  metrics:\n"
        "  - name: total\n"
        "    expression:\n      dialects:\n"
        f"      - dialect: ANSI_SQL\n        expression: SUM({reference})\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    measure = parse(files["model/cubes/orders.yml"])["cubes"][0]["measures"][0]
    # The member reference, which inlines `amount * 2` -- not `{CUBE}.AMOUNT`, a raw
    # column that would bypass the member's expression and sum the wrong thing.
    assert measure == {"name": "total", "sql": "{CUBE.amount}", "type": "sum"}


def test_a_quoted_identifier_keeps_its_exact_case():
    """The spec's normalization strips quotes without upper-casing, so a quoted
    identifier stays an exact match -- `"Amount"` is not the field `amount`."""
    from ossie_cube._common import normalize_identifier

    assert normalize_identifier("amount") == "AMOUNT"
    assert normalize_identifier('"Amount"') == "Amount"
    assert normalize_identifier('"a""b"') == 'a"b'


@pytest.mark.parametrize("order", [
    ["ratio", "ratio_part_1"],
    ["ratio_part_1", "ratio"],
])
def test_generated_part_names_do_not_depend_on_metric_order(order):
    """Allocating against only the measures built *so far* made this order-dependent:
    the composite metric first took `ratio_part_1` and the later metric of that name
    then collided, while the reverse order worked."""
    def metric(name):
        expr = ("SUM(orders.amount) / COUNT(DISTINCT users.id)"
                if name == "ratio" else "SUM(orders.amount)")
        return (f"  - name: {name}\n    expression:\n      dialects:\n"
                f"      - dialect: ANSI_SQL\n        expression: {expr}\n")

    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: a.b.orders\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "    - name: amount\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: amount\n"
        "      datatype: Decimal\n"
        "  - name: users\n"
        "    source: a.b.users\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "  relationships:\n"
        "  - name: r\n    from: orders\n    to: users\n"
        "    from_columns: [id]\n    to_columns: [id]\n"
        "  metrics:\n" + "".join(metric(n) for n in order)
    )
    files, _ = convert_ossie_to_cube(ossie)
    names = [m["name"] for m in
             parse(files["model/cubes/orders.yml"])["cubes"][0]["measures"]]
    users = [m["name"] for m in
             parse(files["model/cubes/users.yml"])["cubes"][0]["measures"]]
    # Same generated names either way, and the user's own metric keeps its name.
    assert sorted(names) == ["ratio", "ratio_part_1", "ratio_part_2"]
    assert users == ["ratio_part_3"]


def test_a_stashed_extra_file_may_not_overwrite_generated_output():
    """`extra_files` restore verbatim, so one landing on a generated path replaced a
    converted cube with arbitrary text and reported nothing."""
    import json

    stash = {"_v": 1, "views": {},
             "extra_files": {"model/cubes/orders.yml": "# hijacked\n"}}
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: a.b.orders\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "  custom_extensions:\n"
        "  - vendor_name: CUBE\n"
        f"    data: '{json.dumps(stash)}'\n"
    )
    with pytest.raises(ConversionError, match="would overwrite the generated"):
        convert_ossie_to_cube(ossie)


def test_is_time_without_a_datatype_does_not_acquire_one():
    """Ossie says not to infer a scalar type from `is_time` alone, so a field that
    carried no datatype must not come back asserting DateTime."""
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: events\n"
        "    source: a.b.events\n"
        "    fields:\n"
        "    - name: occurred_at\n"
        "      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: occurred_at\n"
        "      dimension:\n        is_time: true\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    dim = parse(files["model/cubes/events.yml"])["cubes"][0]["dimensions"][0]
    assert dim["type"] == "time"
    assert dim["meta"]["ossie"]["untyped"] is True
    ossie2, _ = convert_cube_to_ossie(files)
    field = by_name(by_name(model_of(ossie2)["datasets"])["events"]["fields"])[
        "occurred_at"]
    assert "datatype" not in field
    assert field["dimension"]["is_time"] is True


# --- review round five -----------------------------------------------------------

_FANOUT_CALC = _files(m=(
    "cubes:\n"
    "  - name: orders\n"
    "    sql_table: a.b.orders\n"
    "    joins:\n"
    "      - name: users\n"
    "        sql: \"{CUBE}.user_id = {users}.id\"\n"
    "        relationship: many_to_one\n"
    "    dimensions:\n"
    "      - name: user_id\n        sql: user_id\n        type: number\n"
    "  - name: users\n"
    "    sql_table: a.b.users\n"
    "    dimensions:\n"
    "      - name: id\n        sql: id\n        type: number\n        primary_key: true\n"
    "    measures:\n"
    "      - name: ltv_pct\n"
    "        sql: \"SUM({CUBE}.ltv) / 100\"\n"
    "        type: number\n"
))


def test_a_calculated_measure_is_judged_on_its_aggregates_not_its_type():
    """A Cube calculated measure is classified by its outer type, which says nothing
    about the aggregates inside: `SUM({CUBE}.ltv) / 100` is a `number` measure whose
    value is still a sum. Judging it by `type` alone let an unsafe expression through
    unreported -- even under strict mode."""
    _, issues = convert_cube_to_ossie(_FANOUT_CALC)
    assert issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)
    with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
        convert_cube_to_ossie(_FANOUT_CALC, strict_fanout=True)


@pytest.mark.parametrize("expr,unsafe", [
    ("SUM(users.ltv) / 100", True),
    ("AVG(users.ltv)", True),
    ("COUNT(users.id)", True),              # no DISTINCT: duplication counts twice
    ("COUNT(DISTINCT users.id)", False),    # idempotent under duplication
    ("MIN(users.x) + MAX(users.y)", False),
    ("COUNT(DISTINCT users.id) / MAX(users.x)", False),
    ("users.a + users.b", False),           # no aggregate at all
])
def test_non_idempotent_aggregate_detection(expr, unsafe):
    from ossie_cube.expressions import unsafe_aggregate_datasets

    datasets, unqualified = unsafe_aggregate_datasets(expr)
    assert bool(datasets or unqualified) is unsafe


def test_an_unparseable_expression_is_assumed_unsafe():
    """`None` means "cannot tell", and the caller then attributes every dataset the
    expression names -- the point being not to emit a silently inflated number."""
    from ossie_cube.expressions import unsafe_aggregate_datasets

    assert unsafe_aggregate_datasets("not valid sql (((") is None


def test_a_cross_cube_member_gets_the_target_cubes_own_spelling():
    """`{users.ID}` does not resolve when the member is declared `id` -- Cube's member
    lookup is case-sensitive even though Ossie's identifiers are not."""
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: a.b.orders\n"
        "    fields:\n"
        "    - name: amount\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: amount\n"
        "      datatype: Decimal\n"
        "  - name: users\n"
        "    source: a.b.users\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "  relationships:\n"
        "  - name: r\n    from: orders\n    to: users\n"
        "    from_columns: [amount]\n    to_columns: [id]\n"
        "  metrics:\n"
        "  - name: m\n    expression:\n      dialects:\n"
        "      - dialect: ANSI_SQL\n        expression: SUM(orders.amount + USERS.ID)\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    assert parse(files["model/cubes/orders.yml"])["cubes"][0][
        "measures"][0]["sql"] == "{CUBE}.amount + {users.id}"


@pytest.mark.parametrize("reference,expected", [
    # An ANSI double-quoted identifier is a *name*, not a string literal, so it is
    # parsed. A quoted reference resolves against the normalized (upper) form, per
    # core-spec, *and* against the name's exact spelling.
    #
    # The exact-spelling key is a deliberate superset of the spec's column-matching
    # table, which says `"id"` does not match a column created as `id`. That rule is
    # about physical database columns, folded by the database; an Ossie member name is
    # whatever the model declares. And a name containing a space or mixed case cannot be
    # written unquoted at all -- `"Order Items"` is the only way to reference a dataset
    # of that name -- so without it such a name would be unreferenceable.
    ('orders."AMOUNT"', "SUM({CUBE.amount})"),
    ('"ORDERS"."AMOUNT"', "SUM({CUBE.amount})"),
    ('orders."amount"', "SUM({CUBE.amount})"),
    # Neither the exact spelling nor the normalized form, so it stays a raw column.
    ('orders."Amount"', 'SUM({CUBE}."Amount")'),
])
def test_quoted_identifiers_are_parsed_not_skipped(reference, expected):
    """The whole double-quoted region used to be treated as opaque -- the same handling
    string literals get -- so a quoted reference was never rewritten and bypassed the
    member it named."""
    from _util import to_cube_sql

    assert to_cube_sql(f"SUM({reference})", "orders", {"amount"}) == expected


def test_a_single_quoted_literal_is_still_opaque():
    """The change above must not weaken literal handling: Cube compiles every string as
    an f-string, so a `{...}` emitted into a literal would be interpolated."""
    from _util import to_cube_sql

    assert to_cube_sql("SUM(orders.amount) || ' orders.amount '", "orders",
                       {"amount"}) == "SUM({CUBE.amount}) || ' orders.amount '"


_CHAIN = (
    "cubes:\n"
    "  - name: orders\n"
    "    sql_table: a.b.orders\n"
    "    joins:\n"
    "      - name: users\n"
    "        sql: \"{CUBE.user_key} = {users.id}\"\n"
    "        relationship: many_to_one\n"
    "    dimensions:\n"
    "      - name: user_key\n        sql: \"{CUBE.mid}\"\n        type: string\n"
    "      - name: mid\n        sql: \"{MID_SQL}\"\n        type: string\n"
    "  - name: users\n"
    "    sql_table: a.b.users\n"
    "    dimensions:\n"
    "      - name: id\n        sql: id\n        type: number\n        primary_key: true\n"
)


@pytest.mark.parametrize("mid_sql,expected", [
    # The chain ends in a real column, so the relationship names that column.
    ("user_id", ["user_id"]),
    # It ends in an expression, so there is no column to name: the join is parked.
    ("CONCAT({CUBE}.a, {CUBE}.b)", None),
    # A cycle Cube would reject must not hang the walk either.
    ("{CUBE.user_key}", None),
])
def test_a_join_member_chain_is_followed_to_its_end(mid_sql, expected):
    """`{CUBE.x}` flattens to the bare name `x`, which *looks* like a column but is only
    one if `x` itself reads one. Resolving a single level treated a computed dimension at
    the end of the chain as a physical column."""
    files = _files(m=_CHAIN.replace("{MID_SQL}", mid_sql))
    ossie, back, _ = _roundtrip(files)
    rels = model_of(ossie).get("relationships")
    if expected is None:
        assert rels is None
    else:
        assert rels[0]["from_columns"] == expected
    # Either way Cube gets its own model back.
    assert parse_files(back) == parse_files(files)


def test_a_generated_part_name_avoids_a_stashed_member():
    """A stashed segment, `switch` dimension or multi-stage measure is restored verbatim
    on export, so a part name colliding with one failed the conversion at the very end
    rather than picking the next free name."""
    import json

    stash = {"_v": 1,
             "cube_extras": {"segments": [{"name": "ratio_part_1", "sql": "x"}]}}
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: a.b.orders\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "    - name: amount\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: amount\n"
        "      datatype: Decimal\n"
        "    custom_extensions:\n"
        "    - vendor_name: CUBE\n"
        f"      data: '{json.dumps(stash)}'\n"
        "  - name: users\n"
        "    source: a.b.users\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "  relationships:\n"
        "  - name: r\n    from: orders\n    to: users\n"
        "    from_columns: [id]\n    to_columns: [id]\n"
        "  metrics:\n"
        "  - name: ratio\n    expression:\n      dialects:\n"
        "      - dialect: ANSI_SQL\n"
        "        expression: SUM(orders.amount) / COUNT(DISTINCT users.id)\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    cube = parse(files["model/cubes/orders.yml"])["cubes"][0]
    assert [m["name"] for m in cube["measures"]] == ["ratio_part_2", "ratio"]
    users = parse(files["model/cubes/users.yml"])["cubes"][0]
    assert [m["name"] for m in users["measures"]] == ["ratio_part_3"]
    assert [s["name"] for s in cube["segments"]] == ["ratio_part_1"]


# --- review round six ------------------------------------------------------------

_TWO_CUBE_CALC = (
    "cubes:\n"
    "  - name: orders\n"
    "    sql_table: a.b.orders\n"
    "    joins:\n"
    "      - name: users\n"
    "        sql: \"{CUBE}.user_id = {users}.id\"\n"
    "        relationship: many_to_one\n"
    "    dimensions:\n"
    "      - name: user_id\n        sql: user_id\n        type: number\n"
    "      - name: id\n        sql: id\n        type: number\n        primary_key: true\n"
    "    measures:\n"
    "      - name: m\n        sql: \"{SQL}\"\n        type: number\n"
    "  - name: users\n"
    "    sql_table: a.b.users\n"
    "    dimensions:\n"
    "      - name: id\n        sql: id\n        type: number\n        primary_key: true\n"
)


@pytest.mark.parametrize("sql,flagged", [
    # The measure sits on `orders`; `users` is the fanned-out side. Checking only the
    # cube a measure is *declared* on reported nothing at all.
    ("SUM({users}.ltv) / SUM({CUBE}.amount)", True),
    # An aggregate the span scanner does not know still has to be caught.
    ("STDDEV({users}.ltv)", True),
    ("VARIANCE({users}.ltv) + 1", True),
    # Unsafe, but only over the cube that is not fanned out.
    ("SUM({CUBE}.amount) / 100", False),
    # Idempotent under duplication, so safe on any cube.
    ("MAX({users}.ltv) - MIN({users}.ltv)", False),
    ("COUNT(DISTINCT {users}.id) / 2", False),
])
def test_fanout_is_judged_per_aggregate_and_per_dataset(sql, flagged):
    files = _files(m=_TWO_CUBE_CALC.replace("{SQL}", sql))
    _, issues = convert_cube_to_ossie(files)
    assert bool(issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)) is flagged
    if flagged:
        with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
            convert_cube_to_ossie(files, strict_fanout=True)


@pytest.mark.parametrize("expr,unsafe", [
    # An allowlist, because the set of aggregate functions is open-ended: listing the
    # unsafe ones declared every unlisted one safe.
    ("STDDEV(users.x)", True),
    ("VARIANCE(users.x)", True),
    ("MEDIAN(users.x)", True),
    ("ARRAY_AGG(users.x)", True),
    ("SUM(users.x)", True),
    ("COUNT(users.id)", True),
    ("MIN(users.x)", False),
    ("MAX(users.x)", False),
    ("COUNT(DISTINCT users.id)", False),
    ("APPROX_COUNT_DISTINCT(users.x)", False),
])
def test_only_provably_idempotent_aggregates_are_treated_as_safe(expr, unsafe):
    """Exercised through the function the converter actually calls, so the allowlist is
    pinned on the production path rather than on a wrapper beside it."""
    from ossie_cube.expressions import unsafe_aggregate_datasets

    datasets, unqualified = unsafe_aggregate_datasets(expr)
    assert bool(datasets or unqualified) is unsafe


def test_a_cross_cube_alias_is_not_prefixed_with_the_own_cube():
    """`{users}.ltv` is a raw column of the *joined* cube, so the trailing column hangs
    off `users`. Prefixing it with the declaring cube produced `orders.users.ltv` -- a
    three-part name no reference matches, which also hid it from the fan-out analysis."""
    files = _files(m=_TWO_CUBE_CALC.replace(
        "{SQL}", "MAX({users}.ltv) - MIN({CUBE}.amount)"))
    ossie, _ = convert_cube_to_ossie(files)
    assert expr_of(by_name(model_of(ossie)["metrics"])["m"]) == (
        "MAX(users.ltv) - MIN(orders.amount)")


def test_an_aggregate_name_inside_a_quoted_identifier_is_not_a_call():
    """`orders."SUM(X)"` is a column whose name happens to contain `SUM(`. Reference
    rewriting has to look inside a quoted identifier -- it is a name -- but aggregate
    *discovery* must not, or it splits out a hidden measure and emits malformed SQL."""
    from ossie_cube.expressions import aggregate_spans

    expr = 'MAX(orders.value) + orders."SUM(X)"'
    assert [expr[s:e] for s, e in aggregate_spans(expr)] == ["MAX(orders.value)"]


def test_an_explicit_raw_column_wins_over_a_dimension_of_that_name():
    """`{CUBE}.tenant_user_id` names a column, full stop -- even where a computed
    dimension of that name also exists. Deciding on the translated text lost the
    distinction, because both reference forms flatten to the same bare name."""
    model = (
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: a.b.orders\n"
        "    joins:\n"
        "      - name: users\n"
        "        sql: \"{CUBE.user_key} = {users.id}\"\n"
        "        relationship: many_to_one\n"
        "    dimensions:\n"
        "      - name: user_key\n        sql: \"{KEY}\"\n        type: string\n"
        "      - name: tenant_user_id\n"
        "        sql: \"CONCAT({CUBE}.a, {CUBE}.b)\"\n        type: string\n"
        "  - name: users\n"
        "    sql_table: a.b.users\n"
        "    dimensions:\n"
        "      - name: id\n        sql: id\n        type: number\n"
        "        primary_key: true\n"
    )
    raw = _files(m=model.replace("{KEY}", "{CUBE}.tenant_user_id"))
    ossie, back, _ = _roundtrip(raw)
    assert model_of(ossie)["relationships"][0]["from_columns"] == ["tenant_user_id"]
    assert parse_files(back) == parse_files(raw)
    # The member form still parks: that one really does read an expression.
    member = _files(m=model.replace("{KEY}", "{CUBE.tenant_user_id}"))
    ossie2, _, _ = _roundtrip(member)
    assert "relationships" not in model_of(ossie2)


# --- review round seven ----------------------------------------------------------

@pytest.mark.parametrize("sql,flagged", [
    # One recognized aggregate used to stop the search, leaving an unrecognized one
    # elsewhere in the same expression unattributed.
    ("SUM({CUBE}.amount) + STDDEV({users}.ltv)", True),
    ("STDDEV({CUBE}.amount) + SUM({users}.ltv)", True),
    ("MEDIAN({users}.ltv) - MIN({CUBE}.amount)", True),
    # Only the safe cube is read unsafely.
    ("STDDEV({CUBE}.amount) + MAX({users}.ltv)", False),
    # DISTINCT collapses duplicates before the aggregate, so fan-out cannot change it.
    ("SUM(DISTINCT {users}.ltv)", False),
    ("AVG(DISTINCT {users}.ltv) / 2", False),
])
def test_every_aggregate_is_attributed_including_unrecognized_ones(sql, flagged):
    files = _files(m=_TWO_CUBE_CALC.replace("{SQL}", sql))
    _, issues = convert_cube_to_ossie(files)
    assert bool(issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)) is flagged
    if flagged:
        with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
            convert_cube_to_ossie(files, strict_fanout=True)


def _join_key_model(key, dims):
    return _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: a.b.orders\n"
        "    joins:\n"
        "      - name: users\n"
        f"        sql: \"{{CUBE.{key}}} = {{users.id}}\"\n"
        "        relationship: many_to_one\n"
        "    dimensions:\n" + dims +
        "  - name: users\n"
        "    sql_table: a.b.users\n"
        "    dimensions:\n"
        "      - name: id\n        sql: id\n        type: number\n"
        "        primary_key: true\n"
        "      - name: region_id\n        sql: region_id\n        type: number\n"
    ))


@pytest.mark.parametrize("key,dims,expected", [
    # A `case` dimension has conditions and no sql, so "no sql means the same-named
    # column" does not apply -- there is no column of that name.
    ("tier",
     "      - name: tier\n        type: string\n        case:\n          when:\n"
     "            - sql: \"{CUBE}.x > 1\"\n              label: hi\n", None),
    # A `switch` dimension enumerates values and reads nothing.
    ("tier",
     "      - name: tier\n        type: switch\n        values:\n          - a\n", None),
    # `{users}.region_id` reads *another* cube's column, so it is not this dataset's.
    ("region_key",
     "      - name: region_key\n        sql: \"{users}.region_id\"\n"
     "        type: number\n", None),
    # This cube's own alias is a genuine raw column and still resolves.
    ("k",
     "      - name: k\n        sql: \"{CUBE}.user_id\"\n        type: number\n",
     ["user_id"]),
])
def test_only_this_cubes_own_columns_resolve_a_join_key(key, dims, expected):
    """Ossie relationship columns are physical columns of the dataset. Anything that is
    not one has to park the join rather than name a column that does not exist."""
    files = _join_key_model(key, dims)
    ossie, back, _ = _roundtrip(files)
    rels = model_of(ossie).get("relationships")
    if expected is None:
        assert rels is None
    else:
        assert rels[0]["from_columns"] == expected
    assert parse_files(back) == parse_files(files)


def test_a_measure_depending_on_a_windowed_one_is_parked_too():
    """A rolling/multi-stage measure has no static Ossie form, and neither does anything
    referencing it. Raising aborted the whole import over one measure; both are parked
    and restored together."""
    files = _files(m=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: a.b.orders\n"
        "    dimensions:\n"
        "      - name: id\n        sql: id\n        type: number\n"
        "        primary_key: true\n"
        "    measures:\n"
        "      - name: rolling\n        sql: amount\n        type: sum\n"
        "        rolling_window:\n          trailing: 3 month\n"
        "      - name: rolling_ratio\n        sql: \"{rolling} / 100\"\n"
        "        type: number\n"))
    ossie, back, issues = _roundtrip(files)
    assert "metrics" not in model_of(ossie)
    parked = {i.element_name for i in issues.of_type(
        IssueType.MULTI_STAGE_MEASURE_PARKED)}
    assert parked == {"orders.rolling", "orders.rolling_ratio"}
    # Both come back, in their original positions.
    cube = parse(back["model/cubes/m.yml"])["cubes"][0]
    assert [m["name"] for m in cube["measures"]] == ["rolling", "rolling_ratio"]


# --- review round eight ----------------------------------------------------------

@pytest.mark.parametrize("sql,flagged", [
    # `users` is the fanned-out side; `orders` (the declaring cube) is not.
    #
    # An aggregate can read a qualified *and* an unqualified operand, and the two were
    # not tracked independently: this reported `users` only, leaving the declaring cube
    # -- which the bare `amount` belongs to -- unmentioned. Here it is `users` that must
    # appear, which it did; the reverse case is covered below.
    ("SUM({users}.ltv + {CUBE}.amount)", True),
    # An *ordered-set* aggregate keeps its value-bearing column in the ORDER BY, on the
    # wrapper rather than the inner function -- so examining only the inner one blamed
    # the declaring cube and let this through.
    ("PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY {users}.ltv)", True),
    # sqlglot models LISTAGG as an unnamed call, so this vanished from the analysis
    # entirely rather than merely being misattributed.
    ("LISTAGG({users}.name, ',') WITHIN GROUP (ORDER BY {users}.name)", True),
    # BOOL_OR / BOOL_AND cannot change when a row is duplicated, and were rejected.
    ("BOOL_OR({users}.flag)", False),
    ("BOOL_AND({users}.flag)", False),
    ("BIT_OR({users}.mask)", False),
    ("MAX({users}.ltv) - MIN({users}.ltv)", False),
])
def test_fanout_covers_every_aggregate_shape(sql, flagged):
    files = _files(m=_TWO_CUBE_CALC.replace("{SQL}", sql))
    _, issues = convert_cube_to_ossie(files)
    assert bool(issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)) is flagged
    if flagged:
        with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
            convert_cube_to_ossie(files, strict_fanout=True)


@pytest.mark.parametrize("expr,datasets,unqualified", [
    # Both kinds of operand, reported independently.
    ("SUM(amount + line_items.qty)", {"line_items"}, True),
    ("SUM(line_items.qty)", {"line_items"}, False),
    ("SUM(amount)", set(), True),
    # No columns at all is read as the declaring cube, which is what `COUNT(*)` means.
    ("COUNT(*)", set(), True),
    # An ordered-set aggregate's column is found on the wrapper.
    ("PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY users.ltv)", {"users"}, False),
    # An unrecognized call is treated as an aggregate: a warning if it is not one is
    # cheaper than a silently inflated number if it is.
    ("MY_UDF(users.x)", {"users"}, False),
    # A nested aggregate is one scope, not two, so the inner one does not also report
    # the declaring cube.
    ("SUM(MY_UDF(users.x))", {"users"}, False),
    # Idempotent, so nothing is attributed.
    ("BOOL_OR(users.flag)", set(), False),
    ("SUM(DISTINCT users.ltv)", set(), False),
])
def test_aggregate_attribution(expr, datasets, unqualified):
    from ossie_cube.expressions import unsafe_aggregate_datasets

    assert unsafe_aggregate_datasets(expr) == (datasets, unqualified)


def test_a_quoted_geo_half_reference_still_inlines_its_sql():
    """A split geo half exists only in Ossie, so a reference to it has to be replaced by
    the half's own SQL. The inline table was keyed by the normalized form alone, unlike
    every other table, so an exact-quoted reference missed the substitution and came out
    as a raw column of a name the database does not have."""
    from _util import to_cube_sql

    for reference in ('users.home_latitude', 'users."HOME_LATITUDE"',
                      'users."home_latitude"'):
        assert to_cube_sql(f"AVG({reference})", "users", {"home"},
                           inline_sql={"home_latitude": "{CUBE}.lat"}) == (
            "AVG({CUBE}.lat)")


@pytest.mark.parametrize("expr,unsafe", [
    # DISTINCT applies to a call SQL parsing does not model, for the same reason it
    # applies to a modelled aggregate: a duplicated row cannot change the distinct set.
    ("LISTAGG(DISTINCT users.name, ',')", False),
    ("LISTAGG(users.name, ',')", True),
    ("APPROX_PERCENTILE(DISTINCT users.x, 0.5)", False),
    ("APPROX_PERCENTILE(users.x, 0.5)", True),
])
def test_distinct_inside_an_unmodelled_call_is_idempotent(expr, unsafe):
    from ossie_cube.expressions import unsafe_aggregate_datasets

    datasets, unqualified = unsafe_aggregate_datasets(expr)
    assert bool(datasets or unqualified) is unsafe


# --- maintainer review ----------------------------------------------------------

@pytest.mark.parametrize("part", ["latitude", "longitude"])
def test_a_geo_coordinate_must_be_a_mapping(part):
    latitude = "lat" if part == "latitude" else "\n          sql: lat"
    longitude = "lon" if part == "longitude" else "\n          sql: lon"
    files = _files(users=(
        "cubes:\n"
        "  - name: users\n"
        "    sql_table: public.users\n"
        "    dimensions:\n"
        "      - name: home\n"
        "        type: geo\n"
        f"        latitude: {latitude}\n"
        f"        longitude: {longitude}\n"
    ))
    with pytest.raises(ConversionError, match=rf"'{part}' must be a mapping"):
        convert_cube_to_ossie(files)


def test_join_and_inside_a_string_literal_is_not_a_clause_separator():
    files = _files(
        orders=(
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "    joins:\n"
            "      - name: users\n"
            "        sql: \"COALESCE({CUBE}.user_id, 'OPEN AND PENDING') = "
            "{users}.id\"\n"
            "        relationship: many_to_one\n"),
        users=(
            "cubes:\n"
            "  - name: users\n"
            "    sql_table: public.users\n"),
    )
    ossie, back, issues = _roundtrip(files)
    assert parse_files(back) == parse_files(files)
    parked = [issue for issue in issues.of_type(IssueType.PARKED_IN_META)
              if issue.element_name == "join 'orders' -> 'users'"]
    assert len(parked) == 1
    assert "OPEN AND PENDING" in parked[0].detail
    assert model_of(ossie).get("relationships") is None


def test_an_empty_rolling_window_is_still_windowed():
    files = _files(orders=(
        "cubes:\n"
        "  - name: orders\n"
        "    sql_table: public.orders\n"
        "    measures:\n"
        "      - name: rolling\n"
        "        sql: amount\n"
        "        type: sum\n"
        "        rolling_window: {}\n"
    ))
    ossie, issues = convert_cube_to_ossie(files)
    assert model_of(ossie).get("metrics") is None
    assert stash_of(model_of(ossie)["datasets"][0])["extra_measures"] == [
        {"index": 0, "measure": {
            "name": "rolling", "sql": "amount", "type": "sum",
            "rolling_window": {}}}]
    assert [issue.element_name for issue in issues.of_type(
        IssueType.MULTI_STAGE_MEASURE_PARKED)] == ["orders.rolling"]


def test_a_stashed_join_without_a_primary_key_is_reported_on_export():
    files = _files(
        orders=(
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "    joins:\n"
            "      - name: users\n"
            "        sql: \"COALESCE({CUBE}.user_id, 0) = {users}.id\"\n"
            "        relationship: many_to_one\n"),
        users=(
            "cubes:\n"
            "  - name: users\n"
            "    sql_table: public.users\n"),
    )
    ossie, _ = convert_cube_to_ossie(files)
    _, issues = convert_ossie_to_cube(ossie)
    warnings = issues.of_type(IssueType.DROPPED_NO_CUBE_EQUIVALENT)
    assert any(issue.element_name == "dataset 'orders'" and
               "requires a primary key" in issue.detail for issue in warnings)


def test_a_falsy_stashed_measure_sql_is_restored_verbatim():
    ossie = (
        "version: 0.2.0.dev0\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: orders\n"
        "    source: public.orders\n"
        "  metrics:\n"
        "  - name: zero\n"
        "    expression:\n"
        "      dialects:\n"
        "      - dialect: ANSI_SQL\n"
        "        expression: '0'\n"
        "    custom_extensions:\n"
        "    - vendor_name: CUBE\n"
        "      data: '{\"_v\": 1, \"sql\": 0, \"type\": \"number\"}'\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    measure = parse(files["model/cubes/orders.yml"])["cubes"][0]["measures"][0]
    assert measure == {"name": "zero", "sql": 0, "type": "number"}


def test_parentheses_inside_a_string_do_not_change_aggregate_classification():
    from ossie_cube._common import classify_metric_expression

    assert classify_metric_expression("SUM(')')") == ("sum", "')'", [])


def test_matching_an_aggregate_parenthesis_ignores_backtick_identifiers():
    from ossie_cube.expressions import _match_paren

    expression = "SUM(`cost(foo`)"
    assert _match_paren(expression, 3) == len(expression) - 1


def test_filter_splitting_ignores_and_inside_a_backtick_identifier():
    from ossie_cube._common import unfold_filtered_operand

    folded = ("CASE WHEN (`status AND state` = 1) AND (amount > 0) "
              "THEN amount END")
    assert unfold_filtered_operand(folded) == (
        "amount", ["`status AND state` = 1", "amount > 0"])
