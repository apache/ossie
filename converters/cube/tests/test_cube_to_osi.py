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

"""Cube data model -> Apache Ossie semantic model."""

import pytest
from _util import by_name, expr_of, load_fixture_dir, model_of, stash_of

from ossie_cube import ConversionError, IssueType, convert_cube_to_ossie
from ossie_cube._common import OSSIE_VERSION, cube_sql_to_ossie


@pytest.fixture
def fixture_a():
    return load_fixture_dir("fixtureA_cube")


@pytest.fixture
def model_a(fixture_a):
    out, issues = convert_cube_to_ossie(fixture_a)
    return model_of(out), issues


# --- model identity -------------------------------------------------------------

def test_version_and_single_model(fixture_a):
    out, _ = convert_cube_to_ossie(fixture_a)
    from _util import parse

    doc = parse(out)
    assert doc["version"] == OSSIE_VERSION
    assert len(doc["semantic_model"]) == 1


def test_mapped_view_supplies_model_identity(model_a):
    model, _ = model_a
    assert model["name"] == "sales"
    assert model["description"] == "Sales overview"
    assert "revenue analysis" in model["ai_context"]["instructions"]


def test_model_name_override(fixture_a):
    out, _ = convert_cube_to_ossie(fixture_a, model_name="custom")
    assert model_of(out)["name"] == "custom"


def test_unknown_view_is_rejected(fixture_a):
    with pytest.raises(ConversionError, match="not found"):
        convert_cube_to_ossie(fixture_a, view="nope")


def test_view_curation_rides_in_the_stash(model_a):
    model, _ = model_a
    stash = stash_of(model)
    assert stash["mapped_view"] == "sales"
    # The natively mapped description/ai_context are stripped; the curation stays.
    view = stash["views"]["sales"]
    assert "description" not in view
    assert "meta" not in view
    assert view["cubes"][0]["join_path"] == "orders"


# --- datasets -------------------------------------------------------------------

def test_cubes_become_datasets(model_a):
    model, _ = model_a
    datasets = by_name(model["datasets"])
    assert set(datasets) == {"orders", "users"}
    assert datasets["orders"]["source"] == "public.orders"
    assert datasets["orders"]["description"] == "Customer orders"
    # A `sql`-defined cube keeps its query as the source.
    assert datasets["users"]["source"].startswith("SELECT * FROM public.users")


def test_primary_key_from_dimension_flag(model_a):
    model, _ = model_a
    datasets = by_name(model["datasets"])
    assert datasets["orders"]["primary_key"] == ["id"]
    assert datasets["users"]["primary_key"] == ["id"]


def test_segments_have_no_ossie_form_and_are_stashed(model_a):
    model, _ = model_a
    users = by_name(model["datasets"])["users"]
    segments = stash_of(users)["cube_extras"]["segments"]
    assert segments[0]["name"] == "active"


# --- fields ---------------------------------------------------------------------

def test_dimension_types_map_to_datatypes(model_a):
    model, _ = model_a
    fields = by_name(by_name(model["datasets"])["orders"]["fields"])
    assert fields["status"]["datatype"] == "String"
    assert fields["is_large"]["datatype"] == "Boolean"
    assert fields["created_at"]["datatype"] == "DateTime"
    assert fields["created_at"]["dimension"]["is_time"] is True


def test_number_dimension_asserts_no_datatype(model_a):
    """Cube collapses Integer/Decimal/Float into `number`, so the converter omits
    `datatype` rather than assert a precision the model does not carry."""
    model, _ = model_a
    fields = by_name(by_name(model["datasets"])["orders"]["fields"])
    assert "datatype" not in fields["id"]
    assert stash_of(fields["id"])["type"] == "number"


def test_dimension_title_becomes_label_and_ai_context_maps(model_a):
    model, _ = model_a
    status = by_name(by_name(model["datasets"])["orders"]["fields"])["status"]
    assert status["label"] == "Order Status"
    assert status["description"] == "Current order status"
    assert status["ai_context"]["instructions"].startswith("Values are pending")


def test_cube_reference_is_stripped_in_a_field_expression(model_a):
    """Field expressions are dataset-scoped, so `{CUBE}.amount` reads as `amount`."""
    model, _ = model_a
    is_large = by_name(by_name(model["datasets"])["orders"]["fields"])["is_large"]
    assert expr_of(is_large) == "amount > 500"


def test_geo_dimension_splits_into_two_fields(model_a):
    model, issues = model_a
    fields = by_name(by_name(model["datasets"])["users"]["fields"])
    assert "location" not in fields
    assert expr_of(fields["location_latitude"]) == "lat"
    assert expr_of(fields["location_longitude"]) == "lon"
    assert fields["location_latitude"]["datatype"] == "Float"
    assert stash_of(fields["location_latitude"])["geo"]["of"] == "location"
    assert issues.of_type(IssueType.GEO_DIMENSION_SPLIT)


# --- relationships --------------------------------------------------------------

def test_many_to_one_join_becomes_a_relationship(model_a):
    model, _ = model_a
    rel = by_name(model["relationships"])["orders_to_users"]
    assert rel["from"] == "orders"
    assert rel["to"] == "users"
    assert rel["from_columns"] == ["user_id"]
    assert rel["to_columns"] == ["id"]
    # The declaring side and the exact Cube spelling round-trip via the stash.
    assert stash_of(rel)["declared_on"] == "orders"
    assert stash_of(rel)["relationship"] == "many_to_one"


def test_one_to_many_join_is_flipped_to_many_side_first():
    """Ossie's `from` is always the many side, so a join declared as one_to_many on
    the one side is flipped -- and the declared orientation stashed."""
    files = {
        "model/cubes/m.yml": (
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
        )
    }
    out, _ = convert_cube_to_ossie(files)
    rel = model_of(out)["relationships"][0]
    assert rel["from"] == "orders"
    assert rel["to"] == "users"
    assert rel["from_columns"] == ["user_id"]
    assert rel["to_columns"] == ["id"]
    assert stash_of(rel)["relationship"] == "one_to_many"
    assert stash_of(rel)["declared_on"] == "users"


def test_non_equi_join_is_preserved_not_guessed_at():
    files = {
        "model/cubes/m.yml": (
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "    joins:\n"
            "      - name: rates\n"
            "        sql: \"{CUBE}.day >= {rates}.valid_from\"\n"
            "        relationship: many_to_one\n"
            "    dimensions:\n"
            "      - name: day\n"
            "        sql: day\n"
            "        type: time\n"
            "  - name: rates\n"
            "    sql_table: public.rates\n"
            "    dimensions:\n"
            "      - name: valid_from\n"
            "        sql: valid_from\n"
            "        type: time\n"
        )
    }
    out, issues = convert_cube_to_ossie(files)
    model = model_of(out)
    assert "relationships" not in model
    orders = by_name(model["datasets"])["orders"]
    assert stash_of(orders)["extra_joins"][0]["join"]["name"] == "rates"
    assert issues.of_type(IssueType.PARKED_IN_META)


def test_join_to_unknown_cube_is_rejected():
    files = {
        "model/cubes/m.yml": (
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "    joins:\n"
            "      - name: ghosts\n"
            "        sql: \"{CUBE}.id = {ghosts}.id\"\n"
            "        relationship: many_to_one\n"
        )
    }
    with pytest.raises(ConversionError, match="not a cube in this model"):
        convert_cube_to_ossie(files)


# --- metrics --------------------------------------------------------------------

def test_measures_are_hoisted_and_disambiguated(model_a):
    """`count` exists on both cubes, so both are qualified and the original names
    stashed; a globally unique measure keeps its own name."""
    model, _ = model_a
    metrics = by_name(model["metrics"])
    assert "orders__count" in metrics
    assert "users__count" in metrics
    assert stash_of(metrics["orders__count"])["name"] == "count"
    assert stash_of(metrics["orders__count"])["cube"] == "orders"
    assert "total_amount" in metrics


def test_bare_count_maps_through_the_primary_key(model_a):
    """Cube renders a bare `count` as count(pk), and count(distinct pk) when the
    cube is fanned out. COUNT(DISTINCT pk) equals both, so it is the one static
    form that stays correct in every join context."""
    model, _ = model_a
    metrics = by_name(model["metrics"])
    assert expr_of(metrics["orders__count"]) == "COUNT(DISTINCT orders.id)"
    assert expr_of(metrics["users__count"]) == "COUNT(DISTINCT users.id)"
    assert metrics["orders__count"]["datatype"] == "Integer"


def test_bare_count_without_a_primary_key_is_rejected():
    """The primary key is load-bearing for a correct `count`, so its absence is an
    error rather than a silently-different number."""
    files = {
        "model/cubes/m.yml": (
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "    dimensions:\n"
            "      - name: status\n"
            "        sql: status\n"
            "        type: string\n"
            "    measures:\n"
            "      - name: count\n"
            "        type: count\n"
        )
    }
    with pytest.raises(ConversionError, match="primary key"):
        convert_cube_to_ossie(files)


def test_aggregate_measures_become_qualified_expressions(model_a):
    model, _ = model_a
    metrics = by_name(model["metrics"])
    assert expr_of(metrics["total_amount"]) == "SUM(orders.amount)"
    assert expr_of(metrics["cities"]) == "COUNT(DISTINCT users.city)"
    assert metrics["total_amount"]["description"] == "Total order amount"
    assert metrics["total_amount"]["ai_context"]["instructions"].startswith("Use this")


def test_measure_filters_fold_into_a_case_expression(model_a):
    """Cube's own applyMeasureFilters wraps the operand as
    CASE WHEN <filters> THEN <operand> END inside the aggregate."""
    model, _ = model_a
    metric = by_name(model["metrics"])["completed_amount"]
    assert expr_of(metric) == (
        "SUM(CASE WHEN (orders.status = 'completed') THEN orders.amount END)")


def test_filtered_and_calculated_measures_keep_the_original(model_a):
    """Export cannot recover `filters` from the folded CASE, nor un-inline a
    calculated measure's references, so both keep the original measure verbatim --
    which is what makes Cube -> Ossie -> Cube lossless."""
    model, _ = model_a
    metrics = by_name(model["metrics"])
    assert stash_of(metrics["completed_amount"])["measure"]["filters"]
    assert stash_of(metrics["avg_order_value"])["measure"]["sql"] == (
        "{total_amount} / {count}")
    # A plain aggregate needs no such copy.
    assert "measure" not in stash_of(metrics["orders__count"])


def test_calculated_measure_inlines_its_measure_references(model_a):
    """Cube resolves `{total_amount} / {count}` to the referenced measures' own
    aggregate SQL; Ossie has no metric-to-metric reference, so it is inlined."""
    model, _ = model_a
    metric = by_name(model["metrics"])["avg_order_value"]
    assert expr_of(metric) == "(SUM(orders.amount)) / (COUNT(DISTINCT orders.id))"


def test_measure_reference_cycle_is_rejected():
    files = {
        "model/cubes/m.yml": (
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "    measures:\n"
            "      - name: a\n"
            "        sql: \"{b} + 1\"\n"
            "        type: number\n"
            "      - name: b\n"
            "        sql: \"{a} + 1\"\n"
            "        type: number\n"
        )
    }
    with pytest.raises(ConversionError, match="cycle"):
        convert_cube_to_ossie(files)


def test_multi_stage_measure_is_dropped_with_an_issue():
    files = {
        "model/cubes/m.yml": (
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "    measures:\n"
            "      - name: rolling\n"
            "        sql: amount\n"
            "        type: sum\n"
            "        multi_stage: true\n"
        )
    }
    out, issues = convert_cube_to_ossie(files)
    assert "metrics" not in model_of(out)
    assert issues.of_type(IssueType.MULTI_STAGE_MEASURE_DROPPED)


# --- fan-out --------------------------------------------------------------------

_FANOUT_MODEL = {
    "model/cubes/m.yml": (
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
        "      - name: lifetime_value\n"
        "        sql: \"{CUBE}.ltv\"\n"
        "        type: sum\n"
    )
}


def test_fanout_unsafe_metric_is_refused_by_default():
    """`users` is the one side of a many-to-one join, so summing over it after the
    join over-counts. Cube deduplicates on the primary key at query time; a static
    Ossie expression cannot, so the default is to refuse rather than emit a number
    that silently disagrees with Cube."""
    with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
        convert_cube_to_ossie(_FANOUT_MODEL)


def test_fanout_unsafe_metric_is_recorded_when_not_strict():
    out, issues = convert_cube_to_ossie(_FANOUT_MODEL, strict_fanout=False)
    metric = by_name(model_of(out)["metrics"])["lifetime_value"]
    assert expr_of(metric) == "SUM(users.ltv)"
    recorded = issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)
    assert len(recorded) == 1
    assert recorded[0].element_name == "users.lifetime_value"
    assert "over-count" in recorded[0].detail


def test_idempotent_aggregates_are_never_flagged(fixture_a):
    """count / count_distinct / min / max are unaffected by duplicate rows, so a
    fanned-out dataset carrying only those converts cleanly under strict mode."""
    _, issues = convert_cube_to_ossie(fixture_a)
    assert not issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)


# --- rejections and preservation ------------------------------------------------

def test_jinja_templated_file_is_preserved_not_parsed():
    files = {
        "model/cubes/dyn.yml": "cubes:\n  - name: o{{ suffix }}\n    sql_table: t\n",
        "model/cubes/ok.yml": (
            "cubes:\n  - name: orders\n    sql_table: public.orders\n"),
    }
    out, issues = convert_cube_to_ossie(files)
    model = model_of(out)
    assert by_name(model["datasets"]).keys() == {"orders"}
    assert "model/cubes/dyn.yml" in stash_of(model)["extra_files"]
    assert issues.of_type(IssueType.TEMPLATED_FILE_SKIPPED)


def test_join_into_a_skipped_file_explains_itself():
    """A file the converter had to skip whole (Jinja, `.js`) can leave a join
    pointing at a cube that is no longer there. The error says so, rather than just
    reporting a missing cube."""
    files = {
        "model/cubes/dyn.yml": (
            "cubes:\n  - name: users\n    sql_table: t{{ suffix }}\n"),
        "model/cubes/orders.yml": (
            "cubes:\n"
            "  - name: orders\n"
            "    sql_table: public.orders\n"
            "    joins:\n"
            "      - name: users\n"
            "        sql: \"{CUBE}.user_id = {users}.id\"\n"
            "        relationship: many_to_one\n"
        ),
    }
    with pytest.raises(ConversionError, match="model/cubes/dyn.yml"):
        convert_cube_to_ossie(files)


def test_javascript_model_is_preserved_not_parsed():
    files = {
        "model/cubes/orders.js": "cube(`orders`, { sql_table: `public.orders` });",
        "model/cubes/ok.yml": (
            "cubes:\n  - name: orders_yaml\n    sql_table: public.orders\n"),
    }
    out, issues = convert_cube_to_ossie(files)
    assert "model/cubes/orders.js" in stash_of(model_of(out))["extra_files"]
    assert issues.of_type(IssueType.TEMPLATED_FILE_SKIPPED)


def test_extends_is_refused_rather_than_half_resolved():
    files = {
        "model/cubes/m.yml": (
            "cubes:\n"
            "  - name: base\n"
            "    sql_table: public.orders\n"
            "  - name: derived\n"
            "    extends: base\n"
        )
    }
    with pytest.raises(ConversionError, match="extends"):
        convert_cube_to_ossie(files)


def test_cube_without_a_source_is_rejected():
    files = {"model/cubes/m.yml": "cubes:\n  - name: orders\n    description: x\n"}
    with pytest.raises(ConversionError, match="neither 'sql' nor 'sql_table'"):
        convert_cube_to_ossie(files)


def test_cube_with_both_sources_is_rejected():
    files = {
        "model/cubes/m.yml": (
            "cubes:\n  - name: orders\n    sql: SELECT 1\n    sql_table: t\n")
    }
    with pytest.raises(ConversionError, match="exactly one"):
        convert_cube_to_ossie(files)


def test_duplicate_cube_name_is_rejected():
    files = {
        "model/cubes/a.yml": "cubes:\n  - name: orders\n    sql_table: a\n",
        "model/cubes/b.yml": "cubes:\n  - name: orders\n    sql_table: b\n",
    }
    with pytest.raises(ConversionError, match="defined twice"):
        convert_cube_to_ossie(files)


def test_model_with_no_cubes_is_rejected():
    with pytest.raises(ConversionError, match="no convertible cubes"):
        convert_cube_to_ossie({"README.md": "not a model"})


# --- reference translation ------------------------------------------------------

@pytest.mark.parametrize("sql,expected", [
    ("{CUBE}.status", "status"),
    ("{TABLE}.status", "status"),
    ("{CUBE.status}", "status"),
    ("{status}", "status"),
    ("{orders.status}", "status"),
    ("{users.city}", "users.city"),
    ("${CUBE}.status", "status"),
    ("LOWER({CUBE}.email)", "LOWER(email)"),
    (r"'\{literal\}'", "'{literal}'"),
])
def test_reference_translation_in_a_field_context(sql, expected):
    """A field expression is dataset-scoped, so own-cube references reduce to a
    bare name. `\\{` stays a literal brace."""
    assert cube_sql_to_ossie(sql, "orders")[0] == expected


@pytest.mark.parametrize("sql,expected", [
    ("{CUBE}.amount", "orders.amount"),
    ("{CUBE.amount}", "orders.amount"),
    ("{amount}", "orders.amount"),
    ("{users.city}", "users.city"),
])
def test_reference_translation_in_a_metric_context(sql, expected):
    """A metric expression is model-level, so own-cube references are qualified."""
    assert cube_sql_to_ossie(sql, "orders", self_prefix="orders")[0] == expected
