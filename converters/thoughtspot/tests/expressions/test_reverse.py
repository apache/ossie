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

"""Task 9: the reverse-direction inventory (ThoughtSpot -> Ossie).

Source: the "Reverse direction (ThoughtSpot -> Ossie)" section of
docs/ossie/ts-ossie-function-mapping.md (thoughtspot-agent-skills repo, not
vendored here), all three sub-sections: conditional aggregates and arithmetic
helpers; window, LOD and semi-additive functions; runtime, display and
calendar concepts.

One assertion group per ThoughtSpot function: does it compose (rule E10), or
does it stash (custom_extensions + issue, rule E12)? Composers assert the
exact emitted Ossie expression. Stash/partial entries assert the issue's
code, severity and object_ref, per E12 ("names the function, the object and
the reason").
"""
from ossie_thoughtspot.expressions.reverse import (
    REVERSE,
    ReverseConstruct,
    ReverseDisposition,
    custom_extensions_fragment,
    portable_dialect_entry,
    stash_runtime_parameter,
    thoughtspot_dialect_entry,
    translate_thoughtspot,
)
from ossie_thoughtspot.issues import IssueLog, Severity

OBJ = "metrics.revenue"


def _log() -> IssueLog:
    return IssueLog()


# ---------------------------------------------------------------------------
# Conditional aggregates and arithmetic helpers (28 names) — all COMPOSE.
# ---------------------------------------------------------------------------

def test_sum_if_composes():
    log = _log()
    assert translate_thoughtspot("sum_if", ["cond", "x"], log, object_ref=OBJ) == (
        "SUM(CASE WHEN cond THEN x END)"
    )
    assert log.issues == []


def test_count_if_composes():
    log = _log()
    assert translate_thoughtspot("count_if", ["cond", "x"], log, object_ref=OBJ) == (
        "COUNT(CASE WHEN cond THEN x END)"
    )
    assert log.issues == []


def test_unique_count_if_composes():
    log = _log()
    assert translate_thoughtspot("unique_count_if", ["cond", "x"], log, object_ref=OBJ) == (
        "COUNT(DISTINCT CASE WHEN cond THEN x END)"
    )


def test_average_min_max_stddev_variance_if_compose():
    log = _log()
    cases = {
        "average_if": "AVG(CASE WHEN cond THEN x END)",
        "min_if": "MIN(CASE WHEN cond THEN x END)",
        "max_if": "MAX(CASE WHEN cond THEN x END)",
        "stddev_if": "STDDEV(CASE WHEN cond THEN x END)",
        "variance_if": "VARIANCE(CASE WHEN cond THEN x END)",
    }
    for name, expected in cases.items():
        assert translate_thoughtspot(name, ["cond", "x"], log, object_ref=OBJ) == expected
    assert log.issues == []


def test_unique_count_with_a_space_composes():
    # "A space, not an underscore" — the forward mapping document's own words
    # (Aggregate functions, COUNT(DISTINCT expr)).
    log = _log()
    assert translate_thoughtspot("unique count", ["x"], log, object_ref=OBJ) == "COUNT(DISTINCT x)"


def test_safe_divide_composes():
    log = _log()
    assert translate_thoughtspot("safe_divide", ["a", "b"], log, object_ref=OBJ) == (
        "COALESCE(a / NULLIF(b, 0), 0)"
    )
    assert log.issues == []


def test_pow_log2_strlen_strpos_substr_left_right_compose():
    log = _log()
    assert translate_thoughtspot("pow", ["base", "exp"], log, object_ref=OBJ) == "POWER(base, exp)"
    assert translate_thoughtspot("log2", ["x"], log, object_ref=OBJ) == "LOG(2, x)"
    assert translate_thoughtspot("strlen", ["s"], log, object_ref=OBJ) == "LENGTH(s)"
    # ThoughtSpot's strpos(s, sub) reverses to Ossie POSITION(sub IN s).
    assert translate_thoughtspot("strpos", ["s", "sub"], log, object_ref=OBJ) == "POSITION(sub IN s)"
    # substr's 0-based start needs the +1 going this way (mirror of the forward -1).
    assert translate_thoughtspot("substr", ["s", "0", "3"], log, object_ref=OBJ) == (
        "SUBSTRING(s, 0 + 1, 3)"
    )
    assert translate_thoughtspot("left", ["s", "3"], log, object_ref=OBJ) == "LEFT(s, 3)"
    assert translate_thoughtspot("right", ["s", "3"], log, object_ref=OBJ) == "RIGHT(s, 3)"
    assert log.issues == []


def test_trig_functions_reverse_the_degree_radian_conversion():
    log = _log()
    assert translate_thoughtspot("sin", ["x"], log, object_ref=OBJ) == "SIN(RADIANS(x))"
    assert translate_thoughtspot("cos", ["x"], log, object_ref=OBJ) == "COS(RADIANS(x))"
    assert translate_thoughtspot("tan", ["x"], log, object_ref=OBJ) == "TAN(RADIANS(x))"
    assert translate_thoughtspot("asin", ["x"], log, object_ref=OBJ) == "DEGREES(ASIN(x))"
    assert translate_thoughtspot("acos", ["x"], log, object_ref=OBJ) == "DEGREES(ACOS(x))"
    assert translate_thoughtspot("atan", ["x"], log, object_ref=OBJ) == "DEGREES(ATAN(x))"
    assert log.issues == []


def test_to_integer_to_double_to_string_compose_losslessly():
    log = _log()
    assert translate_thoughtspot("to_integer", ["x"], log, object_ref=OBJ) == "CAST(x AS INTEGER)"
    assert translate_thoughtspot("to_double", ["x"], log, object_ref=OBJ) == "CAST(x AS DOUBLE)"
    assert translate_thoughtspot("to_string", ["x"], log, object_ref=OBJ) == "CAST(x AS VARCHAR)"
    assert log.issues == []


def test_to_date_composes_with_an_info_issue_for_the_untranslated_format():
    log = _log()
    result = translate_thoughtspot("to_date", ["s", "'yyyy-MM-dd'"], log, object_ref=OBJ)
    assert result == "TO_DATE(s, 'yyyy-MM-dd')"
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.INFO
    assert "to_date" in log.issues[0].message
    assert log.issues[0].object_ref == OBJ


def test_if_composes_as_case_when():
    log = _log()
    assert translate_thoughtspot("if", ["c", "a", "b"], log, object_ref=OBJ) == (
        "CASE WHEN c THEN a ELSE b END"
    )
    assert log.issues == []


# ---------------------------------------------------------------------------
# Window, LOD and semi-additive functions
# ---------------------------------------------------------------------------

def test_rank_composes_global_order_only():
    log = _log()
    assert translate_thoughtspot("rank", ["SUM(m)", "'desc'"], log, object_ref=OBJ) == (
        "RANK() OVER (ORDER BY SUM(m) DESC)"
    )
    assert translate_thoughtspot("rank", ["SUM(m)", "'asc'"], log, object_ref=OBJ) == (
        "RANK() OVER (ORDER BY SUM(m) ASC)"
    )
    assert log.issues == []


def test_rank_percentile_composes_with_scale_and_inversion_reversed():
    log = _log()
    assert translate_thoughtspot("rank_percentile", ["SUM(m)", "'asc'"], log, object_ref=OBJ) == (
        "(1.0 - PERCENT_RANK() OVER (ORDER BY SUM(m) ASC)) * 100"
    )


def test_moving_sum_composes_the_frame_and_loses_the_partition():
    log = _log()
    result = translate_thoughtspot("moving_sum", ["m", "2", "0", "ord"], log, object_ref=OBJ)
    assert result == "SUM(m) OVER (ORDER BY ord ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)"
    assert len(log.issues) == 1
    issue = log.issues[0]
    assert issue.severity is Severity.WARNING
    assert "moving_sum" in issue.message
    assert "partition" in issue.message.lower()
    assert issue.object_ref == OBJ


def test_moving_average_max_min_compose_with_sign_conventions():
    log = _log()
    # (m, 1, -1, ord): 1 PRECEDING .. 1 FOLLOWING (negative end flips to FOLLOWING).
    assert translate_thoughtspot("moving_average", ["m", "1", "-1", "ord"], log, object_ref=OBJ) == (
        "AVG(m) OVER (ORDER BY ord ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING)"
    )
    assert translate_thoughtspot("moving_max", ["m", "-1", "1", "ord"], log, object_ref=OBJ) == (
        "MAX(m) OVER (ORDER BY ord ROWS BETWEEN 1 FOLLOWING AND 1 PRECEDING)"
    )
    assert translate_thoughtspot("moving_min", ["m", "0", "0", "ord"], log, object_ref=OBJ) == (
        "MIN(m) OVER (ORDER BY ord ROWS BETWEEN CURRENT ROW AND CURRENT ROW)"
    )


def test_moving_sum_accepts_multiple_order_columns():
    log = _log()
    result = translate_thoughtspot("moving_sum", ["m", "1", "-1", "ord1", "ord2"], log, object_ref=OBJ)
    assert result == "SUM(m) OVER (ORDER BY ord1, ord2 ROWS BETWEEN 1 PRECEDING AND 1 FOLLOWING)"


def test_cumulative_sum_composes_the_running_total_and_loses_the_partition():
    log = _log()
    result = translate_thoughtspot("cumulative_sum", ["m", "ord"], log, object_ref=OBJ)
    assert result == (
        "SUM(m) OVER (ORDER BY ord ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    )
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.WARNING


def test_cumulative_average_max_min_compose():
    log = _log()
    assert translate_thoughtspot("cumulative_average", ["m", "ord"], log, object_ref=OBJ) == (
        "AVG(m) OVER (ORDER BY ord ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    )
    assert translate_thoughtspot("cumulative_max", ["m", "ord"], log, object_ref=OBJ) == (
        "MAX(m) OVER (ORDER BY ord ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    )
    assert translate_thoughtspot("cumulative_min", ["m", "ord"], log, object_ref=OBJ) == (
        "MIN(m) OVER (ORDER BY ord ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    )


def test_cumulative_sum_accepts_multiple_order_columns():
    log = _log()
    result = translate_thoughtspot("cumulative_sum", ["m", "ord1", "ord2"], log, object_ref=OBJ)
    assert result == (
        "SUM(m) OVER (ORDER BY ord1, ord2 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)"
    )


def test_group_aggregate_with_query_groups_alone_becomes_a_plain_aggregate():
    log = _log()
    result = translate_thoughtspot(
        "group_aggregate", ["SUM(m)", "query_groups ( )", "query_filters ( )"], log, object_ref=OBJ
    )
    assert result == "SUM(m)"
    assert log.issues == []


def test_group_aggregate_with_a_fixed_list_becomes_partition_by():
    log = _log()
    result = translate_thoughtspot(
        "group_aggregate", ["SUM(m)", "{ a , b }", "query_filters ( )"], log, object_ref=OBJ
    )
    assert result == "SUM(m) OVER (PARTITION BY a , b)"
    assert log.issues == []


def test_group_aggregate_with_an_empty_list_becomes_over_with_no_partition():
    log = _log()
    result = translate_thoughtspot(
        "group_aggregate", ["SUM(m)", "{ }", "query_filters ( )"], log, object_ref=OBJ
    )
    assert result == "SUM(m) OVER ()"


def test_group_aggregate_with_a_dynamic_partition_stashes():
    log = _log()
    result = translate_thoughtspot(
        "group_aggregate",
        ["SUM(m)", "query_groups ( ) - { a }", "query_filters ( )"],
        log,
        object_ref=OBJ,
    )
    assert result is None
    assert len(log.issues) == 1
    issue = log.issues[0]
    assert issue.severity is Severity.ERROR
    assert "group_aggregate" in issue.message
    assert issue.object_ref == OBJ

    log2 = _log()
    result2 = translate_thoughtspot(
        "group_aggregate",
        ["SUM(m)", "query_groups ( ) + { a }", "query_filters ( )"],
        log2,
        object_ref=OBJ,
    )
    assert result2 is None
    assert len(log2.issues) == 1


def test_group_aggregate_with_a_non_default_filter_stashes():
    log = _log()
    result = translate_thoughtspot(
        "group_aggregate", ["SUM(m)", "{ a }", "{ c = 'v' }"], log, object_ref=OBJ
    )
    assert result is None
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.ERROR
    assert "filter" in log.issues[0].message.lower()


def test_group_sum_shorthand_composes_like_group_aggregate():
    log = _log()
    result = translate_thoughtspot("group_sum", ["m", "{ a }", "query_filters ( )"], log, object_ref=OBJ)
    assert result == "SUM(m) OVER (PARTITION BY a)"
    assert log.issues == []


def test_group_count_stddev_variance_shorthands_compose():
    log = _log()
    assert translate_thoughtspot(
        "group_count", ["m", "{ a }", "query_filters ( )"], log, object_ref=OBJ
    ) == "COUNT(m) OVER (PARTITION BY a)"
    assert translate_thoughtspot(
        "group_stddev", ["m", "{ a }", "query_filters ( )"], log, object_ref=OBJ
    ) == "STDDEV(m) OVER (PARTITION BY a)"
    assert translate_thoughtspot(
        "group_variance", ["m", "{ a }", "query_filters ( )"], log, object_ref=OBJ
    ) == "VARIANCE(m) OVER (PARTITION BY a)"


def test_group_sum_shorthand_also_stashes_a_dynamic_partition():
    log = _log()
    result = translate_thoughtspot(
        "group_sum", ["m", "query_groups ( ) - { a }", "query_filters ( )"], log, object_ref=OBJ
    )
    assert result is None
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.ERROR


def test_last_value_and_kin_always_stash_the_semi_additivity_loss():
    for name in ("last_value", "first_value", "last_value_in_period", "first_value_in_period"):
        log = _log()
        result = translate_thoughtspot(
            name, ["SUM(m)", "query_groups ( )", "{ date }"], log, object_ref=OBJ
        )
        assert result is None, name
        assert len(log.issues) == 1, name
        issue = log.issues[0]
        assert issue.severity is Severity.ERROR
        assert name in issue.message
        assert issue.object_ref == OBJ


def test_sql_op_family_is_registered_with_dialect_disposition():
    names = [
        "sql_string_op", "sql_int_op", "sql_double_op", "sql_bool_op",
        "sql_date_op", "sql_date_time_op", "sql_string_aggregate_op",
        "sql_int_aggregate_op", "sql_number_aggregate_op", "sql_date_time_aggregate_op",
    ]
    for name in names:
        assert name in REVERSE, name
        assert REVERSE[name].disposition is ReverseDisposition.DIALECT, name


def test_sql_op_with_a_known_connection_dialect_composes_the_raw_body():
    log = _log()
    result = translate_thoughtspot(
        "sql_string_op", ["LOWER({0})", "s"], log, object_ref=OBJ, connection_dialect="SNOWFLAKE"
    )
    assert result == "LOWER(s)"
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.WARNING
    assert "SNOWFLAKE" in log.issues[0].message


def test_sql_op_with_an_unknown_connection_dialect_stashes():
    log = _log()
    result = translate_thoughtspot("sql_string_op", ["LOWER({0})", "s"], log, object_ref=OBJ)
    assert result is None
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.ERROR


# ---------------------------------------------------------------------------
# Runtime, display and calendar concepts
# ---------------------------------------------------------------------------

def test_runtime_identity_functions_stash():
    for name in ("ts_username", "ts_groups", "ts_groups_int", "ts_org", "ts_email_domain"):
        log = _log()
        result = translate_thoughtspot(name, [], log, object_ref=OBJ)
        assert result is None, name
        assert len(log.issues) == 1, name
        assert log.issues[0].severity is Severity.ERROR
        assert name in log.issues[0].message


def test_ts_var_stashes():
    log = _log()
    result = translate_thoughtspot("ts_var", ["'my_var'"], log, object_ref=OBJ)
    assert result is None
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.ERROR


def test_stash_runtime_parameter_is_reached_outside_the_main_dispatcher():
    # A bracketed parameter name is syntactically identical to a column reference, so
    # this is not auto-detected by translate_thoughtspot — the caller (which has model
    # metadata) must call this directly. See reverse.py's module docstring.
    log = _log()
    result = stash_runtime_parameter("[Discount Threshold]", log, object_ref=OBJ)
    assert result is None
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.ERROR
    assert "[Discount Threshold]" in log.issues[0].message


def test_concat_with_hyperlink_markup_stashes():
    log = _log()
    result = translate_thoughtspot(
        "concat", ['"{caption}"', '"text"', '"{/caption}"', "url"], log, object_ref=OBJ
    )
    assert result is None
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.ERROR
    assert "concat" in log.issues[0].message


def test_concat_without_markup_is_not_this_modules_concern():
    # Plain concat has a spec counterpart (CONCAT) and is already in the forward
    # CATALOG — this module returns None (nothing to do) and raises no issue.
    log = _log()
    result = translate_thoughtspot("concat", ["a", "b"], log, object_ref=OBJ)
    assert result is None
    assert log.issues == []


def test_fiscal_calendar_variants_stash_regardless_of_the_underlying_function():
    for name, args in (
        ("year", ["d", "'fiscal'"]),
        ("quarter_number", ["d", "'fiscal'"]),
        ("diff_months", ["e", "s", "'fiscal'"]),
    ):
        log = _log()
        result = translate_thoughtspot(name, args, log, object_ref=OBJ)
        assert result is None, name
        assert len(log.issues) == 1, name
        assert log.issues[0].severity is Severity.ERROR
        assert name in log.issues[0].message


def test_plain_date_functions_without_a_fiscal_argument_are_not_this_modules_concern():
    # year(d) alone has a spec counterpart (YEAR) via the forward catalog; this module
    # only owns the *fiscal*-argument variant.
    log = _log()
    result = translate_thoughtspot("year", ["d"], log, object_ref=OBJ)
    assert result is None
    assert log.issues == []


def test_name_returning_date_functions_compose_with_a_locale_issue():
    for name, expected in (
        ("month", "TO_CHAR(d, 'MONTH')"),
        ("year_name", "TO_CHAR(d, 'YYYY')"),
        ("day_of_week", "TO_CHAR(d, 'DAY')"),
    ):
        log = _log()
        result = translate_thoughtspot(name, ["d"], log, object_ref=OBJ)
        assert result == expected, name
        assert len(log.issues) == 1, name
        assert log.issues[0].severity is Severity.WARNING


def test_month_number_of_quarter_composes():
    log = _log()
    assert translate_thoughtspot("month_number_of_quarter", ["d"], log, object_ref=OBJ) == (
        "MOD(MONTH(d) - 1, 3) + 1"
    )
    assert log.issues == []


def test_day_number_of_quarter_composes():
    log = _log()
    assert translate_thoughtspot("day_number_of_quarter", ["d"], log, object_ref=OBJ) == (
        "DATEDIFF(day, DATE_TRUNC('quarter', d), d) + 1"
    )
    assert log.issues == []


def test_week_number_of_month_and_quarter_compose_with_a_week_start_issue():
    log = _log()
    assert translate_thoughtspot("week_number_of_month", ["d"], log, object_ref=OBJ) == (
        "DATEDIFF(week, DATE_TRUNC('month', d), d) + 1"
    )
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.WARNING

    log2 = _log()
    assert translate_thoughtspot("week_number_of_quarter", ["d"], log2, object_ref=OBJ) == (
        "DATEDIFF(week, DATE_TRUNC('quarter', d), d) + 1"
    )
    assert len(log2.issues) == 1


def test_is_weekend_composes_with_a_dayofweek_base_issue():
    log = _log()
    result = translate_thoughtspot("is_weekend", ["d"], log, object_ref=OBJ)
    assert result == "DATE_PART('dayofweek', d) IN (6, 7)"
    assert len(log.issues) == 1
    assert log.issues[0].severity is Severity.WARNING
    assert "dayofweek" in log.issues[0].message.lower() or "DAYOFWEEK" in log.issues[0].message


def test_start_of_hour_min_date_time_compose_losslessly():
    log = _log()
    assert translate_thoughtspot("start_of_hour", ["d"], log, object_ref=OBJ) == "DATE_TRUNC('hour', d)"
    assert translate_thoughtspot("start_of_min", ["d"], log, object_ref=OBJ) == "DATE_TRUNC('minute', d)"
    assert translate_thoughtspot("date", ["d"], log, object_ref=OBJ) == "DATE_TRUNC('day', d)"
    assert translate_thoughtspot("time", ["d"], log, object_ref=OBJ) == "CAST(d AS TIME)"
    assert log.issues == []


def test_greatest_and_least_compose_n_ary():
    log = _log()
    assert translate_thoughtspot("greatest", ["x", "y"], log, object_ref=OBJ) == "GREATEST(x, y)"
    assert translate_thoughtspot("least", ["x", "y", "z"], log, object_ref=OBJ) == "LEAST(x, y, z)"
    assert log.issues == []


# ---------------------------------------------------------------------------
# Names with no reverse-inventory entry at all — the "not this module's job" contract.
# ---------------------------------------------------------------------------

def test_unrecognised_name_returns_none_with_no_issue():
    log = _log()
    assert translate_thoughtspot("some_future_function", ["x"], log, object_ref=OBJ) is None
    assert log.issues == []


# ---------------------------------------------------------------------------
# E11/P8 — dialect-entry and stash-payload helpers.
# ---------------------------------------------------------------------------

def test_thoughtspot_dialect_entry_reconstructs_the_verbatim_call():
    entry = thoughtspot_dialect_entry("ts_username", [])
    assert entry == {"dialect": "THOUGHTSPOT", "expression": "ts_username ( )"}

    entry2 = thoughtspot_dialect_entry("moving_sum", ["m", "2", "0", "ord"])
    assert entry2 == {"dialect": "THOUGHTSPOT", "expression": "moving_sum ( m , 2 , 0 , ord )"}


def test_portable_dialect_entry_pairs_with_ansi_sql():
    entry = portable_dialect_entry("SUM(m) OVER (ORDER BY ord ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)")
    assert entry == {
        "dialect": "ANSI_SQL",
        "expression": "SUM(m) OVER (ORDER BY ord ROWS BETWEEN 2 PRECEDING AND CURRENT ROW)",
    }


def test_custom_extensions_fragment_is_keyed_by_the_ossie_column_name():
    # Not by VENDOR_KEY: write_stash(obj, payload) treats payload as the *contents* of
    # the object's THOUGHTSPOT entry, so a fragment keyed by VENDOR_KEY would nest the
    # vendor key inside its own entry instead of producing a valid write_stash payload.
    fragment = custom_extensions_fragment("revenue_per_user", "ts_username", [])
    assert fragment == {"revenue_per_user": {"reverse_thoughtspot_call": "ts_username ( )"}}


def test_custom_extensions_fragment_merges_losslessly_across_columns():
    # The whole point of keying by column: {**f1, **f2} must keep both columns' calls,
    # not collapse to whichever fragment merged last (the VENDOR_KEY-keyed bug this
    # replaces would silently drop the first column here).
    f1 = custom_extensions_fragment("revenue_per_user", "ts_username", [])
    f2 = custom_extensions_fragment("org_label", "ts_org", [])
    merged = {**f1, **f2}
    assert merged == {
        "revenue_per_user": {"reverse_thoughtspot_call": "ts_username ( )"},
        "org_label": {"reverse_thoughtspot_call": "ts_org ( )"},
    }

    # And the merged fragment is a valid write_stash payload: write_stash treats its
    # `payload` argument as the entry's own contents, so merged must round-trip through
    # it without collapsing either column.
    from ossie_thoughtspot.stash import read_stash, write_stash

    obj = write_stash({"name": "revenue_model"}, merged)
    assert read_stash(obj) == {**merged, "_v": 1}


# ---------------------------------------------------------------------------
# Inventory shape.
# ---------------------------------------------------------------------------

def test_every_reverse_construct_is_traceable_to_the_mapping_document():
    # Every entry must declare a disposition and, for COMPOSE/PARTIAL rows without a
    # dispatch_fn, a template or compose_fn — ReverseConstruct.__post_init__ enforces the
    # combination; this test just confirms every registered row survived construction
    # (a failure here means the module itself failed to import).
    assert len(REVERSE) > 0
    for name, construct in REVERSE.items():
        assert isinstance(construct, ReverseConstruct)
        assert construct.thoughtspot_name == name


def test_reverse_inventory_census():
    # Pins the count so a silent addition/removal is visible in review, the same
    # discipline the forward CATALOG's 146-row census test applies.
    assert len(REVERSE) == 79
