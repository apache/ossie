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

"""Catalog coverage: Aggregate functions + Type conversion.

Source: the `Aggregate functions` and `Type conversion` sections of
docs/ossie/ts-ossie-function-mapping.md (thoughtspot-agent-skills repo, not
vendored here). 20 rows total — 14 direct / 6 passthrough / 0 unmappable.

Construct names are spelled exactly as `spec_construct_names()` extracts them
(see catalog.py's module docstring, "Spelling" section) — in particular `CAST`
and `TRY_CAST`, not `CAST(expression AS target_type)` / `TRY_CAST(expression AS
target_type)`, which is how the mapping document's own row headers write them.
"""
from ossie_thoughtspot.expressions import CATALOG
from ossie_thoughtspot.expressions._types import Classification, Variant

EXPECTED: dict[str, Classification] = {
    # -- Aggregate functions (18 rows: 12 direct / 6 passthrough) --------------
    "SUM(expr)": Classification.DIRECT,
    "COUNT(expr)": Classification.DIRECT,
    "COUNT(*)": Classification.DIRECT,
    "COUNT(DISTINCT expr)": Classification.DIRECT,
    "AVG(expr)": Classification.DIRECT,
    "MIN(expr)": Classification.DIRECT,
    "MAX(expr)": Classification.DIRECT,
    "STDDEV(expr)": Classification.DIRECT,
    "STDDEV_POP(expr)": Classification.PASSTHROUGH,
    "STDDEV_SAMP(expr)": Classification.DIRECT,
    "VARIANCE(expr)": Classification.DIRECT,
    "VAR_POP(expr)": Classification.PASSTHROUGH,
    "VAR_SAMP(expr)": Classification.DIRECT,
    "MEDIAN(expr)": Classification.DIRECT,
    "PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY expr)": Classification.PASSTHROUGH,
    "PERCENTILE_DISC(p) WITHIN GROUP (ORDER BY expr)": Classification.PASSTHROUGH,
    "APPROX_COUNT_DISTINCT(expr)": Classification.PASSTHROUGH,
    "APPROX_PERCENTILE(expr, p)": Classification.PASSTHROUGH,
    # -- Type conversion (2 rows: 2 direct) ------------------------------------
    "CAST": Classification.DIRECT,
    "TRY_CAST": Classification.DIRECT,
}

#: Expected `Variant` for every passthrough row in this family. Getting
#: this wrong is the failure mode with no safety net: the wrong variant emits a
#: column that imports cleanly and aggregates wrongly, and nothing downstream
#: catches it.
EXPECTED_VARIANTS: dict[str, Variant] = {
    "STDDEV_POP(expr)": Variant.NUMBER_AGGREGATE,
    "VAR_POP(expr)": Variant.NUMBER_AGGREGATE,
    "PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY expr)": Variant.NUMBER_AGGREGATE,
    "PERCENTILE_DISC(p) WITHIN GROUP (ORDER BY expr)": Variant.NUMBER_AGGREGATE,
    "APPROX_COUNT_DISTINCT(expr)": Variant.INT_AGGREGATE,
    "APPROX_PERCENTILE(expr, p)": Variant.NUMBER_AGGREGATE,
}


def test_row_count_for_this_family():
    ours = [c for c in CATALOG.values() if c.spec_name in EXPECTED]
    assert len(ours) == 20


def test_classifications():
    for name, expected in EXPECTED.items():
        assert CATALOG[name].classification is expected, name


def test_passthrough_count_and_variants():
    passthrough_names = {n for n, c in EXPECTED.items() if c is Classification.PASSTHROUGH}
    assert len(passthrough_names) == 6
    assert passthrough_names == set(EXPECTED_VARIANTS)
    for name, variant in EXPECTED_VARIANTS.items():
        assert CATALOG[name].variant is variant, name


def test_direct_count():
    direct_names = {n for n, c in EXPECTED.items() if c is Classification.DIRECT}
    assert len(direct_names) == 14


def test_no_unmappable_rows_in_this_family():
    assert not any(c is Classification.UNMAPPABLE for c in EXPECTED.values())


# --------------------------------------------------------------------------
# Rows the document only explains via its surrounding prose.
# --------------------------------------------------------------------------

def test_count_star_uses_a_non_null_column_not_a_literal_star():
    # ThoughtSpot has no count(*); the row is emitted as count() over a column
    # the converter believes is non-null (the model's declared primary_key).
    row = CATALOG["COUNT(*)"]
    assert row.classification is Classification.DIRECT
    assert "count" in row.template.lower()


def test_count_distinct_uses_a_space_not_an_underscore():
    # count_distinct(...) is rejected by the ThoughtSpot formula parser.
    row = CATALOG["COUNT(DISTINCT expr)"]
    assert "unique count" in row.template
    assert "count_distinct" not in row.template.lower()


def test_stddev_and_variance_samp_aliases_map_to_the_sample_form():
    # STDDEV_SAMP / VAR_SAMP are specification aliases for STDDEV / VARIANCE —
    # both are sample statistics in ThoughtSpot, so both alias rows stay direct.
    assert CATALOG["STDDEV_SAMP(expr)"].template == CATALOG["STDDEV(expr)"].template
    assert CATALOG["VAR_SAMP(expr)"].template == CATALOG["VARIANCE(expr)"].template


def test_population_statistics_are_passthrough_because_ts_stddev_is_sample_only():
    # STDDEV / VARIANCE are sample-only in ThoughtSpot; there is no population
    # form, so STDDEV_POP / VAR_POP cannot reuse the sample-form template.
    assert CATALOG["STDDEV_POP(expr)"].template != CATALOG["STDDEV(expr)"].template
    assert CATALOG["VAR_POP(expr)"].template != CATALOG["VARIANCE(expr)"].template


def test_try_cast_shares_casts_mapping():
    # ThoughtSpot's to_integer/to_double/to_string already return NULL on
    # failure, which is exactly TRY_CAST semantics — so CAST and TRY_CAST
    # share a mapping, and it is CAST that is the imprecise one, not TRY_CAST.
    cast = CATALOG["CAST"]
    try_cast = CATALOG["TRY_CAST"]
    assert cast.classification is Classification.DIRECT
    assert try_cast.classification is Classification.DIRECT
