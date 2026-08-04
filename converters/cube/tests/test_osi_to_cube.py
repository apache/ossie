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

"""Apache Ossie semantic model -> Cube data model."""

import pytest
from _util import by_name, expr_of, model_of, parse

from ossie_cube import (
    ConversionError,
    IssueType,
    convert_cube_to_ossie,
    convert_ossie_to_cube,
)
from ossie_cube._common import OSSIE_VERSION


def _ossie(datasets, relationships="", metrics="", model_extra=""):
    return (f"version: {OSSIE_VERSION}\n"
            "semantic_model:\n"
            "- name: shop\n"
            f"{model_extra}"
            "  datasets:\n"
            f"{datasets}"
            f"{relationships}"
            f"{metrics}")


_ORDERS = (
    "  - name: orders\n"
    "    source: sales.public.orders\n"
    "    primary_key:\n"
    "    - id\n"
    "    fields:\n"
    "    - name: id\n"
    "      expression:\n"
    "        dialects:\n"
    "        - dialect: ANSI_SQL\n"
    "          expression: id\n"
    "      datatype: Integer\n"
    "    - name: amount\n"
    "      expression:\n"
    "        dialects:\n"
    "        - dialect: ANSI_SQL\n"
    "          expression: amount\n"
    "      datatype: Decimal\n"
)


def _cubes(files, path="model/cubes/orders.yml"):
    return by_name(parse(files[path])["cubes"])


# --- layout ---------------------------------------------------------------------

def test_emits_one_file_per_cube_plus_a_view():
    files, _ = convert_ossie_to_cube(_ossie(_ORDERS))
    assert set(files) == {"model/cubes/orders.yml", "model/views/shop.yml"}


def test_version_is_enforced():
    with pytest.raises(ConversionError, match="Unsupported Ossie version"):
        convert_ossie_to_cube("version: 9.9.9\nsemantic_model: []\n")


def test_model_without_datasets_is_rejected():
    with pytest.raises(ConversionError, match="no datasets"):
        convert_ossie_to_cube(
            f"version: {OSSIE_VERSION}\nsemantic_model:\n- name: shop\n  datasets: []\n")


def test_relationship_to_unknown_dataset_is_rejected():
    rel = ("  relationships:\n"
           "  - name: r\n    from: orders\n    to: ghosts\n"
           "    from_columns: [x]\n    to_columns: [y]\n")
    with pytest.raises(ConversionError, match="unknown dataset"):
        convert_ossie_to_cube(_ossie(_ORDERS, rel))


def test_mismatched_relationship_columns_are_rejected():
    rel = ("  relationships:\n"
           "  - name: r\n    from: orders\n    to: orders\n"
           "    from_columns: [a, b]\n    to_columns: [c]\n")
    with pytest.raises(ConversionError, match="same length"):
        convert_ossie_to_cube(_ossie(_ORDERS, rel))


# --- datasets and fields --------------------------------------------------------

def test_source_becomes_sql_table_or_sql():
    files, _ = convert_ossie_to_cube(_ossie(_ORDERS))
    assert _cubes(files)["orders"]["sql_table"] == "sales.public.orders"

    query = _ORDERS.replace("source: sales.public.orders",
                            "source: SELECT * FROM raw.orders")
    files, _ = convert_ossie_to_cube(_ossie(query))
    cube = _cubes(files)["orders"]
    assert cube["sql"] == "SELECT * FROM raw.orders"
    assert "sql_table" not in cube


def test_every_dimension_declares_a_type():
    """Cube's schema requires `type` on every dimension, so the converter always
    emits one -- falling back to `string` with an issue when Ossie carries none."""
    no_type = (
        "  - name: orders\n"
        "    source: t\n"
        "    fields:\n"
        "    - name: note\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: note\n"
    )
    files, issues = convert_ossie_to_cube(_ossie(no_type))
    assert _cubes(files)["orders"]["dimensions"][0]["type"] == "string"
    # A guess, not a loss and not a park: Cube demands a type Ossie never gave.
    assert issues.of_type(IssueType.APPROXIMATED)


@pytest.mark.parametrize("datatype,expected", [
    ("String", "string"),
    ("Integer", "number"),
    ("Decimal", "number"),
    ("Float", "number"),
    ("Boolean", "boolean"),
    ("Date", "time"),
    ("DateTime", "time"),
    ("DateTimeTz", "time"),
    ("Opaque", "string"),
])
def test_datatype_maps_to_cube_type(datatype, expected):
    ds = (
        "  - name: orders\n"
        "    source: t\n"
        "    fields:\n"
        "    - name: f\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: f\n"
        f"      datatype: {datatype}\n"
    )
    files, _ = convert_ossie_to_cube(_ossie(ds))
    assert _cubes(files)["orders"]["dimensions"][0]["type"] == expected


def test_is_time_on_a_non_temporal_datatype_is_reported():
    """Cube marks time dimensions by `type`, so an Integer year grain cannot carry
    the temporal role -- that is a real loss and it is reported, not hidden."""
    ds = (
        "  - name: date_dim\n"
        "    source: t\n"
        "    fields:\n"
        "    - name: d_year\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: d_year\n"
        "      datatype: Integer\n"
        "      dimension:\n"
        "        is_time: true\n"
    )
    files, issues = convert_ossie_to_cube(_ossie(ds))
    dim = parse(files["model/cubes/date_dim.yml"])["cubes"][0]["dimensions"][0]
    assert dim["type"] == "number"
    # The temporal role is gone from the output, so this is a drop.
    detail = issues.of_type(IssueType.DROPPED_NO_CUBE_EQUIVALENT)[0].detail
    assert "temporal role is not carried" in detail


def test_primary_key_column_without_a_field_is_synthesized():
    ds = (
        "  - name: orders\n"
        "    source: t\n"
        "    primary_key:\n"
        "    - ticket_no\n"
        "    fields:\n"
        "    - name: amount\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: amount\n"
        "      datatype: Decimal\n"
    )
    files, issues = convert_ossie_to_cube(_ossie(ds))
    dims = by_name(_cubes(files)["orders"]["dimensions"])
    assert dims["ticket_no"] == {
        "name": "ticket_no", "sql": "ticket_no", "type": "string",
        "primary_key": True, "public": False,
        "meta": {"ossie": {"synthetic_key": True}}}
    # `type: string` is chosen by the converter, not carried by Ossie.
    assert issues.of_type(IssueType.APPROXIMATED)


def test_field_name_is_sanitized_and_collisions_are_rejected():
    ds = (
        "  - name: orders\n"
        "    source: t\n"
        "    fields:\n"
        "    - name: Order Status\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: status\n"
        "    - name: order status\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: status2\n"
    )
    with pytest.raises(ConversionError, match="collides"):
        convert_ossie_to_cube(_ossie(ds))


def test_field_collision_is_rejected_before_any_metric_is_placed():
    """Dimension names are resolved once, up front. Resolving them per stage let a
    collision go undetected while measures were being placed -- so the member set
    that decides `{CUBE.member}` vs `{CUBE}.column` could be silently short a name,
    and the error surfaced later and less clearly."""
    ds = (
        "  - name: orders\n"
        "    source: t\n"
        "    fields:\n"
        "    - name: Order Status\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: status\n"
        "    - name: order status\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: status2\n"
    )
    metrics = _metric("m", "SUM(orders.amount)")
    with pytest.raises(ConversionError, match="collides"):
        convert_ossie_to_cube(_ossie(ds, metrics=metrics))


def test_missing_dialect_drops_the_field_with_an_issue():
    ds = (
        "  - name: orders\n"
        "    source: t\n"
        "    fields:\n"
        "    - name: f\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: MDX\n"
        "          expression: '[f]'\n"
    )
    files, issues = convert_ossie_to_cube(_ossie(ds))
    assert "dimensions" not in _cubes(files)["orders"]
    assert issues.of_type(IssueType.NO_USABLE_DIALECT)


def test_preferred_dialect_wins_over_ansi():
    ds = (
        "  - name: orders\n"
        "    source: t\n"
        "    fields:\n"
        "    - name: email\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: LOWER(email)\n"
        "        - dialect: SNOWFLAKE\n"
        "          expression: LOWER(email)::VARCHAR\n"
        "      datatype: String\n"
    )
    files, _ = convert_ossie_to_cube(_ossie(ds), dialect="SNOWFLAKE")
    assert _cubes(files)["orders"]["dimensions"][0]["sql"] == "LOWER(email)::VARCHAR"


# --- joins ----------------------------------------------------------------------

_TWO_DATASETS = _ORDERS + (
    "  - name: users\n"
    "    source: sales.public.users\n"
    "    primary_key:\n"
    "    - id\n"
    "    fields:\n"
    "    - name: id\n"
    "      expression:\n"
    "        dialects:\n"
    "        - dialect: ANSI_SQL\n"
    "          expression: id\n"
    "      datatype: Integer\n"
)
_REL = ("  relationships:\n"
        "  - name: orders_to_users\n"
        "    from: orders\n"
        "    to: users\n"
        "    from_columns: [user_id]\n"
        "    to_columns: [id]\n")


def test_relationship_lands_on_the_many_side_as_many_to_one():
    files, _ = convert_ossie_to_cube(_ossie(_TWO_DATASETS, _REL))
    join = _cubes(files)["orders"]["joins"][0]
    # Alias-dot on both sides: Ossie's from_columns/to_columns name columns, so the
    # far side is a raw column reference too, not a member reference.
    assert join == {"name": "users", "sql": "{CUBE}.user_id = {users}.id",
                    "relationship": "many_to_one"}
    # The one side declares nothing; Cube needs the join on one side only.
    assert "joins" not in _cubes(files, "model/cubes/users.yml")["users"]


def test_composite_relationship_becomes_an_and_chain():
    rel = ("  relationships:\n"
           "  - name: r\n    from: orders\n    to: users\n"
           "    from_columns: [user_id, region]\n"
           "    to_columns: [id, region]\n")
    files, _ = convert_ossie_to_cube(_ossie(_TWO_DATASETS, rel))
    assert _cubes(files)["orders"]["joins"][0]["sql"] == (
        "{CUBE}.user_id = {users}.id AND {CUBE}.region = {users}.region")


def test_relationship_ai_context_is_reported_as_dropped_not_parked():
    """A Cube join entry takes only name/sql/relationship -- no `meta` -- so this is
    one of the few things that genuinely cannot be preserved. It is reported under
    DROPPED_NO_CUBE_EQUIVALENT rather than PARKED_IN_META, so a caller gating on
    issue types can tell real loss from "preserved but invisible to Cube"."""
    rel = _REL + "    ai_context:\n      instructions: Join carefully.\n"
    _, issues = convert_ossie_to_cube(_ossie(_TWO_DATASETS, rel))
    dropped = issues.of_type(IssueType.DROPPED_NO_CUBE_EQUIVALENT)
    assert [i.element_name for i in dropped] == ["relationship 'orders_to_users'"]
    assert "ai_context" in dropped[0].detail
    assert not issues.of_type(IssueType.PARKED_IN_META)


# --- metrics --------------------------------------------------------------------

def _metric(name, expr):
    return ("  metrics:\n"
            f"  - name: {name}\n"
            "    expression:\n"
            "      dialects:\n"
            "      - dialect: ANSI_SQL\n"
            f"        expression: {expr}\n")


@pytest.mark.parametrize("expr,expected", [
    ("SUM(orders.amount)", {"type": "sum", "sql": "{CUBE}.amount"}),
    ("AVG(orders.amount)", {"type": "avg", "sql": "{CUBE}.amount"}),
    ("MIN(orders.amount)", {"type": "min", "sql": "{CUBE}.amount"}),
    ("MAX(orders.amount)", {"type": "max", "sql": "{CUBE}.amount"}),
    ("COUNT(DISTINCT orders.amount)",
     {"type": "count_distinct", "sql": "{CUBE}.amount"}),
    ("APPROX_COUNT_DISTINCT(orders.amount)",
     {"type": "count_distinct_approx", "sql": "{CUBE}.amount"}),
])
def test_aggregate_expressions_become_structured_measures(expr, expected):
    files, _ = convert_ossie_to_cube(_ossie(_ORDERS, metrics=_metric("m", expr)))
    measure = _cubes(files)["orders"]["measures"][0]
    assert {k: v for k, v in measure.items() if k != "name"} == expected


def test_count_distinct_over_the_primary_key_becomes_a_bare_count():
    """The inverse of the import rule: COUNT(DISTINCT <pk>) is exactly Cube's
    fan-out-safe `type: count`, so it round-trips back to the idiomatic form."""
    files, _ = convert_ossie_to_cube(
        _ossie(_ORDERS, metrics=_metric("m", "COUNT(DISTINCT orders.id)")))
    measure = _cubes(files)["orders"]["measures"][0]
    assert measure == {"name": "m", "type": "count"}


def test_declared_member_gets_a_member_reference_and_a_raw_column_does_not():
    """`{CUBE.member}` reuses a declared member's SQL and is compile-time checked;
    `{CUBE}.column` passes a raw column through. The choice follows from whether
    the dataset declares a field of that name."""
    files, _ = convert_ossie_to_cube(
        _ossie(_ORDERS, metrics=_metric("m", "SUM(orders.shipping_fee)")))
    # `shipping_fee` is not a declared field, so it stays a raw column.
    assert _cubes(files)["orders"]["measures"][0]["sql"] == "{CUBE}.shipping_fee"


def test_a_ratio_is_split_into_one_measure_per_aggregate():
    """Each aggregate becomes its own `public: false` measure on the cube its operand
    comes from, and the public measure references them. Cube corrects for row
    multiplication per measure, so splitting is what lets each aggregate be corrected
    on its own cube instead of the whole ratio being one opaque expression."""
    files, _ = convert_ossie_to_cube(_ossie(
        _TWO_DATASETS, _REL,
        _metric("aov", "SUM(orders.amount) / COUNT(DISTINCT users.id)")))
    orders = by_name(_cubes(files)["orders"]["measures"])
    users = by_name(parse(files["model/cubes/users.yml"])["cubes"][0]["measures"])

    assert orders["aov_part_1"] == {
        "name": "aov_part_1", "sql": "{CUBE}.amount", "type": "sum",
        "meta": {"ossie": {"part_of": "aov"}}, "public": False}
    # `users.id` is that cube's primary key, so its aggregate is a bare Cube count --
    # the form Cube corrects for fan-out.
    assert users["aov_part_2"] == {
        "name": "aov_part_2", "type": "count",
        "meta": {"ossie": {"part_of": "aov"}}, "public": False}
    # `{CUBE.aov_part_1}` rather than `{orders.aov_part_1}`: an own-cube reference
    # stays correct when the cube is extended.
    assert orders["aov"] == {
        "name": "aov", "type": "number",
        "sql": "{CUBE.aov_part_1} / {users.aov_part_2}"}


def test_a_dotted_token_inside_a_string_literal_is_left_alone():
    """Cube compiles a YAML `sql` as a Python f-string, so a `{...}` written into a
    string literal is still interpolated -- it would replace the literal's own text
    with a column reference. So the rewrite has to stop at the quotes."""
    files, _ = convert_ossie_to_cube(_ossie(_ORDERS, metrics=_metric(
        "m", "CONCAT(CAST(SUM(orders.amount) AS VARCHAR), ' orders.amount ')")))
    assert _cubes(files)["orders"]["measures"][0]["sql"] == (
        "CONCAT(CAST(SUM({CUBE}.amount) AS VARCHAR), ' orders.amount ')")


def test_an_aggregate_name_inside_a_string_literal_is_not_an_aggregate():
    """Otherwise the literal is treated as a second aggregate and gets a measure
    reference spliced into the middle of it."""
    files, issues = convert_ossie_to_cube(_ossie(_TWO_DATASETS, _REL, _metric(
        "label", "SUM(orders.amount) || ' per COUNT(users.id) unit'")))
    measures = _cubes(files)["orders"]["measures"]
    # One measure, not a decomposed pair, and the literal survives verbatim.
    assert [m["name"] for m in measures] == ["label"]
    assert measures[0]["sql"] == (
        "SUM({CUBE}.amount) || ' per COUNT(users.id) unit'")
    assert "measures" not in _cubes(files, "model/cubes/users.yml")["users"]
    # `users` is named only inside the literal, so this is not a cross-cube metric.
    assert not issues.of_type(IssueType.APPROXIMATED)


@pytest.mark.parametrize("shape,expr", [
    ("decomposed", "SUM(orders.amount) / COUNT(DISTINCT users.id)"),
    ("single aggregate", "SUM(orders.amount - users.id)"),
    ("calculated", "SUM(orders.amount) + users.id"),
])
def test_a_cross_dataset_metric_is_reported_whatever_shape_it_takes(shape, expr):
    """Cube reaches another cube's members through an implicit join, so the model
    needs a join path this converter cannot verify. The report used to come only from
    the calculated-measure fallback, which meant the decomposed shape -- the one with
    the *most* cross-cube references -- reported nothing."""
    _, issues = convert_ossie_to_cube(
        _ossie(_TWO_DATASETS, _REL, _metric("m", expr)))
    reported = issues.of_type(IssueType.APPROXIMATED)
    assert len(reported) == 1, shape
    assert "orders, users" in reported[0].detail
    assert "join path" in reported[0].detail


def test_a_single_dataset_metric_is_not_reported():
    _, issues = convert_ossie_to_cube(_ossie(
        _TWO_DATASETS, _REL, _metric("m", "SUM(orders.amount)")))
    assert not issues.of_type(IssueType.APPROXIMATED)


def test_a_split_ratio_comes_back_as_the_metric_it_was_split_from():
    """The split is an implementation detail of the Cube side: the parts are marked
    generated, so import skips them and inlines their SQL back through the public
    measure's references, recovering the original expression verbatim."""
    expression = "SUM(orders.amount) / COUNT(DISTINCT users.id)"
    files, _ = convert_ossie_to_cube(
        _ossie(_TWO_DATASETS, _REL, _metric("aov", expression)))
    ossie, _ = convert_cube_to_ossie(files)
    metrics = model_of(ossie)["metrics"]
    assert [m["name"] for m in metrics] == ["aov"]
    assert expr_of(metrics[0]) == expression


def test_metric_lands_on_the_dataset_its_expression_references():
    files, _ = convert_ossie_to_cube(_ossie(
        _TWO_DATASETS, _REL, _metric("users_seen", "COUNT(DISTINCT users.id)")))
    assert "measures" not in _cubes(files)["orders"]
    assert _cubes(files, "model/cubes/users.yml")["users"]["measures"][0]["name"] == (
        "users_seen")


def test_two_metrics_colliding_on_one_cube_are_rejected():
    metrics = ("  metrics:\n"
               "  - name: Total Amount\n"
               "    expression:\n      dialects:\n      - dialect: ANSI_SQL\n"
               "        expression: SUM(orders.amount)\n"
               "  - name: total amount\n"
               "    expression:\n      dialects:\n      - dialect: ANSI_SQL\n"
               "        expression: SUM(orders.id)\n")
    with pytest.raises(ConversionError, match="two metrics map to measure"):
        convert_ossie_to_cube(_ossie(_ORDERS, metrics=metrics))


# --- views ----------------------------------------------------------------------

def test_generated_view_is_rooted_at_the_fk_sink():
    files, _ = convert_ossie_to_cube(_ossie(_TWO_DATASETS, _REL))
    view = parse(files["model/views/shop.yml"])["views"][0]
    assert view["cubes"] == [
        {"join_path": "orders", "includes": "*"},
        # `prefix: true` because both cubes have an `id`: a view flattens every
        # included member into one namespace and Cube refuses a collision, so this is
        # Cube's own remedy rather than a stylistic choice.
        {"join_path": "orders.users", "includes": "*", "prefix": True},
    ]


def test_ambiguous_base_cube_is_rejected_and_the_hint_resolves_it():
    two_facts = _TWO_DATASETS  # no relationships at all
    with pytest.raises(ConversionError, match="no relationships"):
        convert_ossie_to_cube(_ossie(two_facts))
    files, _ = convert_ossie_to_cube(_ossie(two_facts), base_cube="orders")
    assert parse(files["model/views/shop.yml"])["views"][0]["cubes"][0][
        "join_path"] == "orders"


def test_unknown_base_cube_is_rejected():
    with pytest.raises(ConversionError, match="not a dataset"):
        convert_ossie_to_cube(_ossie(_TWO_DATASETS, _REL), base_cube="nope")


def test_synonyms_reach_cube_as_prose_and_are_parked_structurally():
    """Cube has no synonyms field; its docs express them as ai_context prose. The
    structured list is parked so the Ossie round trip stays exact."""
    ds = (
        "  - name: orders\n"
        "    source: t\n"
        "    ai_context:\n"
        "      instructions: Order facts.\n"
        "      synonyms:\n"
        "      - purchases\n"
        "      - sales\n"
        "    fields:\n"
        "    - name: id\n"
        "      expression:\n"
        "        dialects:\n"
        "        - dialect: ANSI_SQL\n"
        "          expression: id\n"
        "      datatype: Integer\n"
    )
    files, _ = convert_ossie_to_cube(_ossie(ds))
    meta = _cubes(files)["orders"]["meta"]
    assert meta["ai_context"] == "Order facts.\nAlso known as: purchases, sales."
    assert meta["ossie"]["ai_context"]["synonyms"] == ["purchases", "sales"]


# --- review findings: export side -----------------------------------------------

@pytest.mark.parametrize("key,path", [
    ("cube_files", "../../outside.yml"),
    ("cube_files", "/etc/outside.yml"),
    ("view_files", "../escaped.yml"),
    ("extra_files", "../../notes.txt"),
])
def test_a_stashed_path_may_not_escape_the_output_directory(key, path):
    """The stash is part of the input document, so a path in it is untrusted. Export
    used to join it onto `--output` unchecked, which wrote outside that directory."""
    import json
    stash = {"_v": 1, "views": {}}
    if key == "view_files":
        # The path is only consulted for a view the stash actually carries.
        stash["views"] = {"shop": {"name": "shop",
                                  "cubes": [{"join_path": "orders",
                                             "includes": "*"}]}}
        stash["view_files"] = {"shop": path}
    elif key == "extra_files":
        stash["extra_files"] = {path: "x"}
    else:
        stash["cube_files"] = {"orders": path}
    ossie = _ossie(_ORDERS) + (
        "  custom_extensions:\n"
        "  - vendor_name: CUBE\n"
        f"    data: '{json.dumps(stash)}'\n")
    with pytest.raises(ConversionError, match="absolute|escapes the output"):
        convert_ossie_to_cube(ossie)


def test_a_field_and_a_metric_sharing_a_name_are_rejected():
    """Cube keeps one member namespace per cube ("orders cube: revenue defined more
    than once"), so this produced a model Cube refuses to compile."""
    ossie = _ossie(_ORDERS, metrics=_metric("amount", "SUM(orders.amount)"))
    with pytest.raises(ConversionError, match="share a name"):
        convert_ossie_to_cube(ossie)


def test_a_metric_datatype_survives_the_round_trip():
    """Cube has no field for a measure's result type, and import can infer one only
    for the count family -- so anything else has to be parked or it is lost."""
    ossie = _ossie(_ORDERS, metrics=(
        "  metrics:\n  - name: total\n    datatype: Decimal\n"
        "    expression:\n      dialects:\n      - dialect: ANSI_SQL\n"
        "        expression: SUM(orders.amount)\n"))
    files, _ = convert_ossie_to_cube(ossie)
    measure = _cubes(files)["orders"]["measures"][0]
    assert measure["meta"]["ossie"]["datatype"] == "Decimal"
    ossie2, _ = convert_cube_to_ossie(files)
    assert model_of(ossie2)["metrics"][0]["datatype"] == "Decimal"


def test_a_count_metric_datatype_is_not_parked_because_import_infers_it():
    ossie = _ossie(_ORDERS, metrics=(
        "  metrics:\n  - name: n\n    datatype: Integer\n"
        "    expression:\n      dialects:\n      - dialect: ANSI_SQL\n"
        "        expression: COUNT(DISTINCT orders.id)\n"))
    files, _ = convert_ossie_to_cube(ossie)
    assert "meta" not in _cubes(files)["orders"]["measures"][0]


def test_relationship_extensions_are_parked_on_the_declaring_cube():
    """A Cube join entry takes only name/sql/relationship, so a relationship's foreign
    extensions have nowhere to go on the join itself. They used to vanish silently."""
    rel = ("  relationships:\n  - name: r\n    from: orders\n    to: users\n"
           "    from_columns: [user_id]\n    to_columns: [id]\n"
           "    custom_extensions:\n    - vendor_name: DBT\n      data: keep-me\n")
    files, issues = convert_ossie_to_cube(_ossie(_TWO_DATASETS, rel))
    parked = _cubes(files)["orders"]["meta"]["ossie"]["join_extensions"]
    assert parked["users"] == [{"vendor_name": "DBT", "data": "keep-me"}]
    assert issues.of_type(IssueType.PARKED_IN_META)
    # And they come back onto the relationship.
    ossie2, _ = convert_cube_to_ossie(files)
    restored = model_of(ossie2)["relationships"][0]["custom_extensions"]
    assert {"vendor_name": "DBT", "data": "keep-me"} in restored


# --- review round three ----------------------------------------------------------

def test_a_wrapped_single_aggregate_stays_one_calculated_measure():
    """Deliberately *not* decomposed, and the reason is worth recording: Cube applies
    its row-multiplication correction to a calculated measure exactly as it does to a
    structured one. Asked directly, `SUM({CUBE}.amount) / 100` as `type: number` and the
    same thing split into a hidden `type: sum` plus a ratio produce identical SQL under
    fan-out -- both go through `SELECT DISTINCT <pk>` and the `keys` subquery, differing
    only in whether Cube renders the aggregate as `SUM` or `sum`. Splitting it would add
    a hidden measure and buy nothing."""
    files, _ = convert_ossie_to_cube(
        _ossie(_ORDERS, metrics=_metric("pct", "SUM(orders.amount) / 100")))
    measures = _cubes(files)["orders"]["measures"]
    assert measures == [
        {"name": "pct", "sql": "SUM({CUBE}.amount) / 100", "type": "number"}]


def test_a_metric_over_a_field_with_no_usable_dialect_is_dropped_too():
    """The field becomes no dimension, so a measure referencing it is a model Cube
    refuses: "orders.legacy_amount cannot be resolved. There's no such member or cube."
    """
    ds = (
        "  - name: orders\n"
        "    source: sales.public.orders\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n"
        "      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "    - name: legacy_amount\n"
        "      expression:\n        dialects:\n"
        "        - dialect: TABLEAU\n          expression: amount\n"
        "      datatype: Decimal\n"
    )
    files, issues = convert_ossie_to_cube(
        _ossie(ds, metrics=_metric("total", "SUM(orders.legacy_amount)")))
    assert "measures" not in _cubes(files)["orders"]
    assert any("dropped with it" in i.detail
               for i in issues.of_type(IssueType.NO_USABLE_DIALECT))


def test_a_generated_part_name_avoids_an_existing_dimension():
    """The suffix loop only sees names it is told about. It used to be given the
    *reference* members rather than every dimension, so a plain field named
    `ratio_part_1` collided and the conversion failed instead of picking the next
    free name."""
    ds = _ORDERS + (
        "    - name: ratio_part_1\n"
        "      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: ratio_part_1\n"
        "      datatype: Decimal\n"
    )
    files, _ = convert_ossie_to_cube(_ossie(ds, metrics=_metric(
        "ratio", "SUM(orders.amount) / COUNT(DISTINCT orders.id)")))
    names = [m["name"] for m in _cubes(files)["orders"]["measures"]]
    assert names == ["ratio_part_2", "ratio_part_3", "ratio"]
    # And the dimension of that name is untouched.
    assert "ratio_part_1" in by_name(_cubes(files)["orders"]["dimensions"])


def test_two_relationships_to_one_dataset_are_refused():
    """A cube's `joins` are keyed by target, so Cube can hold one join per target.
    Emitting two does not fail -- the transpiler keeps the last and silently discards
    the first, so every query through the lost relationship joins on the surviving
    predicate. Verified against Cube: with `buyer` and `seller` both declared, the SQL
    joins on `seller_id` and `buyer` is simply gone."""
    rel = ("  relationships:\n"
           "  - name: buyer\n    from: orders\n    to: users\n"
           "    from_columns: [id]\n    to_columns: [id]\n"
           "  - name: seller\n    from: orders\n    to: users\n"
           "    from_columns: [amount]\n    to_columns: [id]\n")
    with pytest.raises(ConversionError, match="one join per target"):
        convert_ossie_to_cube(_ossie(_TWO_DATASETS, rel))


def test_a_stashed_measure_title_is_not_escaped_twice():
    """It came out of the stash, so it is already whatever Cube needs. Escaping it
    again turned a valid `Revenue \\{USD\\}` into `Revenue \\\\{USD\\\\}`."""
    src = {"model/cubes/orders.yml": (
        "cubes:\n  - name: orders\n    sql_table: a.b.orders\n    dimensions:\n"
        "      - name: id\n        sql: id\n        type: number\n"
        "        primary_key: true\n"
        "    measures:\n      - name: revenue\n        sql: amount\n        type: sum\n"
        "        title: 'Revenue \\{USD\\}'\n")}
    ossie, _ = convert_cube_to_ossie(src)
    back, _ = convert_ossie_to_cube(ossie)
    measure = parse(back["model/cubes/orders.yml"])["cubes"][0]["measures"][0]
    assert measure["title"] == "Revenue \\{USD\\}"


def test_a_generated_view_excludes_members_a_prefix_cannot_disambiguate():
    """The ordinary star schema: the fact carries `users_id` as its foreign key, and
    prefixing `users`' own `id` produces that same name -- so the prefix remedy collides
    in its own right. Refusing was wrong; the model is as standard as they come. The
    clashing member is excluded from the view and reported, and stays queryable on the
    cube itself."""
    datasets = (
        "  - name: orders\n"
        "    source: shop.public.orders\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "    - name: users_id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: users_id\n"
        "      datatype: Integer\n"
        "  - name: users\n"
        "    source: shop.public.users\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
    )
    rel = ("  relationships:\n"
           "  - name: r\n    from: orders\n    to: users\n"
           "    from_columns: [users_id]\n    to_columns: [id]\n")
    files, issues = convert_ossie_to_cube(_ossie(datasets, rel))
    view = parse(files["model/views/shop.yml"])["views"][0]
    assert view["cubes"] == [
        {"join_path": "orders", "includes": "*"},
        # `users.id` would become `users_id`, which `orders` already has.
        {"join_path": "orders.users", "includes": "*", "prefix": True,
         "excludes": ["id"]},
    ]
    assert any("excluded from the generated view" in i.detail
               for i in issues.of_type(IssueType.APPROXIMATED))


@pytest.mark.parametrize("reference,expected", [
    # A metric is authored against *Ossie* names, which for a name needing sanitization
    # is not the Cube name: dataset `Order Items` becomes cube `order_items`.
    ('"ORDER ITEMS"."GROSS AMOUNT"', "{CUBE.gross_amount}"),
    ("order_items.gross_amount", "{CUBE.gross_amount}"),
])
def test_a_reference_may_use_either_the_ossie_or_the_cube_name(reference, expected):
    ossie = (
        f"version: {OSSIE_VERSION}\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: Order Items\n"
        "    source: shop.public.oi\n"
        "    fields:\n"
        "    - name: Gross Amount\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: gross_raw * 2\n"
        "      datatype: Decimal\n"
        "  metrics:\n"
        "  - name: m\n    expression:\n      dialects:\n"
        f"      - dialect: ANSI_SQL\n        expression: SUM({reference})\n"
    )
    files, _ = convert_ossie_to_cube(ossie)
    cube = parse(files["model/cubes/order_items.yml"])["cubes"][0]
    assert cube["measures"] == [{"name": "m", "sql": expected, "type": "sum"}]


def test_a_quoted_reference_to_a_dropped_field_drops_its_metric_too():
    """The dropped-field check matched only unquoted references, so a metric over a
    field that became no dimension survived with a dangling reference."""
    ds = (
        "  - name: orders\n"
        "    source: shop.public.orders\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "    - name: legacy_amount\n      expression:\n        dialects:\n"
        "        - dialect: TABLEAU\n          expression: amount\n"
        "      datatype: Decimal\n"
    )
    files, issues = convert_ossie_to_cube(
        _ossie(ds, metrics=_metric("t", 'SUM(orders."LEGACY_AMOUNT")')))
    assert "measures" not in _cubes(files)["orders"]
    assert any("dropped with it" in i.detail
               for i in issues.of_type(IssueType.NO_USABLE_DIALECT))


def test_mapping_form_stashed_segments_are_reserved_and_checked():
    """Cube accepts `segments:` as a list *or* as a mapping keyed by name. Handling only
    the list form meant a mapping iterated as bare strings and was skipped -- so a
    generated part could take a restored segment's name, and the collision check missed
    it too, emitting a model Cube rejects."""
    import json

    stash = {"_v": 1,
             "cube_extras": {"segments": {"ratio_part_1": {"sql": "x"}}}}
    ds = _ORDERS.replace(
        "      datatype: Decimal\n",
        "      datatype: Decimal\n"
        "    custom_extensions:\n"
        "    - vendor_name: CUBE\n"
        f"      data: '{json.dumps(stash)}'\n", 1)
    files, _ = convert_ossie_to_cube(_ossie(ds, metrics=_metric(
        "ratio", "SUM(orders.amount) / COUNT(DISTINCT orders.id)")))
    cube = _cubes(files)["orders"]
    assert [m["name"] for m in cube["measures"]] == [
        "ratio_part_2", "ratio_part_3", "ratio"]
    assert set(cube["segments"]) == {"ratio_part_1"}


def test_a_name_that_must_be_quoted_resolves_when_quoted_exactly():
    """`Order Items` cannot be written unquoted at all, so `"Order Items"` is the only
    way to reference it -- exact-quoted has to resolve or the name is unusable."""
    ossie = (
        f"version: {OSSIE_VERSION}\n"
        "semantic_model:\n"
        "- name: shop\n"
        "  datasets:\n"
        "  - name: Order Items\n"
        "    source: shop.public.oi\n"
        "    fields:\n"
        "    - name: Gross Amount\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: gross_raw * 2\n"
        "      datatype: Decimal\n"
        "  metrics:\n"
        "  - name: m\n    expression:\n      dialects:\n"
        '      - dialect: ANSI_SQL\n        expression: SUM("Order Items"."Gross Amount")\n'
    )
    files, _ = convert_ossie_to_cube(ossie)
    assert parse(files["model/cubes/order_items.yml"])["cubes"][0][
        "measures"] == [{"name": "m", "sql": "{CUBE.gross_amount}", "type": "sum"}]


def test_a_plain_field_reference_is_canonicalized_to_its_column():
    """A plain member is the same thing either way, but the column has a canonical
    spelling: emitting `"AMOUNT"` as written would force an exact uppercase match in the
    database against a column named `amount`."""
    ds = (
        "  - name: orders\n"
        "    source: shop.public.orders\n"
        "    fields:\n"
        "    - name: amount\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: amount\n"
        "      datatype: Decimal\n"
    )
    files, _ = convert_ossie_to_cube(
        _ossie(ds, metrics=_metric("m", 'SUM(orders."AMOUNT")')))
    assert _cubes(files)["orders"]["measures"][0]["sql"] == "{CUBE}.amount"


def test_a_mapping_form_segment_is_counted_when_disambiguating_a_view():
    """Collecting the members a generated view must disambiguate assumed every collection
    was a list, so a mapping-form segment was skipped -- and a segment named `users_id`
    plus a prefixed `users.id` both reached the view under that one name."""
    import json

    stash = {"_v": 1, "cube_extras": {"segments": {"users_id": {"sql": "x"}}}}
    datasets = (
        "  - name: orders\n"
        "    source: shop.public.orders\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
        "    - name: user_id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: user_id\n"
        "      datatype: Integer\n"
        "    custom_extensions:\n"
        "    - vendor_name: CUBE\n"
        f"      data: '{json.dumps(stash)}'\n"
        "  - name: users\n"
        "    source: shop.public.users\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        "        - dialect: ANSI_SQL\n          expression: id\n"
        "      datatype: Integer\n"
    )
    rel = ("  relationships:\n"
           "  - name: r\n    from: orders\n    to: users\n"
           "    from_columns: [user_id]\n    to_columns: [id]\n")
    files, issues = convert_ossie_to_cube(_ossie(datasets, rel))
    entry = parse(files["model/views/shop.yml"])["views"][0]["cubes"][1]
    assert entry["excludes"] == ["id"]
    assert issues.of_type(IssueType.APPROXIMATED)


# --- a model from another converter -----------------------------------------------
#
# Everything else here starts from a Cube model or from Ossie written for this test
# suite. A document another converter produced is shaped differently, and the two
# differences below both used to end the conversion in silence.

def _databricks_ossie():
    from _util import load_fixture

    return load_fixture("databricks_ossie.yaml")


def test_a_model_with_only_a_warehouse_dialect_still_converts():
    """The Databricks converter emits `DATABRICKS` and no ANSI_SQL. Requiring ANSI meant
    every field and metric was dropped and the export was an *empty* Cube model -- which
    Cube compiles, so nothing downstream noticed either."""
    files, issues = convert_ossie_to_cube(_databricks_ossie())
    cube = _cubes(files)["orders"]
    assert [d["name"] for d in cube["dimensions"]] == ["o_orderkey", "o_orderdate"]
    assert [m["name"] for m in cube["measures"]] == ["total_revenue", "order_count"]
    # Reported, because Cube will pass that SQL to whatever the data source is.
    assert any("only dialect on offer" in i.detail
               for i in issues.of_type(IssueType.APPROXIMATED))


def test_a_non_sql_dialect_is_still_not_usable():
    """The fallback is to warehouse SQL only. MDX, TABLEAU and MAQL are query or
    calculation languages, so there is nothing for Cube to pass through."""
    ds = (
        "  - name: orders\n"
        "    source: shop.public.orders\n"
        "    fields:\n"
        "    - name: note\n      expression:\n        dialects:\n"
        "        - dialect: TABLEAU\n          expression: note\n"
    )
    files, issues = convert_ossie_to_cube(_ossie(ds))
    assert "dimensions" not in _cubes(files)["orders"]
    assert issues.of_type(IssueType.NO_USABLE_DIALECT)


def test_unique_keys_supply_the_primary_key_cube_requires_for_a_join():
    """Cube refuses a cube that declares a join without a primary key, and several source
    formats have no primary-key concept -- a Databricks metric view does not. The first
    `unique_keys` entry identifies a row just as well, and was sitting parked in
    `meta.ossie` while Cube rejected the model for want of exactly it."""
    files, issues = convert_ossie_to_cube(_databricks_ossie())
    keys = [d for d in _cubes(files)["orders"]["dimensions"] if d.get("primary_key")]
    assert [d["sql"] for d in keys] == ["o_orderkey"]
    assert any("unique_keys entry" in i.detail
               for i in issues.of_type(IssueType.APPROXIMATED))
    # And it is still parked, so the round trip keeps it.
    assert _cubes(files)["orders"]["meta"]["ossie"]["unique_keys"] == [["o_orderkey"]]


def test_a_join_with_no_key_at_all_says_what_cube_will_refuse():
    """Nothing can be invented here, so the issue names Cube's requirement and the
    remedy rather than leaving a model that quietly will not load."""
    import yaml as _yaml

    doc = _yaml.safe_load(_databricks_ossie())
    for ds in doc["semantic_model"][0]["datasets"]:
        ds.pop("unique_keys", None)
    _, issues = convert_ossie_to_cube(_yaml.dump(doc, sort_keys=False))
    dropped = issues.of_type(IssueType.DROPPED_NO_CUBE_EQUIVALENT)
    assert any("requires a primary key on any cube with a join" in i.detail
               for i in dropped)


@pytest.mark.parametrize("dialect", ["SNOWFLAKE", "DATABRICKS", "BIGQUERY"])
def test_any_warehouse_dialect_alone_is_enough_to_convert(dialect):
    """The fallback is not Databricks-specific: a model carrying only Snowflake or
    BigQuery SQL converts too, since Cube passes SQL to whatever the data source is."""
    ds = (
        "  - name: orders\n"
        "    source: shop.public.orders\n"
        "    primary_key:\n    - id\n"
        "    fields:\n"
        "    - name: id\n      expression:\n        dialects:\n"
        f"        - dialect: {dialect}\n          expression: id\n"
        "      datatype: Integer\n"
    )
    files, issues = convert_ossie_to_cube(_ossie(ds, metrics=_metric(
        "n", "COUNT(DISTINCT orders.id)")))
    cube = _cubes(files)["orders"]
    assert [d["name"] for d in cube["dimensions"]] == ["id"]
    assert cube["measures"] == [{"name": "n", "type": "count"}]
    assert any(dialect in i.detail
               for i in issues.of_type(IssueType.APPROXIMATED))
