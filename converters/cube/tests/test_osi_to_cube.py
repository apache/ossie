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
from _util import by_name, parse

from ossie_cube import ConversionError, IssueType, convert_ossie_to_cube
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
    assert issues.of_type(IssueType.PARKED_IN_META)


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
    detail = issues.of_type(IssueType.PARKED_IN_META)[0].detail
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
    assert issues.of_type(IssueType.PARKED_IN_META)


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
    assert join == {"name": "users", "sql": "{CUBE}.user_id = {users.id}",
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
        "{CUBE}.user_id = {users.id} AND {CUBE}.region = {users.region}")


def test_relationship_ai_context_has_no_cube_home():
    rel = _REL + "    ai_context:\n      instructions: Join carefully.\n"
    _, issues = convert_ossie_to_cube(_ossie(_TWO_DATASETS, rel))
    assert any("ai_context" in i.detail for i in issues.of_type(
        IssueType.PARKED_IN_META))


# --- metrics --------------------------------------------------------------------

def _metric(name, expr):
    return ("  metrics:\n"
            f"  - name: {name}\n"
            "    expression:\n"
            "      dialects:\n"
            "      - dialect: ANSI_SQL\n"
            f"        expression: {expr}\n")


@pytest.mark.parametrize("expr,expected", [
    ("SUM(orders.amount)", {"type": "sum", "sql": "{CUBE.amount}"}),
    ("AVG(orders.amount)", {"type": "avg", "sql": "{CUBE.amount}"}),
    ("MIN(orders.amount)", {"type": "min", "sql": "{CUBE.amount}"}),
    ("MAX(orders.amount)", {"type": "max", "sql": "{CUBE.amount}"}),
    ("COUNT(DISTINCT orders.amount)",
     {"type": "count_distinct", "sql": "{CUBE.amount}"}),
    ("APPROX_COUNT_DISTINCT(orders.amount)",
     {"type": "count_distinct_approx", "sql": "{CUBE.amount}"}),
    ("COUNT(*)", {"type": "count"}),
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


def test_ratio_becomes_a_calculated_measure():
    files, issues = convert_ossie_to_cube(_ossie(
        _TWO_DATASETS, _REL,
        _metric("aov", "SUM(orders.amount) / COUNT(DISTINCT users.id)")))
    measure = _cubes(files)["orders"]["measures"][0]
    assert measure["type"] == "number"
    assert measure["sql"] == "SUM({CUBE.amount}) / COUNT(DISTINCT {users.id})"
    assert any("spans several datasets" in i.detail
               for i in issues.of_type(IssueType.PARKED_IN_META))


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
