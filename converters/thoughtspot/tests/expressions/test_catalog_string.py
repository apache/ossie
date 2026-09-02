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

"""Catalog coverage: String functions.

Source: the `String functions` section of docs/ossie/ts-ossie-function-mapping.md
(thoughtspot-agent-skills repo, not vendored here). 21 rows total — 10 direct /
11 passthrough / 0 unmappable.

This family is over half passthrough, and the reasons are counter-intuitive:
TRIM/LTRIM/RTRIM/REPLACE/LOWER/UPPER are passthrough because ThoughtSpot has no
native trim/replace/lower/upper function at all — not because they behave
differently. STARTSWITH/ENDSWITH are the opposite surprise: direct despite
having no native function, because the composition out of strpos/substr/strlen
is exact and uses only native functions (rule E2).

Construct names in this family are spelled identically to the mapping
document's own row headers — none of this family's keys diverge the way CAST/
TRY_CAST or the Date/time typed literals did (see catalog.py's module
docstring, "Spelling" section, and `spec_construct_names()` itself).
"""
from ossie_thoughtspot.expressions import CATALOG
from ossie_thoughtspot.expressions._types import Classification, Variant

EXPECTED: dict[str, Classification] = {
    "CONCAT(str1, str2, ...)": Classification.DIRECT,
    "LENGTH(str)": Classification.DIRECT,
    "LOWER(str)": Classification.PASSTHROUGH,
    "UPPER(str)": Classification.PASSTHROUGH,
    "TRIM(str)": Classification.PASSTHROUGH,
    "LTRIM(str)": Classification.PASSTHROUGH,
    "RTRIM(str)": Classification.PASSTHROUGH,
    "LEFT(str, n)": Classification.DIRECT,
    "RIGHT(str, n)": Classification.DIRECT,
    "SUBSTRING(str, start, length)": Classification.DIRECT,
    "REPLACE(str, from, to)": Classification.PASSTHROUGH,
    "SPLIT_PART(str, delimiter, part)": Classification.PASSTHROUGH,
    "POSITION(substr IN str)": Classification.DIRECT,
    "CHARINDEX(substr, str)": Classification.DIRECT,
    "CONTAINS(str, substr)": Classification.DIRECT,
    "STARTSWITH(str, prefix)": Classification.DIRECT,
    "ENDSWITH(str, suffix)": Classification.DIRECT,
    "REGEXP_LIKE(str, pattern)": Classification.PASSTHROUGH,
    "REGEXP_EXTRACT(str, pattern)": Classification.PASSTHROUGH,
    "REGEXP_REPLACE(str, pattern, replacement)": Classification.PASSTHROUGH,
    "REGEXP_COUNT(str, pattern)": Classification.PASSTHROUGH,
}

#: Expected `Variant` for every passthrough row in this family (E4/E7). Getting
#: this wrong is the failure mode with no safety net: the wrong variant emits a
#: column that imports cleanly and then aggregates or types wrongly, and
#: nothing downstream catches it.
EXPECTED_VARIANTS: dict[str, Variant] = {
    "LOWER(str)": Variant.STRING,
    "UPPER(str)": Variant.STRING,
    "TRIM(str)": Variant.STRING,
    "LTRIM(str)": Variant.STRING,
    "RTRIM(str)": Variant.STRING,
    "REPLACE(str, from, to)": Variant.STRING,
    "SPLIT_PART(str, delimiter, part)": Variant.STRING,
    "REGEXP_LIKE(str, pattern)": Variant.BOOL,
    "REGEXP_EXTRACT(str, pattern)": Variant.STRING,
    "REGEXP_REPLACE(str, pattern, replacement)": Variant.STRING,
    "REGEXP_COUNT(str, pattern)": Variant.INT,
}


def test_row_count_for_this_family():
    ours = [c for c in CATALOG.values() if c.spec_name in EXPECTED]
    assert len(ours) == 21


def test_classifications():
    for name, expected in EXPECTED.items():
        assert CATALOG[name].classification is expected, name


def test_passthrough_count_and_variants():
    passthrough_names = {n for n, c in EXPECTED.items() if c is Classification.PASSTHROUGH}
    assert len(passthrough_names) == 11
    assert passthrough_names == set(EXPECTED_VARIANTS)
    for name, variant in EXPECTED_VARIANTS.items():
        assert CATALOG[name].variant is variant, name


def test_direct_count():
    direct_names = {n for n, c in EXPECTED.items() if c is Classification.DIRECT}
    assert len(direct_names) == 10


def test_no_unmappable_rows_in_this_family():
    assert not any(c is Classification.UNMAPPABLE for c in EXPECTED.values())


# --------------------------------------------------------------------------
# Rows the document only explains via its surrounding prose.
# --------------------------------------------------------------------------

def test_the_whole_trim_family_is_passthrough_not_just_two_sided_trim():
    # Live-verified 2026-07-29 on se-thoughtspot (BL-170): ThoughtSpot has no
    # native trim at all, rejected with `Search did not find "trim ("`. TRIM,
    # LTRIM and RTRIM are all passthrough for the same reason, not because a
    # two-sided trim exists and the one-sided forms don't compose from it.
    for name in ("TRIM(str)", "LTRIM(str)", "RTRIM(str)"):
        row = CATALOG[name]
        assert row.classification is Classification.PASSTHROUGH
        assert row.variant is Variant.STRING


def test_lower_and_upper_have_no_native_equivalent():
    # No native lower/upper in ThoughtSpot — the most-used functions in the
    # whole passthrough set, per the document's own framing.
    assert CATALOG["LOWER(str)"].classification is Classification.PASSTHROUGH
    assert CATALOG["UPPER(str)"].classification is Classification.PASSTHROUGH


def test_replace_was_direct_on_documentation_but_moved_on_live_verification():
    # Live-verified 2026-07-29 on se-thoughtspot (BL-170): rejected with
    # `Search did not find "replace ("`. The row was direct on documentation
    # alone; the live pass moved it to the documented pass-through fallback.
    row = CATALOG["REPLACE(str, from, to)"]
    assert row.classification is Classification.PASSTHROUGH
    assert row.variant is Variant.STRING


def test_startswith_and_endswith_are_direct_despite_no_native_function():
    # No native starts_with/ends_with (live-verified 2026-07-29, BL-170), but
    # both compositions use only native functions (strpos/substr/strlen), so
    # rule E2 keeps them direct rather than passthrough.
    for name in ("STARTSWITH(str, prefix)", "ENDSWITH(str, suffix)"):
        row = CATALOG[name]
        assert row.classification is Classification.DIRECT
        assert row.variant is None
        assert "sql_" not in row.template


def test_substring_index_base_shift_is_present_in_the_template():
    # ANSI SUBSTRING is 1-based; ThoughtSpot's substr is 0-based. The -1 shift
    # is mandatory and is the single most likely off-by-one in the mapping.
    row = CATALOG["SUBSTRING(str, start, length)"]
    assert row.classification is Classification.DIRECT
    assert "- 1" in row.template


def test_position_and_charindex_reverse_operand_order():
    # ThoughtSpot's strpos takes the haystack first; the specification's
    # POSITION(substr IN str) and CHARINDEX(substr, str) both put the needle
    # first, so both templates reverse the operand order onto strpos.
    position = CATALOG["POSITION(substr IN str)"]
    charindex = CATALOG["CHARINDEX(substr, str)"]
    assert position.classification is Classification.DIRECT
    assert charindex.classification is Classification.DIRECT
    assert position.template == charindex.template


def test_no_regular_expression_support_of_any_kind():
    # ThoughtSpot has no regex engine at all — every REGEXP_* row is
    # passthrough, with no native fallback for any of them.
    for name in (
        "REGEXP_LIKE(str, pattern)",
        "REGEXP_EXTRACT(str, pattern)",
        "REGEXP_REPLACE(str, pattern, replacement)",
        "REGEXP_COUNT(str, pattern)",
    ):
        assert CATALOG[name].classification is Classification.PASSTHROUGH


def test_regexp_like_is_bool_variant_not_string():
    # REGEXP_LIKE returns a boolean, so its pass-through variant is
    # sql_bool_op, not sql_string_op like its REGEXP_* siblings.
    assert CATALOG["REGEXP_LIKE(str, pattern)"].variant is Variant.BOOL


def test_regexp_count_is_int_variant():
    # REGEXP_COUNT returns an integer count, so its variant is sql_int_op.
    assert CATALOG["REGEXP_COUNT(str, pattern)"].variant is Variant.INT
