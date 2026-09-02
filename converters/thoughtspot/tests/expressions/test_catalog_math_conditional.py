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

"""Catalog coverage: Mathematical and Conditional functions.

Source: the `Mathematical functions` and `Conditional functions` sections of
docs/ossie/ts-ossie-function-mapping.md (thoughtspot-agent-skills repo, not
vendored here). 34 rows total — 32 direct / 2 passthrough / 0 unmappable.

Nearly everything here is direct, several by composition (rule E2): SIGN is an
`if` chain with a mandatory `else 0` (ThoughtSpot rejects an `if` with no
`else`); RADIANS/DEGREES are bare arithmetic (no native function); PI is a
literal at the precision ThoughtSpot's own documented composites use.
ThoughtSpot trigonometry is in degrees while the specification is in radians,
so every forward trig function multiplies by 180/pi and every inverse trig
function divides by it — the opposite conversion, easy to get backwards.
GREATEST/LEAST are deliberately not MAX/MIN: ThoughtSpot's max/min are
aggregate-only, so mapping the row-wise N-ary forms onto them would both
collapse the column to one value and flip it from attribute to measure (E7).

Only two rows are passthrough: TRUNC/TRUNCATE (no native truncation, and
neither floor nor round is a safe substitute) and ATAN2 (quadrant-aware and
defined where x = 0, so it is not a two-argument ATAN composition).

Construct names in this family are spelled identically to the mapping
document's own row headers, with the same alias-merge convention as CEIL/
CEILING and TRUNC/TRUNCATE (see catalog.py's module docstring, "Spelling"
section) — the merged spelling (`CEIL(x)`, `TRUNC(x, d)`) is what
`spec_construct_names()` actually extracts, confirmed live before writing
this file.
"""
from ossie_thoughtspot.expressions import CATALOG
from ossie_thoughtspot.expressions._types import Classification, Variant

EXPECTED: dict[str, Classification] = {
    # Mathematical functions (25 rows: 23 direct / 2 passthrough)
    "ABS(x)": Classification.DIRECT,
    "ROUND(x, d)": Classification.DIRECT,
    "FLOOR(x)": Classification.DIRECT,
    "CEIL(x)": Classification.DIRECT,
    "TRUNC(x, d)": Classification.PASSTHROUGH,
    "MOD(x, y)": Classification.DIRECT,
    "SIGN(x)": Classification.DIRECT,
    "POWER(x, y)": Classification.DIRECT,
    "SQRT(x)": Classification.DIRECT,
    "EXP(x)": Classification.DIRECT,
    "LN(x)": Classification.DIRECT,
    "LOG(base, x)": Classification.DIRECT,
    "LOG10(x)": Classification.DIRECT,
    "SIN(x)": Classification.DIRECT,
    "COS(x)": Classification.DIRECT,
    "TAN(x)": Classification.DIRECT,
    "ASIN(x)": Classification.DIRECT,
    "ACOS(x)": Classification.DIRECT,
    "ATAN(x)": Classification.DIRECT,
    "ATAN2(y, x)": Classification.PASSTHROUGH,
    "RADIANS(degrees)": Classification.DIRECT,
    "DEGREES(radians)": Classification.DIRECT,
    "PI()": Classification.DIRECT,
    "GREATEST(x, y, ...)": Classification.DIRECT,
    "LEAST(x, y, ...)": Classification.DIRECT,
    # Conditional functions (9 rows: all direct)
    "IF(condition, true_result, false_result)": Classification.DIRECT,
    "IFF(condition, true_result, false_result)": Classification.DIRECT,
    "NULLIF(expr1, expr2)": Classification.DIRECT,
    "COALESCE(expr1, expr2, ...)": Classification.DIRECT,
    "IFNULL(expr, default)": Classification.DIRECT,
    "NVL(expr, default)": Classification.DIRECT,
    "NVL2(expr, not_null_result, null_result)": Classification.DIRECT,
    "ZEROIFNULL(expr)": Classification.DIRECT,
    "NULLIFZERO(expr)": Classification.DIRECT,
}

#: Expected `Variant` for every passthrough row in this family (E4/E7). Getting
#: this wrong is the failure mode with no safety net: the wrong variant emits a
#: column that imports cleanly and then aggregates or types wrongly, and
#: nothing downstream catches it.
EXPECTED_VARIANTS: dict[str, Variant] = {
    "TRUNC(x, d)": Variant.DOUBLE,
    "ATAN2(y, x)": Variant.DOUBLE,
}


def test_row_count_for_this_family():
    ours = [c for c in CATALOG.values() if c.spec_name in EXPECTED]
    assert len(ours) == 34


def test_classifications():
    for name, expected in EXPECTED.items():
        assert CATALOG[name].classification is expected, name


def test_passthrough_count_and_variants():
    passthrough_names = {n for n, c in EXPECTED.items() if c is Classification.PASSTHROUGH}
    assert len(passthrough_names) == 2
    assert passthrough_names == set(EXPECTED_VARIANTS)
    for name, variant in EXPECTED_VARIANTS.items():
        assert CATALOG[name].variant is variant, name


def test_direct_count():
    direct_names = {n for n, c in EXPECTED.items() if c is Classification.DIRECT}
    assert len(direct_names) == 32


def test_no_unmappable_rows_in_this_family():
    assert not any(c is Classification.UNMAPPABLE for c in EXPECTED.values())


# --------------------------------------------------------------------------
# Rows the document only explains via its surrounding prose.
# --------------------------------------------------------------------------

def test_sign_is_an_if_chain_with_a_mandatory_final_else():
    # No native `sign`, but the three-way result composes exactly from `if`.
    # ThoughtSpot rejects an `if` chain with no `else` — the `else 0` is not
    # optional decoration, it is required for the formula to import at all.
    row = CATALOG["SIGN(x)"]
    assert row.classification is Classification.DIRECT
    assert "else 0" in row.template


def test_power_uses_pow_not_power():
    # `power` is rejected by the ThoughtSpot formula parser; the function is
    # spelled `pow`.
    row = CATALOG["POWER(x, y)"]
    assert row.template.startswith("pow (")


def test_trig_functions_convert_degrees_because_thoughtspot_is_degrees_native():
    # ThoughtSpot trigonometry is in degrees; the specification is in radians.
    # A bare sin(x) would return the sine of x *degrees* and be wrong for
    # every non-zero input, so SIN/COS/TAN all multiply by 180/pi.
    for name in ("SIN(x)", "COS(x)", "TAN(x)"):
        row = CATALOG[name]
        assert row.classification is Classification.DIRECT
        assert "180" in row.template and "3.14159265358979" in row.template


def test_inverse_trig_functions_convert_the_other_way():
    # ASIN/ACOS/ATAN return degrees from ThoughtSpot's native functions but
    # the specification expects radians, so these divide by 180/pi instead of
    # multiplying by it — the opposite direction from SIN/COS/TAN.
    for name in ("ASIN(x)", "ACOS(x)", "ATAN(x)"):
        row = CATALOG[name]
        assert row.classification is Classification.DIRECT
        assert "/ 180" in row.template


def test_atan2_is_passthrough_not_a_two_argument_atan():
    # atan2 is quadrant-aware and defined where x = 0; it is not simply a
    # two-argument form of atan, so no native composition is attempted.
    row = CATALOG["ATAN2(y, x)"]
    assert row.classification is Classification.PASSTHROUGH
    assert row.variant is Variant.DOUBLE


def test_radians_and_degrees_are_bare_arithmetic():
    # No native radians/degrees function; the conversion is exact, dialect-free
    # arithmetic, not a passthrough.
    radians = CATALOG["RADIANS(degrees)"]
    degrees = CATALOG["DEGREES(radians)"]
    assert radians.classification is Classification.DIRECT
    assert degrees.classification is Classification.DIRECT
    assert radians.variant is None
    assert degrees.variant is None


def test_pi_is_a_literal_at_the_documented_composite_precision():
    # No native pi(). The literal matches the precision ThoughtSpot's own
    # documented composites (SIN/COS/TAN etc.) already use in this family.
    row = CATALOG["PI()"]
    assert row.classification is Classification.DIRECT
    assert row.template.strip() == "3.14159265358979"


def test_greatest_and_least_are_not_max_and_min():
    # ThoughtSpot's max/min are aggregate-only; greatest/least are the
    # row-wise N-ary functions. Mapping GREATEST to max would both collapse
    # the column to one value and flip it from attribute to measure (E7).
    greatest = CATALOG["GREATEST(x, y, ...)"]
    least = CATALOG["LEAST(x, y, ...)"]
    assert greatest.classification is Classification.DIRECT
    assert least.classification is Classification.DIRECT
    assert "greatest (" in greatest.template
    assert "least (" in least.template


def test_trunc_has_no_safe_native_substitute():
    # floor only agrees with TRUNC for x >= 0 and d = 0; round disagrees at
    # every half-value. Neither is a safe substitute, hence passthrough.
    row = CATALOG["TRUNC(x, d)"]
    assert row.classification is Classification.PASSTHROUGH
    assert row.variant is Variant.DOUBLE


def test_if_requires_parenthesized_condition():
    # The parentheses around the condition are mandatory for TML import —
    # without them the parser reports "Expecting keyword '('". Applies to
    # every condition shape, including a bare BOOL column reference.
    row = CATALOG["IF(condition, true_result, false_result)"]
    assert row.classification is Classification.DIRECT
    assert row.template.startswith("if (")


def test_iff_is_an_alias_for_if():
    assert CATALOG["IFF(condition, true_result, false_result)"].template == (
        CATALOG["IF(condition, true_result, false_result)"].template
    )


def test_coalesce_is_a_right_nested_ifnull_chain():
    # ThoughtSpot's ifnull is strictly two-argument, so an N-ary COALESCE
    # becomes a right-nested chain rather than a flat N-ary call.
    row = CATALOG["COALESCE(expr1, expr2, ...)"]
    assert row.classification is Classification.DIRECT
    assert row.template.count("ifnull (") == 2


def test_nvl_is_alias_for_two_argument_coalesce_via_ifnull():
    assert CATALOG["NVL(expr, default)"].template == CATALOG["IFNULL(expr, default)"].template


def test_nvl2_has_no_native_three_way_null_function():
    # No native three-way null function; the composition using isnotnull is
    # exact.
    row = CATALOG["NVL2(expr, not_null_result, null_result)"]
    assert row.classification is Classification.DIRECT
    assert "isnotnull (" in row.template


def test_zeroifnull_and_nullifzero_are_mirror_images():
    zeroifnull = CATALOG["ZEROIFNULL(expr)"]
    nullifzero = CATALOG["NULLIFZERO(expr)"]
    assert zeroifnull.classification is Classification.DIRECT
    assert nullifzero.classification is Classification.DIRECT
    assert "ifnull (" in zeroifnull.template
    assert "nullif (" in nullifzero.template
