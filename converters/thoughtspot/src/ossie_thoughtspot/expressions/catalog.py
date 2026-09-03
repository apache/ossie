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

`CATALOG` is organised into families below — Aggregate functions, Type conversion,
Date/time functions, String functions, Mathematical + Conditional functions, Operators
and constructs, and Window functions — one block per family, together covering the full
specification. `spec_construct_names()` is an oracle read from the **upstream**
`core-spec/expression_language.md`: it independently reports every construct the
specification defines, so a construct added upstream in the future fails this package's
build instead of silently going unsupported (see `test_catalog_covers_the_spec.py`).

Extraction approach
--------------------
The spec document mixes three kinds of content that must be told apart:

1. Genuine constructs — a named function, operator or literal form the
   specification defines, almost always as one row of a markdown table whose
   row carries a `Syntax` column (e.g. `SUM(expr)`), or, for a handful of
   operators/keywords with no table of their own, a row of the top-level
   "Supported SQL Constructs" table.
2. Argument vocabularies — the `EXTRACT`/`DATE_PART` parts, the `DATE_TRUNC`
   precisions, the `TO_DATE`/`TO_CHAR` format tokens and the `CAST` target
   types. These describe values an argument may take, not constructs in
   their own right, and must be excluded.
3. Informative tables — the per-engine "Common Dialect Variations" table and
   the "Cross-Reference: Tool Mappings" section describe *other products'*
   spellings (Tableau, Looker Studio, DAX, and per-engine SQL). Names that
   appear only there are not Ossie constructs.

The exclusions are keyed off the document's own structure — a table's own header naming
and section heading text ("Not Supported in Expressions", "Common Dialect Variations",
"Cross-Reference") — rather than a hardcoded list of names to drop, so a renamed or added
upstream construct cannot go silently unnoticed. `_extract_tables()` only looks at lines
starting with "|", so the argument-vocabulary bullet lists are simply invisible to it.

Spelling: `CATALOG` keys must match `spec_construct_names()` exactly
------------------------------------------------------------------------------
`spec_construct_names()` is the oracle for a `CATALOG` key's exact spelling, not any
mapping document's prose. Several rows write their Ossie-side syntax differently than
this parser extracts it; a `CATALOG` entry keyed on a mapping document's own wording
instead of this function's output will read as an "invented" construct even though it is
a real, intended row. When adding or editing a row, run `spec_construct_names()` and
match a member of it exactly, rather than transcribing another document's column text.
Grouped by which extractor produces the divergent spelling:

- **`_extract_tables()`** (an ordinary table with a `Syntax` column):
    - Alias pairs the spec merges into ONE table row keep this parser's single
      extracted spelling: `CEIL(x)` (not `CEIL(x) / CEILING(x)`), `TRUNC(x, d)`
      (not `.../ TRUNCATE(x, d)`).
    - Two-alternative-syntax rows keep the spec's own joining word, "or":
      `"CURRENT_DATE or CURRENT_DATE()"`, `"CURRENT_TIMESTAMP or
      CURRENT_TIMESTAMP()"`, `"CURRENT_TIME or CURRENT_TIME()"`.
    - The merged boolean-literal row (`BOOLEAN`'s `Syntax` cell) is one entry,
      comma-joined: `"TRUE, FALSE"`.
    - The Boolean Functions table's `AND`/`OR` rows keep the spec's own
      `expr1`/`expr2` placeholder names: `"expr1 AND expr2"`, `"expr1 OR expr2"`.
- **`_extract_summary_rows()`** (the top-level "Supported SQL Constructs"
  table, bare backtick token): `BETWEEN`, `IN`, `NOT IN`, `IS NULL`, `IS NOT NULL`,
  `CASE WHEN`, and the raw symbols `+ - * / % = <> != < > <= >=`.
- **`_extract_null_safe_comparison_operators()`** (the "Null-Safe Comparison"
  code fence, not the summary table): `IS DISTINCT FROM`, `IS NOT DISTINCT FROM`.
- **`_extract_extraction_syntax_functions()`** (the "Alternative Extraction
  Syntax" code fence, bare token, no argument list): `EXTRACT`, `DATE_PART`.
- **`_extract_single_construct_headings()`** (a standalone heading with no
  table, bare token): `CAST`, `TRY_CAST` (not `CAST(expression AS target_type)`).

See `CONVENTION_DIVERGENCES` below for the (much shorter) list of constructs that have
no entry in `spec_construct_names()` at all and are exempted instead.
"""
import re
from pathlib import Path

from ._types import Classification, Construct, Variant

# --------------------------------------------------------------------------
# CATALOG: organised into families, one block per family below.
# --------------------------------------------------------------------------
CATALOG: dict[str, Construct] = {}

# --------------------------------------------------------------------------
# Aggregate functions — 18 rows: 12 direct / 6 passthrough / 0 unmappable.
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
# Type conversion — 2 rows: 2 direct / 0 passthrough / 0 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Type conversion" section.
#
# CAST/TRY_CAST are, per rule E3, direct rows whose target-type argument
# vocabulary is only partly covered (5 of 8 types direct, 3 fall back to a
# pass-through) — the per-type dispatch is not counted as its own construct
# (rule E1: the target-type table is an argument vocabulary, marked "not
# counted" in the mapping document) and is not resolved here. Resolving a
# `CAST` occurrence to an actual formula from its target type would need an
# expression parser, which is out of scope, and whether to take on a sqlglot
# dependency for that is still unresolved; `template` records the document's
# own ThoughtSpot-column text for traceability rather than a
# directly-substitutable formula.
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
# Date/time functions — 24 rows: 17 direct / 7 passthrough / 0 unmappable.
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
# CAST/TRY_CAST in Type conversion above): `template` records the mapping document's own
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
# String functions — 21 rows: 10 direct / 11 passthrough / 0 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "String functions" section
# (thoughtspot-agent-skills repo — not vendored here; prose above/below the table
# read in full, per rule E1-E4).
#
# This family is over half passthrough, and the reasons run against intuition
# rather than with it: LOWER/UPPER/TRIM/LTRIM/RTRIM/REPLACE are passthrough not
# because they behave differently in ThoughtSpot but because ThoughtSpot has no
# native equivalent at all (live-verified 2026-07-29: TRIM and REPLACE were
# rejected with "Search did not find ..."). STARTSWITH/ENDSWITH run the other
# way: also no native function, but their compositions use only native
# functions (strpos/substr/strlen), so rule E2 keeps them direct. There is no
# regular-expression support of any kind, so every REGEXP_* row is passthrough
# with no native fallback.
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
                "2026-07-29, rejected with 'Search did not find \"trim (\"'. "
                "The whole trim family is a pass-through, not just the "
                "one-sided forms."
            ),
        ),
        "LTRIM(str)": Construct(
            "LTRIM(str)", Classification.PASSTHROUGH,
            template="LTRIM({0})", variant=Variant.STRING,
            note="No native ltrim — live-verified 2026-07-29.",
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
                "2026-07-29, rejected with 'Search did not find \"replace (\"'."
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
                "There is no native starts_with — live-verified 2026-07-29. "
                "Still direct because the composition is exact and uses only "
                "native functions: strpos is 1-based, so a true prefix sits "
                "at position 1."
            ),
        ),
        "ENDSWITH(str, suffix)": Construct(
            "ENDSWITH(str, suffix)", Classification.DIRECT,
            template="substr ( {0} , strlen ( {0} ) - strlen ( {1} ) , strlen ( {1} ) ) = {1}",
            note=(
                "There is no native ends_with — live-verified 2026-07-29. "
                "Direct by composition, as STARTSWITH."
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
# Mathematical + Conditional functions — 34 rows: 32 direct /
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
# Operators and constructs — 33 rows: 30 direct / 2 passthrough /
# 1 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Operators and constructs"
# section (thoughtspot-agent-skills repo — not vendored here; prose above/below
# the table read in full, per rule E1-E4).
#
# The document's own section header states that CASE (both forms) and the
# boolean literals/operators are rowed HERE, not under Conditional functions —
# confirmed by the arithmetic: 25 Math + 9 Conditional (the Mathematical +
# Conditional functions family above) + 33 here would double-count CASE otherwise.
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
# (rule E2), the same reasoning as the String functions family's STARTSWITH/
# ENDSWITH rows above. ILIKE is passthrough for the opposite reason —
# case-insensitive matching has no native form, and the usual lower()-fold
# workaround is itself a passthrough, so there is nothing to compose from. The
# DISTINCT aggregate modifier is passthrough for every aggregate except COUNT,
# which already has its own native unique count row (COUNT(DISTINCT expr),
# in Aggregate functions above).
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
                "table: the caller must choose -{0} or {0} "
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
                "2026-07-29: the round-parenthesis form is rejected with "
                "'Expecting one of the valid keywords, such as, \"ts_var\", "
                "\"{\"'. It forces >- block-scalar YAML. The braces are "
                "doubled ({{ }}) in the template because emit_direct renders "
                "via str.format, which reads a single literal brace as the "
                "start of a field name (see test_emit.py's catalog-wide "
                "sweep)."
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
                "(live-verified 2026-07-29), so the "
                "first two shapes are compositions of native functions "
                "(rule E2), same as the STARTSWITH/ENDSWITH rows. These "
                "three shapes are the overwhelming majority of LIKE use. "
                "Interior wildcards and any _ single-character wildcard have "
                "no native form and fall back to "
                'sql_bool_op ( "{0} LIKE {1}" , [s] , [pattern] ) (E3). '
                "The per-pattern-shape dispatch is out of this catalog's "
                "scope, same treatment as CAST's per-type dispatch "
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
                "template uses symbolic c1/r1/c2/r2/d names rather than "
                "being forced into a fixed {0}/{1} scheme — the same "
                "out-of-scope-dispatch treatment "
                "as CAST's per-type table."
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

# --------------------------------------------------------------------------
# Window functions — 14 rows: 5 direct / 9 passthrough / 0 unmappable.
# Source: docs/ossie/ts-ossie-function-mapping.md, "Window functions" section, plus
# "Window rows live-confirmed — 2026-07-30" (thoughtspot-agent-skills repo — not
# vendored here; prose above/below the table read in full, per rule E1-E4).
#
# This is the hardest family, and the last one — it completes the 146-row catalog.
# Three rules govern it:
#
# - E5 — a raw aggregate cannot be nested inside a ThoughtSpot window function. The
#   argument must be a column reference or a group_aggregate ( ... ). Live-confirmed
#   both directions: the raw-aggregate form is rejected, the group_aggregate form
#   validates, for moving_* and cumulative_* alike.
# - E6 — ThoughtSpot's ORDER BY column must be a physical column reference, not a
#   formula. A formula column in the sort position fails to resolve.
# - E13 — a ThoughtSpot window formula cannot declare its own PARTITION BY; the
#   window shape is completed from the search context. There is no argument slot
#   for a partition and none can be added — live-confirmed by rejection,
#   2026-07-30 (a fifth { [attr] } or query_groups ( ) argument to moving_sum,
#   and a third to cumulative_sum, are both rejected at the parser). rank /
#   rank_percentile are the stricter case: arity fixed at exactly two, enforced
#   ("Function rank expects only 2 arguments"), so they are always global. This
#   is why LAG, LEAD, the OVER clause and window aggregation moved
#   direct -> passthrough in the 2026-07-30 rework (52 live probes, 31 accepted /
#   21 rejected) — a native idiom (moving_sum as the LAG/LEAD idiom) exists but
#   is NOT equivalent to any OVER shape, because it has no partition slot and
#   ThoughtSpot's partition is never empty.
#
# FIRST_VALUE/LAST_VALUE are the section's one exception: they take a genuine,
# explicit partition argument and a genuine, explicit order axis — both
# live-confirmed accepted, including a multi-column fixed partition — so the
# formula does define its own window, and they stay direct. RANK/PERCENT_RANK and
# the frame-clause boundaries also stay direct, with their native reach now proven
# by rejection rather than asserted.
#
# PERCENT_RANK is direct via rank_percentile (both the 0-100 scale and the
# inversion are required); CUME_DIST is NOT a rank_percentile substitute —
# PERCENT_RANK divides by n-1 and starts at 0, CUME_DIST divides by n and ends at
# 1 — so CUME_DIST stays passthrough with no native fallback at all.
#
# RANK, PERCENT_RANK, FIRST_VALUE and LAST_VALUE record the document's own worked
# example verbatim — symbolic bracket names ([m], [dim], [ord], [attr], [T::date]),
# not numbered {0}/{1} substitution slots — because resolving which actual column
# fills each slot needs model metadata not known at catalog-construction time; same
# out-of-scope-dispatch treatment as CASE WHEN's c1/r1 names and CAST's per-type
# table. FIRST_VALUE/LAST_VALUE's worked example carries ThoughtSpot's literal
# `{ [T::date] }` list syntax for the axis argument; since these are DIRECT rows
# rendered via emit_direct's str.format, the literal braces are doubled ({{ }})
# the same fix the IN/NOT IN rows above need for the same reason — verified here by
# actually calling emit_direct and checking the rendered output has single braces
# again (see test_catalog_window.py).
#
# Three of the 14 rows have no discrete row of their own in the upstream spec —
# the OVER clause, the frame clause and window aggregation are keyed via the
# pre-existing CONVENTION_DIVERGENCES entries (copied verbatim, not retyped, to
# avoid an em-dash/ellipsis mismatch) rather than spec_construct_names(). The
# other 11 key on spec_construct_names()'s own extraction from the "Ranking
# Functions" and "Offset Functions" tables' Syntax column — confirmed live before
# writing this block.
# --------------------------------------------------------------------------
CATALOG.update(
    {
        "ROW_NUMBER() OVER (...)": Construct(
            "ROW_NUMBER() OVER (...)", Classification.PASSTHROUGH,
            template="ROW_NUMBER() OVER (PARTITION BY {0} ORDER BY {1})",
            variant=Variant.INT_AGGREGATE,
            note=(
                "ThoughtSpot's rank is competition rank, not a row number, so it "
                "is not a substitute. Wrap in group_aggregate per E8 so the "
                "partition column reaches the GROUP BY even when the user's "
                "search omits it."
            ),
        ),
        "RANK() OVER (...)": Construct(
            "RANK() OVER (...)", Classification.DIRECT,
            template="rank ( sum ( [m] ) , 'desc' )",
            note=(
                "direct for one shape only, and the boundary is proven rather "
                "than asserted: the global, ORDER BY-only form over an "
                "aggregate. Live-confirmed 2026-07-30: rank ( sum ( [m] ) , "
                "'desc' ) and 'asc' both validate, and the arity is enforced at "
                "exactly two — a third argument in any shape (bare attribute, "
                "{ [attr] }, or query_groups ( )) is rejected with 'Function "
                "rank expects only 2 arguments', so an explicit PARTITION BY is "
                "provably not expressible (E13). Two further live-proven "
                "restrictions: the first argument must be aggregated (rank "
                "( [m] , 'desc' ) -> 'Function rank expects 1st argument to be "
                "aggregated'), so an Ossie ORDER BY <non-aggregated column> has "
                "no native target either; and it may not be a "
                "group_aggregate ( ... ), so the partition cannot be smuggled "
                "in through the measure. Every non-covered shape falls back to "
                "sql_int_aggregate_op ( \"RANK() OVER (PARTITION BY {0} ORDER "
                "BY SUM({1}) DESC)\" , ... ) (E3), wrapped per E8. Query-context "
                "caveat: rank carries no dynamic partition (E13) but it is "
                "evaluated over the query's result rows, so the covered shape "
                "is faithful to RANK() OVER (ORDER BY ...) only when the search "
                "returns the grain the expression assumed — a query-time "
                "semantic no import probe can observe, taken from ThoughtSpot's "
                "formula documentation rather than this run. The direction "
                "string is not validated at import ('descending' was "
                "accepted), so acceptance proves the call shape, never the "
                "ordering."
            ),
        ),
        "DENSE_RANK() OVER (...)": Construct(
            "DENSE_RANK() OVER (...)", Classification.PASSTHROUGH,
            template="dense_rank() over (order by sum({0}) desc)",
            variant=Variant.INT_AGGREGATE,
            note=(
                "ThoughtSpot's rank skips ranks after a tie; dense ranking has "
                "no native form — live-confirmed 2026-07-30, dense_rank ( ... ) "
                "rejected with 'Search did not find \"dense_rank ( sum (\"'. "
                "Passthrough is correct: no native ThoughtSpot construct produces "
                "dense-rank semantics."
            ),
        ),
        "NTILE(n) OVER (...)": Construct(
            "NTILE(n) OVER (...)", Classification.PASSTHROUGH,
            template="NTILE(4) OVER (ORDER BY SUM({0}))",
            variant=Variant.INT_AGGREGATE,
            note="n is a literal, baked into the template, as the aggregate percentiles are.",
        ),
        "PERCENT_RANK() OVER (...)": Construct(
            "PERCENT_RANK() OVER (...)", Classification.DIRECT,
            template="1 - rank_percentile ( sum ( [m] ) , 'asc' ) / 100",
            note=(
                "ThoughtSpot's rank_percentile is documented as "
                "(1.0 - PERCENT_RANK() OVER (ORDER BY ...)) * 100, so the "
                "inverse is exact. Two adjustments are both required: the "
                "scale (ThoughtSpot 0-100, specification 0-1) and the "
                "inversion. Dropping either produces a plausible-looking "
                "column that is wrong everywhere. Same shape restriction as "
                "RANK, and the same live-proven boundary — rank_percentile is "
                "also fixed at exactly two arguments ('Function rank_percentile "
                "expects only 2 arguments', live-verified 2026-07-30), so it "
                "too is global-only and an explicit PARTITION BY falls back to "
                "sql_number_aggregate_op ( \"PERCENT_RANK() OVER (PARTITION BY "
                "{0} ORDER BY SUM({1}))\" , ... ) (E3, E13). Same evidence-class "
                "caveat as RANK: the arity is probe-proven, the global-window "
                "semantic is documentation-derived. CUME_DIST is deliberately "
                "NOT given this same composition — see that row."
            ),
        ),
        "CUME_DIST() OVER (...)": Construct(
            "CUME_DIST() OVER (...)", Classification.PASSTHROUGH,
            template="CUME_DIST() OVER (ORDER BY SUM({0}))",
            variant=Variant.NUMBER_AGGREGATE,
            note=(
                "rank_percentile is NOT a substitute, despite PERCENT_RANK's "
                "row looking equivalent: PERCENT_RANK divides by n - 1 and "
                "starts at 0; CUME_DIST divides by n and ends at 1. They agree "
                "on no row of a tie-free window except the last, so there is no "
                "native fallback at all for this row."
            ),
        ),
        "LAG(expr, offset, default) OVER (...)": Construct(
            "LAG(expr, offset, default) OVER (...)", Classification.PASSTHROUGH,
            template="LAG({0}, 1) OVER (PARTITION BY {1} ORDER BY {2})",
            variant=Variant.NUMBER_AGGREGATE,
            note=(
                "Reclassified direct -> passthrough 2026-07-30 (E13). The "
                "native idiom moving_sum ( [m] , n , -n , [ord] ) is real and "
                "validates (a frame of n PRECEDING to n PRECEDING) but is not "
                "equivalent to any OVER shape: moving_sum has no partition "
                "slot, and ThoughtSpot completes the partition from the "
                "query's own dimensions instead. So an Ossie LAG with a "
                "PARTITION BY cannot be expressed, and one without a "
                "PARTITION BY still cannot, because ThoughtSpot's partition is "
                "not empty. The converter emits the pass-through by default "
                "and offers the native moving_sum idiom as a documented "
                "downgrade the user must accept: correct exactly when the "
                "search's dimensions are the intended partition. The default "
                "argument has no equivalent in the native idiom — ThoughtSpot "
                "yields null outside the frame — a second reason the native "
                "form is a downgrade (the pass-through carries default fine). "
                "Subject to E5 and E6. Variant recorded here is the documented "
                "default (sql_number_aggregate_op); the typed sibling applies "
                "for a non-numeric expr — LAG returns its argument's own type, "
                "not an aggregate, so a string-typed expr (LAG(order_status, "
                "1) OVER (...)) needs the typed sibling, not this default, or "
                "it imports cleanly and aggregates wrongly."
            ),
        ),
        "LEAD(expr, offset, default) OVER (...)": Construct(
            "LEAD(expr, offset, default) OVER (...)", Classification.PASSTHROUGH,
            template="LEAD({0}, 1) OVER (PARTITION BY {1} ORDER BY {2})",
            variant=Variant.NUMBER_AGGREGATE,
            note=(
                "Mirror of LAG, reclassified for the same reason and on the "
                "same date. The native downgrade is "
                "moving_sum ( [m] , -n , n , [ord] ) — ThoughtSpot's start/end "
                "arguments use opposite sign conventions, so a forward offset "
                "is a negative start (both live-confirmed 2026-07-30). Same "
                "default limitation as LAG. Variant recorded here is the "
                "documented default (sql_number_aggregate_op); the typed "
                "sibling applies for a non-numeric expr, same reason as LAG's "
                "note — LEAD returns its argument's own type, not an aggregate."
            ),
        ),
        "FIRST_VALUE(expr) OVER (...)": Construct(
            "FIRST_VALUE(expr) OVER (...)", Classification.DIRECT,
            template="first_value ( sum ( [m] ) , query_groups ( ) , {{ [T::date] }} )",
            note=(
                "The section's exception, and the only window row whose direct "
                "verdict survived the 2026-07-30 rework — first_value takes a "
                "genuine explicit partition argument and a genuine explicit "
                "order axis, so the formula does define its own window (E13). "
                "Live-confirmed 2026-07-30: query_groups ( ), "
                "a fixed single-column { [attr] }, a multi-column "
                "{ [a] , [b] }, the grand-total { } and the dynamic "
                "query_groups ( ) - { [attr] } all validate in the partition "
                "slot, so a static Ossie PARTITION BY list maps straight onto "
                "it. The axis slot is typed and enforced — a bare column "
                "reference is rejected with 'Function last_value expects 3rd "
                "argument to be List', so the { } braces are mandatory (and "
                "force >- block-scalar YAML on the document side; doubled here "
                "as {{ }} because emit_direct renders via str.format, the same "
                "fix the IN/NOT IN rows above need for the same reason — "
                "verified by calling emit_direct and checking the rendered "
                "output has single braces again). "
                "Two boundaries remain: ThoughtSpot's first_value is a "
                "semi-additive function over a date axis rather than a general "
                "window function, so an OVER shape with a row frame other than "
                "the whole partition falls back to "
                "sql_number_aggregate_op ( \"FIRST_VALUE({0}) OVER (...)\" , "
                "... ) (E3); and the axis column's type is not validated at "
                "import (a VARCHAR axis was accepted), so acceptance proves "
                "the call shape, not that the axis is temporal."
            ),
        ),
        "LAST_VALUE(expr) OVER (...)": Construct(
            "LAST_VALUE(expr) OVER (...)", Classification.DIRECT,
            template="last_value ( sum ( [m] ) , query_groups ( ) , {{ [T::date] }} )",
            note=(
                "Same conditions, same live evidence and same fallback as "
                "FIRST_VALUE. last_value_in_period and first_value_in_period "
                "also validate in the identical three-argument shape and are "
                "the period-completeness variants (see the reverse-direction "
                "table) — out of this row's scope. Braces doubled on the axis "
                "argument for the same str.format reason as FIRST_VALUE."
            ),
        ),
        "NTH_VALUE(expr, n) OVER (...)": Construct(
            "NTH_VALUE(expr, n) OVER (...)", Classification.PASSTHROUGH,
            template="NTH_VALUE({0}, 2) OVER (ORDER BY {1})",
            variant=Variant.NUMBER_AGGREGATE,
            note=(
                "ThoughtSpot's semi-additive functions reach only the first "
                "and last values of the axis — live-confirmed 2026-07-30, "
                "nth_value ( ... ) rejected with 'Search did not find "
                "\"nth_value ( sum (\"'. n is a literal, baked into the "
                "template, as NTILE's. Variant recorded here is the "
                "documented default (sql_number_aggregate_op); the typed "
                "sibling applies for a non-numeric expr, same reason as LAG's "
                "note — NTH_VALUE returns its argument's own type, not an "
                "aggregate."
            ),
        ),
        "OVER (PARTITION BY ... ORDER BY ...) clause": Construct(
            "OVER (PARTITION BY ... ORDER BY ...) clause", Classification.PASSTHROUGH,
            template="per-clause-shape — see note",
            variant=Variant.NUMBER_AGGREGATE,
            note=(
                "CONVENTION_DIVERGENCE: the generic OVER syntax template is a "
                "fenced code block, not a table. Reclassified direct -> "
                "passthrough 2026-07-30. The previous verdict claimed a clean "
                "structural rewrite — 'PARTITION BY attrs becomes the "
                "group_aggregate grouping argument; ORDER BY becomes the "
                "window function's trailing attribute arguments' — but that "
                "holds for PARTITION BY alone and breaks the moment an "
                "ORDER BY is present, which is most window use. There are two "
                "disjoint targets and only one accepts a partition: an OVER "
                "clause with a PARTITION BY and no ORDER BY/frame is "
                "group_aggregate ( agg ( [m] ) , { [T::a] , [T::b] } , "
                "query_filters ( ) ) and is lossless; an OVER clause with an "
                "ORDER BY must target moving_*/cumulative_*, which have no "
                "partition slot at all (E13). Live-confirmed accepted: a "
                "fixed single-column grouping { [T::pk] } inside "
                "group_aggregate (as a moving_* and a cumulative_* argument), "
                "and query_groups ( ) - { [attr] } / "
                "query_groups ( ) + { [attr] } inside group_aggregate. "
                "Live-confirmed rejected: moving_sum ( ... , [ord] , "
                "{ [attr] } ) and moving_sum ( ... , [ord] , query_groups ( ) "
                "), plus cumulative_sum ( ... , [ord] , { [attr] } ). Not "
                "probed: a bare { } or a bare query_groups ( ) as the "
                "group_aggregate grouping argument, and the query_groups ( ) "
                "form of the cumulative_sum rejection — those three cells rest "
                "on the formula reference, not this run. A partitioned, "
                "ordered window therefore has no native home and the whole "
                "clause is out of catalog scope for the general case — "
                "template records the dispatch rather than one substitutable "
                "body, same treatment as CAST's per-type table. Variant "
                "recorded here is the documented default "
                "(sql_number_aggregate_op); the typed sibling applies for a "
                "non-numeric aggregate. The reverse direction is lossy for the "
                "mirror-image reason — ThoughtSpot's ordered window functions "
                "add the query's own dimensions to the partition dynamically, "
                "which the specification cannot express (ask A10)."
            ),
        ),
        "Frame clause — ROWS BETWEEN ... / RANGE BETWEEN ...": Construct(
            "Frame clause — ROWS BETWEEN ... / RANGE BETWEEN ...", Classification.DIRECT,
            template="per-frame-shape — see note",
            note=(
                "CONVENTION_DIVERGENCE: frame options are a bullet list under "
                "the OVER syntax section, not a table. direct for the frame "
                "boundaries only — deliberately scoped, so the partition loss "
                "is counted once, on the OVER clause row, and not twice. "
                "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW -> "
                "cumulative_*. Bounded ROWS frames -> moving_* with "
                "n PRECEDING -> positive n, CURRENT ROW -> 0, n FOLLOWING -> "
                "negative -n. All four boundary shapes were live-confirmed, "
                "2026-07-30 (moving_sum ( [m] , 2 , 0 , [ord] "
                "), ( ... , 1 , -1 , ... ), ( ... , -1 , 1 , ... ), "
                "cumulative_sum ( [m] , [ord] )), and the positional signature "
                "is enforced — moving_sum ( [m] , [ord] ) is rejected with "
                "'Function moving_sum expects 2nd argument to be Numeric'. "
                "RANGE frames fall back to sql_number_aggregate_op (the same "
                "variant the window-aggregation row below falls back to): "
                "ThoughtSpot's frames are row-positional, not value-ranged — "
                "live-verified on gapped dates, moving_* counts surviving rows "
                "regardless of the calendar distance between them — so a "
                "RANGE frame over a gapped sort column would silently return "
                "different numbers (E3). A frame reaches ThoughtSpot natively "
                "only when the accompanying OVER clause declares no "
                "PARTITION BY; otherwise it is emitted verbatim inside the "
                "pass-through template the OVER row selects. Per-shape "
                "dispatch out of catalog scope, same treatment as CAST's "
                "per-type table."
            ),
        ),
        "Window aggregation — AGG(expr) OVER (...)": Construct(
            "Window aggregation — AGG(expr) OVER (...)", Classification.PASSTHROUGH,
            template="SUM({0}) OVER (PARTITION BY {1} ORDER BY {2} "
                     "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
            variant=Variant.NUMBER_AGGREGATE,
            note=(
                "CONVENTION_DIVERGENCE: the Window Aggregations section is "
                "prose and code examples, not a table. Reclassified direct -> "
                "passthrough 2026-07-30, inheriting the OVER row's problem: "
                "the specification allows every aggregate as a window "
                "function, but every ordered ThoughtSpot target "
                "(cumulative_*, moving_*) completes its partition from the "
                "query (E13). The unordered case remains lossless and is the "
                "group_aggregate path on the OVER row. The native family is "
                "also narrower than the specification's: cumulative_*/"
                "moving_* cover SUM, AVG, MIN and MAX only — live-confirmed "
                "2026-07-30 that moving_count, moving_stddev and "
                "cumulative_count do not exist ('Search did not find "
                "\"moving_count (\"' and siblings) — so a windowed COUNT, "
                "MEDIAN, STDDEV or VARIANCE has a partitioned form via "
                "group_count/group_stddev/group_variance and no ordered or "
                "framed form of any kind. The frame is an exemplar, the same "
                "convention as NTILE's literal 4 (see the Construct.template "
                "docstring): ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW "
                "is the cumulative-aggregate boundary the Frame clause row "
                "above maps cumulative_* to, and is one concrete, valid frame "
                "among the ones a real occurrence could carry — a caller "
                "rebuilds the frame per occurrence, same as any other "
                "exemplar row. The mapping document's own cell for this row "
                "writes the frame as a literal ellipsis ('ROWS BETWEEN …'), "
                "which is prose shorthand for 'a frame clause goes here', not "
                "renderable SQL — transcribing it verbatim rendered warehouse "
                "syntax errors at query time, so this template supplies a "
                "concrete, valid frame instead. Variant recorded here is the "
                "documented default (sql_number_aggregate_op); the typed "
                "sibling applies for a non-numeric aggregate. Subject to E5."
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
#: document's actual row list — see catalog.py's docstring for the
#: reconciliation (137 + 9 == 146).
#:
#: Two mechanisms an earlier pass mistakenly guessed would appear here do NOT:
#: `CEIL(x)`/`CEILING(x)`, `TRUNC(x, d)`/`TRUNCATE(x, d)` and `TRUE`/`FALSE` are
#: each ONE row in the mapping document too (not split), matching spec_name's
#: single merged entry — a spelling-convention question addressed throughout
#: this file (see the module docstring's "Spelling" note), not a divergence.
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
