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


Query compliance tests
======================

Test cases for the
[Relational Query Interface](../../core-spec/relational_semantics.md).

This directory sets up the layout and the case format.

Layout
------

| Path | Contents |
|------|----------|
| [`scott/scott.sql`](scott/scott.sql) | The SCOTT schema and its data. |
| [`scott/scott_model.ossie.yaml`](scott/scott_model.ossie.yaml) | An Ossie model over SCOTT, with dataset-scoped metrics. |
| [`cases/`](cases/) | One directory for each case. |

All cases use the same data and the same model.

Each case directory has three files:

| File | Contents |
|------|----------|
| `query.ossie.sql` | The Ossie query. It evaluates measures with `AGG()`, and it refers to the model relations by two-part name, for example `scott_model.emp`. |
| `expected.csv` | The expected result of the Ossie query. |
| `reference.duckdb.sql` | A DuckDB query over the base tables in `scott/scott.sql`. Its result is `expected.csv`. |

The part of the file name before `.sql` gives the SQL dialect of the file:
`.ossie.sql` for an Ossie query, and `.duckdb.sql` for DuckDB SQL.

An implementation passes a case if it loads the data and the model, accepts
`query.ossie.sql`, and returns a result that matches `expected.csv` by the
rules in "Comparison of results". The implementation does not use
`reference.duckdb.sql`.

See [`cases/a01_grand_total/`](cases/a01_grand_total/) for an example.

Case files
----------

`query.ossie.sql` starts with a header in SQL comments: the case ID and
title, one or two sentences about the behavior under test, and the spec
sections (for example, `Spec: 6, 7.2`). The query follows the header. A
harness can submit the file as it is.

`reference.duckdb.sql` uses the same column names as `query.ossie.sql`.

- For a case on one relation, the reference query is the Ossie query with each
  `AGG(m)` replaced by the expression of `m`, and each model relation replaced
  by its source table.
- For a case with joins, the reference query applies the reference evaluation
  strategy of Section 9 of the spec. It removes join duplicates by row identity
  (the dataset's `primary_key`), and then it aggregates.

`expected.csv` is a CSV file (RFC 4180) with one header row:

- The header row holds the column names of the query.
- NULL is an empty field without quotes. An empty string is `""`.
- Values are not rounded, for example `2073.214285714286`.
- If the query has `ORDER BY`, the rows are in that order.

DuckDB writes this format with `COPY ... TO ... (HEADER)`.

Run
---

DuckDB needs no extensions. To run all reference queries:

```sh
cat compliance/query/scott/scott.sql \
  compliance/query/cases/*/reference.duckdb.sql | duckdb
```

To write `expected.csv` for one case from its reference query:

```sh
CASE=compliance/query/cases/a01_grand_total
duckdb -c ".read compliance/query/scott/scott.sql" \
  -c "SET VARIABLE q = (SELECT content FROM read_text('$CASE/reference.duckdb.sql'))" \
  -c "COPY (FROM query(getvariable('q'))) TO '$CASE/expected.csv' (HEADER)"
```

To check `expected.csv` instead, write to `/dev/stdout` and compare with
`diff - "$CASE/expected.csv"`.

To check the model:

```sh
uv run validation/validate.py compliance/query/scott/scott_model.ossie.yaml
```

Semantics of the expected results
---------------------------------

The expected results use standard SQL semantics (ISO/IEC 9075) for aggregates
and for scalar operators. Section 8.1 of the spec refers to the "host
dialect". For these tests, the host dialect is standard SQL. For example, `SUM`
and `AVG` over no rows give NULL, and `COUNT` over no rows gives 0.

If the defaults of an engine are different (for example, if `SUM` over no rows
gives 0), run the engine in a mode with standard SQL semantics.

Comparison of results
---------------------

- Compare column names without regard to case.
- Compare row order only if the Ossie query has `ORDER BY`.
- Do not compare data types. The SQL standard lets an implementation choose
  some result types, for example the precision of `COUNT(*)`.
- A value is exact or approximate by the type that the SQL standard gives to
  its expression. `COUNT(*)` is exact. `SUM(sal)` and `AVG(sal)` are
  approximate, because `sal` is `DOUBLE PRECISION`.
- Exact numerics must be equal.
- An approximate numeric matches if
  `|actual - expected| <= 1e-9 * max(|expected|, 1)`. This is a relative
  tolerance of 1e-9. For an expected value between -1 and 1, it is an absolute
  tolerance of 1e-9.

Adding a case
-------------

- Add one directory in `cases/` for each case, with the three files above.
  Name the directory with the lowercase case ID, with a two-digit number, and
  a short name, for example `a02_group_by`. The two-digit number keeps the
  directories in ID order.
- Use the ID prefix `A` for a case on one relation, and `B` for a case with
  joins.
- Use only the data in `scott/scott.sql`. Do not change the data, because the
  expected results of all cases depend on it.
- Use the model in `scott/scott_model.ossie.yaml`. If a case needs a new metric,
  add the metric in the same pull request, and check the model with
  `validation/validate.py`.
- Write `expected.csv` from `reference.duckdb.sql` with the command in "Run".
  Do not write it by hand. If possible, confirm it with a second, independent
  query. For a case with joins, the second query can write the input set of
  each measure directly from Section 8.1, for example with `EXISTS` over the
  join condition.
- Do not use an expression whose result type the SQL standard leaves
  implementation-defined, such as `AVG` over an exact numeric or the division
  of two exact numerics. Use a `DOUBLE PRECISION` column or a `CAST` instead.
- Prefer queries whose result does not depend on other implementation-defined
  behavior. If a result does depend on it, say so in the header of
  `query.ossie.sql`.

Future work
-----------

These tests are a light version of the `.ost` test format
([#501](https://github.com/apache/ossie/issues/501)) and the view expander
([#502](https://github.com/apache/ossie/issues/502)). When those are
available, each case can become an `.ost` test, and a runner can check the
Ossie query directly.
