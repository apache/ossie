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

ThoughtSpot BI SQL examples
===========================

Real SQL that [ThoughtSpot](https://www.thoughtspot.com/) generated while querying
metrics defined outside the tool, in a warehouse-native semantic layer.

These are the query shapes a SQL interface to Ossie would have to serve. They are
captured rather than composed: each one is what the tool actually emitted for an
ordinary question, so they show the constructs a real BI generator reaches for, and the
assumptions it makes on the way.

The model
---------

A six-table star, with a Databricks Metric View defined over it. Measures are read
through `MEASURE()`. `setup.sql` declares the tables and the view, and also carries a
Snowflake Semantic View over the same star, so the same measures can be compared as two
vendors express them.

No data ships. The queries are reproduced for their SQL shape rather than their results,
so nothing here depends on the original rows.

How the SQL was produced
------------------------

None of the SQL was written by hand.

Most queries are saved ThoughtSpot Answers — a question typed into the search bar — read
back with `POST /api/rest/2.0/metadata/answer/sql`. The four in
`07-scalar-and-string-functions.sql` are AgentQL (Semantic SQL) statements written
against the model and compiled by the same generator. The line above each query is the
input that produced it.

Each statement is verbatim apart from three edits: the generator's own
`/* Viz id : ... */` and `/* Query N */` comments are removed, trailing whitespace is
stripped, and a terminator is added so each file reads as a script.

What the captures show
----------------------

Eight files, by feature area. A few things in them bear directly on what a SQL interface
to reusable semantics has to decide:

- **A period comparison is emitted as one scan per period plus a spine, though it need
  not be.**
  `SUM(CASE WHEN ... THEN MEASURE(amount) END)` really is rejected — an aggregate cannot
  nest inside another — but an aggregate `FILTER (WHERE ...)` over the same measure,
  given the same overall period scope, returns the identical result in a single pass. The shape reflects how this generator
  composes period scopes, not a cost of reading a measure by name
  (`05-period-over-period.sql`).
- **A top-N sub-select carries no tiebreak.** It sorts on the measure alone, so which
  rows survive `LIMIT` at a tie is not determined. A folded top-N whose search asked for a
  sort does carry one, so the tiebreak comes from the request
  (`03-top-n-and-subselects.sql`).
- **A measure can be declared so that it is expressible where plain SQL would refuse
  it.** A windowed measure declared as a dimension is selected bare, grouped by, and
  filtered on in `WHERE` — neither of the last two of which a window function permits
  at the same query level. The same measure is declared as a *metric* on the Snowflake layer over the same
  star, and the two then disagree under a filter: one evaluates the window before the
  filter, the other after, and neither errors
  (`04-lod-two-stage-aggregation.sql`).
- **Functions are rewritten four different ways** on the path to the warehouse — passed
  through, renamed for the dialect, re-expressed as a different function, or expanded
  into a nested template. `SELECT DISTINCT` becomes `GROUP BY` every time
  (`07-scalar-and-string-functions.sql`).
- **Little of the visual form reaches the SQL.** A pivot table and a straight table
  built from byte-identical searches compile to the same shape, differing only in the
  order of the `SELECT` list — and because aliases are positional, that difference alone
  makes `ca_2` a different measure in each (`08-result-shape.sql`).
- **The generator guards constants.** A string literal escaped through three nested
  `replace()` calls, `NULLIF(3,0)`, `case 1 when 1 then 1 else ...` with an unreachable
  arm. Each is harmless; together they are a house style worth recognising when reading
  generated SQL.

Reading the captured SQL
------------------------

Everything outside the comments is ThoughtSpot's own bar the terminator, including the
backtick quoting and
Databricks dialect, the fully qualified `catalog`.`schema`.`view` names, and the
generated `ta_N` (table), `ca_N` (column) and `qt_N` (CTE) aliases.

Three properties matter if you reuse these statements rather than just read them:

- **They are dated.** A relative date in the input — `[Order Date].'last month'` — is
  resolved when the SQL is generated and arrives as two bare constants, with no
  `CURRENT_DATE` anywhere. A capture permanently means the month it was taken in.
- **Column aliases are positional, not stable.** Two Answers with byte-identical inputs
  project their measures in different orders, so `ca_2` names a different measure in
  each. Binding by alias would silently swap them.
- **A conditional is decided before generation, and the losing arm is discarded.** An
  Answer whose formula switches between two measures emits one of them and no `CASE` —
  no condition, no parameter, no mention of the measure not chosen. The SQL therefore
  cannot tell you whether the switch worked or its condition simply never matched.

Some lines are long. The inputs are reproduced so they can be pasted back into
ThoughtSpot, one generated expression repeats eleven times in a single statement, and one
Snowflake metric declaration is long in the deployed definition. Wrapping the generated
SQL or the inputs would misrepresent what the tool produced.
