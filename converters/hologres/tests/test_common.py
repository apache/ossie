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

"""Tests for the shared helpers: quoting, the stash protocol, and the sqlglot layer."""

import json

import pytest
from ossie_hologres._common import (
    DIALECT_ANSI,
    ConversionError,
    assert_row_level,
    column_refs,
    dump_yaml,
    foreign_vendor_extensions,
    load_yaml,
    merge_description,
    metric_aggregate,
    normalize_expression,
    parse_expression,
    pick_expression,
    qualify_columns,
    quote_identifier,
    quote_literal,
    read_stash,
    render_expression,
    require_str,
    unqualify_columns,
    write_stash,
)


class TestQuoteIdentifier:
    @pytest.mark.parametrize(
        "name",
        ["orders", "svacc_orders", "o", "_private", "a1", "region_dim"],
    )
    def test_bare_lowercase_identifiers_are_not_quoted(self, name):
        assert quote_identifier(name, "test") == name

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            # PostgreSQL folds unquoted identifiers to lower case, so anything that is
            # not already lower case must be quoted to survive the round trip.
            ("Orders", '"Orders"'),
            ("ORDERS", '"ORDERS"'),
            ("my table", '"my table"'),
            ("with-dash", '"with-dash"'),
            ("城市", '"城市"'),
            # An embedded double quote is escaped by doubling.
            ('a"b', '"a""b"'),
            # Reserved keywords are a syntax error when left bare. sqlglot's generator
            # does not quote these, which is why we do it here.
            ("select", '"select"'),
            ("table", '"table"'),
            ("group", '"group"'),
            ("user", '"user"'),
            ("order", '"order"'),
        ],
    )
    def test_identifiers_needing_quotes_are_quoted(self, name, expected):
        assert quote_identifier(name, "test") == expected

    @pytest.mark.parametrize("bad", ["", "   ", None, 42])
    def test_empty_or_non_string_is_rejected(self, bad):
        with pytest.raises(ConversionError, match="identifier"):
            quote_identifier(bad, "test")

    def test_nul_byte_is_rejected(self):
        with pytest.raises(ConversionError, match="NUL"):
            quote_identifier("a\x00b", "test")


class TestQuoteLiteral:
    def test_plain_text(self):
        assert quote_literal("total revenue", "test") == "'total revenue'"

    def test_single_quote_is_doubled(self):
        assert quote_literal("customer's city", "test") == "'customer''s city'"

    def test_backslash_is_literal(self):
        # standard_conforming_strings is on by default, so a backslash needs no escape.
        assert quote_literal("a\\b", "test") == "'a\\b'"

    def test_unicode_passes_through(self):
        assert quote_literal("客户城市", "test") == "'客户城市'"

    def test_nul_byte_is_rejected(self):
        with pytest.raises(ConversionError, match="NUL"):
            quote_literal("a\x00b", "test")

    def test_non_string_is_rejected(self):
        with pytest.raises(ConversionError, match="must be a string"):
            quote_literal(7, "test")


class TestYaml12Semantics:
    def test_bare_on_off_stay_strings(self):
        # A YAML 1.1 reader turns these into booleans, silently corrupting a dimension
        # named `no` or a description of `on`.
        loaded = load_yaml("a: on\nb: no\nc: yes\nd: y\n")
        assert loaded == {"a": "on", "b": "no", "c": "yes", "d": "y"}

    def test_real_booleans_still_parse(self):
        assert load_yaml("a: true\nb: false\n") == {"a": True, "b": False}

    def test_dump_quotes_bool_like_strings(self):
        # Force-quoted on output so a stock yaml.safe_load reader also reads a string.
        assert dump_yaml({"a": "on"}).strip() == "a: 'on'"

    def test_dump_preserves_key_order_and_unicode(self):
        text = dump_yaml({"name": "b", "description": "客户"})
        assert text == "name: b\ndescription: 客户\n"

    def test_invalid_yaml_raises_conversion_error(self):
        with pytest.raises(ConversionError, match="Invalid YAML"):
            load_yaml("a: [unclosed\n")


class TestStash:
    def test_round_trip(self):
        obj = {}
        write_stash(obj, {"owner": "o"})
        assert read_stash(obj) == {"owner": "o"}

    def test_version_marker_is_written_but_hidden_from_readers(self):
        obj = {}
        write_stash(obj, {"owner": "o"})
        assert json.loads(obj["custom_extensions"][0]["data"])["_v"] == 1
        assert "_v" not in read_stash(obj)

    def test_empty_data_is_a_no_op(self):
        # Keeps hand-authored Ossie free of empty extension blocks.
        obj = {}
        write_stash(obj, {})
        assert obj == {}

    def test_merges_into_existing_hologres_entry(self):
        obj = {}
        write_stash(obj, {"owner": "o"})
        write_stash(obj, {"view_schema": "public"})
        assert len(obj["custom_extensions"]) == 1
        assert read_stash(obj) == {"view_schema": "public"}

    def test_absent_stash_reads_as_empty(self):
        assert read_stash({}) == {}
        assert read_stash(None) == {}
        assert read_stash({"custom_extensions": [{"vendor_name": "DBT", "data": "{}"}]}) == {}

    def test_foreign_vendors_are_reported_not_read(self):
        obj = {
            "custom_extensions": [
                {"vendor_name": "HOLOGRES", "data": '{"owner": "o"}'},
                {"vendor_name": "DBT", "data": "{}"},
            ]
        }
        assert read_stash(obj) == {"owner": "o"}
        assert [e["vendor_name"] for e in foreign_vendor_extensions(obj)] == ["DBT"]

    def test_malformed_json_raises(self):
        obj = {"custom_extensions": [{"vendor_name": "HOLOGRES", "data": "{not json"}]}
        with pytest.raises(ConversionError, match="not valid JSON"):
            read_stash(obj)

    def test_non_object_json_raises(self):
        obj = {"custom_extensions": [{"vendor_name": "HOLOGRES", "data": "[1, 2]"}]}
        with pytest.raises(ConversionError, match="must be a JSON object"):
            read_stash(obj)


class TestPickExpression:
    def _expr(self, *pairs):
        return {"dialects": [{"dialect": d, "expression": e} for d, e in pairs]}

    def test_selects_the_ansi_dialect(self):
        assert pick_expression(self._expr((DIALECT_ANSI, "region"))) == "region"

    def test_selects_ansi_from_among_several_dialects(self):
        expr = self._expr(("SNOWFLAKE", "region::varchar"), (DIALECT_ANSI, "region"))
        assert pick_expression(expr) == "region"

    def test_returns_none_when_no_ansi_dialect(self):
        # This converter introduces no dialect token of its own, so anything that is not
        # ANSI_SQL is simply not usable here.
        assert pick_expression(self._expr(("MDX", "[Region]"))) is None
        assert pick_expression(self._expr(("HOLOGRES", "region::text"))) is None
        assert pick_expression({}) is None
        assert pick_expression(None) is None

    def test_non_string_expression_raises(self):
        with pytest.raises(ConversionError, match="must be a string"):
            pick_expression({"dialects": [{"dialect": DIALECT_ANSI, "expression": 7}]})


class TestMergeDescription:
    def test_string_ai_context_is_appended(self):
        assert merge_description("desc", "extra") == "desc\nextra"

    def test_string_ai_context_alone_becomes_the_description(self):
        assert merge_description(None, "extra") == "extra"

    def test_object_ai_context_is_left_for_the_caller_to_report(self):
        assert merge_description("desc", {"synonyms": ["a"]}) == "desc"

    def test_blank_ai_context_changes_nothing(self):
        assert merge_description("desc", "   ") == "desc"


class TestRequireStr:
    def test_returns_value(self):
        assert require_str({"name": "o"}, "name", "table") == "o"

    @pytest.mark.parametrize("obj", [{}, {"name": None}, {"name": "  "}])
    def test_missing_null_or_blank_raises(self, obj):
        with pytest.raises(ConversionError):
            require_str(obj, "name", "table")

    def test_non_string_raises(self):
        with pytest.raises(ConversionError, match="must be a string"):
            require_str({"name": 7}, "name", "table")


class TestExpressionLayer:
    def test_parse_failure_raises_rather_than_passing_sql_through(self):
        with pytest.raises(ConversionError, match="cannot parse expression"):
            parse_expression("sum(", "metric 'x'")

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_expression_raises(self, bad):
        with pytest.raises(ConversionError, match="non-empty string"):
            parse_expression(bad, "metric 'x'")

    def test_column_refs_reports_qualifier_and_name(self):
        node = parse_expression("o.amount + quantity", "test")
        assert sorted(column_refs(node)) == [("", "quantity"), ("o", "amount")]

    def test_qualify_adds_the_owning_alias(self):
        node = qualify_columns(
            parse_expression("upper(region) || city", "test"), "o", {"o", "c"}, "test"
        )
        assert render_expression(node) == "UPPER(o.region) || o.city"

    def test_qualify_leaves_matching_qualifier_alone(self):
        node = qualify_columns(parse_expression("o.region", "test"), "o", {"o"}, "test")
        assert render_expression(node) == "o.region"

    def test_qualify_rejects_a_reference_to_another_known_table(self):
        with pytest.raises(ConversionError, match="cannot span tables"):
            qualify_columns(parse_expression("c.city", "dim 'x'"), "o", {"o", "c"}, "dim 'x'")

    def test_qualify_rejects_an_unknown_table(self):
        with pytest.raises(ConversionError, match="unknown table 'zz'"):
            qualify_columns(parse_expression("zz.city", "dim 'x'"), "o", {"o", "c"}, "dim 'x'")

    def test_unqualify_is_the_inverse_of_qualify(self):
        original = "UPPER(region) || city"
        node = qualify_columns(parse_expression(original, "test"), "o", {"o"}, "test")
        assert render_expression(unqualify_columns(node, "o")) == original

    def test_unqualify_keeps_other_qualifiers(self):
        node = unqualify_columns(parse_expression("o.a + c.b", "test"), "o")
        assert render_expression(node) == "a + c.b"

    def test_normalize_is_stable_under_repetition(self):
        # Round-trip fidelity is normalization-stable, not byte-stable: sqlglot
        # upper-cases functions and rewrites casts.
        once = normalize_expression("sum(o.amount)")
        assert once == "SUM(o.amount)"
        assert normalize_expression(once) == once
        assert normalize_expression("o.region::text") == "CAST(o.region AS TEXT)"

    @pytest.mark.parametrize(
        ("expr", "fragment"),
        [
            ("sum(o.amount)", "an aggregate function"),
            ("sum(o.amount) OVER (PARTITION BY o.region)", "a window function"),
            ("(SELECT 1)", "a subquery"),
            ("count(o.x) FILTER (WHERE o.y > 1)", "an aggregate FILTER clause"),
        ],
    )
    def test_assert_row_level_rejects_non_row_shapes(self, expr, fragment):
        with pytest.raises(ConversionError, match=fragment):
            assert_row_level(parse_expression(expr, "dim 'x'"), "dim 'x'")

    @pytest.mark.parametrize(
        "expr",
        [
            "o.region",
            "region",
            "upper(region) || city",
            "o.region::text",
            "CASE WHEN o.amount > 10 THEN 'hi' ELSE 'lo' END",
            "1",
        ],
    )
    def test_assert_row_level_accepts_row_shapes(self, expr):
        assert_row_level(parse_expression(expr, "dim 'x'"), "dim 'x'")


class TestMetricAggregate:
    @pytest.mark.parametrize(
        ("expr", "agg"),
        [
            ("sum(o.amount)", "sum"),
            ("SUM(o.amount)", "sum"),
            ("avg(c.credit_limit)", "avg"),
            ("min(o.amount)", "min"),
            ("max(o.amount)", "max"),
            ("count(*)", "count"),
            ("count(o.customer_id)", "count"),
            ("count(DISTINCT o.customer_id)", "count"),
            # Redundant parentheses do not change the shape.
            ("(sum(o.amount))", "sum"),
        ],
    )
    def test_whitelisted_aggregates_are_accepted(self, expr, agg):
        name, _ = metric_aggregate(parse_expression(expr, "metric 'm'"), "metric 'm'")
        assert name == agg

    @pytest.mark.parametrize(
        "expr",
        [
            # Ratio / derived metrics: the whole reason Hologres rejects these is that
            # they cannot be aggregated per metric group.
            "sum(o.amount) / count(*)",
            "SUM(store_sales.ss_ext_sales_price) / COUNT(DISTINCT customer.c_customer_sk)",
            "sum(o.amount) + sum(o.tax)",
            # Not in the Hologres aggregate whitelist, though sqlglot still calls these
            # AggFuncs -- which is why membership is tested by exact node type.
            "stddev(o.amount)",
            "array_agg(o.amount)",
            # An aggregate wrapped in something else is no longer a bare aggregate.
            "coalesce(sum(o.amount), 0)",
            "CASE WHEN sum(o.amount) > 1 THEN 1 ELSE 0 END",
            # Not an aggregate at all.
            "o.amount",
        ],
    )
    def test_unsupported_metric_shapes_are_rejected(self, expr):
        with pytest.raises(ConversionError, match="count/sum/avg/min/max"):
            metric_aggregate(parse_expression(expr, "metric 'm'"), "metric 'm'")

    def test_nested_aggregate_inside_the_operand_is_rejected(self):
        with pytest.raises(ConversionError, match="an aggregate function"):
            metric_aggregate(parse_expression("sum(sum(o.amount))", "metric 'm'"), "metric 'm'")

    def test_count_star_has_no_column_reference(self):
        # This is why `count(*)` metrics need an explicit owner stash: the owning table
        # simply is not recoverable from the expression.
        node = parse_expression("count(*)", "metric 'm'")
        metric_aggregate(node, "metric 'm'")
        assert column_refs(node) == []
