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
        "primary_key": True, "public": False}
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
        {"join_path": "orders.users", "includes": "*"},
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
