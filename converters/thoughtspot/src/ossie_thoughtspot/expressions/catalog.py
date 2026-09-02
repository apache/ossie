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

"""The catalog: every specification construct mapped to a ThoughtSpot rendering.

`CATALOG` is populated across Tasks 3-8 of the expression-translation plan, one
family per task; Task 3 (Aggregate functions + Type conversion) is the first.
Until Task 8 lands the rest, `spec_construct_names()` — an oracle read from the
**upstream** `core-spec/expression_language.md`, not from any document of our
own — still reports every construct `CATALOG` has not yet covered, so that a
construct added upstream fails this package's build instead of silently going
unsupported (see `test_catalog_covers_the_spec.py`).

Extraction approach
--------------------
The spec document mixes three kinds of content that must be told apart:

1. Genuine constructs — a named function, operator or literal form the
   specification defines, almost always as one row of a markdown table whose
   row carries a `Syntax` column (e.g. `SUM(expr)`), or, for a handful of
   operators/keywords with no table of their own, a row of the top-level
   "Supported SQL Constructs" table.
2. Argument vocabularies (rule E1) — the `EXTRACT`/`DATE_PART` parts, the
   `DATE_TRUNC` precisions, the `TO_DATE`/`TO_CHAR` format tokens and the
   `CAST` target types. These describe values an argument may take, not
   constructs in their own right, and must be excluded.
3. Informative tables — the per-engine "Common Dialect Variations" table and
   the "Cross-Reference: Tool Mappings" section describe *other products'*
   spellings (Tableau, Looker Studio, DAX, and per-engine SQL). Names that
   appear only there are not Ossie constructs.

The exclusions are keyed off the document's own structure — a table's own
header naming ("Token" columns, a "Form" cell reading "Cast") and the section
heading text ("Not Supported in Expressions", "Common Dialect Variations",
"Cross-Reference") — rather than a hardcoded list of names to drop. A hardcoded
list would go stale the moment upstream renamed or added a construct, which is
exactly the failure mode this gate exists to catch. The `EXTRACT`/`DATE_PART`
date-part list, the `DATE_TRUNC` precision list and the `CAST` target-type list
need no such marker at all: `_extract_tables()` only ever looks at lines
starting with "|", so a plain bullet list is simply invisible to it, argument
vocabulary or not.

Spelling: `CATALOG` keys must match `spec_construct_names()` exactly (READ THIS
BEFORE TASKS 3-8, AND WHEN IN DOUBT DO NOT TRUST THIS LIST FROM MEMORY)
------------------------------------------------------------------------------
`spec_construct_names()` is the oracle, not the mapping document's prose, and not
this list. Several rows write their Ossie-side syntax differently than this
parser extracts it, and a `CATALOG` entry keyed on the mapping document's own
wording — not this function's output — will read as an "invented" construct
even though it is a real, intended row. **The authoritative check is always:
run `spec_construct_names()`, print it, and match a member of it exactly** —
this list is a convenience audited against that output, not a substitute for
it, and a previous version of this list both omitted a case and misattributed
another's source (both listed below, corrected). If this list and a live run
of `spec_construct_names()` ever disagree, the live run wins.

Grouped by which extractor produces the divergent spelling, so the source is
never ambiguous:

- **`_extract_tables()`** (an ordinary table with a `Syntax` column — the key is
  that column's value, not the mapping document's `Ossie`-column header):
    - Alias pairs the spec merges into ONE table row keep this parser's single
      extracted spelling: `CEIL(x)` (not `CEIL(x) / CEILING(x)`), `TRUNC(x, d)`
      (not `.../ TRUNCATE(x, d)`).
    - Two-alternative-syntax rows keep the spec's own joining word, "or" — not
      the mapping document's "/": `"CURRENT_DATE or CURRENT_DATE()"`,
      `"CURRENT_TIMESTAMP or CURRENT_TIMESTAMP()"`, `"CURRENT_TIME or
      CURRENT_TIME()"`.
    - The merged boolean-literal row (`BOOLEAN`'s `Syntax` cell) is one entry,
      comma-joined: `"TRUE, FALSE"` (mapping document header: `` `TRUE` /
      `FALSE` (boolean literals) ``).
    - The Boolean Functions table's `AND`/`OR` rows keep the spec's own
      `expr1`/`expr2` placeholder names, not the mapping document's `a`/`b`:
      `"expr1 AND expr2"` (mapping document header: `` `a AND b` ``),
      `"expr1 OR expr2"` (mapping document header: `` `a OR b` ``).
- **`_extract_summary_rows()`** (the top-level "Supported SQL Constructs"
  table, bare backtick token — not the mapping document's `a`/`b`/`x`-style
  worked example): `BETWEEN`, `IN`, `NOT IN`, `IS NULL`, `IS NOT NULL`, `CASE
  WHEN`, and the raw symbols `+ - * / % = <> != < > <= >=`.
- **`_extract_null_safe_comparison_operators()`** (the "Null-Safe Comparison"
  code fence — NOT the summary table, despite reading like one more row of it):
  `IS DISTINCT FROM`, `IS NOT DISTINCT FROM`.
- **`_extract_extraction_syntax_functions()`** (the "Alternative Extraction
  Syntax" code fence, bare token, no argument list): `EXTRACT`, `DATE_PART`.
- **`_extract_single_construct_headings()`** (a standalone heading with no
  table, bare token): `CAST`, `TRY_CAST` (not `CAST(expression AS
  target_type)`).

When adding a row, cross-check its key against `spec_construct_names()`'s output
rather than transcribing the mapping document's column text verbatim. See
`CONVENTION_DIVERGENCES` below for the (much shorter) list of constructs that
have no entry in `spec_construct_names()` at all and are exempted instead.
"""
import re
from pathlib import Path

from ._types import Classification, Construct, Variant

# --------------------------------------------------------------------------
# CATALOG: populated across Tasks 3-8, one family per task.
# --------------------------------------------------------------------------
CATALOG: dict[str, Construct] = {}

# --------------------------------------------------------------------------
# Aggregate functions (Task 3) — 18 rows: 12 direct / 6 passthrough / 0 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Aggregate functions" section
# (thoughtspot-agent-skills repo — not vendored here; prose above/below the table
# read in full, per rule E1-E4).
# --------------------------------------------------------------------------
CATALOG.update(
    {
        "SUM(expr)": Construct(
            "SUM(expr)", Classification.DIRECT, template="sum ( {0} )",
        ),
        "COUNT(expr)": Construct(
            "COUNT(expr)", Classification.DIRECT, template="count ( {0} )",
            note="Counts non-null values on both sides.",
        ),
        "COUNT(*)": Construct(
            "COUNT(*)", Classification.DIRECT, template="count ( {0} )",
            note=(
                "ThoughtSpot has no count(*); the row count is count() over a column "
                "known to be non-null. The converter uses the dataset's primary_key "
                "when the model declares one, and raises an issue rather than "
                "guessing a column when it does not."
            ),
        ),
        "COUNT(DISTINCT expr)": Construct(
            "COUNT(DISTINCT expr)", Classification.DIRECT, template="unique count ( {0} )",
            note=(
                "A space, not an underscore. count_distinct(...) is rejected by the "
                "formula parser. See ask A9 on DISTINCT as a general modifier."
            ),
        ),
        "AVG(expr)": Construct(
            "AVG(expr)", Classification.DIRECT, template="average ( {0} )",
        ),
        "MIN(expr)": Construct(
            "MIN(expr)", Classification.DIRECT, template="min ( {0} )",
            note=(
                "ThoughtSpot min is aggregate-only — it never compares two columns "
                "row-wise. Scalar two-argument minima are LEAST, a separate row."
            ),
        ),
        "MAX(expr)": Construct(
            "MAX(expr)", Classification.DIRECT, template="max ( {0} )",
            note="Aggregate-only, as MIN.",
        ),
        "STDDEV(expr)": Construct(
            "STDDEV(expr)", Classification.DIRECT, template="stddev ( {0} )",
            note="Sample standard deviation on both sides.",
        ),
        "STDDEV_POP(expr)": Construct(
            "STDDEV_POP(expr)", Classification.PASSTHROUGH,
            template="STDDEV_POP({0})", variant=Variant.NUMBER_AGGREGATE,
            note=(
                "ThoughtSpot stddev is sample-only; there is no population form, and "
                "substituting it would change the divisor from n-1 to n."
            ),
        ),
        "STDDEV_SAMP(expr)": Construct(
            "STDDEV_SAMP(expr)", Classification.DIRECT, template="stddev ( {0} )",
            note="Specification alias for STDDEV (:171).",
        ),
        "VARIANCE(expr)": Construct(
            "VARIANCE(expr)", Classification.DIRECT, template="variance ( {0} )",
            note="Sample variance on both sides.",
        ),
        "VAR_POP(expr)": Construct(
            "VAR_POP(expr)", Classification.PASSTHROUGH,
            template="VAR_POP({0})", variant=Variant.NUMBER_AGGREGATE,
            note="Same divisor reason as STDDEV_POP.",
        ),
        "VAR_SAMP(expr)": Construct(
            "VAR_SAMP(expr)", Classification.DIRECT, template="variance ( {0} )",
            note="Specification alias for VARIANCE (:174).",
        ),
        "MEDIAN(expr)": Construct(
            "MEDIAN(expr)", Classification.DIRECT, template="median ( {0} )",
        ),
        "PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY expr)": Construct(
            "PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY expr)", Classification.PASSTHROUGH,
            template="PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY {0})",
            variant=Variant.NUMBER_AGGREGATE,
            note=(
                "No native percentile function. p is a literal in the specification's "
                "syntax, so it is baked into the template rather than passed as a "
                "placeholder. p = 0.5 is the one case with a native equivalent — "
                "median ( [x] ) — and the converter should prefer it."
            ),
        ),
        "PERCENTILE_DISC(p) WITHIN GROUP (ORDER BY expr)": Construct(
            "PERCENTILE_DISC(p) WITHIN GROUP (ORDER BY expr)", Classification.PASSTHROUGH,
            template="PERCENTILE_DISC(0.75) WITHIN GROUP (ORDER BY {0})",
            variant=Variant.NUMBER_AGGREGATE,
            note=(
                "As PERCENTILE_CONT; the discrete/interpolated distinction is "
                "preserved only because the template is emitted verbatim."
            ),
        ),
        "APPROX_COUNT_DISTINCT(expr)": Construct(
            "APPROX_COUNT_DISTINCT(expr)", Classification.PASSTHROUGH,
            template="APPROX_COUNT_DISTINCT({0})", variant=Variant.INT_AGGREGATE,
            note=(
                "ThoughtSpot's unique count ( [x] ) is the exact-semantics "
                "alternative: same answer to within the sketch's ~2% error, at "
                "exact-count cost. The converter emits the pass-through by default "
                "— the specification chose approximate deliberately — and offers "
                "the exact form as a documented downgrade."
            ),
        ),
        "APPROX_PERCENTILE(expr, p)": Construct(
            "APPROX_PERCENTILE(expr, p)", Classification.PASSTHROUGH,
            template="APPROX_PERCENTILE({0}, 0.5)", variant=Variant.NUMBER_AGGREGATE,
            note="p baked into the template as for the exact percentiles.",
        ),
    }
)

# --------------------------------------------------------------------------
# Type conversion (Task 3) — 2 rows: 2 direct / 0 passthrough / 0 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Type conversion" section.
#
# CAST/TRY_CAST are, per rule E3, direct rows whose target-type argument
# vocabulary is only partly covered (5 of 8 types direct, 3 fall back to a
# pass-through) — the per-type dispatch is not counted as its own construct
# (rule E1: the target-type table is an argument vocabulary, marked "not
# counted" in the mapping document) and is not resolved here. Resolving a
# `CAST` occurrence to an actual formula from its target type is out of this
# plan's scope (see task-9-brief.md, "The expression parser and the sqlglot
# question"); `template` records the document's own ThoughtSpot-column text
# for traceability rather than a directly-substitutable formula.
# --------------------------------------------------------------------------
CATALOG.update(
    {
        "CAST": Construct(
            "CAST", Classification.DIRECT,
            template="per-type — see the target-type table below",
            note=(
                "5 of the 8 specified target types are direct; the other three — "
                "BOOLEAN, TIMESTAMP and TIME — fall back to a pass-through (E3)."
            ),
        ),
        "TRY_CAST": Construct(
            "TRY_CAST", Classification.DIRECT,
            template="the same functions as CAST",
            note=(
                "ThoughtSpot's to_integer / to_double / to_string already return "
                "NULL on failure, which is exactly TRY_CAST semantics — so the two "
                "rows share a mapping and it is CAST, not TRY_CAST, that is the "
                "imprecise one. A strict CAST that must error rather than null is "
                "not expressible; the converter records that in the issue log when "
                "the source distinguishes them."
            ),
        ),
    }
)

# --------------------------------------------------------------------------
# Date/time functions (Task 4) — 24 rows: 17 direct / 7 passthrough / 0 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Date/time functions" section
# (thoughtspot-agent-skills repo — not vendored here; prose above/below the table
# read in full, per rule E1-E4).
#
# EXTRACT/DATE_PART date-parts, DATE_TRUNC precisions, DATEADD/DATEDIFF parts and
# TO_DATE/TO_CHAR format tokens are argument vocabularies under rule E1 and get no
# entry of their own (see the mapping document's "(not counted — arguments)"
# sub-tables). EXTRACT, DATE_PART, DATE_TRUNC(part, date_expr),
# DATEADD(part, amount, date_expr) and DATEDIFF(part, start_date, end_date) are
# themselves still DIRECT rows in the 24 — the per-argument dispatch happens for
# each, but the dispatch table itself is out of catalog scope (same pattern as
# Task 3's CAST/TRY_CAST): `template` records the mapping document's own
# ThoughtSpot-column text for traceability, and the real per-argument content
# (which native function each part/precision rewrites to, and the argument-order
# caveats) is recorded in `note`.
# --------------------------------------------------------------------------
CATALOG.update(
    {
        "CURRENT_DATE or CURRENT_DATE()": Construct(
            "CURRENT_DATE or CURRENT_DATE()", Classification.DIRECT, template="today ( )",
            note="Both specification spellings map to the same function.",
        ),
        "CURRENT_TIMESTAMP or CURRENT_TIMESTAMP()": Construct(
            "CURRENT_TIMESTAMP or CURRENT_TIMESTAMP()", Classification.DIRECT, template="now ( )",
        ),
        "CURRENT_TIME or CURRENT_TIME()": Construct(
            "CURRENT_TIME or CURRENT_TIME()", Classification.DIRECT,
            template="time ( now ( ) )",
            note=(
                "ThoughtSpot has no current-time function, but time ( ) extracts "
                "the time part of a datetime, so the composition is exact (E2)."
            ),
        ),
        "YEAR(date_expr)": Construct(
            "YEAR(date_expr)", Classification.DIRECT, template="year ( {0} )",
        ),
        "QUARTER(date_expr)": Construct(
            "QUARTER(date_expr)", Classification.DIRECT, template="quarter_number ( {0} )",
            note="The function is quarter_number, not quarter.",
        ),
        "MONTH(date_expr)": Construct(
            "MONTH(date_expr)", Classification.DIRECT, template="month_number ( {0} )",
            note=(
                "Not month ( ) — ThoughtSpot's month returns the month NAME "
                "('January'); month_number returns 1-12, which is what the "
                "specification means. Mapping to month would silently change "
                "the column's type from integer to string."
            ),
        ),
        "DAY(date_expr)": Construct(
            "DAY(date_expr)", Classification.DIRECT, template="day ( {0} )",
            note="Day of month, 1-31 on both sides.",
        ),
        "DAYOFYEAR(date_expr)": Construct(
            "DAYOFYEAR(date_expr)", Classification.DIRECT,
            template="day_number_of_year ( {0} )",
            note="The function is day_number_of_year, not day_of_year.",
        ),
        "HOUR(timestamp_expr)": Construct(
            "HOUR(timestamp_expr)", Classification.DIRECT, template="hour_of_day ( {0} )",
            note="The function is hour_of_day, not hour.",
        ),
        "MINUTE(timestamp_expr)": Construct(
            "MINUTE(timestamp_expr)", Classification.PASSTHROUGH,
            template="MINUTE({0})", variant=Variant.INT,
            note=(
                "No native minute-of-hour extractor; add_minutes and "
                "diff_minutes exist but neither extracts."
            ),
        ),
        "SECOND(timestamp_expr)": Construct(
            "SECOND(timestamp_expr)", Classification.PASSTHROUGH,
            template="SECOND({0})", variant=Variant.INT,
            note="As MINUTE.",
        ),
        "EXTRACT": Construct(
            "EXTRACT", Classification.DIRECT,
            template="per-part — see the date-part table below",
            note=(
                "Rewritten to the part's own ThoughtSpot function; there is no "
                "generic extractor. 8 of the 11 specified parts are direct "
                "(YEAR->year, QUARTER->quarter_number, MONTH->month_number, "
                "WEEK->week_number_of_year, DAY->day, "
                "DAYOFWEEK->day_number_of_week, DAYOFYEAR->day_number_of_year, "
                "HOUR->hour_of_day); MINUTE, SECOND and MILLISECOND fall back "
                "to sql_int_op (E3)."
            ),
        ),
        "DATE_PART": Construct(
            "DATE_PART", Classification.DIRECT,
            template="per-part — see the date-part table below",
            note="Identical treatment to EXTRACT; the two spellings collapse onto one rewrite (:276-279).",
        ),
        "DATE_TRUNC(part, date_expr)": Construct(
            "DATE_TRUNC(part, date_expr)", Classification.DIRECT,
            template="per-precision — see the truncation table below",
            note=(
                "ThoughtSpot has no date_trunc. The start_of_* family covers 7 "
                "of the 8 specified precisions ('year'->start_of_year, "
                "'quarter'->start_of_quarter, 'month'->start_of_month, "
                "'week'->start_of_week, 'day'->date ( ), 'hour'->start_of_hour, "
                "'minute'->start_of_min — the function is start_of_min, not "
                "start_of_minute); 'second' falls back to sql_date_time_op "
                "(E3). The specification says week truncation is Monday-start; "
                "ThoughtSpot's week start is an instance setting, so the "
                "converter verifies alignment and raises an issue when it cannot."
            ),
        ),
        "DATEADD(part, amount, date_expr)": Construct(
            "DATEADD(part, amount, date_expr)", Classification.DIRECT,
            template="per-part add_* — see the arithmetic table below",
            note=(
                "Argument order differs: ThoughtSpot is add_days ( [d] , n ), "
                "the specification is DATEADD(day, n, d). Every specified part "
                "is reachable: day->add_days, week->add_weeks, "
                "month->add_months, year->add_years, minute->add_minutes, "
                "second->add_seconds, plus two by arithmetic on a coarser unit "
                "since there is no native add_quarters or add_hours: "
                "quarter->add_months ( [d] , 3 * n ), "
                "hour->add_minutes ( [d] , 60 * n )."
            ),
        ),
        "DATEDIFF(part, start_date, end_date)": Construct(
            "DATEDIFF(part, start_date, end_date)", Classification.DIRECT,
            template="per-part diff_* — see the arithmetic table below",
            note=(
                "Argument order is reversed: ThoughtSpot is "
                "diff_days ( [end] , [start] ) — end first. Getting this wrong "
                "silently negates every duration in the model. day->diff_days, "
                "week->diff_weeks, month->diff_months, quarter->diff_quarters, "
                "year->diff_years, hour->diff_hours, minute->diff_minutes, "
                "second->diff_time (returns seconds)."
            ),
        ),
        "DATE '2024-01-15'": Construct(
            "DATE '2024-01-15'", Classification.DIRECT,
            template="to_date ( '{0}' , 'yyyy-MM-dd' )",
            note=(
                "A bare '2024-01-15' in a ThoughtSpot formula is parsed as "
                "arithmetic (2024 - 1 - 15), so the typed literal must always "
                "be wrapped. to_date takes exactly two arguments, so the "
                "converter supplies the ISO format model; {0} is the literal "
                "date string."
            ),
        ),
        "TIMESTAMP_NTZ '2024-01-15 10:30:00'": Construct(
            "TIMESTAMP_NTZ '2024-01-15 10:30:00'", Classification.PASSTHROUGH,
            template="CAST('2024-01-15 10:30:00' AS TIMESTAMP)",
            variant=Variant.DATE_TIME,
            note=(
                "to_date returns a DATE and drops the time part, so there is "
                "no native way to construct a wall-clock timestamp. A "
                "zero-placeholder template is a documented form of the "
                "pass-through — the document's own worked example is recorded "
                "verbatim here; a real occurrence's literal value is "
                "substituted per-occurrence when the template is built, out of "
                "this catalog's scope (same as CAST's per-type dispatch)."
            ),
        ),
        "TIME '10:30:00'": Construct(
            "TIME '10:30:00'", Classification.PASSTHROUGH,
            template="CAST('10:30:00' AS TIME)",
            variant=Variant.DATE_TIME,
            note=(
                "ThoughtSpot has no TIME column type — time ( ) extracts a "
                "time FROM a datetime, it does not construct one — so the "
                "pass-through returns DATETIME and the date part is whatever "
                "the warehouse defaults to. Flagged with an issue for that "
                "reason, not only for the dialect."
            ),
        ),
        "TO_DATE(string)": Construct(
            "TO_DATE(string)", Classification.DIRECT,
            template="to_date ( {0} , 'yyyy-MM-dd' )",
            note=(
                "The single-argument ISO form. ThoughtSpot's to_date is "
                "strictly two-argument, so the converter supplies 'yyyy-MM-dd'."
            ),
        ),
        "TO_TIMESTAMP(string)": Construct(
            "TO_TIMESTAMP(string)", Classification.PASSTHROUGH,
            template="TO_TIMESTAMP({0})", variant=Variant.DATE_TIME,
            note="to_date is date-only; parsing to a timestamp would drop the time silently.",
        ),
        "TO_DATE(string, format)": Construct(
            "TO_DATE(string, format)", Classification.DIRECT,
            template="to_date ( {0} , <translated format> )",
            note=(
                "EXPERIMENTAL. Format tokens are translated, not passed "
                "through — see the format-token table. ThoughtSpot accepts "
                "Java/LDML tokens (yyyy-MM-dd) and strptime %-codes, which "
                "between them cover the specification's entire portable core."
            ),
        ),
        "TO_TIMESTAMP(string, format)": Construct(
            "TO_TIMESTAMP(string, format)", Classification.PASSTHROUGH,
            template="TO_TIMESTAMP({0}, 'YYYY-MM-DD HH24:MI:SS')",
            variant=Variant.DATE_TIME,
            note=(
                "EXPERIMENTAL. Date-only to_date again. The format model "
                "inside the template is the warehouse's, not Ossie's, so the "
                "token translation table does not apply — this is the "
                "sharpest case of the pass-through caveat."
            ),
        ),
        "TO_CHAR(date_expr, format)": Construct(
            "TO_CHAR(date_expr, format)", Classification.PASSTHROUGH,
            template="TO_CHAR({0}, 'YYYY-MM')", variant=Variant.STRING,
            note=(
                "EXPERIMENTAL. ThoughtSpot has no general date formatter. "
                "Single-token formats do have native equivalents and the "
                "converter prefers them: 'YYYY' -> year_name ( [d] ), "
                "'MONTH' -> month ( [d] ), 'DAY' -> day_of_week ( [d] ). Those "
                "three return locale-dependent text on both sides."
            ),
        ),
    }
)

# --------------------------------------------------------------------------
# String functions (Task 5) — 21 rows: 10 direct / 11 passthrough / 0 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "String functions" section
# (thoughtspot-agent-skills repo — not vendored here; prose above/below the table
# read in full, per rule E1-E4).
#
# This family is over half passthrough, and the reasons run against intuition
# rather than with it: LOWER/UPPER/TRIM/LTRIM/RTRIM/REPLACE are passthrough not
# because they behave differently in ThoughtSpot but because ThoughtSpot has no
# native equivalent at all (live-verified 2026-07-29 on se-thoughtspot, BL-170 —
# TRIM and REPLACE were rejected with "Search did not find ...", moving them
# from an earlier direct/conservative-passthrough reading to confirmed
# passthrough). STARTSWITH/ENDSWITH run the other way: also no native function,
# but their compositions use only native functions (strpos/substr/strlen), so
# rule E2 keeps them direct. There is no regular-expression support of any kind,
# so every REGEXP_* row is passthrough with no native fallback.
# --------------------------------------------------------------------------
CATALOG.update(
    {
        "CONCAT(str1, str2, ...)": Construct(
            "CONCAT(str1, str2, ...)", Classification.DIRECT,
            template="concat ( {0} , {1} , ... )",
            note=(
                "N-ary on both sides. + does not concatenate in ThoughtSpot — "
                "it is numeric-only and the parser rejects string operands, so "
                "both || and CONCAT land here."
            ),
        ),
        "LENGTH(str)": Construct(
            "LENGTH(str)", Classification.DIRECT, template="strlen ( {0} )",
            note="Characters, not bytes, on both sides.",
        ),
        "LOWER(str)": Construct(
            "LOWER(str)", Classification.PASSTHROUGH,
            template="LOWER({0})", variant=Variant.STRING,
            note="There is no native lower in ThoughtSpot.",
        ),
        "UPPER(str)": Construct(
            "UPPER(str)", Classification.PASSTHROUGH,
            template="UPPER({0})", variant=Variant.STRING,
            note=(
                "There is no native upper in ThoughtSpot. LOWER/UPPER are the "
                "most-used functions in the whole passthrough set, and their "
                "absence is also what forces ILIKE and case-insensitive "
                "comparison into pass-throughs."
            ),
        ),
        "TRIM(str)": Construct(
            "TRIM(str)", Classification.PASSTHROUGH,
            template="TRIM({0})", variant=Variant.STRING,
            note=(
                "There is no native trim in ThoughtSpot — live-verified "
                "2026-07-29 on se-thoughtspot (BL-170), rejected with "
                "'Search did not find \"trim (\"'. The whole trim family is a "
                "pass-through, not just the one-sided forms."
            ),
        ),
        "LTRIM(str)": Construct(
            "LTRIM(str)", Classification.PASSTHROUGH,
            template="LTRIM({0})", variant=Variant.STRING,
            note=(
                "No native ltrim — live-verified 2026-07-29, se-thoughtspot "
                "(BL-170). This row was already passthrough on the "
                "conservative reading that trim was two-sided-only; the "
                "verification confirms the classification and strengthens the "
                "reason — there is no trim to substitute at all."
            ),
        ),
        "RTRIM(str)": Construct(
            "RTRIM(str)", Classification.PASSTHROUGH,
            template="RTRIM({0})", variant=Variant.STRING,
            note="As LTRIM.",
        ),
        "LEFT(str, n)": Construct(
            "LEFT(str, n)", Classification.DIRECT, template="left ( {0} , {1} )",
        ),
        "RIGHT(str, n)": Construct(
            "RIGHT(str, n)", Classification.DIRECT, template="right ( {0} , {1} )",
        ),
        "SUBSTRING(str, start, length)": Construct(
            "SUBSTRING(str, start, length)", Classification.DIRECT,
            template="substr ( {0} , {1} - 1 , {2} )",
            note=(
                "Index base differs. ANSI SUBSTRING is 1-based; ThoughtSpot's "
                "substr is 0-based. The -1 is mandatory and is the single most "
                "likely off-by-one in the whole mapping. When start is an "
                "expression rather than a literal, the arithmetic is emitted "
                "rather than folded."
            ),
        ),
        "REPLACE(str, from, to)": Construct(
            "REPLACE(str, from, to)", Classification.PASSTHROUGH,
            template="REPLACE({0}, {1}, {2})",
            variant=Variant.STRING,
            note=(
                "There is no native replace in ThoughtSpot — live-verified "
                "2026-07-29 on se-thoughtspot (BL-170), rejected with "
                "'Search did not find \"replace (\"'. This row was direct on "
                "documentation; the live pass moved it to the documented "
                "fallback."
            ),
        ),
        "SPLIT_PART(str, delimiter, part)": Construct(
            "SPLIT_PART(str, delimiter, part)", Classification.PASSTHROUGH,
            template="SPLIT_PART({0}, {1}, {2})",
            variant=Variant.STRING,
            note=(
                "ThoughtSpot has no tokenising function at all — not split, "
                "split_part or an nth-occurrence search — so there is no "
                "composition to fall back on."
            ),
        ),
        "POSITION(substr IN str)": Construct(
            "POSITION(substr IN str)", Classification.DIRECT,
            template="strpos ( {1} , {0} )",
            note=(
                "Operand order is reversed (haystack first in ThoughtSpot) and "
                "the specification's infix IN form becomes a comma. 1-based, "
                "returning 0 when absent, on both sides."
            ),
        ),
        "CHARINDEX(substr, str)": Construct(
            "CHARINDEX(substr, str)", Classification.DIRECT,
            template="strpos ( {1} , {0} )",
            note=(
                "Specification alias for POSITION (:419) with the operands "
                "already in prefix order; the reversal is the same."
            ),
        ),
        "CONTAINS(str, substr)": Construct(
            "CONTAINS(str, substr)", Classification.DIRECT,
            template="contains ( {0} , {1} )",
            note="Returns boolean on both sides.",
        ),
        "STARTSWITH(str, prefix)": Construct(
            "STARTSWITH(str, prefix)", Classification.DIRECT,
            template="strpos ( {0} , {1} ) = 1",
            note=(
                "There is no native starts_with — live-verified 2026-07-29, "
                "se-thoughtspot (BL-170). Still direct because the composition "
                "is exact and uses only native functions (per the "
                "classification definition): strpos is 1-based, so a true "
                "prefix sits at position 1. The composition itself was "
                "verified to import."
            ),
        ),
        "ENDSWITH(str, suffix)": Construct(
            "ENDSWITH(str, suffix)", Classification.DIRECT,
            template="substr ( {0} , strlen ( {0} ) - strlen ( {1} ) , strlen ( {1} ) ) = {1}",
            note=(
                "There is no native ends_with — live-verified 2026-07-29, "
                "se-thoughtspot (BL-170). Direct by composition, as "
                "STARTSWITH; verified to import."
            ),
        ),
        "REGEXP_LIKE(str, pattern)": Construct(
            "REGEXP_LIKE(str, pattern)", Classification.PASSTHROUGH,
            template="REGEXP_LIKE({0}, {1})",
            variant=Variant.BOOL,
            note=(
                "Boolean return, so not sql_string_op. ThoughtSpot has no "
                "regular-expression support of any kind."
            ),
        ),
        "REGEXP_EXTRACT(str, pattern)": Construct(
            "REGEXP_EXTRACT(str, pattern)", Classification.PASSTHROUGH,
            template="REGEXP_SUBSTR({0}, {1})",
            variant=Variant.STRING,
            note=(
                "The function name inside the template is dialect-specific — "
                "Snowflake spells it REGEXP_SUBSTR, others REGEXP_EXTRACT — so "
                "the converter selects it from the connection's dialect and "
                "raises an issue when the dialect is unknown."
            ),
        ),
        "REGEXP_REPLACE(str, pattern, replacement)": Construct(
            "REGEXP_REPLACE(str, pattern, replacement)", Classification.PASSTHROUGH,
            template="REGEXP_REPLACE({0},{1},{2})",
            variant=Variant.STRING,
            note=(
                "Name is portable; the pattern dialect (POSIX vs PCRE, "
                "backreference syntax) is not."
            ),
        ),
        "REGEXP_COUNT(str, pattern)": Construct(
            "REGEXP_COUNT(str, pattern)", Classification.PASSTHROUGH,
            template="REGEXP_COUNT({0}, {1})",
            variant=Variant.INT,
            note="Integer return.",
        ),
    }
)

# --------------------------------------------------------------------------
# Mathematical + Conditional functions (Task 6) — 34 rows: 32 direct /
# 2 passthrough / 0 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Mathematical functions" and
# "Conditional functions" sections (thoughtspot-agent-skills repo — not
# vendored here; prose above/below the tables read in full, per rule E1-E4).
#
# Nearly every row here is direct, several by composition (rule E2): SIGN has
# no native function but composes exactly as a three-way `if` chain — the
# trailing `else 0` is mandatory, ThoughtSpot rejects an `if` with no `else`.
# RADIANS/DEGREES are bare dialect-free arithmetic, not passthroughs. PI is a
# literal at the precision ThoughtSpot's own documented trig composites use.
# ThoughtSpot's trigonometry is degrees-native while the specification is
# radians-native, so SIN/COS/TAN convert degrees->radians on the way in
# (`* 180 / pi`) and ASIN/ACOS/ATAN convert radians->degrees on the way out
# (`* pi / 180`) — opposite directions, easy to transpose by mistake.
# GREATEST/LEAST are deliberately NOT mapped to max/min: ThoughtSpot's max/min
# are aggregate-only, so that mapping would both collapse the row-wise N-ary
# result to one value and flip it from attribute to measure (E7).
#
# Only two rows are passthrough: TRUNC/TRUNCATE (no native truncation — floor
# only agrees with it for x >= 0, d = 0, and round disagrees at every
# half-value) and ATAN2 (quadrant-aware and defined where x = 0, so it is not
# a two-argument ATAN composition, unlike every other inverse trig function
# in this family).
# --------------------------------------------------------------------------
CATALOG.update(
    {
        "ABS(x)": Construct(
            "ABS(x)", Classification.DIRECT, template="abs ( {0} )",
        ),
        "ROUND(x, d)": Construct(
            "ROUND(x, d)", Classification.DIRECT, template="round ( {0} , {1} )",
        ),
        "FLOOR(x)": Construct(
            "FLOOR(x)", Classification.DIRECT, template="floor ( {0} )",
        ),
        "CEIL(x)": Construct(
            "CEIL(x)", Classification.DIRECT, template="ceil ( {0} )",
            note="Specification alias pair CEIL(x) / CEILING(x); both spellings map to ceil.",
        ),
        "TRUNC(x, d)": Construct(
            "TRUNC(x, d)", Classification.PASSTHROUGH,
            template="TRUNC({0}, {1})", variant=Variant.DOUBLE,
            note=(
                "Specification alias pair TRUNC(x, d) / TRUNCATE(x, d). "
                "ThoughtSpot has no truncation function. floor agrees with "
                "TRUNC only for x >= 0 and d = 0, and round disagrees at every "
                "half-value, so neither is a safe substitute."
            ),
        ),
        "MOD(x, y)": Construct(
            "MOD(x, y)", Classification.DIRECT, template="mod ( {0} , {1} )",
            note="Sign-of-result for negative operands follows the warehouse on both sides.",
        ),
        "SIGN(x)": Construct(
            "SIGN(x)", Classification.DIRECT,
            template="if ( {0} > 0 ) then 1 else if ( {0} < 0 ) then -1 else 0",
            note=(
                "No native sign, but the three-way result is exactly "
                "expressible as an if chain. The else 0 is required — "
                "ThoughtSpot rejects an if chain with no else."
            ),
        ),
        "POWER(x, y)": Construct(
            "POWER(x, y)", Classification.DIRECT, template="pow ( {0} , {1} )",
            note="The function is pow. power is rejected by the parser.",
        ),
        "SQRT(x)": Construct(
            "SQRT(x)", Classification.DIRECT, template="sqrt ( {0} )",
        ),
        "EXP(x)": Construct(
            "EXP(x)", Classification.DIRECT, template="exp ( {0} )",
        ),
        "LN(x)": Construct(
            "LN(x)", Classification.DIRECT, template="ln ( {0} )",
        ),
        "LOG(base, x)": Construct(
            "LOG(base, x)", Classification.DIRECT,
            template="safe_divide ( ln ( {1} ) , ln ( {0} ) )",
            note=(
                "ThoughtSpot has fixed-base log2 and log10 only; base is a "
                "runtime argument here, not a literal known at catalog time, "
                "so the general change-of-base composition is the one "
                "template that is exact for every base. safe_divide rather "
                "than / guards base = 1."
            ),
        ),
        "LOG10(x)": Construct(
            "LOG10(x)", Classification.DIRECT, template="log10 ( {0} )",
        ),
        "SIN(x)": Construct(
            "SIN(x)", Classification.DIRECT,
            template="sin ( {0} * 180 / 3.14159265358979 )",
            note=(
                "ThoughtSpot trigonometry is in degrees; the specification is "
                "in radians. The conversion is mandatory — a bare sin ( {0} ) "
                "returns the sine of x degrees and is wrong for every "
                "non-zero input."
            ),
        ),
        "COS(x)": Construct(
            "COS(x)", Classification.DIRECT,
            template="cos ( {0} * 180 / 3.14159265358979 )",
            note="Degrees, as SIN.",
        ),
        "TAN(x)": Construct(
            "TAN(x)", Classification.DIRECT,
            template="tan ( {0} * 180 / 3.14159265358979 )",
            note="Degrees, as SIN.",
        ),
        "ASIN(x)": Construct(
            "ASIN(x)", Classification.DIRECT,
            template="( asin ( {0} ) * 3.14159265358979 / 180 )",
            note=(
                "Inverse functions convert the other way: ThoughtSpot returns "
                "degrees, the specification expects radians."
            ),
        ),
        "ACOS(x)": Construct(
            "ACOS(x)", Classification.DIRECT,
            template="( acos ( {0} ) * 3.14159265358979 / 180 )",
            note="Degrees -> radians, as ASIN.",
        ),
        "ATAN(x)": Construct(
            "ATAN(x)", Classification.DIRECT,
            template="( atan ( {0} ) * 3.14159265358979 / 180 )",
            note="Degrees -> radians, as ASIN.",
        ),
        "ATAN2(y, x)": Construct(
            "ATAN2(y, x)", Classification.PASSTHROUGH,
            template="ATAN2({0}, {1})", variant=Variant.DOUBLE,
            note=(
                "atan2 is not a two-argument atan — it is quadrant-aware and "
                "defined where x = 0. Composing it from atan plus sign tests "
                "is possible but the branch table is easy to get wrong at the "
                "axes, so the pass-through is the honest mapping."
            ),
        ),
        "RADIANS(degrees)": Construct(
            "RADIANS(degrees)", Classification.DIRECT,
            template="{0} * 3.14159265358979 / 180",
            note="No native radians; the arithmetic is exact and dialect-free.",
        ),
        "DEGREES(radians)": Construct(
            "DEGREES(radians)", Classification.DIRECT,
            template="{0} * 180 / 3.14159265358979",
            note="No native degrees; as RADIANS.",
        ),
        "PI()": Construct(
            "PI()", Classification.DIRECT, template="3.14159265358979",
            note=(
                "No native pi. The literal is emitted at the precision "
                "ThoughtSpot's own documented composites use; "
                'sql_double_op ( "pi()" ) is available where full warehouse '
                "precision matters."
            ),
        ),
        "GREATEST(x, y, ...)": Construct(
            "GREATEST(x, y, ...)", Classification.DIRECT,
            template="greatest ( {0} , {1} , ... )",
            note=(
                "Not max. ThoughtSpot's max is an aggregate; greatest is the "
                "row-wise N-ary function. Mapping GREATEST to max would "
                "collapse the column to one value and also flip it from "
                "attribute to measure."
            ),
        ),
        "LEAST(x, y, ...)": Construct(
            "LEAST(x, y, ...)", Classification.DIRECT,
            template="least ( {0} , {1} , ... )",
            note="Not min, for the same reason as GREATEST.",
        ),
        "IF(condition, true_result, false_result)": Construct(
            "IF(condition, true_result, false_result)", Classification.DIRECT,
            template="if ( {0} ) then {1} else {2}",
            note=(
                "The parentheses around the condition are mandatory for TML "
                "import — without them the parser reports \"Expecting keyword "
                "'('\". Applies to every condition shape, including a bare "
                "BOOL column reference."
            ),
        ),
        "IFF(condition, true_result, false_result)": Construct(
            "IFF(condition, true_result, false_result)", Classification.DIRECT,
            template="if ( {0} ) then {1} else {2}",
            note="Specification alias for IF.",
        ),
        "NULLIF(expr1, expr2)": Construct(
            "NULLIF(expr1, expr2)", Classification.DIRECT,
            template="nullif ( {0} , {1} )",
        ),
        "COALESCE(expr1, expr2, ...)": Construct(
            "COALESCE(expr1, expr2, ...)", Classification.DIRECT,
            template="ifnull ( {0} , ifnull ( {1} , {2} ) )",
            note=(
                "ThoughtSpot's ifnull is strictly two-argument, so an N-ary "
                "COALESCE becomes a right-nested chain. Two arguments is the "
                "common case and needs no nesting."
            ),
        ),
        "IFNULL(expr, default)": Construct(
            "IFNULL(expr, default)", Classification.DIRECT,
            template="ifnull ( {0} , {1} )",
        ),
        "NVL(expr, default)": Construct(
            "NVL(expr, default)", Classification.DIRECT,
            template="ifnull ( {0} , {1} )",
            note="Specification alias for two-argument COALESCE.",
        ),
        "NVL2(expr, not_null_result, null_result)": Construct(
            "NVL2(expr, not_null_result, null_result)", Classification.DIRECT,
            template="if ( isnotnull ( {0} ) ) then {1} else {2}",
            note="No native three-way null function; the composition is exact.",
        ),
        "ZEROIFNULL(expr)": Construct(
            "ZEROIFNULL(expr)", Classification.DIRECT,
            template="ifnull ( {0} , 0 )",
        ),
        "NULLIFZERO(expr)": Construct(
            "NULLIFZERO(expr)", Classification.DIRECT,
            template="nullif ( {0} , 0 )",
        ),
    }
)

# --------------------------------------------------------------------------
# Operators and constructs (Task 7) — 33 rows: 30 direct / 2 passthrough /
# 1 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Operators and constructs"
# section (thoughtspot-agent-skills repo — not vendored here; prose above/below
# the table read in full, per rule E1-E4).
#
# The document's own section header states that CASE (both forms) and the
# boolean literals/operators are rowed HERE, not under Conditional functions —
# confirmed by the arithmetic: 25 Math + 9 Conditional (Task 6) + 33 here would
# double-count CASE otherwise.
#
# spec_construct_names() extracts the BARE operator/keyword token for most of
# this family, not the document's own "a + b"-style worked-example row header —
# confirmed live before writing this block (see test_catalog_operators.py's
# docstring for the full list). Six rows have no discrete spec table row at
# all and are keyed via CONVENTION_DIVERGENCES instead: unary -x/+x, the
# simple CASE form, Parentheses, the DISTINCT modifier, the column/metric
# reference, and EXISTS_IN() itself.
#
# This family holds the single UNMAPPABLE row in the whole 146-row catalog:
# EXISTS_IN() is named at :131 as the sanctioned way to filter on a subquery,
# but the specification defines it nowhere — no signature, no argument order,
# no semantics, absent from every function table. Construct.__post_init__
# forbids a template or variant on an UNMAPPABLE row, so this is the one entry
# in the whole file with neither.
#
# LIKE is direct despite ThoughtSpot having no native starts_with/ends_with:
# the prefix/suffix/contains compositions it needs use only native functions
# (rule E2), the same reasoning as Task 5's STARTSWITH/ENDSWITH rows. ILIKE is
# passthrough for the opposite reason — case-insensitive matching has no
# native form, and the usual lower()-fold workaround is itself a passthrough,
# so there is nothing to compose from. The DISTINCT aggregate modifier is
# passthrough for every aggregate except COUNT, which already has its own
# native unique count row (COUNT(DISTINCT expr), Task 3).
# --------------------------------------------------------------------------
CATALOG.update(
    {
        "+": Construct(
            "+", Classification.DIRECT, template="{0} + {1}",
            note=(
                "Numeric only. ThoughtSpot's + rejects string operands, so a + "
                "that concatenates on the source side must become concat ( ). "
                "The specification does not overload +, so this only bites "
                "when translating a dialect expression."
            ),
        ),
        "-": Construct(
            "-", Classification.DIRECT, template="{0} - {1}",
        ),
        "*": Construct(
            "*", Classification.DIRECT, template="{0} * {1}",
        ),
        "/": Construct(
            "/", Classification.DIRECT, template="{0} / {1}",
            note=(
                "Both yield NULL (or a warehouse error) on divide-by-zero. "
                "ThoughtSpot's safe_divide returns 0, not NULL, so it is not "
                "a faithful substitute and is used only where the source "
                "itself guards the denominator."
            ),
        ),
        "%": Construct(
            "%", Classification.DIRECT, template="mod ( {0} , {1} )",
            note="ThoughtSpot has no % operator — the modulo is the function.",
        ),
        "-x / +x (unary)": Construct(
            "-x / +x (unary)", Classification.DIRECT,
            template="per-spelling — see note",
            note=(
                "CONVENTION_DIVERGENCE: unary +/- is named only in the "
                "'Operator Precedence' list, never a table row. This row "
                "merges two Ossie spellings that need DIFFERENT output — "
                "unary minus is -[x] (negation), unary plus is the identity "
                "([x] unchanged) — so a single {0}-substitutable template "
                "would be wrong for whichever spelling didn't produce it: "
                "an earlier draft used template=\"-{0}\", which is correct "
                "for -x but silently negates a parsed +x node (right arg "
                "count, wrong semantics, no exception — the arg-count guard "
                "cannot catch it). Forced external dispatch instead, the "
                "same treatment as TRUE, FALSE below and CAST's per-type "
                "table (Task 3): the caller must choose -{0} or {0} "
                "unchanged based on which spelling it parsed, rather than "
                "getting a plausible-looking wrong answer from this row. "
                "Unary minus is where the bare-date-literal trap "
                "originates: '2024-05-01' unquoted is parsed as "
                "2024 - 5 - 1. Date literals are always wrapped in "
                "to_date ( )."
            ),
        ),
        "=": Construct(
            "=", Classification.DIRECT, template="{0} = {1}",
        ),
        "<>": Construct(
            "<>", Classification.DIRECT, template="{0} <> {1}",
        ),
        "!=": Construct(
            "!=", Classification.DIRECT, template="{0} != {1}",
            note="ThoughtSpot accepts both inequality spellings, so the two rows are independent and both direct.",
        ),
        "<": Construct(
            "<", Classification.DIRECT, template="{0} < {1}",
        ),
        ">": Construct(
            ">", Classification.DIRECT, template="{0} > {1}",
        ),
        "<=": Construct(
            "<=", Classification.DIRECT, template="{0} <= {1}",
        ),
        ">=": Construct(
            ">=", Classification.DIRECT, template="{0} >= {1}",
        ),
        "expr1 AND expr2": Construct(
            "expr1 AND expr2", Classification.DIRECT, template="{0} and {1}",
            note="Lower-case, infix.",
        ),
        "expr1 OR expr2": Construct(
            "expr1 OR expr2", Classification.DIRECT, template="{0} or {1}",
            note="Lower-case, infix.",
        ),
        "NOT expr": Construct(
            "NOT expr", Classification.DIRECT, template="not ( {0} )",
            note=(
                "Function form with parentheses, not a prefix operator — "
                "not [x] does not parse."
            ),
        ),
        "BETWEEN": Construct(
            "BETWEEN", Classification.DIRECT,
            template="{0} between {1} and {2}",
            note="Inclusive on both sides.",
        ),
        "IN": Construct(
            "IN", Classification.DIRECT,
            template="{0} in {{ {1} , {2} , ... }}",
            note=(
                "Literal lists only on both sides — no subqueries. The "
                "curly-brace delimiter is confirmed, live-verified "
                "2026-07-29 on se-thoughtspot (BL-170): the round-parenthesis "
                "form is rejected with 'Expecting one of the valid keywords, "
                "such as, \"ts_var\", \"{\"'. It forces >- block-scalar YAML. "
                "The braces are doubled ({{ }}) in the template because "
                "emit_direct renders via str.format, which reads a single "
                "literal brace as the start of a field name — the "
                "corrected form was verified by actually calling "
                "emit_direct and checking the rendered output has single "
                "braces (see test_emit.py's catalog-wide sweep)."
            ),
        ),
        "NOT IN": Construct(
            "NOT IN", Classification.DIRECT,
            template="not ( {0} in {{ {1} , {2} , ... }} )",
            note=(
                "Emitted as a negated in rather than a not in keyword — the "
                "bare keyword form is not reliably accepted. Braces doubled "
                "for str.format, as IN above."
            ),
        ),
        "str LIKE pattern": Construct(
            "str LIKE pattern", Classification.DIRECT,
            template="per-pattern-shape — see note",
            note=(
                "Prefix ('foo%') -> strpos ( {0} , 'foo' ) = 1; suffix "
                "('%foo') -> substr ( {0} , strlen ( {0} ) - strlen ( 'foo' "
                ") , strlen ( 'foo' ) ) = 'foo'; contains ('%foo%') -> "
                "contains ( {0} , 'foo' ). Only contains is a native "
                "function — starts_with and ends_with do not exist "
                "(live-verified 2026-07-29, se-thoughtspot — BL-170), so the "
                "first two shapes are compositions of native functions "
                "(rule E2), same as the STARTSWITH/ENDSWITH rows. These "
                "three shapes are the overwhelming majority of LIKE use. "
                "Interior wildcards and any _ single-character wildcard have "
                "no native form and fall back to "
                'sql_bool_op ( "{0} LIKE {1}" , [s] , [pattern] ) (E3). '
                "The per-pattern-shape dispatch is out of this catalog's "
                "scope, same treatment as CAST's per-type dispatch (Task 3) "
                "— the actual pattern literal is a runtime value, not known "
                "at catalog-construction time."
            ),
        ),
        "str ILIKE pattern": Construct(
            "str ILIKE pattern", Classification.PASSTHROUGH,
            template="{0} ILIKE {1}", variant=Variant.BOOL,
            note=(
                "Case-insensitive matching has no native form, and the "
                "usual workaround — fold both sides with lower — is itself "
                "a pass-through, so there is nothing to compose from."
            ),
        ),
        "IS NULL": Construct(
            "IS NULL", Classification.DIRECT, template="isnull ( {0} )",
        ),
        "IS NOT NULL": Construct(
            "IS NOT NULL", Classification.DIRECT, template="isnotnull ( {0} )",
            note="Native, so not composed as not ( isnull ( ) ).",
        ),
        "IS DISTINCT FROM": Construct(
            "IS DISTINCT FROM", Classification.DIRECT,
            template=(
                "if ( isnull ( {0} ) and isnull ( {1} ) ) then false else "
                "if ( isnull ( {0} ) or isnull ( {1} ) ) then true else "
                "{0} != {1}"
            ),
            note=(
                "No native null-safe comparison, but the three-case truth "
                "table is exactly expressible. The nesting order matters: "
                "both-null must be tested before either-null."
            ),
        ),
        "IS NOT DISTINCT FROM": Construct(
            "IS NOT DISTINCT FROM", Classification.DIRECT,
            template=(
                "if ( isnull ( {0} ) and isnull ( {1} ) ) then true else "
                "if ( isnull ( {0} ) or isnull ( {1} ) ) then false else "
                "{0} = {1}"
            ),
            note=(
                "The negation of the row above, written directly rather "
                "than wrapped in not ( ) — one fewer nesting level for the "
                "parser."
            ),
        ),
        "CASE WHEN": Construct(
            "CASE WHEN", Classification.DIRECT,
            template="if ( c1 ) then r1 else if ( c2 ) then r2 else d",
            note=(
                "The searched CASE WHEN c1 THEN r1 ... ELSE d END form. No "
                "native CASE; the chain is else if, two words. The final "
                "else is mandatory and must be type-matched — else 0 for a "
                "measure, else '' for an attribute. Omitting it raises "
                "'Unknown data type', and a CASE with no ELSE (legal in the "
                "specification, yielding NULL) therefore needs one "
                "synthesised. The branch count is unbounded, so the "
                "template is transcribed with the document's own symbolic "
                "c1/r1/c2/r2/d names rather than forced into a fixed "
                "{0}/{1} scheme — the same out-of-scope-dispatch treatment "
                "as CAST's per-type table (Task 3)."
            ),
        ),
        "CASE expr WHEN v1 THEN r1 ... END (simple)": Construct(
            "CASE expr WHEN v1 THEN r1 ... END (simple)", Classification.DIRECT,
            template="if ( [expr] = v1 ) then r1 else if ( [expr] = v2 ) then r2 else d",
            note=(
                "CONVENTION_DIVERGENCE: the simple CASE form is described "
                "only in the CASE Expression code fence, never a table row. "
                "Expanded to the searched form with an explicit equality "
                "per branch. expr is repeated per branch, so a converter "
                "should hoist an expensive expr into its own formula first. "
                "Symbolic template, as CASE WHEN above, for the same "
                "unbounded-branch-count reason."
            ),
        ),
        "str1 || str2": Construct(
            "str1 || str2", Classification.DIRECT, template="concat ( {0} , {1} )",
            note=(
                "ThoughtSpot has no concatenation operator at all — + is "
                "numeric-only — so || and CONCAT share one target."
            ),
        ),
        "Parentheses — expression grouping": Construct(
            "Parentheses — expression grouping", Classification.DIRECT,
            template="( {0} )",
            note=(
                "CONVENTION_DIVERGENCE: its Supported SQL Constructs row "
                "carries no backtick token in either cell, the only marker "
                "the top-table extraction keys on. Precedence is the "
                "standard SQL ordering on the Ossie side. The converter "
                "emits explicit parentheses around every rewritten "
                "sub-expression rather than relying on the two languages "
                "agreeing about precedence — cheap, and it removes a whole "
                "class of silent arithmetic errors."
            ),
        ),
        "TRUE, FALSE": Construct(
            "TRUE, FALSE", Classification.DIRECT, template="true / false",
            note=(
                "The Boolean Functions table's Syntax cell merges TRUE and "
                "FALSE into one comma-joined entry, matching what "
                "spec_construct_names() extracts. Which of the two "
                "lower-case literals is emitted depends on which the source "
                "wrote — TRUE -> true, FALSE -> false — resolved "
                "per-occurrence, out of this catalog's scope (same as "
                "CAST's per-type dispatch). A bare BOOL column reference "
                "used as a condition still needs its parentheses: "
                "if ( [T::flag] ) then ... parses, if [T::flag] then ... "
                "does not."
            ),
        ),
        "DISTINCT aggregate modifier": Construct(
            "DISTINCT aggregate modifier", Classification.PASSTHROUGH,
            template="SUM(DISTINCT {0})", variant=Variant.NUMBER_AGGREGATE,
            note=(
                "CONVENTION_DIVERGENCE: described only in the Conditional "
                "Aggregations prose/code block, never a table row. The "
                "specification allows DISTINCT on SUM as well as COUNT. "
                "ThoughtSpot has exactly one distinct-aware aggregate — "
                "unique count — which is COUNT(DISTINCT) and already has "
                "its own row. Every other DISTINCT aggregate is a "
                "pass-through."
            ),
        ),
        "Column / metric reference — field, dataset.field": Construct(
            "Column / metric reference — field, dataset.field",
            Classification.DIRECT,
            template="[TABLE::Column], or [Formula Name] for a metric",
            note=(
                "CONVENTION_DIVERGENCE: its Supported SQL Constructs row "
                "carries no backtick token in either cell, same reason as "
                "Parentheses. Always rewritten from resolved metadata, "
                "never passed through textually — the rewrite, the "
                "case-sensitivity rules and the display-name-versus-"
                "identifier problem are the construct-mapping document's "
                "ID1-ID4, out of this catalog's scope."
            ),
        ),
        "EXISTS_IN()": Construct(
            "EXISTS_IN()", Classification.UNMAPPABLE,
            note=(
                "CONVENTION_DIVERGENCE: named only in the Reason column of "
                "the excluded 'Not Supported in Expressions' table, never "
                "in a table of its own. The single unmappable row in the "
                "whole 146-row catalog: named at :131 as the sanctioned way "
                "to filter on a subquery, but defined nowhere in the "
                "specification — no signature, no argument order, no "
                "semantics, absent from every function table. Even given a "
                "signature, ThoughtSpot's nearest capability is a "
                "sql_bool_op subquery template that requires a "
                "fully-qualified warehouse table name, which is not "
                "derivable from an Ossie expression. See ask A9."
            ),
        ),
    }
)

#: Constructs the mapping document (docs/ossie/ts-ossie-function-mapping.md in the
#: thoughtspot-agent-skills repo) counts separately under rule E1 ("one row per
#: construct") that core-spec/expression_language.md does not give a discrete
#: table row of their own. Each entry records WHY it diverges. This is NOT an
#: escape hatch for missing coverage: the 137 names in spec_construct_names() are
#: still parsed from the live upstream file, so an upstream addition still fails
#: the build. It exists because the mapping document's 146-row census counts by a
#: different unit (one row per construct, including constructs the spec only
#: describes in prose) than spec_construct_names() counts by (one entry per
#: parseable table row / heading). Verified directly against the mapping
#: document's actual row list — see catalog.py's docstring and task-1-report.md
#: for the reconciliation (137 + 9 == 146).
#:
#: Two mechanisms an earlier pass mistakenly guessed would appear here do NOT:
#: `CEIL(x)`/`CEILING(x)`, `TRUNC(x, d)`/`TRUNCATE(x, d)` and `TRUE`/`FALSE` are
#: each ONE row in the mapping document too (not split), matching spec_name's
#: single merged entry — a spelling-convention question for Tasks 3-8 (see the
#: module docstring's "Spelling" note), not a divergence.
CONVENTION_DIVERGENCES: dict[str, str] = {
    "-x / +x (unary)": (
        "unary +/- is named only in the 'Operator Precedence' list "
        "(core-spec/expression_language.md:142), never a table row"
    ),
    "CASE expr WHEN v1 THEN r1 ... END (simple)": (
        "simple CASE is described only in the CASE Expression code fence "
        "(core-spec/expression_language.md:508-513) alongside searched CASE; "
        "the top-level summary table's single bare 'CASE WHEN' token covers "
        "the searched form and does not extend to this one"
    ),
    "Parentheses — expression grouping": (
        "its Supported SQL Constructs row (core-spec/expression_language.md:120) "
        "carries no backtick token in either cell, the only marker the "
        "top-table extraction keys on"
    ),
    "DISTINCT aggregate modifier": (
        "the DISTINCT modifier is described only in the Conditional "
        "Aggregations prose/code block (core-spec/expression_language.md:219-230), "
        "never a table row"
    ),
    "Column / metric reference — field, dataset.field": (
        "its Supported SQL Constructs row (core-spec/expression_language.md:108) "
        "carries no backtick token in either cell, same reason as Parentheses"
    ),
    "EXISTS_IN()": (
        "named only in the Reason column of the excluded 'Not Supported in "
        "Expressions' table (core-spec/expression_language.md:131), never in a "
        "table of its own"
    ),
    "OVER (PARTITION BY ... ORDER BY ...) clause": (
        "the generic OVER syntax template (core-spec/expression_language.md:548-560) "
        "is a fenced code block, not a table"
    ),
    "Frame clause — ROWS BETWEEN ... / RANGE BETWEEN ...": (
        "frame options are a bullet list under the OVER syntax section "
        "(core-spec/expression_language.md:556-560), not a table"
    ),
    "Window aggregation — AGG(expr) OVER (...)": (
        "the Window Aggregations section (core-spec/expression_language.md:583-599) "
        "is prose and code examples, not a table"
    ),
}


# --------------------------------------------------------------------------
# spec_construct_names(): the upstream-spec oracle.
# --------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")

# A section whose entire content is about something *other than* an Ossie
# construct in its own right. Matched case-insensitively as a substring of
# the heading text at any level, so a subsection ("### Common Dialect
# Variations" under "## Dialect Extensions") is caught the same way as a
# top-level one ("## Cross-Reference: Tool Mappings").
_EXCLUDED_SECTION_MARKERS = (
    "not supported",  # "Not Supported in Expressions": SELECT/FROM/GROUP BY/... are
    # explicitly things Ossie expressions do NOT support - the opposite of a construct.
    "dialect variation",  # "Common Dialect Variations": other engines' spellings of
    # constructs already counted from their Ossie-standard table.
    "cross-reference",  # "Cross-Reference: Tool Mappings": Tableau / Looker Studio / DAX
    # spellings, not Ossie constructs.
)

# A standalone H3 section that defines exactly one construct via prose/a code
# fence rather than a table - e.g. "### CAST (REQUIRED)". Matches only when
# the heading's own name is a single token (letters, digits, underscore, or a
# markdown-escaped underscore "\_"), which is what distinguishes "CAST" or
# "TRY\_CAST" from a descriptive multi-word heading like "Alternative
# Extraction Syntax" or "Null-Safe Comparison" (those need bespoke handling
# below, because more than one construct - or a construct whose name isn't
# the heading text - lives in their body).
_SINGLE_CONSTRUCT_HEADING_RE = re.compile(
    r"^### ([A-Za-z0-9_]+(?:\\_[A-Za-z0-9_]+)*) \((?:REQUIRED|RECOMMENDED|EXPERIMENTAL)\)\s*$",
    re.MULTILINE,
)


def _find_spec_path() -> Path:
    """Walk up from this file to the repository root and locate the upstream spec."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "core-spec" / "expression_language.md"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"core-spec/expression_language.md not found by walking up from {here}"
    )


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row on '|', but never inside a backtick code span.

    The document contains at least one cell whose code span itself contains a
    pipe character - the `||` concatenation operator, written as
    `` `str1 || str2` `` - which a naive `str.split("|")` would shred into
    extra empty cells. Backticks otherwise never appear as literal (non-code)
    content in this document's tables, so "toggle on backtick" is a safe,
    general rule rather than a special case for that one row.
    """
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code_span = False
    for ch in line:
        if ch == "`":
            in_code_span = not in_code_span
            current.append(ch)
        elif ch == "|" and not in_code_span:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    cells.append("".join(current).strip())
    return cells


def _clean(cell: str) -> str:
    """Strip markdown code-span backticks, leaving the underlying syntax text."""
    return cell.replace("`", "").strip()


def _is_section_excluded(heading_stack: dict[int, str]) -> bool:
    return any(
        marker in heading.lower()
        for heading in heading_stack.values()
        for marker in _EXCLUDED_SECTION_MARKERS
    )


def _extract_tables(text: str) -> tuple[set[str], set[str], list[tuple[str, str]]]:
    """One pass over the document collecting constructs from ordinary tables.

    Returns:
        names: constructs whose spec_name is a `Syntax` column value.
        identifier_tokens: the identifying (first) column's backtick tokens for every
            such table row, used to de-duplicate against the top-level summary table.
        summary_rows: (construct_cell, notes_cell) pairs from the top-level
            "Supported SQL Constructs" table, processed by the caller once every
            detailed table has been seen.
    """
    names: set[str] = set()
    identifier_tokens: set[str] = set()
    summary_rows: list[tuple[str, str]] = []

    heading_stack: dict[int, str] = {}
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            for lvl in [lvl for lvl in heading_stack if lvl >= level]:
                del heading_stack[lvl]
            heading_stack[level] = heading_match.group(2).strip()
            i += 1
            continue

        stripped = line.strip()

        # A fenced code block is never itself a table; skip its body outright.
        # (The few constructs defined only inside a fence are picked up by the
        # bespoke passes in spec_construct_names(), keyed by heading shape.)
        if stripped.startswith("```"):
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                i += 1
            i += 1
            continue

        if not stripped.startswith("|"):
            i += 1
            continue

        if _is_section_excluded(heading_stack):
            # Skip the whole table without even parsing it.
            while i < n and lines[i].strip().startswith("|"):
                i += 1
            continue

        table_lines = []
        while i < n and lines[i].strip().startswith("|"):
            table_lines.append(lines[i])
            i += 1
        if len(table_lines) < 2:
            continue  # a header with no separator row is not a real table

        header = [_split_table_row(table_lines[0])]
        header_lower = [c.lower() for c in header[0]]
        data_rows = [_split_table_row(r) for r in table_lines[2:]]

        # Rule E1: a table whose identifying column is literally "Token" is a
        # format-token argument vocabulary (TO_CHAR/TO_DATE's `format` argument).
        if header_lower and header_lower[0] == "token":
            continue

        if "syntax" in header_lower:
            syntax_idx = header_lower.index("syntax")
            form_idx = header_lower.index("form") if "form" in header_lower else None
            for row in data_rows:
                if len(row) <= syntax_idx:
                    continue
                if (
                    form_idx is not None
                    and len(row) > form_idx
                    and row[form_idx].strip().lower() == "cast"
                ):
                    # A "Cast" row in the Date/Time Construction table is a worked
                    # example of the already-catalogued generic CAST(...) construct,
                    # not a new one.
                    continue
                for token in _CODE_SPAN_RE.findall(row[0]) if row else []:
                    identifier_tokens.add(token.strip().upper())
                syntax_value = _clean(row[syntax_idx])
                if syntax_value:
                    names.add(syntax_value)
        elif header_lower[:2] == ["construct", "notes"]:
            # The top-level "Supported SQL Constructs" table. Its rows range from
            # genuine one-off operators/keywords (BETWEEN, CASE WHEN, IN / NOT IN)
            # to category headers elaborated in detail elsewhere (Aggregate
            # functions, Window functions) - processed once every detailed table
            # has been seen, so it can tell the two apart (see _extract_summary_rows).
            for row in data_rows:
                if row:
                    summary_rows.append((row[0], row[1] if len(row) > 1 else ""))
        # Any other table shape ("Database Support", "Decomposability Reference",
        # the working-group roster, a comparison-of-quoting-styles example) names
        # no new construct and is left unread.

    return names, identifier_tokens, summary_rows


def _extract_summary_rows(
    summary_rows: list[tuple[str, str]], identifier_tokens: set[str]
) -> set[str]:
    """Pull genuine constructs out of the top "Supported SQL Constructs" table.

    A row counts only if its Construct cell or its Notes cell carries a
    backtick-quoted token - the document's own marker for "this cell names a
    real piece of syntax" - which is how e.g. `BETWEEN`, `` `IN` / `NOT IN` ``
    and `` `CASE WHEN` `` are told apart from plain category labels like
    "Column and Metric references" or "Aggregate functions" (elaborated in
    detailed tables elsewhere, and carrying no backtick markup of their own).

    A token already seen as a detailed table's identifying column (e.g. `LIKE`
    from the Pattern Matching table) is skipped here to avoid counting the same
    construct twice under two different spellings.
    """
    names: set[str] = set()
    for construct_cell, notes_cell in summary_rows:
        tokens = _CODE_SPAN_RE.findall(construct_cell)
        if not tokens:
            for part in notes_cell.split(","):
                tokens.extend(_CODE_SPAN_RE.findall(part))
        for token in tokens:
            token = token.strip()
            if token.upper() in identifier_tokens:
                continue
            names.add(token)
    return names


def _extract_extraction_syntax_functions(text: str) -> set[str]:
    """EXTRACT and DATE_PART: named in a code fence, not a table.

    The "Alternative Extraction Syntax" section is the only place either
    function is named; the bullet list immediately below it enumerates the
    date parts they accept (rule E1: an argument vocabulary, not a construct).
    That list needs no special exclusion - it is a bullet list, not a table,
    so `_extract_tables()` never looks at it in the first place.
    """
    section = re.search(
        r"### Alternative Extraction Syntax.*?\n(.*?)\n###", text, re.S
    )
    if not section:
        return set()
    return set(re.findall(r"\b([A-Z_]+)\(", section.group(1)))


def _extract_null_safe_comparison_operators(text: str) -> set[str]:
    """IS DISTINCT FROM / IS NOT DISTINCT FROM: named only inside a code fence."""
    section = re.search(r"### Null-Safe Comparison.*?\n(.*?)\n---", text, re.S)
    if not section:
        return set()
    return {m.group(0) for m in re.finditer(r"\bIS (?:NOT )?DISTINCT FROM\b", section.group(1))}


def _extract_single_construct_headings(text: str) -> set[str]:
    """A standalone H3 whose own name (not a table) is the one construct it defines.

    Structural, not name-based: matches any "### {token} ({compliance level})"
    heading whose section body contains no pipe-table. CAST and TRY_CAST are
    the only two headings in the current document shaped this way.
    """
    names: set[str] = set()
    for match in _SINGLE_CONSTRUCT_HEADING_RE.finditer(text):
        token = match.group(1).replace("\\_", "_")
        start = match.end()
        next_heading = re.search(r"^#{1,6} ", text[start:], re.M)
        body = text[start : start + next_heading.start()] if next_heading else text[start:]
        if "|" not in body:
            names.add(token)
    return names


def spec_construct_names() -> set[str]:
    """The construct inventory of the upstream expression-language specification.

    Reads `core-spec/expression_language.md` fresh on every call - the file is
    small and this is a test-time oracle, not a runtime hot path.
    """
    text = _find_spec_path().read_text(encoding="utf-8")

    table_names, identifier_tokens, summary_rows = _extract_tables(text)
    names = set(table_names)
    names |= _extract_summary_rows(summary_rows, identifier_tokens)
    names |= _extract_extraction_syntax_functions(text)
    names |= _extract_null_safe_comparison_operators(text)
    names |= _extract_single_construct_headings(text)
    return names
