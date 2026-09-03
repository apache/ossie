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

"""Catalog coverage: Operators and constructs.

Source: the `Operators and constructs` section of
docs/ossie/ts-ossie-function-mapping.md (thoughtspot-agent-skills repo, not
vendored here). 33 rows total - 30 direct / 2 passthrough / 1 unmappable.

This is the only family with an `unmappable` row in the whole 146-row catalog:
`EXISTS_IN()` is named at :131 as the sanctioned way to filter on a subquery,
but the specification never defines it anywhere - no signature, no argument
order, no semantics. `str ILIKE pattern` is passthrough because
case-insensitive matching has no native form (and the usual `lower`
workaround is itself a passthrough); `DISTINCT` as an aggregate modifier is
passthrough because ThoughtSpot has exactly one distinct-aware aggregate
(`unique count`, i.e. `COUNT(DISTINCT)`, already its own row) and nothing
else. `str LIKE pattern` stays direct despite ThoughtSpot having no native
`starts_with`/`ends_with`: the prefix/suffix/contains compositions use only
native functions (rule E2).

Six of the family's 33 rows have no discrete row of their own in the upstream
core-spec/expression_language.md - they are named only in prose, a bullet
list, or an "Operator Precedence"/"Not Supported" table with no backtick
marker - so they are keyed via `CONVENTION_DIVERGENCES` rather than
`spec_construct_names()`: unary `-x`/`+x`, the simple `CASE` form,
`Parentheses`, the `DISTINCT` modifier, the column/metric reference, and
`EXISTS_IN()` itself. The other 27 rows key on `spec_construct_names()`'s own
extraction - confirmed live before writing this file - which for this family
means the BARE operator/keyword token, not the mapping document's `a op b`
worked-example header: `+`, `-`, `*`, `/`, `%`, `=`, `<>`, `!=`, `<`, `>`,
`<=`, `>=`, `BETWEEN`, `IN`, `NOT IN`, `NOT expr`, `IS NULL`, `IS NOT NULL`,
`IS DISTINCT FROM`, `IS NOT DISTINCT FROM`, `CASE WHEN`, `TRUE, FALSE`,
`expr1 AND expr2`, `expr1 OR expr2`, `str LIKE pattern`, `str ILIKE pattern`,
`str1 || str2`.
"""
from ossie_thoughtspot.expressions import CATALOG
from ossie_thoughtspot.expressions._types import Classification, Variant

EXPECTED: dict[str, Classification] = {
    # Arithmetic operators (spec_construct_names() extracts the bare symbol,
    # not the document's "a + b" worked-example header)
    "+": Classification.DIRECT,
    "-": Classification.DIRECT,
    "*": Classification.DIRECT,
    "/": Classification.DIRECT,
    "%": Classification.DIRECT,
    "-x / +x (unary)": Classification.DIRECT,  # CONVENTION_DIVERGENCES
    # Comparison operators (same bare-symbol extraction)
    "=": Classification.DIRECT,
    "<>": Classification.DIRECT,
    "!=": Classification.DIRECT,
    "<": Classification.DIRECT,
    ">": Classification.DIRECT,
    "<=": Classification.DIRECT,
    ">=": Classification.DIRECT,
    # Logical operators (Boolean Functions table's own expr1/expr2 placeholders)
    "expr1 AND expr2": Classification.DIRECT,
    "expr1 OR expr2": Classification.DIRECT,
    "NOT expr": Classification.DIRECT,
    # Set/range/pattern operators
    "BETWEEN": Classification.DIRECT,
    "IN": Classification.DIRECT,
    "NOT IN": Classification.DIRECT,
    "str LIKE pattern": Classification.DIRECT,
    "str ILIKE pattern": Classification.PASSTHROUGH,
    # Null tests
    "IS NULL": Classification.DIRECT,
    "IS NOT NULL": Classification.DIRECT,
    "IS DISTINCT FROM": Classification.DIRECT,
    "IS NOT DISTINCT FROM": Classification.DIRECT,
    # CASE (both forms are rowed here, not under Conditional functions)
    "CASE WHEN": Classification.DIRECT,
    "CASE expr WHEN v1 THEN r1 ... END (simple)": Classification.DIRECT,  # CONVENTION_DIVERGENCES
    # Concatenation, grouping, literals
    "str1 || str2": Classification.DIRECT,
    "Parentheses — expression grouping": Classification.DIRECT,  # CONVENTION_DIVERGENCES
    "TRUE, FALSE": Classification.DIRECT,
    # Aggregate modifier
    "DISTINCT aggregate modifier": Classification.PASSTHROUGH,  # CONVENTION_DIVERGENCES
    # References
    "Column / metric reference — field, dataset.field": Classification.DIRECT,  # CONVENTION_DIVERGENCES
    # The one unmappable row in the whole 146-row catalog
    "EXISTS_IN()": Classification.UNMAPPABLE,  # CONVENTION_DIVERGENCES
}

#: Expected `Variant` for every passthrough row in this family (E4/E7).
EXPECTED_VARIANTS: dict[str, Variant] = {
    "str ILIKE pattern": Variant.BOOL,
    "DISTINCT aggregate modifier": Variant.NUMBER_AGGREGATE,
}


def test_row_count_for_this_family():
    ours = [c for c in CATALOG.values() if c.spec_name in EXPECTED]
    assert len(ours) == 33


def test_classifications():
    for name, expected in EXPECTED.items():
        assert CATALOG[name].classification is expected, name


def test_direct_count():
    direct_names = {n for n, c in EXPECTED.items() if c is Classification.DIRECT}
    assert len(direct_names) == 30


def test_passthrough_count_and_variants():
    passthrough_names = {n for n, c in EXPECTED.items() if c is Classification.PASSTHROUGH}
    assert len(passthrough_names) == 2
    assert passthrough_names == set(EXPECTED_VARIANTS)
    for name, variant in EXPECTED_VARIANTS.items():
        assert CATALOG[name].variant is variant, name


def test_unmappable_count():
    unmappable_names = {n for n, c in EXPECTED.items() if c is Classification.UNMAPPABLE}
    assert unmappable_names == {"EXISTS_IN()"}


# --------------------------------------------------------------------------
# Rows the document only explains via its surrounding prose.
# --------------------------------------------------------------------------

def test_exists_in_is_unmappable_with_no_template_and_no_variant():
    # The single unmappable row in the entire 146-row catalog. Named at :131
    # as the sanctioned way to filter on a subquery, but defined nowhere in
    # the specification - no signature, no argument order, no semantics -
    # so there is nothing to translate, let alone compose.
    row = CATALOG["EXISTS_IN()"]
    assert row.classification is Classification.UNMAPPABLE
    assert row.template is None
    assert row.variant is None


def test_ilike_is_passthrough_because_case_fold_has_no_native_form():
    # Case-insensitive matching has no native form, and the usual workaround
    # (fold both sides with `lower`) is itself a passthrough - there is
    # nothing native to compose from.
    row = CATALOG["str ILIKE pattern"]
    assert row.classification is Classification.PASSTHROUGH
    assert row.variant is Variant.BOOL


def test_like_stays_direct_despite_no_native_starts_with_ends_with():
    # Unlike ILIKE, LIKE's prefix/suffix/contains compositions use only
    # native functions (strpos/substr/contains), so rule E2 keeps it direct.
    row = CATALOG["str LIKE pattern"]
    assert row.classification is Classification.DIRECT


def test_distinct_modifier_is_passthrough_except_count_distinct():
    # ThoughtSpot has exactly one distinct-aware aggregate - unique count,
    # i.e. COUNT(DISTINCT) - which already has its own catalog row. Every
    # other DISTINCT aggregate (e.g. SUM(DISTINCT ...)) is a passthrough.
    row = CATALOG["DISTINCT aggregate modifier"]
    assert row.classification is Classification.PASSTHROUGH
    assert row.variant is Variant.NUMBER_AGGREGATE
    assert "sql_number_aggregate_op (" not in row.template


def test_both_case_forms_are_direct_with_a_mandatory_typed_else():
    # No native CASE; both forms compose as an `else if` chain. The final
    # `else` is mandatory and must be type-matched - omitting it raises
    # "Unknown data type" at import.
    searched = CATALOG["CASE WHEN"]
    simple = CATALOG["CASE expr WHEN v1 THEN r1 ... END (simple)"]
    assert searched.classification is Classification.DIRECT
    assert simple.classification is Classification.DIRECT
    assert "else if" in searched.template
    assert "else if" in simple.template


def test_in_and_not_in_use_the_curly_brace_list_form():
    # Live-verified 2026-07-29: the round-paren form is rejected. The
    # curly-brace delimiter is the confirmed syntax.
    in_row = CATALOG["IN"]
    not_in_row = CATALOG["NOT IN"]
    assert in_row.classification is Classification.DIRECT
    assert not_in_row.classification is Classification.DIRECT
    assert "{" in in_row.template and "}" in in_row.template
    assert "not (" in not_in_row.template


def test_is_distinct_from_tests_both_null_before_either_null():
    # No native null-safe comparison, but the three-case truth table is
    # exactly expressible. The nesting order matters: both-null must be
    # tested before either-null, or the either-null branch would also catch
    # the both-null case first.
    row = CATALOG["IS DISTINCT FROM"]
    assert row.classification is Classification.DIRECT
    both_null_idx = row.template.index("and")
    either_null_idx = row.template.index("or")
    assert both_null_idx < either_null_idx


def test_not_expr_is_a_function_form_not_a_prefix_operator():
    # "not [x]" does not parse; NOT is a function call with parentheses.
    row = CATALOG["NOT expr"]
    assert row.classification is Classification.DIRECT
    assert row.template.startswith("not (")


def test_concatenation_operator_and_function_share_one_target():
    # ThoughtSpot has no concatenation operator at all - `+` is numeric-only
    # - so `||` and CONCAT(...) both land on concat ( ).
    row = CATALOG["str1 || str2"]
    assert row.classification is Classification.DIRECT
    assert row.template.startswith("concat (")


def test_boolean_literals_row_is_the_merged_true_false_entry():
    # The Boolean Functions table's Syntax cell merges TRUE and FALSE into
    # one comma-joined entry, matching what spec_construct_names() extracts -
    # not two separate catalog rows.
    row = CATALOG["TRUE, FALSE"]
    assert row.classification is Classification.DIRECT
    assert "true" in row.template and "false" in row.template
