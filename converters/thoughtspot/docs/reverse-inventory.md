<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

<!-- GENERATED FILE -- do not edit by hand.
     Produced by `tools/generate_reference_docs.py` from:
     - `src/ossie_thoughtspot/expressions/reverse.py`
     Regenerate with:
       uv run --python 3.13 python tools/generate_reference_docs.py
     tests/test_reference_docs_current.py fails the suite if this file
     drifts from what the generator currently produces. -->

# ThoughtSpot -> Ossie Reverse Inventory

ThoughtSpot's own native functions with no counterpart in the Ossie specification (`ThoughtSpot -> Ossie`, the reverse of the expression mapping above), and how each reaches — or does not reach — a portable Ossie expression. This inventory is not yet called from the shipped `TML -> Ossie` conversion path; see `converters/thoughtspot/README.md`'s "Expression translation" section for the converter's current, more conservative default.

## Coverage

| Disposition | Count | Share | Meaning |
|---|---|---|---|
| compose | 49 | 62% | A full, portable Ossie expression is produced. |
| partial | 8 | 10% | A real Ossie expression is produced, but it is provably incomplete. |
| dialect | 10 | 13% | Resolves to the Ossie `dialects[]` mechanism, not a portable expression. |
| stash | 12 | 15% | No Ossie expression exists at all; preserved verbatim for round-trip only. |
| **Total** | **79** | **100%** |  |

## Cross-cutting dispatch, not name-keyed

Two checks apply before an ordinary lookup by name into `REVERSE`, so they are not rows of the table below:

- **Fiscal-calendar argument.** Any call whose last argument is `'fiscal'`, `fiscal` stashes unconditionally, for any function name at all, before the name is looked up.
- **Hyperlink markup.** A `concat` call whose string arguments contain `{caption}` or `{/caption}` is redirected to the `concat (hyperlink markup)` row below; plain `concat` has a specification counterpart already covered by `CATALOG` and is not this module's concern.

## Every entry

| ThoughtSpot construct | Disposition | Composes to | Issue | Notes |
|---|---|---|---|---|
| `sum_if` | compose | `SUM(CASE WHEN {0} THEN {1} END)` | — | sum_if ( cond , x ) -> SUM(CASE WHEN cond THEN x END). |
| `count_if` | compose | `COUNT(CASE WHEN {0} THEN {1} END)` | — | count_if ( cond , x ) -> COUNT(CASE WHEN cond THEN x END). |
| `average_if` | compose | `AVG(CASE WHEN {0} THEN {1} END)` | — | average_if ( cond , x ) -> AVG(CASE WHEN cond THEN x END). |
| `min_if` | compose | `MIN(CASE WHEN {0} THEN {1} END)` | — | min_if ( cond , x ) -> MIN(CASE WHEN cond THEN x END). |
| `max_if` | compose | `MAX(CASE WHEN {0} THEN {1} END)` | — | max_if ( cond , x ) -> MAX(CASE WHEN cond THEN x END). |
| `stddev_if` | compose | `STDDEV(CASE WHEN {0} THEN {1} END)` | — | stddev_if ( cond , x ) -> STDDEV(CASE WHEN cond THEN x END). |
| `variance_if` | compose | `VARIANCE(CASE WHEN {0} THEN {1} END)` | — | variance_if ( cond , x ) -> VARIANCE(CASE WHEN cond THEN x END). |
| `unique_count_if` | compose | `COUNT(DISTINCT CASE WHEN {0} THEN {1} END)` | — | unique_count_if ( cond , x ) -> COUNT(DISTINCT CASE WHEN cond THEN x END). |
| `unique count` | compose | `COUNT(DISTINCT {0})` | — | ThoughtSpot's own spelling has a space, not an underscore. |
| `safe_divide` | compose | `COALESCE({0} / NULLIF({1}, 0), 0)` | — | The zero-not-null result is preserved by the explicit COALESCE. |
| `pow` | compose | `POWER({0}, {1})` | — | — |
| `log2` | compose | `LOG(2, {0})` | — | — |
| `strlen` | compose | `LENGTH({0})` | — | — |
| `strpos` | compose | `POSITION({1} IN {0})` | — | ThoughtSpot strpos(s, sub) -> Ossie POSITION(sub IN s); operand order reverses. |
| `substr` | compose | `SUBSTRING({0}, {1} + 1, {2})` | — | ThoughtSpot's substr is 0-based; the +1 is mandatory going this way. |
| `left` | compose | `LEFT({0}, {1})` | — | — |
| `right` | compose | `RIGHT({0}, {1})` | — | — |
| `sin` | compose | `SIN(RADIANS({0}))` | — | ThoughtSpot trigonometry is in degrees; the conversion reverses. |
| `cos` | compose | `COS(RADIANS({0}))` | — | ThoughtSpot trigonometry is in degrees; the conversion reverses. |
| `tan` | compose | `TAN(RADIANS({0}))` | — | ThoughtSpot trigonometry is in degrees; the conversion reverses. |
| `asin` | compose | `DEGREES(ASIN({0}))` | — | ThoughtSpot's inverse trig functions return degrees. |
| `acos` | compose | `DEGREES(ACOS({0}))` | — | ThoughtSpot's inverse trig functions return degrees. |
| `atan` | compose | `DEGREES(ATAN({0}))` | — | ThoughtSpot's inverse trig functions return degrees. |
| `to_integer` | compose | `CAST({0} AS INTEGER)` | — | — |
| `to_double` | compose | `CAST({0} AS DOUBLE)` | — | — |
| `to_string` | compose | `CAST({0} AS VARCHAR)` | — | — |
| `to_date` | compose | `TO_DATE({0}, {1})` | `TS-EXPR-FORMAT-TOKENS-PASSTHROUGH` · INFO | Judgment call: format-token reversal is deferred until an expression parser exists to do the translation. |
| `if` | compose | `CASE WHEN {0} THEN {1} ELSE {2} END` | — | if ( c ) then a else b -> CASE WHEN c THEN a ELSE b END, or IF(c, a, b). |
| `rank` | compose | dynamic — see `_compose_rank` in `reverse.py` | — | Global, ORDER-BY-only shape only — rank's arity is fixed at exactly two (live-confirmed), so there is never a partition to lose in this direction. |
| `rank_percentile` | compose | dynamic — see `_compose_rank_percentile` in `reverse.py` | — | Scale (0-100 -> 0-1) and inversion both reverse. |
| `moving_sum` | partial | dynamic — see `_compose_moving.<locals>._compose` in `reverse.py` | `TS-EXPR-PARTIAL-PARTITION` · WARNING | Frame and order translate exactly; the partition does not — a ThoughtSpot window formula cannot declare its own PARTITION BY, and the specification has no way to express that limitation. |
| `cumulative_sum` | partial | dynamic — see `_compose_cumulative.<locals>._compose` in `reverse.py` | `TS-EXPR-PARTIAL-PARTITION` · WARNING | Frame and order translate exactly; the partition does not — a ThoughtSpot window formula cannot declare its own PARTITION BY, and the specification has no way to express that limitation. |
| `moving_average` | partial | dynamic — see `_compose_moving.<locals>._compose` in `reverse.py` | `TS-EXPR-PARTIAL-PARTITION` · WARNING | Frame and order translate exactly; the partition does not — a ThoughtSpot window formula cannot declare its own PARTITION BY, and the specification has no way to express that limitation. |
| `cumulative_average` | partial | dynamic — see `_compose_cumulative.<locals>._compose` in `reverse.py` | `TS-EXPR-PARTIAL-PARTITION` · WARNING | Frame and order translate exactly; the partition does not — a ThoughtSpot window formula cannot declare its own PARTITION BY, and the specification has no way to express that limitation. |
| `moving_max` | partial | dynamic — see `_compose_moving.<locals>._compose` in `reverse.py` | `TS-EXPR-PARTIAL-PARTITION` · WARNING | Frame and order translate exactly; the partition does not — a ThoughtSpot window formula cannot declare its own PARTITION BY, and the specification has no way to express that limitation. |
| `cumulative_max` | partial | dynamic — see `_compose_cumulative.<locals>._compose` in `reverse.py` | `TS-EXPR-PARTIAL-PARTITION` · WARNING | Frame and order translate exactly; the partition does not — a ThoughtSpot window formula cannot declare its own PARTITION BY, and the specification has no way to express that limitation. |
| `moving_min` | partial | dynamic — see `_compose_moving.<locals>._compose` in `reverse.py` | `TS-EXPR-PARTIAL-PARTITION` · WARNING | Frame and order translate exactly; the partition does not — a ThoughtSpot window formula cannot declare its own PARTITION BY, and the specification has no way to express that limitation. |
| `cumulative_min` | partial | dynamic — see `_compose_cumulative.<locals>._compose` in `reverse.py` | `TS-EXPR-PARTIAL-PARTITION` · WARNING | Frame and order translate exactly; the partition does not — a ThoughtSpot window formula cannot declare its own PARTITION BY, and the specification has no way to express that limitation. |
| `group_aggregate` | compose | dynamic — see `_dispatch_group_aggregate` in `reverse.py` | — | Shape dispatch on the grouping/filter arguments — see _compose_grouped. |
| `group_sum` | compose | dynamic — see `_make_group_shorthand_dispatch.<locals>._dispatch` in `reverse.py` | — | Shorthand for group_aggregate(sum(m), ...) — same shape dispatch. Judgment call: the (m, grouping, filter) 3-argument shape is assumed by analogy with group_aggregate's live-confirmed form; the shorthand family's own arity was not independently live-tested. |
| `group_count` | compose | dynamic — see `_make_group_shorthand_dispatch.<locals>._dispatch` in `reverse.py` | — | Shorthand for group_aggregate(count(m), ...) — same shape dispatch. Judgment call: the (m, grouping, filter) 3-argument shape is assumed by analogy with group_aggregate's live-confirmed form; the shorthand family's own arity was not independently live-tested. |
| `group_stddev` | compose | dynamic — see `_make_group_shorthand_dispatch.<locals>._dispatch` in `reverse.py` | — | Shorthand for group_aggregate(stddev(m), ...) — same shape dispatch. Judgment call: the (m, grouping, filter) 3-argument shape is assumed by analogy with group_aggregate's live-confirmed form; the shorthand family's own arity was not independently live-tested. |
| `group_variance` | compose | dynamic — see `_make_group_shorthand_dispatch.<locals>._dispatch` in `reverse.py` | — | Shorthand for group_aggregate(variance(m), ...) — same shape dispatch. Judgment call: the (m, grouping, filter) 3-argument shape is assumed by analogy with group_aggregate's live-confirmed form; the shorthand family's own arity was not independently live-tested. |
| `last_value` | stash | — | `TS-EXPR-SEMI-ADDITIVE` · ERROR | The window clause itself round-trips; only the roll-up declaration is lost. |
| `first_value` | stash | — | `TS-EXPR-SEMI-ADDITIVE` · ERROR | The window clause itself round-trips; only the roll-up declaration is lost. |
| `last_value_in_period` | stash | — | `TS-EXPR-SEMI-ADDITIVE` · ERROR | The window clause itself round-trips; only the roll-up declaration is lost. |
| `first_value_in_period` | stash | — | `TS-EXPR-SEMI-ADDITIVE` · ERROR | The window clause itself round-trips; only the roll-up declaration is lost. |
| `sql_string_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_int_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_double_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_bool_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_date_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_date_time_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_string_aggregate_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_int_aggregate_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_number_aggregate_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `sql_date_time_aggregate_op` | dialect | dynamic — see `_dispatch_sql_op` in `reverse.py` | — | Resolves to the Ossie dialects[] mechanism for the connection's own dialect, not a portable expression — the right home for raw warehouse SQL. |
| `<runtime parameter reference>` | stash | — | `TS-EXPR-RUNTIME-PARAMETER` · ERROR | Synthetic key — not a callable name. See stash_runtime_parameter(). |
| `ts_username` | stash | — | `TS-EXPR-RUNTIME-IDENTITY` · ERROR | `ts_username` resolves signed-in-user identity at query time; an interchange document that carried it would describe an access-control decision, not semantics. Preserved verbatim for roundtrip. |
| `ts_groups` | stash | — | `TS-EXPR-RUNTIME-IDENTITY` · ERROR | `ts_groups` resolves signed-in-user identity at query time; an interchange document that carried it would describe an access-control decision, not semantics. Preserved verbatim for roundtrip. |
| `ts_groups_int` | stash | — | `TS-EXPR-RUNTIME-IDENTITY` · ERROR | `ts_groups_int` resolves signed-in-user identity at query time; an interchange document that carried it would describe an access-control decision, not semantics. Preserved verbatim for roundtrip. |
| `ts_org` | stash | — | `TS-EXPR-RUNTIME-IDENTITY` · ERROR | `ts_org` resolves signed-in-user identity at query time; an interchange document that carried it would describe an access-control decision, not semantics. Preserved verbatim for roundtrip. |
| `ts_email_domain` | stash | — | `TS-EXPR-RUNTIME-IDENTITY` · ERROR | `ts_email_domain` resolves signed-in-user identity at query time; an interchange document that carried it would describe an access-control decision, not semantics. Preserved verbatim for roundtrip. |
| `ts_var` | stash | — | `TS-EXPR-RUNTIME-IDENTITY` · ERROR | `ts_var` resolves signed-in-user identity at query time; an interchange document that carried it would describe an access-control decision, not semantics. Preserved verbatim for roundtrip. |
| `concat (hyperlink markup)` | stash | — | `TS-EXPR-HYPERLINK-MARKUP` · ERROR | Synthetic key, reached only via the content-pattern check in translate_thoughtspot -- plain concat (no markup) is out of this module's scope entirely. |
| `month` | compose | `TO_CHAR({0}, 'MONTH')` | `TS-EXPR-LOCALE-DEPENDENT` · WARNING | Name-returning form, distinct from month_number/year/day_number_of_week. |
| `year_name` | compose | `TO_CHAR({0}, 'YYYY')` | `TS-EXPR-LOCALE-DEPENDENT` · WARNING | Name-returning form, distinct from month_number/year/day_number_of_week. |
| `day_of_week` | compose | `TO_CHAR({0}, 'DAY')` | `TS-EXPR-LOCALE-DEPENDENT` · WARNING | Name-returning form, distinct from month_number/year/day_number_of_week. |
| `month_number_of_quarter` | compose | `MOD(MONTH({0}) - 1, 3) + 1` | — | — |
| `day_number_of_quarter` | compose | `DATEDIFF(day, DATE_TRUNC('quarter', {0}), {0}) + 1` | — | — |
| `week_number_of_month` | compose | `DATEDIFF(week, DATE_TRUNC('month', {0}), {0}) + 1` | `TS-EXPR-WEEK-START-ASSUMED` · WARNING | `week_number_of_month` is correct only if the target engine's week start agrees with the specification's fixed Monday start; ThoughtSpot's week start is an instance setting. Verify alignment before relying on this column. |
| `week_number_of_quarter` | compose | `DATEDIFF(week, DATE_TRUNC('quarter', {0}), {0}) + 1` | `TS-EXPR-WEEK-START-ASSUMED` · WARNING | `week_number_of_quarter` is correct only if the target engine's week start agrees with the specification's fixed Monday start; ThoughtSpot's week start is an instance setting. Verify alignment before relying on this column. |
| `is_weekend` | compose | `DATE_PART('dayofweek', {0}) IN (6, 7)` | `TS-EXPR-DAYOFWEEK-BASE` · WARNING | `is_weekend`'s member list (6, 7) uses ThoughtSpot's own DAYOFWEEK base (1 = Monday); the specification does not fix a base and engines disagree — confirm the target engine's base agrees before relying on this column. |
| `start_of_hour` | compose | `DATE_TRUNC('hour', {0})` | — | — |
| `start_of_min` | compose | `DATE_TRUNC('minute', {0})` | — | — |
| `date` | compose | `DATE_TRUNC('day', {0})` | — | — |
| `time` | compose | `CAST({0} AS TIME)` | — | — |
| `greatest` | compose | dynamic — see `_compose_variadic.<locals>._compose` in `reverse.py` | — | Never MAX — that would turn a row-wise attribute into an aggregate measure. |
| `least` | compose | dynamic — see `_compose_variadic.<locals>._compose` in `reverse.py` | — | Never MIN, for the same reason. |

