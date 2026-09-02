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

import pytest
from ossie_thoughtspot.formula import (
    find_column_refs, find_parameter_refs, is_bare_column_ref,
    rewrite_column_refs, split_call,
)


class TestSplitCall:
    def test_simple_call(self):
        assert split_call("sum ( [ORDERS::Amount] )") == ("sum", ["[ORDERS::Amount]"])

    def test_no_space_before_paren(self):
        assert split_call("sum([ORDERS::Amount])") == ("sum", ["[ORDERS::Amount]"])

    def test_multiple_arguments(self):
        name, args = split_call("concat ( [A::x] , '-' , [A::y] )")
        assert name == "concat"
        assert args == ["[A::x]", "'-'", "[A::y]"]

    def test_nested_call_is_one_argument(self):
        name, args = split_call("sum ( if ( [A::x] > 0 ) then [A::x] else 0 )")
        assert name == "sum"
        assert args == ["if ( [A::x] > 0 ) then [A::x] else 0"]

    def test_comma_inside_nested_parens_does_not_split(self):
        name, args = split_call("round ( divide ( [A::x] , [A::y] ) , 2 )")
        assert args == ["divide ( [A::x] , [A::y] )", "2"]

    def test_comma_inside_a_quoted_literal_does_not_split(self):
        name, args = split_call("concat ( [A::x] , ', ' , [A::y] )")
        assert args == ["[A::x]", "', '", "[A::y]"]

    def test_brace_group_is_one_argument(self):
        # The documented window shape: braces are ThoughtSpot's grouping syntax
        # and no SQL parser handles them, which is half the reason we tokenize.
        name, args = split_call(
            "last_value ( sum ( [T::c] ) , query_groups ( ) , { [D::date] } )"
        )
        assert name == "last_value"
        assert args == ["sum ( [T::c] )", "query_groups ( )", "{ [D::date] }"]

    def test_empty_argument_list(self):
        assert split_call("query_groups ( )") == ("query_groups", [])

    def test_not_a_call_returns_none(self):
        assert split_call("[ORDERS::Amount]") is None
        assert split_call("42") is None

    def test_expression_that_merely_contains_a_call_is_not_a_single_call(self):
        # `sum(a) + 1` has a call in it but is not one — a caller that treated
        # it as `("sum", ["a"])` would silently drop the `+ 1`.
        assert split_call("sum ( [A::x] ) + 1") is None

    def test_two_calls_side_by_side_is_not_a_single_call(self):
        assert split_call("sum ( [A::x] ) / count ( [A::y] )") is None

    def test_unbalanced_parens_return_none_rather_than_raising(self):
        assert split_call("sum ( [A::x]") is None


class TestFindColumnRefs:
    def test_finds_each_reference_in_order(self):
        assert find_column_refs("[A::x] + [B::y]") == [("A", "x"), ("B", "y")]

    def test_keeps_duplicates(self):
        assert find_column_refs("[A::x] + [A::x]") == [("A", "x"), ("A", "x")]

    def test_ignores_a_parameter_reference(self):
        # `[Growth Rate]` has no `::` — it is a runtime parameter, not a column.
        assert find_column_refs("[A::x] * [Growth Rate]") == [("A", "x")]

    def test_no_references(self):
        assert find_column_refs("42") == []


class TestFindParameterRefs:
    def test_finds_bracketed_names_without_a_table_qualifier(self):
        assert find_parameter_refs("[A::x] * [Growth Rate]") == ["Growth Rate"]

    def test_returns_empty_when_every_reference_is_qualified(self):
        assert find_parameter_refs("[A::x] + [B::y]") == []


class TestIsBareColumnRef:
    def test_a_lone_reference(self):
        assert is_bare_column_ref("[ORDERS::Amount]") == ("ORDERS", "Amount")

    def test_surrounding_whitespace_is_tolerated(self):
        assert is_bare_column_ref("  [ORDERS::Amount]  ") == ("ORDERS", "Amount")

    def test_anything_more_is_not_bare(self):
        assert is_bare_column_ref("[ORDERS::Amount] + 1") is None
        assert is_bare_column_ref("sum ( [ORDERS::Amount] )") is None
        assert is_bare_column_ref("[Growth Rate]") is None


class TestRewriteColumnRefs:
    def test_rewrites_every_reference(self):
        out = rewrite_column_refs(
            "[A::x] + [B::y]", lambda t, c: f"{t.lower()}.{c.lower()}"
        )
        assert out == "a.x + b.y"

    def test_leaves_a_parameter_reference_untouched(self):
        out = rewrite_column_refs(
            "[A::x] * [Growth Rate]", lambda t, c: f"{t.lower()}.{c.lower()}"
        )
        assert out == "a.x * [Growth Rate]"

    def test_preserves_everything_between_references_byte_for_byte(self):
        src = "concat ( [A::x] ,   ', '  , [A::y] )"
        out = rewrite_column_refs(src, lambda t, c: f"{t}.{c}")
        assert out == "concat ( A.x ,   ', '  , A.y )"

    def test_rewriting_is_not_confused_by_a_bracket_inside_a_literal(self):
        src = "concat ( [A::x] , '[not::a::ref]' )"
        out = rewrite_column_refs(src, lambda t, c: f"{t}.{c}")
        assert out == "concat ( A.x , '[not::a::ref]' )"

    def test_is_byte_preserving_when_rename_returns_the_reference_unchanged(self):
        # A stronger check than "rewrites every reference": if rename hands
        # back exactly the bracketed text it was asked to replace, the whole
        # expression — including irregular internal spacing — must come back
        # unchanged. This would catch an off-by-one in the span arithmetic
        # that a same-length rewrite (e.g. "a.x") could hide.
        src = "  concat( [A::x]  ,'-',[B::y] )   "
        out = rewrite_column_refs(
            src, lambda t, c: f"[{t}::{c}]"
        )
        assert out == src


class TestAdditionalEdgeCases:
    def test_doubled_quote_inside_a_quoted_literal_does_not_split_the_argument(self):
        # ThoughtSpot (like standard SQL) escapes an embedded quote by doubling
        # it: 'it''s' is meant as the single literal `it's`. `_scan` has no
        # explicit doubling special-case — it just toggles `quote` on every
        # matching quote char — but that toggle still yields `in_quote=True`
        # on every character of the pair (the close and the immediate reopen
        # are both reported as quoted), so a comma between two doubled quotes
        # would still read as inside a literal and would not split. Verified
        # empirically (not just by hand-trace) before asserting this: the
        # whole doubled-quote literal survives as one untouched argument.
        name, args = split_call("concat ( [A::x] , 'it''s' , [A::y] )")
        assert name == "concat"
        assert args == ["[A::x]", "'it''s'", "[A::y]"]

    def test_column_reference_with_a_space_in_the_column_name(self):
        # Real ThoughtSpot display names routinely contain spaces
        # ("Order Date", "Sales Amount"). `_BRACKETED` matches everything
        # between `[` and `]` with no whitespace restriction, so this must
        # work exactly like any other reference.
        assert find_column_refs("[Orders::Order Date] + 1") == [
            ("Orders", "Order Date")
        ]
        assert is_bare_column_ref("[Orders::Order Date]") == (
            "Orders",
            "Order Date",
        )
        out = rewrite_column_refs(
            "[Orders::Order Date]", lambda t, c: f"{t}.{c.replace(' ', '_')}"
        )
        assert out == "Orders.Order_Date"
