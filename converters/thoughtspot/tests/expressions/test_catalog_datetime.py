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

"""Catalog coverage: Date/time functions.

Source: the `Date/time functions` section of
docs/ossie/ts-ossie-function-mapping.md (thoughtspot-agent-skills repo, not
vendored here). 24 rows total — 17 direct / 7 passthrough / 0 unmappable.

Construct names are spelled exactly as `spec_construct_names()` extracts them
from the UPSTREAM core-spec/expression_language.md (see catalog.py's module
docstring, "Spelling" section) — several diverge from the mapping document's
own row header:

- The three "current" rows keep the spec's own joining word, "or": e.g.
  `"CURRENT_DATE or CURRENT_DATE()"`, not `CURRENT_DATE` / `CURRENT_DATE()`.
- `EXTRACT` and `DATE_PART` are bare tokens (from the "Alternative Extraction
  Syntax" code fence), not `EXTRACT(part FROM date_expr)` or
  `DATE_PART('part', date_expr)`.
- The typed-literal and EXPERIMENTAL rows match only the backticked portion of
  the mapping document's row header — the trailing `(typed literal)` /
  `(EXPERIMENTAL)` annotation sits outside the backtick span and is not part
  of the key: `"DATE '2024-01-15'"`, `"TIMESTAMP_NTZ '2024-01-15 10:30:00'"`,
  `"TIME '10:30:00'"`, `"TO_DATE(string, format)"`,
  `"TO_TIMESTAMP(string, format)"`, `"TO_CHAR(date_expr, format)"`.
"""
from ossie_thoughtspot.expressions import CATALOG
from ossie_thoughtspot.expressions._types import Classification, Variant

EXPECTED: dict[str, Classification] = {
    # -- Current date/time (3 rows: 3 direct) ----------------------------------
    "CURRENT_DATE or CURRENT_DATE()": Classification.DIRECT,
    "CURRENT_TIMESTAMP or CURRENT_TIMESTAMP()": Classification.DIRECT,
    "CURRENT_TIME or CURRENT_TIME()": Classification.DIRECT,
    # -- Date/time extraction (8 rows: 6 direct / 2 passthrough) ---------------
    "YEAR(date_expr)": Classification.DIRECT,
    "QUARTER(date_expr)": Classification.DIRECT,
    "MONTH(date_expr)": Classification.DIRECT,
    "DAY(date_expr)": Classification.DIRECT,
    "DAYOFYEAR(date_expr)": Classification.DIRECT,
    "HOUR(timestamp_expr)": Classification.DIRECT,
    "MINUTE(timestamp_expr)": Classification.PASSTHROUGH,
    "SECOND(timestamp_expr)": Classification.PASSTHROUGH,
    # -- Alternative extraction syntax (2 rows: 2 direct) -----------------------
    "EXTRACT": Classification.DIRECT,
    "DATE_PART": Classification.DIRECT,
    # -- Truncation and arithmetic (3 rows: 3 direct) ---------------------------
    "DATE_TRUNC(part, date_expr)": Classification.DIRECT,
    "DATEADD(part, amount, date_expr)": Classification.DIRECT,
    "DATEDIFF(part, start_date, end_date)": Classification.DIRECT,
    # -- Construction: typed literals (3 rows: 1 direct / 2 passthrough) -------
    "DATE '2024-01-15'": Classification.DIRECT,
    "TIMESTAMP_NTZ '2024-01-15 10:30:00'": Classification.PASSTHROUGH,
    "TIME '10:30:00'": Classification.PASSTHROUGH,
    # -- Construction: parse functions (2 rows: 1 direct / 1 passthrough) ------
    "TO_DATE(string)": Classification.DIRECT,
    "TO_TIMESTAMP(string)": Classification.PASSTHROUGH,
    # -- Construction from format strings, EXPERIMENTAL (2 rows: 1 direct / 1 passthrough) --
    "TO_DATE(string, format)": Classification.DIRECT,
    "TO_TIMESTAMP(string, format)": Classification.PASSTHROUGH,
    # -- Formatting, EXPERIMENTAL (1 row: 1 passthrough) ------------------------
    "TO_CHAR(date_expr, format)": Classification.PASSTHROUGH,
}

#: Expected `Variant` for every passthrough row in this family (E4/E7). Getting
#: this wrong is the failure mode with no safety net: the wrong variant emits a
#: column that imports cleanly and aggregates wrongly, and nothing downstream
#: catches it. Taken individually from the document, not inferred.
EXPECTED_VARIANTS: dict[str, Variant] = {
    "MINUTE(timestamp_expr)": Variant.INT,
    "SECOND(timestamp_expr)": Variant.INT,
    "TIMESTAMP_NTZ '2024-01-15 10:30:00'": Variant.DATE_TIME,
    "TIME '10:30:00'": Variant.DATE_TIME,
    "TO_TIMESTAMP(string)": Variant.DATE_TIME,
    "TO_TIMESTAMP(string, format)": Variant.DATE_TIME,
    "TO_CHAR(date_expr, format)": Variant.STRING,
}


def test_row_count_for_this_family():
    ours = [c for c in CATALOG.values() if c.spec_name in EXPECTED]
    assert len(ours) == 24


def test_classifications():
    for name, expected in EXPECTED.items():
        assert CATALOG[name].classification is expected, name


def test_passthrough_count_and_variants():
    passthrough_names = {n for n, c in EXPECTED.items() if c is Classification.PASSTHROUGH}
    assert len(passthrough_names) == 7
    assert passthrough_names == set(EXPECTED_VARIANTS)
    for name, variant in EXPECTED_VARIANTS.items():
        assert CATALOG[name].variant is variant, name


def test_direct_count():
    direct_names = {n for n, c in EXPECTED.items() if c is Classification.DIRECT}
    assert len(direct_names) == 17


def test_no_unmappable_rows_in_this_family():
    assert not any(c is Classification.UNMAPPABLE for c in EXPECTED.values())


# --------------------------------------------------------------------------
# Rows the document only explains via its surrounding prose.
# --------------------------------------------------------------------------

def test_month_uses_month_number_not_month_name():
    # ThoughtSpot's month() returns the month NAME ("January"); month_number()
    # returns 1-12, which is what the specification's MONTH(date_expr) means.
    # Mapping to month() would silently change the column's type.
    row = CATALOG["MONTH(date_expr)"]
    assert row.template == "month_number ( {0} )"


def test_quarter_uses_quarter_number_not_quarter():
    row = CATALOG["QUARTER(date_expr)"]
    assert row.template == "quarter_number ( {0} )"


def test_hour_uses_hour_of_day_not_hour():
    row = CATALOG["HOUR(timestamp_expr)"]
    assert row.template == "hour_of_day ( {0} )"


def test_dayofyear_uses_day_number_of_year_not_day_of_year():
    row = CATALOG["DAYOFYEAR(date_expr)"]
    assert row.template == "day_number_of_year ( {0} )"


def test_current_time_is_a_composition_of_time_and_now():
    # ThoughtSpot has no current-time function; time ( now ( ) ) is exact (E2).
    row = CATALOG["CURRENT_TIME or CURRENT_TIME()"]
    assert row.template == "time ( now ( ) )"


def test_no_native_minute_or_second_extractor():
    # There is no MINUTE/SECOND-of-hour extractor in ThoughtSpot at all —
    # add_minutes/diff_minutes exist but neither extracts.
    minute = CATALOG["MINUTE(timestamp_expr)"]
    second = CATALOG["SECOND(timestamp_expr)"]
    assert minute.classification is Classification.PASSTHROUGH
    assert second.classification is Classification.PASSTHROUGH
    assert minute.variant is Variant.INT
    assert second.variant is Variant.INT


def test_extract_and_date_part_collapse_to_the_same_rewrite():
    # The two spellings are identical treatment per the specification.
    extract = CATALOG["EXTRACT"]
    date_part = CATALOG["DATE_PART"]
    assert extract.classification is Classification.DIRECT
    assert date_part.classification is Classification.DIRECT
    assert extract.template == date_part.template


def test_no_native_date_trunc():
    # ThoughtSpot has no date_trunc; the row is still DIRECT because the
    # start_of_* family covers 7 of 8 precisions (only 'second' falls back).
    row = CATALOG["DATE_TRUNC(part, date_expr)"]
    assert row.classification is Classification.DIRECT
    assert "date_trunc" not in row.template.lower()


def test_dateadd_argument_order_is_documented_as_reversed_from_thoughtspot():
    # ThoughtSpot is add_days ( [d] , n ); the specification is
    # DATEADD(day, n, d). Getting this backwards is a silent-wrong-answer bug.
    row = CATALOG["DATEADD(part, amount, date_expr)"]
    assert row.classification is Classification.DIRECT
    assert "argument order" in row.note.lower()


def test_datediff_argument_order_is_documented_as_reversed():
    # ThoughtSpot is diff_days ( [end] , [start] ) - end first. Getting this
    # wrong silently negates every duration in the model.
    row = CATALOG["DATEDIFF(part, start_date, end_date)"]
    assert row.classification is Classification.DIRECT
    assert "argument order" in row.note.lower()
    assert "end" in row.note.lower()


def test_date_literal_must_be_wrapped_in_to_date():
    # A bare '2024-01-15' in a ThoughtSpot formula parses as arithmetic
    # (2024 - 1 - 15), so the typed literal must always be wrapped.
    row = CATALOG["DATE '2024-01-15'"]
    assert row.classification is Classification.DIRECT
    assert "to_date" in row.template.lower()


def test_timestamp_ntz_and_time_literals_have_no_native_construction():
    # to_date() returns a DATE and drops the time part, so there is no native
    # way to construct a wall-clock TIMESTAMP or a TIME value.
    timestamp_ntz = CATALOG["TIMESTAMP_NTZ '2024-01-15 10:30:00'"]
    time_literal = CATALOG["TIME '10:30:00'"]
    assert timestamp_ntz.classification is Classification.PASSTHROUGH
    assert time_literal.classification is Classification.PASSTHROUGH
    assert timestamp_ntz.variant is Variant.DATE_TIME
    assert time_literal.variant is Variant.DATE_TIME


def test_to_date_single_arg_supplies_the_iso_format_model():
    # ThoughtSpot's to_date is strictly two-argument; the converter supplies
    # 'yyyy-MM-dd' for the specification's single-argument ISO form.
    row = CATALOG["TO_DATE(string)"]
    assert row.classification is Classification.DIRECT
    assert "yyyy-mm-dd" in row.template.lower()


def test_to_timestamp_single_arg_is_passthrough_because_to_date_drops_time():
    row = CATALOG["TO_TIMESTAMP(string)"]
    assert row.classification is Classification.PASSTHROUGH
    assert row.variant is Variant.DATE_TIME


def test_to_char_prefers_no_native_general_formatter():
    # ThoughtSpot has no general date formatter; single-token formats (YYYY,
    # MONTH, DAY) do have native equivalents the converter should prefer, but
    # the general TO_CHAR(date_expr, format) row itself is passthrough.
    row = CATALOG["TO_CHAR(date_expr, format)"]
    assert row.classification is Classification.PASSTHROUGH
    assert row.variant is Variant.STRING
