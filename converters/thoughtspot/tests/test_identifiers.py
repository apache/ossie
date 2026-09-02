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

from ossie_thoughtspot import identifiers


@pytest.mark.parametrize("display,expected", [
    ("Order Date", "order_date"),
    ("Order-Date", "order_date"),
    ("Total Sales (AUD)", "total_sales_aud"),
    ("  Leading and trailing  ", "leading_and_trailing"),
    ("Multiple   spaces", "multiple_spaces"),
    ("Already_snake", "already_snake"),
    ("2024 Revenue", "n_2024_revenue"),
])
def test_normalise(display, expected):
    assert identifiers.normalise(display) == expected


def test_normalise_rejects_a_name_that_normalises_to_nothing():
    with pytest.raises(ValueError, match="normalises to an empty identifier"):
        identifiers.normalise("!!!")


@pytest.mark.parametrize("display,expected", [
    # Diacritics are folded via NFKD decomposition (not an earlier
    # ASCII-only drop) — see the module docstring's "Known limitation" note.
    # These pin the *current* behaviour so a future change can't silently
    # regress it; they do not bless the remaining non-Latin-script limitation
    # as correct.
    ("Café", "cafe"),
    ("Ürün", "urun"),
    ("Zürich", "zurich"),
    ("İstanbul", "istanbul"),
    ("naïve", "naive"),
])
def test_normalise_folds_diacritics_known_limitation(display, expected):
    assert identifiers.normalise(display) == expected


def test_normalise_on_a_cjk_only_name_is_non_latin_script_known_limitation():
    # NFKD decomposition has no ASCII form for non-Latin scripts, so a
    # CJK-only name still raises — the narrower residual of the limitation.
    with pytest.raises(ValueError, match="normalises to an empty identifier"):
        identifiers.normalise("北京市")


def test_allocator_resolves_a_collision_with_a_numeric_suffix():
    # ID2: two distinct display names folding onto one identifier.
    alloc = identifiers.Allocator()
    assert alloc.allocate("Order Date") == "order_date"
    assert alloc.allocate("Order-Date") == "order_date_2"
    assert alloc.allocate("Order.Date") == "order_date_3"


def test_allocator_folds_case_when_detecting_collisions():
    # ID2: Ossie resolves regular identifiers case-insensitively, so a case-only
    # difference is ambiguous even though validate.py would accept it.
    alloc = identifiers.Allocator()
    assert alloc.allocate("Region") == "region"
    assert alloc.allocate("REGION") == "region_2"


def test_split_and_format_column_refs_round_trip():
    # ID3.
    assert identifiers.split_column_ref("[ORDERS::Order Date]") == ("ORDERS", "Order Date")
    assert identifiers.format_column_ref("ORDERS", "Order Date") == "[ORDERS::Order Date]"


def test_split_column_ref_rejects_a_malformed_reference():
    with pytest.raises(ValueError, match="not a ThoughtSpot column reference"):
        identifiers.split_column_ref("ORDERS::Order Date")


def test_split_column_ref_rejects_an_ambiguous_reference():
    # ID3: more than one '::' must raise rather than silently taking the
    # first delimiter and mis-splitting table/column.
    with pytest.raises(ValueError, match="ambiguous"):
        identifiers.split_column_ref("[A::B::C]")


def test_split_column_ref_rejects_a_reference_formatted_from_a_delimiter_containing_name():
    # A table name that itself contains '::' formats
    # into a reference that must fail loudly on split, not silently mis-split
    # the table/column boundary.
    ref = identifiers.format_column_ref("A::B", "C")
    assert ref == "[A::B::C]"
    with pytest.raises(ValueError, match="ambiguous"):
        identifiers.split_column_ref(ref)


def test_split_column_ref_rejects_a_table_with_a_trailing_colon():
    # str.count("::") is non-overlapping, so a run of three consecutive
    # colons ("ORDERS" + trailing ":" + the "::" delimiter) only counts as
    # one match and previously slipped through, silently mis-splitting to
    # ("ORDERS", ":Col") instead of raising.
    ref = identifiers.format_column_ref("ORDERS:", "Col")
    assert ref == "[ORDERS:::Col]"
    with pytest.raises(ValueError, match="ambiguous"):
        identifiers.split_column_ref(ref)


def test_split_column_ref_rejects_a_column_with_a_leading_colon():
    # The same three-colon-run string is equally producible from a column
    # that itself starts with ':' — genuinely ambiguous either way.
    ref = identifiers.format_column_ref("ORDERS", ":Col")
    assert ref == "[ORDERS:::Col]"
    with pytest.raises(ValueError, match="ambiguous"):
        identifiers.split_column_ref(ref)


def test_split_column_ref_accepts_a_table_name_with_a_single_colon():
    # A single ':' in the table position is not the same as the genuinely
    # ambiguous '::'/leading-colon shapes above — the table group matches
    # lazily up to the first '::', it does not reject colons outright.
    assert identifiers.split_column_ref("[A:B::x]") == ("A:B", "x")


def test_split_column_ref_accepts_a_column_name_with_a_single_colon():
    assert identifiers.split_column_ref("[A::x:y]") == ("A", "x:y")


def test_split_and_format_round_trip_a_table_name_containing_a_colon():
    # Regression: format_column_ref("A:B", "x") -> "[A:B::x]", which
    # split_column_ref used to refuse (the table group excluded colons
    # outright, a stricter grammar than the documented ambiguity rule). Not
    # ambiguous — there is exactly one '::' — so it must round-trip.
    table, column = "A:B", "x"
    ref = identifiers.format_column_ref(table, column)
    assert ref == "[A:B::x]"
    assert identifiers.split_column_ref(ref) == (table, column)
