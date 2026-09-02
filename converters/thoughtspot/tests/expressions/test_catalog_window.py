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

"""Catalog coverage for Task 8: Window functions - the last family, completing the catalog.

Source: the `Window functions` section of docs/ossie/ts-ossie-function-mapping.md
(thoughtspot-agent-skills repo, not vendored here), plus the "Window rows
live-confirmed - 2026-07-30" section that records the 52-probe evidence behind the
classifications. 14 rows total - 5 direct / 9 passthrough / 0 unmappable.

This is the hardest family. Three rules govern it:

- E5 - a raw aggregate cannot be nested inside a ThoughtSpot window function.
- E6 - the ORDER BY column must be a physical column reference, not a formula.
- E13 - a ThoughtSpot window formula cannot declare its own PARTITION BY; the
  partition is always completed from the query's own dimensions. This is why nine
  of fourteen rows are passthrough, and why LAG, LEAD, the OVER clause and window
  aggregation moved direct -> passthrough after 52 live probes on 2026-07-30.
  `rank` / `rank_percentile` have arity fixed at exactly two, proven by rejection
  on a live instance (`Function rank expects only 2 arguments`).

`FIRST_VALUE` / `LAST_VALUE` are the section's one exception: they take a genuine
explicit partition argument and a genuine explicit order axis, so the formula does
define its own window, and they stay direct along with `RANK`, `PERCENT_RANK` and
the frame-clause boundaries (whose native reach is now proven by rejection rather
than asserted).

`PERCENT_RANK` is direct via `rank_percentile`, but `CUME_DIST` is deliberately
NOT: `PERCENT_RANK` divides by n-1 and starts at 0; `CUME_DIST` divides by n and
ends at 1. They agree on no row of a tie-free window except the last, so
`rank_percentile` is not a substitute and `CUME_DIST` stays passthrough with no
native fallback at all.

Three of the family's 14 rows have no discrete row of their own in the upstream
core-spec/expression_language.md - the generic `OVER (...)` syntax template and its
frame-clause bullet list are a fenced code block, and window aggregation is prose -
so they are keyed via `CONVENTION_DIVERGENCES` rather than `spec_construct_names()`:
the `OVER` clause, the frame clause, and window aggregation. The other 11 rows key
on `spec_construct_names()`'s own extraction from the "Ranking Functions" and
"Offset Functions" tables' `Syntax` column - confirmed live before writing this
file.
"""
from ossie_thoughtspot.expressions import CATALOG
from ossie_thoughtspot.expressions._types import Classification, Variant
from ossie_thoughtspot.expressions.emit import emit_direct

EXPECTED: dict[str, Classification] = {
    # Ranking functions (spec_construct_names() extracts the Syntax-column value)
    "ROW_NUMBER() OVER (...)": Classification.PASSTHROUGH,
    "RANK() OVER (...)": Classification.DIRECT,
    "DENSE_RANK() OVER (...)": Classification.PASSTHROUGH,
    "NTILE(n) OVER (...)": Classification.PASSTHROUGH,
    "PERCENT_RANK() OVER (...)": Classification.DIRECT,
    "CUME_DIST() OVER (...)": Classification.PASSTHROUGH,
    # Offset functions
    "LAG(expr, offset, default) OVER (...)": Classification.PASSTHROUGH,
    "LEAD(expr, offset, default) OVER (...)": Classification.PASSTHROUGH,
    "FIRST_VALUE(expr) OVER (...)": Classification.DIRECT,
    "LAST_VALUE(expr) OVER (...)": Classification.DIRECT,
    "NTH_VALUE(expr, n) OVER (...)": Classification.PASSTHROUGH,
    # Structural rows (CONVENTION_DIVERGENCES - no discrete spec table row)
    "OVER (PARTITION BY ... ORDER BY ...) clause": Classification.PASSTHROUGH,
    "Frame clause — ROWS BETWEEN ... / RANGE BETWEEN ...": Classification.DIRECT,
    "Window aggregation — AGG(expr) OVER (...)": Classification.PASSTHROUGH,
}

#: Expected `Variant` for every passthrough row in this family (E4/E7).
EXPECTED_VARIANTS: dict[str, Variant] = {
    "ROW_NUMBER() OVER (...)": Variant.INT_AGGREGATE,
    "DENSE_RANK() OVER (...)": Variant.INT_AGGREGATE,
    "NTILE(n) OVER (...)": Variant.INT_AGGREGATE,
    "CUME_DIST() OVER (...)": Variant.NUMBER_AGGREGATE,
    "LAG(expr, offset, default) OVER (...)": Variant.NUMBER_AGGREGATE,
    "LEAD(expr, offset, default) OVER (...)": Variant.NUMBER_AGGREGATE,
    "NTH_VALUE(expr, n) OVER (...)": Variant.NUMBER_AGGREGATE,
    "OVER (PARTITION BY ... ORDER BY ...) clause": Variant.NUMBER_AGGREGATE,
    "Window aggregation — AGG(expr) OVER (...)": Variant.NUMBER_AGGREGATE,
}


def test_row_count_for_this_family():
    ours = [c for c in CATALOG.values() if c.spec_name in EXPECTED]
    assert len(ours) == 14


def test_classifications():
    for name, expected in EXPECTED.items():
        assert CATALOG[name].classification is expected, name


def test_direct_count():
    direct_names = {n for n, c in EXPECTED.items() if c is Classification.DIRECT}
    assert len(direct_names) == 5


def test_passthrough_count_and_variants():
    passthrough_names = {n for n, c in EXPECTED.items() if c is Classification.PASSTHROUGH}
    assert len(passthrough_names) == 9
    assert passthrough_names == set(EXPECTED_VARIANTS)
    for name, variant in EXPECTED_VARIANTS.items():
        assert CATALOG[name].variant is variant, name


def test_no_unmappable_rows_in_this_family():
    unmappable_names = {n for n, c in EXPECTED.items() if c is Classification.UNMAPPABLE}
    assert unmappable_names == set()


# --------------------------------------------------------------------------
# E13: the rule this family turns on. Locking in the four rows the July rework
# moved off `direct`, and the two structural rows (partition/frame) that are
# NOT swept up by the same reclassification.
# --------------------------------------------------------------------------

def test_the_four_rows_e13_reclassified_are_passthrough_not_direct():
    # LAG, LEAD, the OVER clause and window aggregation all moved direct ->
    # passthrough on 2026-07-30 because a ThoughtSpot window formula cannot
    # declare its own PARTITION BY. A regression here (restoring one to
    # direct because a native idiom exists) would silently reintroduce a
    # formula that only happens to be correct when the search's own
    # dimensions match the intended partition.
    for name in (
        "LAG(expr, offset, default) OVER (...)",
        "LEAD(expr, offset, default) OVER (...)",
        "OVER (PARTITION BY ... ORDER BY ...) clause",
        "Window aggregation — AGG(expr) OVER (...)",
    ):
        assert CATALOG[name].classification is Classification.PASSTHROUGH, name


def test_first_value_and_last_value_are_the_surviving_exception():
    # The only window rows whose direct verdict survived the 2026-07-30 rework:
    # first_value/last_value take a genuine explicit partition AND order axis,
    # so the formula does define its own window.
    for name in ("FIRST_VALUE(expr) OVER (...)", "LAST_VALUE(expr) OVER (...)"):
        row = CATALOG[name]
        assert row.classification is Classification.DIRECT
        assert "query_groups" in row.template


def test_frame_clause_stays_direct_scoped_to_boundaries_only():
    # direct for the frame boundaries alone - the partition loss is counted
    # once, on the OVER clause row, not twice.
    row = CATALOG["Frame clause — ROWS BETWEEN ... / RANGE BETWEEN ..."]
    assert row.classification is Classification.DIRECT


# --------------------------------------------------------------------------
# rank / rank_percentile: arity fixed at exactly two, proven by rejection.
# --------------------------------------------------------------------------

def test_rank_and_percent_rank_carry_no_partition_argument():
    # rank and rank_percentile are both fixed at exactly two arguments
    # (live-confirmed by rejection: "Function rank expects only 2
    # arguments"), so neither template may carry a PARTITION BY - there is no
    # argument slot for one, in any spelling.
    for name in ("RANK() OVER (...)", "PERCENT_RANK() OVER (...)"):
        row = CATALOG[name]
        assert row.classification is Classification.DIRECT
        assert "partition" not in row.template.lower()


def test_percent_rank_is_direct_via_rank_percentile_with_both_adjustments():
    # Two adjustments are both required: the scale (ThoughtSpot 0-100,
    # specification 0-1) and the inversion. Dropping either produces a
    # plausible-looking column that is wrong everywhere.
    row = CATALOG["PERCENT_RANK() OVER (...)"]
    assert "rank_percentile" in row.template
    assert "100" in row.template
    assert row.template.strip().startswith("1 -")


def test_cume_dist_is_not_substituted_by_rank_percentile():
    # The document is explicit that this is NOT a permissible substitution:
    # PERCENT_RANK divides by n-1 and starts at 0; CUME_DIST divides by n and
    # ends at 1. Guards against "restoring" this row because the two names
    # look equivalent.
    row = CATALOG["CUME_DIST() OVER (...)"]
    assert row.classification is Classification.PASSTHROUGH
    assert "rank_percentile" not in row.template


# --------------------------------------------------------------------------
# E8: which templates carry PARTITION BY and therefore require partition_column
# at emission time. Exactly the rows the document gives a PARTITION BY clause to.
# --------------------------------------------------------------------------

def test_only_the_documented_rows_carry_partition_by_for_e8():
    partitioned = {
        "ROW_NUMBER() OVER (...)",
        "LAG(expr, offset, default) OVER (...)",
        "LEAD(expr, offset, default) OVER (...)",
        "Window aggregation — AGG(expr) OVER (...)",
    }
    for name in EXPECTED_VARIANTS:
        carries = "partition by" in CATALOG[name].template.lower()
        assert carries == (name in partitioned), name


# --------------------------------------------------------------------------
# Brace escaping: FIRST_VALUE/LAST_VALUE are DIRECT rows whose ThoughtSpot
# rendering uses `{ ... }` list syntax for the axis argument. DIRECT templates
# render via str.format (emit_direct), so a literal brace must be doubled or
# the call raises "unexpected '{' in field name" - exactly the Task 7 IN/NOT IN
# bug. This exercises emit_direct directly, not just a substring check on the
# template text, so it would have caught that bug.
# --------------------------------------------------------------------------

def test_first_value_and_last_value_render_with_single_braces():
    for name in ("FIRST_VALUE(expr) OVER (...)", "LAST_VALUE(expr) OVER (...)"):
        row = CATALOG[name]
        rendered = emit_direct(row, [])
        assert "{{" not in rendered and "}}" not in rendered
        assert "{" in rendered and "}" in rendered


def test_every_direct_row_in_this_family_has_natural_arity_zero():
    # Every direct row in this family records the document's own worked
    # example (symbolic bracket names like [m]/[dim]/[ord]/[attr]/[T::date]),
    # not a numbered {0}/{1} substitution slot - the same out-of-scope-dispatch
    # treatment as CASE WHEN's c1/r1 names and CAST's per-type table. So each
    # renders with zero arguments.
    for name, classification in EXPECTED.items():
        if classification is not Classification.DIRECT:
            continue
        row = CATALOG[name]
        assert emit_direct(row, []) == row.template.replace("{{", "{").replace("}}", "}")
