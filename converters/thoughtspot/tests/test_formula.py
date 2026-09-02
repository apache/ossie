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
    _scan, find_column_refs, find_parameter_refs, is_bare_column_ref,
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

    def test_a_keyword_prefix_is_not_mistaken_for_a_function_name(self):
        # `_CALL_HEAD` allows unbounded space-separated words because some
        # real ThoughtSpot function names are multi-word (`unique count`).
        # But an operator/control-flow keyword can never be part of a
        # function name, so `true and count (...)` is an operator expression
        # ending in something that merely looks like a call head — not a
        # call named "true and count".
        assert split_call("true and count ( [B::y] )") is None

    def test_a_real_multi_word_function_name_still_works(self):
        # The keyword blocklist must not catch legitimate multi-word names.
        assert split_call("unique count ( [B::y] )") == (
            "unique count",
            ["[B::y]"],
        )


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

    def test_an_ambiguous_reference_raises_rather_than_silently_misreading(self):
        # `identifiers.split_column_ref` raises on a reference with more than
        # one `::` delimiter rather than silently taking the first one. This
        # module delegates to it instead of a bare `str.split("::", 1)`, so
        # the same failure must surface here too — even when the ambiguous
        # reference sits among otherwise-valid ones in a longer expression.
        # Silently misreading one reference is worse than failing the call.
        with pytest.raises(ValueError):
            find_column_refs("[A::x] + [ORDERS:::Col] + [B::y]")


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


class TestQuoteInsideBracketBody:
    """A `[...]` body is an opaque identifier, not code — a quote character inside one
    (a display name like `Manager's Bonus`, entirely routine in real ThoughtSpot data) is
    part of the name, not a string delimiter. Before the fix, `_scan` toggled quote state
    on any `'`/`"` anywhere in the text, including inside brackets, which desynchronised
    quote tracking for everything after the apostrophe — silently dropping a later
    reference, leaving it unrewritten, or rejecting a valid single call.
    """

    def test_find_column_refs_does_not_lose_a_later_reference(self):
        assert find_column_refs("[Managers::Manager's Bonus] + [B::y]") == [
            ("Managers", "Manager's Bonus"),
            ("B", "y"),
        ]

    def test_rewrite_column_refs_still_rewrites_a_later_reference(self):
        out = rewrite_column_refs(
            "[Managers::Manager's Bonus] + [B::y]", lambda t, c: f"{t}.{c}"
        )
        assert out == "Managers.Manager's Bonus + B.y"

    def test_split_call_still_recognises_a_valid_single_call(self):
        assert split_call("sum ( [Managers::Manager's Bonus] )") == (
            "sum",
            ["[Managers::Manager's Bonus]"],
        )

    def test_apostrophe_name_nested_two_calls_deep(self):
        # The bracket stack must un-suppress correctly on each `]` no matter
        # how many enclosing `(` it is nested inside.
        outer = split_call("sum ( count ( [Managers::Manager's Bonus] ) )")
        assert outer is not None
        name, args = outer
        assert name == "sum"
        assert args == ["count ( [Managers::Manager's Bonus] )"]
        inner = split_call(args[0])
        assert inner == ("count", ["[Managers::Manager's Bonus]"])

    def test_column_name_containing_a_double_quote(self):
        assert find_column_refs('[A::Say "Hi"] + [B::y]') == [
            ("A", 'Say "Hi"'),
            ("B", "y"),
        ]


class TestScanBracketStackTypeAwarePop:
    """`_scan`'s bracket-type stack must pop only when a closer matches the type of its top
    entry, never unconditionally — an unconditional pop lets a mismatched or stray closer
    desynchronise the stack, which can then incorrectly toggle quote suppression for
    whatever follows. `_scan` is a shallow tokenizer over malformed input here, not a
    validator, so these pin the actual observed output (checked by running the scanner
    before writing the assertion, not the output one might expect) rather than any claim
    that the malformed input is handled "correctly" in some absolute sense.
    """

    def test_a_mismatched_closer_does_not_pop_the_bracket_stack(self):
        # `}` does not match the `[` on top of the stack, so the stack keeps
        # treating everything after it as still inside the never-closed `[`
        # body — quote-toggling for the trailing `'z'` literal stays
        # suppressed rather than (incorrectly) starting a real quote.
        by_index = {i: in_quote for i, _ch, _d, in_quote in _scan("[A::x} 'z'")}
        assert by_index[7] is False  # opening quote of 'z'
        assert by_index[9] is False  # closing quote of 'z'

    def test_a_stray_closer_with_nothing_open_does_not_crash_or_suppress_quoting(self):
        # After the properly closed `[A::x]`, an extra `)` has an empty
        # stack to pop from — no matching opener anywhere. It must not
        # raise, and with nothing open afterwards the trailing 'z' literal
        # is read as a real quoted string, not suppressed.
        by_index = {i: in_quote for i, _ch, _d, in_quote in _scan("[A::x] ) 'z'")}
        assert by_index[9] is True  # opening quote of 'z'
        assert by_index[11] is True  # closing quote of 'z'
