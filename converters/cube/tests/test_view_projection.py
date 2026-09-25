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

"""Projection of one Cube view's public surface onto an Ossie model.

Every projected document goes through the spec's own validator and back out through
`convert_ossie_to_cube`, so a shape the per-field assertions cannot see still fails.
"""

import pytest
from _cube_gate import assert_cube_compiles, assert_ossie_is_valid, cube_gate
from _roundtrip_helpers import RandomRnd, build_cube_model
from _util import by_name, expr_of, model_of, stash_of

from ossie_cube import (
    ConversionError,
    IssueType,
    convert_cube_to_ossie,
    convert_cube_view_to_ossie,
    convert_ossie_to_cube,
)
from ossie_cube._common import dump_yaml, load_yaml
from ossie_cube.cube_to_osi import _WINDOWING_KEYS
from ossie_cube.view_projection import _MULTI_STAGE_SHAPES

_MODEL = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - name: users
        sql: "{CUBE}.user_id = {users}.id"
        relationship: many_to_one
    dimensions:
      - name: id
        sql: id
        type: number
        primary_key: true
      - name: user_id
        sql: user_id
        type: number
      - name: status
        sql: status
        type: string
        title: Source status
      - name: gross
        sql: gross
        type: number
      - name: discount
        sql: discount
        type: number
      - name: net_amount
        sql: "{gross} - {discount}"
        type: number
    measures:
      - name: count
        type: count
      - name: revenue
        sql: "{net_amount}"
        type: sum
      - name: average_value
        sql: "{revenue} / {count}"
        type: number
  - name: users
    sql_table: main.sales.users
    dimensions:
      - name: id
        sql: id
        type: number
        primary_key: true
      - name: first_name
        sql: first_name
        type: string
      - name: last_name
        sql: last_name
        type: string
      - name: full_name
        sql: "CONCAT({first_name}, ' ', {last_name})"
        type: string
      - name: city
        sql: city
        type: string
    measures:
      - name: lifetime_value
        sql: ltv
        type: sum
views:
  - name: sales
    description: Curated sales surface
    meta:
      ai_context: Prefer this view for sales questions.
    cubes:
      - join_path: orders
        includes:
          - status
          - average_value
      - join_path: orders.users
        alias: customer
        prefix: true
        includes:
          - name: full_name
            alias: name
            title: Customer name
            description: Full customer name
            meta:
              ai_context: Use this instead of separate name parts.
"""


def _project(text=_MODEL, view="sales", **kwargs):
    """Project, then prove the output is a valid Ossie model Cube can take back."""
    out, source, issues = convert_cube_view_to_ossie({"model.yml": text}, view, **kwargs)
    assert_ossie_is_valid(out, f"projection of view '{view}'")
    convert_ossie_to_cube(out, base_cube=source)
    return out, source, issues


def _surface(out):
    """({dataset: {field names}}, {metric names}) of a projected model."""
    model = model_of(out)
    fields = {ds["name"]: set(by_name(ds.get("fields"))) for ds in model["datasets"]}
    return fields, set(by_name(model.get("metrics")))


def _metric(out, name):
    return expr_of(by_name(model_of(out)["metrics"])[name])


def _field(out, dataset, name):
    return expr_of(by_name(by_name(model_of(out)["datasets"])[dataset]["fields"])[name])


def _single_cube(measures="", dimensions="", includes="[revenue]"):
    return f"""
cubes:
  - name: orders
    sql_table: main.sales.orders
    dimensions:
      - {{name: id, sql: id, type: number, primary_key: true}}
{dimensions}    measures:
      - {{name: revenue, sql: amount, type: sum}}
{measures}views:
  - name: sales
    cubes:
      - {{join_path: orders, includes: {includes}}}
"""


# --- the surface ------------------------------------------------------------------

def test_projects_exact_view_surface_and_resolves_source():
    out, source, issues = _project()
    model = model_of(out)
    assert source == "orders"
    assert not list(issues)
    assert model["name"] == "sales"
    assert model["description"] == "Curated sales surface"
    assert model["ai_context"]["instructions"].startswith("Prefer this view")
    assert _surface(out) == ({"orders": {"status"}, "users": {"customer_name"}},
                             {"average_value"})
    # The flat document: model properties at the root.
    assert "semantic_model" not in model


def test_lossless_import_remains_unprojected():
    out, _ = convert_cube_to_ossie({"model.yml": _MODEL}, view="sales")
    model = model_of(out)
    datasets = by_name(model["datasets"])
    assert {"id", "user_id", "status", "gross", "discount", "net_amount"} <= set(
        by_name(datasets["orders"]["fields"]))
    assert {"count", "revenue", "average_value", "lifetime_value"} <= set(
        by_name(model["metrics"]))


def test_member_alias_prefix_and_metadata_override_are_effective():
    out, _, _ = _project()
    field = by_name(by_name(model_of(out)["datasets"])["users"]["fields"])["customer_name"]
    assert field["label"] == "Customer name"
    assert field["description"] == "Full customer name"
    assert field["ai_context"]["instructions"].startswith("Use this instead")
    # The member's own title is kept when the view does not override it.
    status = by_name(by_name(model_of(out)["datasets"])["orders"]["fields"])["status"]
    assert status["label"] == "Source status"


def test_hidden_calculated_measure_dependencies_are_inlined():
    out, _, _ = _project()
    assert _metric(out, "average_value") == (
        "SUM(orders.gross - orders.discount) / COUNT(DISTINCT orders.id)")


def test_hidden_computed_dimensions_are_inlined_in_fields_and_metrics():
    out, _, _ = _project()
    assert _field(out, "users", "customer_name") == "CONCAT(first_name, ' ', last_name)"
    assert "orders.net_amount" not in _metric(out, "average_value")


def test_an_inlined_dimension_keeps_its_precedence():
    out, _, _ = _project(_single_cube(
        dimensions='      - {name: net, sql: "{gross} - {discount}", type: number}\n'
                   '      - {name: gross, sql: gross, type: number}\n'
                   '      - {name: discount, sql: discount, type: number}\n',
        measures='      - {name: doubled, sql: "{net} * 2", type: sum}\n',
        includes="[doubled]"))
    assert _metric(out, "doubled") == "SUM((orders.gross - orders.discount) * 2)"


def test_bare_count_uses_primary_key_physical_sql():
    text = """
cubes:
  - name: orders
    sql_table: samples.tpch.orders
    dimensions:
      - {name: id, sql: o_orderkey, type: number, primary_key: true}
    measures:
      - {name: count, type: count}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [count]}
"""
    out, _, _ = _project(text)
    assert _metric(out, "count") == "COUNT(DISTINCT orders.o_orderkey)"
    # The dataset's key names the column, since no `id` field is published.
    assert by_name(model_of(out)["datasets"])["orders"]["primary_key"] == ["o_orderkey"]
    assert by_name(model_of(out)["metrics"])["count"]["datatype"] == "Integer"


def test_bare_count_over_a_composite_key_concatenates_it_portably():
    text = """
cubes:
  - name: lines
    sql_table: samples.tpch.lineitem
    dimensions:
      - {name: order_id, sql: l_orderkey, type: number, primary_key: true}
      - {name: line_id, sql: l_linenumber, type: number, primary_key: true}
      - {name: active, sql: is_active, type: boolean}
    measures:
      - {name: count, type: count}
      - name: active_count
        type: count
        filters:
          - sql: "{active} = true"
views:
  - name: sales
    cubes:
      - {join_path: lines, includes: [count, active_count]}
"""
    out, _, _ = _project(text)
    key = "CONCAT(CAST(lines.l_orderkey AS VARCHAR), CAST(lines.l_linenumber AS VARCHAR))"
    assert _metric(out, "count") == f"COUNT(DISTINCT {key})"
    assert _metric(out, "active_count") == (
        f"COUNT(DISTINCT CASE WHEN (lines.is_active = true) THEN {key} END)")


@pytest.mark.parametrize("hidden_dependency", [False, True])
def test_bare_count_primary_key_dependencies_add_required_joins(hidden_dependency):
    selected = "published" if hidden_dependency else "count"
    calculated = ('      - {name: published, sql: "{count}", type: number}\n'
                  if hidden_dependency else "")
    text = f"""
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {{name: users, sql: "{{CUBE}}.user_id = {{users}}.id", relationship: many_to_one}}
    dimensions:
      - name: external_id
        sql: "{{users}}.external_id"
        type: number
        primary_key: true
    measures:
      - {{name: count, type: count}}
{calculated}  - name: users
    sql_table: main.sales.users
    dimensions:
      - {{name: id, sql: id, type: number, primary_key: true}}
views:
  - name: sales
    cubes:
      - {{join_path: orders, includes: [{selected}]}}
"""
    out, _, issues = _project(text)
    model = model_of(out)
    assert [(r["from"], r["to"]) for r in model["relationships"]] == [("orders", "users")]
    assert _metric(out, selected) == "COUNT(DISTINCT users.external_id)"
    # The key reads another cube's column, so the dataset can name no column of its own.
    assert "primary_key" not in by_name(model["datasets"])["orders"]
    assert any(i.element_name == "cube 'orders'" and "primary key" in i.detail
               for i in issues.of_type(IssueType.DROPPED_FROM_PROJECTION))


@pytest.mark.parametrize("hidden_dependency", [False, True])
def test_bare_count_primary_key_dependencies_are_validated(hidden_dependency):
    selected = "published" if hidden_dependency else "count"
    calculated = ('      - {name: published, sql: "{count}", type: number}\n'
                  if hidden_dependency else "")
    text = f"""
cubes:
  - name: orders
    sql_table: main.sales.orders
    dimensions:
      - name: id
        sql: "{{missing_id}}"
        type: number
        primary_key: true
    measures:
      - {{name: count, type: count}}
{calculated}views:
  - name: sales
    cubes:
      - {{join_path: orders, includes: [{selected}]}}
"""
    with pytest.raises(ConversionError, match="missing_id.*does not match"):
        _project(text)


def test_bare_count_without_a_primary_key_is_refused():
    text = _single_cube(measures="      - {name: count, type: count}\n",
                        includes="[count]").replace(
        "      - {name: id, sql: id, type: number, primary_key: true}\n", "")
    with pytest.raises(ConversionError, match="needs the cube's primary key"):
        _project(text)


def test_bare_count_preserves_a_recorded_physical_primary_key():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    meta:
      ossie:
        primary_key: [id]
    dimensions:
      - {name: id, sql: surrogate_id, type: number}
    measures:
      - {name: count, type: count}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [count]}
"""
    out, _, _ = _project(text)
    assert _metric(out, "count") == "COUNT(DISTINCT orders.id)"
    assert by_name(model_of(out)["datasets"])["orders"]["primary_key"] == ["id"]


def test_case_dimensions_render_sql_labels():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: users, sql: "{CUBE}.user_id = {users}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: user_id, sql: user_id, type: number}
      - {name: status, sql: status_code, type: string}
      - {name: preferred_label, sql: preferred_label, type: string}
      - {name: default_label, sql: default_label, type: string}
      - name: source_segment
        type: string
        case:
          when:
            - sql: "{status} = 'vip'"
              label: {sql: "{preferred_label}"}
          else:
            label: {sql: "{default_label}"}
      - {name: segment_code, sql: "UPPER({source_segment})", type: string}
  - name: users
    sql_table: main.sales.users
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: tier, sql: tier_code, type: string}
      - name: joined_segment
        type: string
        case:
          when:
            - sql: "{tier} = 'vip'"
              label: V'IP
          else:
            label: other
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [source_segment, segment_code]}
      - {join_path: orders.users, includes: [joined_segment]}
"""
    out, _, _ = _project(text)
    case = ("CASE WHEN status_code = 'vip' THEN preferred_label "
            "ELSE default_label END")
    assert _field(out, "orders", "source_segment") == case
    # A `case` dimension referenced elsewhere inlines as its CASE, parenthesized like
    # any inlined expression with top-level structure.
    assert _field(out, "orders", "segment_code") == f"UPPER(({case}))"
    assert _field(out, "users", "joined_segment") == (
        "CASE WHEN tier_code = 'vip' THEN 'V''IP' ELSE 'other' END")


def test_wildcard_and_excludes_follow_cube_view_semantics():
    text = _MODEL.replace(
        "includes:\n          - status\n          - average_value",
        "includes: '*'\n        excludes: [id, user_id, gross, discount, net_amount, "
        "count, revenue]",
    ).replace(
        "includes:\n          - name: full_name\n            alias: name\n"
        "            title: Customer name\n            description: Full customer name\n"
        "            meta:\n              ai_context: Use this instead of separate name "
        "parts.",
        "includes: '*'\n        excludes: [id, first_name, last_name, lifetime_value]",
    )
    out, _, _ = _project(text)
    assert _surface(out) == (
        {"orders": {"status"}, "users": {"customer_full_name", "customer_city"}},
        {"average_value"})


def test_wildcard_skips_private_members_but_explicit_includes_can_publish_them():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: status, sql: status, type: string}
      - {name: internal_status, sql: internal_status, type: string, public: false}
    measures:
      - {name: revenue, sql: revenue, type: sum}
      - {name: internal_revenue, sql: internal_revenue, type: sum, public: false}
views:
  - name: wildcard_sales
    cubes:
      - {join_path: orders, includes: '*'}
  - name: explicit_sales
    cubes:
      - {join_path: orders, includes: [internal_status, internal_revenue]}
"""
    out, _, _ = _project(text, "wildcard_sales")
    assert _surface(out) == ({"orders": {"id", "status"}}, {"revenue"})
    out, _, issues = _project(text, "explicit_sales")
    assert _surface(out) == ({"orders": {"internal_status"}}, {"internal_revenue"})
    # `public: false` is what the view overrides, not a property lost on the way.
    assert not issues.of_type(IssueType.DROPPED_FROM_PROJECTION)
    assert "public" not in str(model_of(out))


def test_wildcard_honours_the_deprecated_visibility_flags():
    text = _single_cube(
        dimensions="      - {name: old, sql: old, type: string, shown: false}\n"
                   "      - {name: older, sql: older, type: string, visible: false}\n"
                   "      - {name: back, sql: back, type: string, shown: false, "
                   "public: true}\n",
        includes="'*'")
    out, _, _ = _project(text)
    # `public` wins over the deprecated flags, as in Cube; a primary key is published,
    # since Cube's wildcard includes it.
    assert _surface(out) == ({"orders": {"id", "back"}}, {"revenue"})


_DECOMPOSED = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - name: total_part
        sql: amount
        type: sum
        public: PUBLIC
        meta:
          ossie:
            part_of: total
      - name: total
        sql: "{total_part}"
        type: number
        meta:
          ossie:
            decomposed: true
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: INCLUDES}
"""


def _decomposed(public, includes):
    return _DECOMPOSED.replace("PUBLIC", public).replace("INCLUDES", includes)


def test_wildcard_skips_ossie_generated_measure_parts():
    out, _, _ = _project(_decomposed("true", "'*'"))
    assert _surface(out)[1] == {"total"}
    assert _metric(out, "total") == "SUM(orders.amount)"


def test_explicit_ossie_generated_measure_part_is_refused():
    with pytest.raises(ConversionError, match="generated as part of 'total'"):
        _project(_decomposed("false", "[total_part]"))


def test_an_uncorroborated_part_marker_is_an_ordinary_measure():
    # The lossless import reads `part_of` only when the named measure records the
    # decomposition, and projection agrees with it.
    text = _decomposed("false", "[total_part]").replace(
        "        meta:\n          ossie:\n            decomposed: true\n", "")
    out, _, _ = _project(text)
    assert _metric(out, "total_part") == "SUM(orders.amount)"


def test_hidden_transitive_cross_cube_dependencies_add_their_cubes_and_joins():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: users, sql: "{CUBE}.user_id = {users}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: user_id, sql: user_id, type: number}
    measures:
      - {name: customer_value, sql: "{users.account_value}", type: number}
  - name: users
    sql_table: main.sales.users
    joins:
      - {name: accounts, sql: "{CUBE}.account_id = {accounts}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: account_id, sql: account_id, type: number}
    measures:
      - {name: account_value, sql: "{accounts.max_lifetime_value}", type: number}
  - name: accounts
    sql_table: main.sales.accounts
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - {name: max_lifetime_value, sql: ltv, type: max}
views:
  - name: sales
    cubes:
      - join_path: orders
        includes: [customer_value]
"""
    out, source, issues = _project(text)
    model = model_of(out)
    assert source == "orders"
    assert not list(issues)
    assert [ds["name"] for ds in model["datasets"]] == ["orders", "users", "accounts"]
    assert [(r["from"], r["to"]) for r in model["relationships"]] == [
        ("orders", "users"), ("users", "accounts")]
    assert _surface(out)[1] == {"customer_value"}
    assert _metric(out, "customer_value") == "MAX(accounts.ltv)"


def test_measure_dimension_dependencies_add_transitive_cubes_and_joins():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: users, sql: "{CUBE}.user_id = {users}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - {name: max_adjusted_score, sql: "{users.adjusted_score}", type: max}
  - name: users
    sql_table: main.sales.users
    joins:
      - {name: profiles, sql: "{CUBE}.profile_id = {profiles}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: multiplier, sql: multiplier, type: number}
      - {name: adjusted_score, sql: "{profiles.base_score} * {multiplier}", type: number}
  - name: profiles
    sql_table: main.sales.profiles
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: base_score, sql: base_score, type: number}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [max_adjusted_score]}
"""
    out, _, issues = _project(text)
    assert not list(issues)
    assert [(r["from"], r["to"]) for r in model_of(out)["relationships"]] == [
        ("orders", "users"), ("users", "profiles")]
    assert _metric(out, "max_adjusted_score") == (
        "MAX(profiles.base_score * users.multiplier)")


def test_a_join_through_a_hidden_key_member_resolves_to_its_column():
    text = _MODEL.replace('sql: "{CUBE}.user_id = {users}.id"',
                          'sql: "{CUBE.user_id} = {users.id}"')
    out, _, _ = _project(text)
    rel = model_of(out)["relationships"][0]
    assert (rel["from_columns"], rel["to_columns"]) == (["user_id"], ["id"])


def test_raw_column_is_not_shadowed_by_a_same_named_computed_dimension():
    out, _, _ = _project(_single_cube(
        dimensions='      - {name: amount, sql: "{CUBE}.gross - 1", type: number}\n',
        measures='      - {name: raw_amount, sql: "{CUBE}.amount", type: sum}\n',
        includes="[raw_amount]"))
    assert _metric(out, "raw_amount") == "SUM(orders.amount)"


@pytest.mark.parametrize(("sql", "expected"), [
    ("DATEDIFF(day, created_at, shipped_at)",
     "AVG(DATEDIFF(day, orders.created_at, orders.shipped_at))"),
    ("TIMESTAMPDIFF(SECOND, created_at, shipped_at)",
     "AVG(TIMESTAMPDIFF(SECOND, orders.created_at, orders.shipped_at))"),
    ("CONVERT(VARCHAR, code)", "AVG(CONVERT(VARCHAR, orders.code))"),
    ("EXTRACT(YEAR FROM created_at)", "AVG(EXTRACT(YEAR FROM orders.created_at))"),
])
def test_unit_and_type_arguments_are_not_columns(sql, expected):
    out, _, _ = convert_cube_view_to_ossie({"model.yml": _single_cube(
        dimensions=f'      - {{name: lead, sql: "{sql}", type: number}}\n',
        measures='      - {name: avg_lead, sql: "{lead}", type: avg}\n',
        includes="[avg_lead]")}, "sales")
    assert _metric(out, "avg_lead") == expected


def test_a_trailing_comment_does_not_swallow_the_expression_it_is_inlined_into():
    text = _single_cube(
        dimensions="      - name: gross\n        sql: |\n"
                   "          amount -- before tax\n        type: number\n",
        measures='      - {name: doubled, sql: "{gross} * 2 /* x */", type: sum}\n',
        includes="[doubled]")
    out, _, _ = _project(text)
    assert _metric(out, "doubled") == "SUM(orders.amount * 2)"


def test_ordered_set_aggregate_sql_is_kept_exactly():
    out, _, _ = _project(_single_cube(
        measures="      - name: median_amount\n"
                 "        sql: PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY amount)\n"
                 "        type: number\n",
        includes="[median_amount]"))
    assert _metric(out, "median_amount") == (
        "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY orders.amount)")


def test_lone_measure_reference_is_not_shadowed_by_same_named_cube():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - {name: users, sql: amount, type: sum}
      - {name: total, sql: "{users}", type: number}
  - name: users
    sql_table: main.sales.users
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [total]}
"""
    out, _, _ = _project(text)
    assert _metric(out, "total") == "SUM(orders.amount)"


def test_local_dimension_reference_is_not_shadowed_by_same_named_cube():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: users, sql: "{CUBE}.user_id = {users}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: users, sql: customer_name, type: string}
      - {name: display_name, sql: "UPPER({users})", type: string}
  - name: users
    sql_table: main.sales.users
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [display_name]}
      - {join_path: orders.users, includes: []}
"""
    out, _, _ = _project(text)
    assert _field(out, "orders", "display_name") == "UPPER(customer_name)"


_PATHS = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: accounts, sql: "{CUBE}.account_id = {accounts}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: balance, sql: SQL, type: number}
    measures:
      - {name: top, sql: SQL, type: max}
  - name: accounts
    sql_table: main.sales.accounts
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [balance, top]}
      - {join_path: orders.accounts, includes: []}
"""


@pytest.mark.parametrize(("sql", "metric", "field"), [
    # A path is a struct field of the declaring cube's table ...
    ("payload.balance", "MAX(orders.payload.balance)", "payload.balance"),
    ('"{CUBE}.accounts.balance"', "MAX(orders.accounts.balance)", "accounts.balance"),
    # ... Cube aliases that table by the cube's own name ...
    ("orders.balance", "MAX(orders.balance)", "balance"),
    # ... and a joined cube is read through its reference.
    ('"{accounts}.balance"', "MAX(accounts.balance)", None),
])
def test_every_column_path_names_its_dataset(sql, metric, field):
    text = _PATHS.replace("SQL", sql)
    if field is None:
        text = text.replace("includes: [balance, top]", "includes: [top]")
    out, _, _ = _project(text)
    assert _metric(out, "top") == metric
    if field is not None:
        assert _field(out, "orders", "balance") == field


def test_a_path_starting_with_another_cubes_name_is_ambiguous():
    # Cube reads `accounts.balance` as the joined table's column whenever `accounts` is
    # in the query, and as a struct field of `orders` otherwise.
    with pytest.raises(ConversionError, match=r"write '\{CUBE\}\.accounts\.balance'.*"
                                              r"'\{accounts\}\.balance'"):
        _project(_PATHS.replace("SQL", "accounts.balance"))


def test_raw_joined_column_adds_its_hidden_dependency_join():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: users, sql: "{CUBE}.user_id = {users}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - {name: max_ltv, sql: "{users}.ltv", type: max}
  - name: users
    sql_table: main.sales.users
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [max_ltv]}
"""
    out, _, _ = _project(text)
    model = model_of(out)
    assert set(by_name(model["datasets"])) == {"orders", "users"}
    assert [(r["from"], r["to"]) for r in model["relationships"]] == [("orders", "users")]
    assert _metric(out, "max_ltv") == "MAX(users.ltv)"

    # A hidden dependency's dataset can be the source; nothing selected is multiplied.
    _, source, _ = _project(text, source="users")
    assert source == "users"


# --- fan-out ----------------------------------------------------------------------

_FANOUT = _MODEL.replace(
    "- name: full_name\n            alias: name\n            title: Customer name\n"
    "            description: Full customer name\n            meta:\n"
    "              ai_context: Use this instead of separate name parts.",
    "- lifetime_value")


def test_unselected_fanout_metric_does_not_block_publication():
    out, _, issues = _project()
    assert "lifetime_value" not in _surface(out)[1]
    assert not issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)


def test_selected_fanout_metric_is_strict_by_default_and_reported_when_relaxed():
    with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
        _project(_FANOUT)
    out, _, issues = _project(_FANOUT, strict_fanout=False)
    assert "customer_lifetime_value" in _surface(out)[1]
    assert [i.element_name for i in issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)] == [
        "users.lifetime_value"]


def test_hidden_unsafe_measure_dependency_blocks_strict_publication():
    text = _MODEL.replace(
        '        sql: "{revenue} / {count}"\n        type: number',
        '        sql: "{revenue} / {count}"\n        type: number\n'
        '      - name: risky_value\n        sql: "{users.lifetime_value} / {count}"\n'
        '        type: number',
    ).replace("          - status\n          - average_value",
              "          - status\n          - risky_value")
    with pytest.raises(ConversionError, match="FANOUT_UNSAFE_METRIC"):
        _project(text)
    _, _, issues = _project(text, strict_fanout=False)
    assert {i.element_name for i in issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)} == {
        "orders.risky_value", "users.lifetime_value"}


_CHAIN = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: users, sql: "{CUBE}.user_id = {users}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - {name: total, sql: amount, type: sum}
  - name: users
    sql_table: main.sales.users
    joins:
      - {name: addresses, sql: "{CUBE}.id = {addresses}.user_id", relationship: one_to_many}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
  - name: addresses
    sql_table: main.sales.addresses
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: city, sql: city, type: string}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [total]}
      - {join_path: orders.users.addresses, includes: [city]}
"""


def test_fanout_through_a_chain_of_joins_is_caught():
    # Grouping by `city` repeats every order once per address of its user, although no
    # single relationship has `orders` on its one side.
    with pytest.raises(ConversionError, match="reads dataset 'orders'.*one-to-many join "
                                              "path to 'addresses'"):
        _project(_CHAIN)
    _, _, issues = _project(_CHAIN, strict_fanout=False)
    assert [i.element_name for i in issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)] == [
        "orders.total"]


def test_a_struct_path_is_attributed_to_its_dataset_for_fanout():
    # sqlglot reads `users.stats.ltv` as table `stats`; the dataset is `users`.
    text = _FANOUT.replace("        sql: ltv\n", "        sql: stats.ltv\n")
    with pytest.raises(ConversionError, match="reads dataset 'users'"):
        _project(text)


def test_a_one_to_one_join_multiplies_nothing():
    text = _FANOUT.replace("relationship: many_to_one", "relationship: one_to_one")
    out, _, issues = _project(text)
    assert not issues.of_type(IssueType.FANOUT_UNSAFE_METRIC)
    # The Cube cardinality survives, since Ossie's many/one orientation cannot say it.
    assert stash_of(model_of(out)["relationships"][0]) == {
        "declared_on": "orders", "relationship": "one_to_one"}


# --- source -----------------------------------------------------------------------

def test_source_override_is_returned_and_validated():
    _, source, _ = _project(_MODEL.replace("          - status\n", ""),
                            source="users")
    assert source == "users"
    with pytest.raises(ConversionError, match="not in the projected datasets"):
        _project(source="payments")


def test_source_that_would_multiply_a_selected_dimension_is_refused():
    # From `users`, `orders` is one-to-many: many orders per user, so `status` would
    # have several values per source row.
    with pytest.raises(ConversionError, match="'status'.*one value per row"):
        _project(source="users")


def test_a_one_to_many_view_path_is_the_views_own_grain():
    text = """
cubes:
  - name: users
    sql_table: main.sales.users
    joins:
      - {name: orders, sql: "{CUBE}.id = {orders}.user_id", relationship: one_to_many}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: city, sql: city, type: string}
  - name: orders
    sql_table: main.sales.orders
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: user_id, sql: user_id, type: number}
      - {name: status, sql: status, type: string}
views:
  - name: sales
    cubes:
      - {join_path: users, includes: [city]}
      - {join_path: users.orders, includes: [status]}
"""
    out, source, _ = _project(text)
    assert source == "users"
    rel = model_of(out)["relationships"][0]
    assert (rel["from"], rel["to"]) == ("orders", "users")
    # Re-rooting at `orders` multiplies nothing selected: `users` becomes one per row.
    _, source, _ = _project(text, source="orders")
    assert source == "orders"


# --- what the published model keeps ----------------------------------------------

def test_only_the_published_surface_carries_a_stash():
    text = _MODEL.replace(
        "      - name: status\n        sql: status\n        type: string\n",
        "      - name: status\n        sql: status\n        type: string\n"
        "        format: id\n        drill_members: [id]\n"
        "        meta: {team: sales}\n",
    ).replace("  - name: users\n    sql_table: main.sales.users\n",
              "  - name: users\n    sql_table: main.sales.users\n"
              "    data_source: crm\n    pre_aggregations: [{name: main}]\n"
              "    access_policy: [{group: '*'}]\n")
    out, _, issues = _project(text)
    model = model_of(out)
    datasets = by_name(model["datasets"])
    assert "custom_extensions" not in model
    assert "custom_extensions" not in datasets["orders"]
    assert stash_of(datasets["users"]) == {"cube_extras": {"data_source": "crm"}}
    assert stash_of(by_name(datasets["orders"]["fields"])["status"]) == {"format": "id"}
    dropped = {i.element_name: i.detail
               for i in issues.of_type(IssueType.DROPPED_FROM_PROJECTION)}
    assert set(dropped) == {"orders.status", "cube 'users'"}
    assert "drill_members, meta" in dropped["orders.status"]
    assert "access_policy, pre_aggregations" in dropped["cube 'users'"]


def test_view_level_cube_only_properties_are_reported():
    text = _MODEL.replace("    description: Curated sales surface\n",
                          "    description: Curated sales surface\n"
                          "    access_policy: [{group: '*'}]\n")
    _, _, issues = _project(text)
    assert [i.element_name for i in issues.of_type(IssueType.DROPPED_FROM_PROJECTION)] == [
        "view 'sales'"]


def test_measure_title_and_format_overrides_ride_in_the_stash():
    text = _single_cube(includes="[{name: revenue, title: Revenue, format: currency}]")
    out, _, _ = _project(text)
    assert stash_of(by_name(model_of(out)["metrics"])["revenue"]) == {
        "title": "Revenue", "format": "currency"}
    files, _ = convert_ossie_to_cube(out)
    cube = load_yaml(files["model/cubes/orders.yml"])["cubes"][0]
    assert by_name(cube["measures"])["revenue"]["format"] == "currency"


def test_the_same_member_can_be_published_under_two_names():
    out, _, _ = _project(_single_cube(includes="[revenue, {name: revenue, alias: sales}]"))
    assert _surface(out)[1] == {"revenue", "sales"}


def test_noop_multi_stage_defaults_remain_static():
    for key, value in (("multi_stage", "false"), ("group_by", "[]"),
                       ("time_shift", "[]"), ("grain", "{}"),
                       ("rolling_window", "null")):
        text = _single_cube().replace("type: sum}", f"type: sum, {key}: {value} }}")
        out, _, _ = _project(text)
        assert _surface(out)[1] == {"revenue"}


def test_the_multi_stage_check_covers_every_windowing_key_the_import_knows():
    assert set(_WINDOWING_KEYS) <= {"rolling_window", *_MULTI_STAGE_SHAPES}


# --- refusals ---------------------------------------------------------------------

@pytest.mark.parametrize("hidden_dependency", [False, True])
@pytest.mark.parametrize(("key", "value"), [
    ("rolling_window", "{trailing: 7 day}"),
    ("rolling_window", "{}"),
    ("multi_stage", "true"),
    ("group_by", "[status]"),
    ("reduce_by", "[status]"),
    ("add_group_by", "[status]"),
    ("time_shift", "[{time_dimension: created_at, interval: 1 day, type: prior}]"),
    ("grain", "{include: [status]}"),
])
def test_windowed_measures_are_refused(hidden_dependency, key, value):
    staged = f"      - {{name: staged, sql: amount, type: sum, {key}: {value} }}\n"
    published = ('      - {name: published, sql: "{staged}", type: number}\n'
                 if hidden_dependency else "")
    text = _single_cube(measures=staged + published,
                        includes="[published]" if hidden_dependency else "[staged]")
    with pytest.raises(ConversionError, match=f"uses (multi-stage )?{key}"):
        _project(text)


@pytest.mark.parametrize(("key", "value"), [
    ("multi_stage", "'false'"), ("group_by", "{}"), ("reduce_by", "false"),
    ("add_group_by", "status"), ("time_shift", "{}"), ("grain", "[]"),
])
def test_malformed_multi_stage_defaults_are_refused(key, value):
    text = _single_cube().replace("type: sum}", f"type: sum, {key}: {value} }}")
    with pytest.raises(ConversionError, match=f"malformed {key}"):
        _project(text)


@pytest.mark.parametrize(("edit", "message"), [
    # Unknown or ambiguous members.
    (("- status\n          - average_value", "- missing"),
     r"member 'orders\.missing' does not exist"),
    (("includes:\n          - status\n          - average_value",
      "includes: '*'\n        excludes: [average_vlaue]"),
     r"excluded member 'orders\.average_vlaue' does not exist"),
    (("alias: name", "alias: STATUS"), "case-insensitive"),
    (("- status\n", "- orders.status\n"), "is a path"),
    (("prefix: true", "prefix: 'yes'"), "prefix.*true or false"),
    (("includes:\n          - status\n          - average_value", "includes: all"),
     "must be '\\*' or a list"),
    # Graph shape.
    (("join_path: orders.users", "join_path: users"), "multiple join roots"),
    (("joins:\n      - name: users", "joins_disabled:\n      - name: users"),
     "declares no join to 'users'"),
    (("join_path: orders.users", "join_path: orders.nobody"),
     "references unknown cube 'nobody'"),
    (("join_path: orders.users", "join_path: orders.users.orders"),
     "visits a cube twice"),
    (("- join_path: orders\n", "- join_path: orders\n        split: true\n"),
     "split view projections"),
])
def test_view_shape_refusals(edit, message):
    old, new = edit
    text = _MODEL.replace(old, new)
    if "STATUS" in new:
        text = text.replace("prefix: true", "prefix: false")
    with pytest.raises(ConversionError, match=message):
        _project(text)


@pytest.mark.parametrize("yaml_value", ["''", "0", "false", "null"])
@pytest.mark.parametrize("target", ["alias: customer", "alias: name"])
def test_falsy_or_non_string_aliases_are_refused(yaml_value, target):
    with pytest.raises(ConversionError, match="alias.*non-empty string"):
        _project(_MODEL.replace(target, f"alias: {yaml_value}"))


def test_selected_members_without_an_ossie_form_are_refused():
    geo = _MODEL.replace(
        "- name: status\n        sql: status\n        type: string",
        "- name: status\n        type: geo\n        latitude: { sql: lat }\n"
        "        longitude: { sql: lon }")
    with pytest.raises(ConversionError, match="uses geo"):
        _project(geo)
    segment = _MODEL.replace(
        "    measures:\n      - name: count",
        "    segments:\n      - name: active\n        sql: active = true\n"
        "    measures:\n      - name: count",
    ).replace("- status\n          - average_value", "- active")
    with pytest.raises(ConversionError, match="selected segment"):
        _project(segment)
    # A wildcard reaches the segment too, as it does in Cube.
    with pytest.raises(ConversionError, match="selected segment"):
        _project(segment.replace("includes:\n          - active", "includes: '*'"))


def test_a_measure_without_a_static_form_is_refused_with_the_reason():
    text = _single_cube(measures='      - {name: nested, sql: "{revenue}", type: sum}\n',
                        includes="[nested]")
    with pytest.raises(ConversionError, match="no static Ossie expression.*nested "
                                              "aggregate"):
        _project(text)


@pytest.mark.parametrize(("sql", "message"), [
    ("{users.nmae}", "does not match a dimension or measure in cube 'users'"),
    ("{usres.name}", "unknown cube qualifier 'usres'"),
    ("{revene}", r"reference '\{revene\}'.*does not match"),
    ("{SECURITY_CONTEXT.tenant}", "unknown cube qualifier 'SECURITY_CONTEXT'"),
    ("{CUBE}", "names a cube, not a column"),
    ("amount IN (SELECT id FROM t)", "cannot be proven statically"),
    ("AGGREGATE(xs, 0, (acc, x) -> acc + x)", "cannot be proven statically"),
    ("SUM(", "cannot be proven statically"),
])
def test_unprovable_measure_sql_is_refused(sql, message):
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: users, sql: "{CUBE}.user_id = {users}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - {name: revenue, sql: amount, type: sum}
      - {name: misspelled, sql: "SQL", type: max}
  - name: users
    sql_table: main.sales.users
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: name, sql: name, type: string}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [misspelled]}
""".replace("SQL", sql.replace('"', '\\"'))
    with pytest.raises(ConversionError, match=message):
        _project(text)


@pytest.mark.parametrize("sql", ["{users.name}", "{users}.name", "UPPER({inner})"])
def test_a_published_dimension_cannot_read_another_dataset(sql):
    text = f"""
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {{name: users, sql: "{{CUBE}}.user_id = {{users}}.id", relationship: many_to_one}}
    dimensions:
      - {{name: id, sql: id, type: number, primary_key: true}}
      - {{name: inner, sql: "{{users.name}}", type: string}}
      - {{name: user_name, sql: "{sql}", type: string}}
  - name: users
    sql_table: main.sales.users
    dimensions:
      - {{name: id, sql: id, type: number, primary_key: true}}
      - {{name: name, sql: name, type: string}}
views:
  - name: sales
    cubes:
      - {{join_path: orders, includes: [user_name]}}
      - {{join_path: orders.users, includes: []}}
"""
    with pytest.raises(ConversionError, match="dataset-scoped Ossie field"):
        _project(text)


def test_a_dimension_reading_a_measure_is_refused():
    text = _single_cube(dimensions='      - {name: big, sql: "{revenue} > 5", type: boolean}\n',
                        includes="[big]")
    with pytest.raises(ConversionError, match="references measure 'orders.revenue'"):
        _project(text)


def test_a_dimension_reference_cycle_is_refused():
    text = _single_cube(dimensions='      - {name: a, sql: "{b}", type: number}\n'
                                   '      - {name: b, sql: "{a}", type: number}\n',
                        includes="[a]")
    with pytest.raises(ConversionError, match="dimension reference cycle"):
        _project(text)


def test_a_referenced_geo_dimension_is_refused():
    text = _single_cube(
        dimensions="      - {name: place, type: geo, latitude: {sql: lat}, "
                   "longitude: {sql: lon} }\n"
                   '      - {name: where, sql: "{place}", type: string}\n',
        includes="[where]")
    with pytest.raises(ConversionError, match="uses geo.*where it is referenced"):
        _project(text)


def test_a_needed_join_with_no_relationship_form_is_refused():
    text = _MODEL.replace('sql: "{CUBE}.user_id = {users}.id"',
                          'sql: "{CUBE}.user_id > {users}.id"')
    with pytest.raises(ConversionError, match="orders' -> 'users'.*no Ossie relationship"):
        _project(text)


_AMBIGUOUS = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    joins:
      - {name: users, sql: "{CUBE}.user_id = {users}.id", relationship: many_to_one}
      - {name: accounts, sql: "{CUBE}.account_id = {accounts}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - {name: value, sql: "REF", type: KIND}
  - name: users
    sql_table: main.sales.users
    joins:
      - {name: accounts, sql: "{CUBE}.account_id = {accounts}.id", relationship: many_to_one}
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
  - name: accounts
    sql_table: main.sales.accounts
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
      - {name: adjusted_value, sql: value * 2, type: number}
    measures:
      - {name: max_value, sql: value, type: max}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [value]}
"""


@pytest.mark.parametrize(("reference", "kind"), [
    ("{accounts.adjusted_value}", "max"), ("{accounts.max_value}", "number")])
def test_implicit_dependencies_reject_ambiguous_join_paths(reference, kind):
    base = _AMBIGUOUS.replace("REF", reference).replace("type: KIND", f"type: {kind}")
    with pytest.raises(ConversionError, match="multiple declared join paths"):
        _project(base)
    # Naming the path in the view settles it.
    text = base.replace(
        "      - {join_path: orders, includes: [value]}\n",
        "      - {join_path: orders, includes: [value]}\n"
        "      - {join_path: orders.accounts, includes: []}\n")
    out, _, _ = _project(text)
    assert [(r["from"], r["to"]) for r in model_of(out)["relationships"]] == [
        ("orders", "accounts")]


def test_a_dependency_with_no_declared_path_is_refused():
    text = """
cubes:
  - name: orders
    sql_table: main.sales.orders
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
    measures:
      - {name: odd, sql: "{users}.x", type: max}
  - name: users
    sql_table: main.sales.users
    dimensions:
      - {name: id, sql: id, type: number, primary_key: true}
views:
  - name: sales
    cubes:
      - {join_path: orders, includes: [odd]}
"""
    with pytest.raises(ConversionError, match="no declared join path leads to it"):
        _project(text)


def test_unreachable_dependency_path_search_is_bounded():
    connected = [f"c{i}" for i in range(12)]
    cubes = "\n".join(
        f"  - name: {name}\n    sql_table: s.t.{name}\n"
        f"    dimensions: [{{name: id, sql: id, type: number, primary_key: true}}]\n"
        f"    joins:\n" + "".join(
            f"      - {{name: {other}, sql: \"{{CUBE}}.id = {{{other}}}.id\", "
            f"relationship: one_to_one}}\n"
            for other in connected if other != name)
        for name in connected)
    text = (f"cubes:\n{cubes}\n  - name: island\n    sql_table: s.t.island\n"
            "views:\n  - name: sales\n    cubes:\n"
            "      - join_path: c0\n        includes: [far]\n")
    text = text.replace(
        "  - name: c0\n    sql_table: s.t.c0\n",
        "  - name: c0\n    sql_table: s.t.c0\n"
        '    measures: [{name: far, sql: "{island}.x", type: max}]\n', 1)
    with pytest.raises(ConversionError, match="no declared join path"):
        convert_cube_view_to_ossie({"model.yml": text}, "sales")


def test_input_errors_are_refused():
    with pytest.raises(ConversionError, match="non-empty mapping"):
        convert_cube_view_to_ossie({}, "sales")
    with pytest.raises(ConversionError, match="name of the view"):
        convert_cube_view_to_ossie({"m.yml": _MODEL}, "")
    with pytest.raises(ConversionError, match="view 'nope' not found"):
        convert_cube_view_to_ossie({"m.yml": _MODEL}, "nope")
    with pytest.raises(ConversionError, match="no cubes"):
        convert_cube_view_to_ossie(
            {"m.yml": "views:\n  - {name: sales, cubes: [{join_path: orders}]}\n"},
            "sales")
    with pytest.raises(ConversionError, match="no `cubes` entries"):
        convert_cube_view_to_ossie(
            {"m.yml": _MODEL.replace("    cubes:\n      - join_path: orders\n",
                                     "    includes: [orders.status]\n    x:\n"
                                     "      - join_path: orders\n")}, "sales")
    with pytest.raises(ConversionError, match="publishes no dimensions or measures"):
        _project(_single_cube(includes="[]"))


def test_the_round_trip_import_is_untouched_by_projection():
    before, _ = convert_cube_to_ossie({"model.yml": _MODEL}, view="sales")
    _project()
    after, _ = convert_cube_to_ossie({"model.yml": _MODEL}, view="sales")
    assert before == after


# --- properties -------------------------------------------------------------------

def _random_view(rnd, files):
    """A random view over a generated model, and the surface it should publish.

    Every cube entry is prefixed with a distinct alias, so the surface is collision-free
    by construction; collisions have their own targeted tests. `geo` dimensions are
    always excluded, since they have no field form.
    """
    cubes = {}
    for name, text in files.items():
        for cube in (load_yaml(text, name) or {}).get("cubes") or []:
            cubes[cube["name"]] = cube
    fact = "fact"
    entries, surface = [], set()
    for index, cname in enumerate([fact] + sorted(c for c in cubes if c != fact)):
        cube = cubes[cname]
        members = [m["name"] for key in ("dimensions", "measures")
                   for m in cube.get(key) or [] if m.get("type") != "geo"]
        geo = [m["name"] for m in cube.get("dimensions") or [] if m.get("type") == "geo"]
        entry = {"join_path": cname if cname == fact else f"{fact}.{cname}",
                 "prefix": True, "alias": f"a{index}"}
        if rnd.chance(0.4):
            excluded = [m for m in members if rnd.chance(0.3)]
            entry["includes"] = "*"
            entry["excludes"] = excluded + geo
            chosen = [(m, m) for m in members if m not in excluded]
        else:
            chosen = []
            for m in members:
                if rnd.chance(0.5):
                    alias = f"{m}_as" if rnd.chance(0.3) else m
                    chosen.append((m, alias))
            entry["includes"] = [m if m == a else {"name": m, "alias": a}
                                 for m, a in chosen]
        entries.append(entry)
        surface |= {f"a{index}_{alias}" for _, alias in chosen}
    return {"name": "published", "cubes": entries}, surface


def _generated(seed):
    rnd = RandomRnd(seed)
    files = build_cube_model(rnd)
    view, surface = _random_view(rnd, files)
    # The generator's own view includes every cube unprefixed, which Cube refuses for
    # colliding names; the projection needs only the random one.
    del files["model/views/main.yml"]
    files["model/views/published.yml"] = dump_yaml({"views": [view]})
    return files, surface


def _check_projection(seed):
    files, surface = _generated(seed)
    if not surface:
        with pytest.raises(ConversionError, match="publishes no"):
            convert_cube_view_to_ossie(files, "published")
        return None
    out, source, _ = convert_cube_view_to_ossie(files, "published")
    assert source == "fact"
    assert_ossie_is_valid(out, f"projection (seed {seed})")
    model = model_of(out)
    fields = {f["name"] for ds in model["datasets"] for f in ds.get("fields") or []}
    metrics = {m["name"] for m in model.get("metrics") or []}
    # Exactly the surface: nothing hidden published, nothing published missing.
    assert fields | metrics == surface
    assert not fields & metrics
    for metric in model.get("metrics") or []:
        text = expr_of(metric)
        # Every reference inlined: no Cube reference, and no bare metric name.
        assert "{" not in text
        assert not (set(text.replace("(", " ").replace(")", " ").split())
                    & {m.split("_", 1)[1] for m in metrics})
    files_back, _ = convert_ossie_to_cube(out)
    return files_back


@pytest.mark.parametrize("seed", range(80))
def test_seeded_projections_publish_exactly_the_view_surface(seed):
    _check_projection(seed)


@cube_gate
@pytest.mark.parametrize("seed", range(10))
def test_seeded_projections_export_to_a_model_cube_compiles(seed):
    try:
        assert_cube_compiles(_generated(seed)[0], f"generated input (seed {seed})")
    except AssertionError:
        # Random free text can be something Cube itself refuses -- a title of just
        # `F` fails its f-string parser -- and then the input is not a Cube model.
        pytest.skip("Cube refuses the generated input itself")
    files = _check_projection(seed)
    if files is not None:
        assert_cube_compiles(files, f"projection exported back (seed {seed})")


_COMPILE_CASES = {
    "reference model": (_MODEL, {}),
    "fan-out relaxed": (_FANOUT, {"strict_fanout": False}),
    "re-rooted": (_MODEL.replace("          - status\n", ""), {"source": "users"}),
    "fan-out through a chain, relaxed": (_CHAIN, {"strict_fanout": False}),
}


@cube_gate
@pytest.mark.parametrize("case", sorted(_COMPILE_CASES))
def test_projected_models_export_to_a_model_cube_compiles(case):
    text, kwargs = _COMPILE_CASES[case]
    out, source, _ = _project(text, **kwargs)
    files, _ = convert_ossie_to_cube(out, base_cube=source)
    assert_cube_compiles(files, case)
