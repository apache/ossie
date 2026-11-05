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

Compliance tests for the
[Relational Query Interface](../../core-spec/relational_semantics.md). All
cases use the same SCOTT data and Ossie model.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are to be
interpreted as described in BCP 14
([RFC 2119](https://www.rfc-editor.org/rfc/rfc2119),
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)) when they appear in
uppercase.

Layout
------

| Path | Contents |
|------|----------|
| [`scott/scott.sql`](scott/scott.sql) | SCOTT schema and data |
| [`scott/scott_model.ossie.yaml`](scott/scott_model.ossie.yaml) | Ossie model with dataset-scoped metrics |
| `cases/<case>/query.ossie.sql` | Ossie query under test |
| `cases/<case>/expected.sql` | Expected result, as standard SQL over literals |
| `cases/<case>/reference.duckdb.sql` | Non-normative DuckDB query that derives the expected result |

The file name suffix gives the SQL dialect: `.ossie.sql` for Ossie SQL,
`.duckdb.sql` for DuckDB SQL, and `.sql` for standard SQL (ISO/IEC 9075).

A case passes when an implementation loads the data and the model, runs
`query.ossie.sql`, and returns a result equal to the result of `expected.sql`
(see "Comparing results"). For an example, see
[`cases/a01_grand_total/`](cases/a01_grand_total/).

The cases refer to each dataset by the name `<model name>.<dataset name>`, for
example `scott_model.emp`.

Case files
----------

`query.ossie.sql` starts with a comment header: the case ID and title, the
behavior under test, and the spec sections (for example, `Spec: 6, 7.2`). The
file can be submitted to an implementation as it is.

`expected.sql` is one query:

```sql
SELECT *
FROM (VALUES (29025.00, 14)) AS expected (sum_sal, emp_count);
```

- `expected.sql` MUST use the column names and order of `query.ossie.sql`.
- Values MUST be standard SQL literals. An exact result MUST be an exact
  numeric literal (`29025.00`). An approximate result MUST be an approximate
  numeric literal (`2.073214285714286E3`).
- A column that is NULL in every row MUST give its type in the first row, with
  `CAST(NULL AS <type>)`.
- An empty result MUST use one typed row and `WHERE 1 = 0`.
- If `query.ossie.sql` has `ORDER BY`, the rows MUST be in that order.

`reference.duckdb.sql` is non-normative. If it disagrees with the spec, the
spec is correct, and the case MUST be fixed. It uses the column names of
`query.ossie.sql`. For one relation, it replaces each `AGG(m)` with the
expression of `m`, and each model relation with its source table. For joins,
it follows the reference evaluation strategy of Section 9: it removes join
duplicates by `primary_key`, and then it aggregates.

Result semantics
----------------

Expected results use standard SQL (ISO/IEC 9075) semantics. For these tests,
the host dialect of Section 8.1 is standard SQL. For example, `SUM` over no
rows is NULL, and `COUNT` over no rows is 0. An implementation with other
defaults MUST run the tests with standard SQL semantics.

A case MUST NOT return an exact numeric whose scale the standard leaves
implementation-defined, such as `AVG` over an exact numeric or the division of
two exact numerics. The tolerance does not cover these results: for `AVG` over
`DECIMAL(7, 2)`, an implementation can return a scale of 2, which differs from
the true value by more than 1e-9. To use `AVG`, cast its argument to an
approximate type, for example `AVG(CAST(sal AS DOUBLE PRECISION))`. A case
MUST NOT return NaN or an infinity.

Comparing results
-----------------

- Columns are matched by position. The column counts MUST be equal. Column
  names are compared case-insensitively, so the implementation's own column
  names are not required to match exactly.
- Rows MUST match as multisets, including the number of duplicates.
- If `query.ossie.sql` has `ORDER BY`, the row order MUST also match, and the
  sort key MUST order the result uniquely.
- Two values match if both are NULL, or if neither is NULL and they are equal.
  Types are not compared: `29025.00` matches `29025`.
- Exact numerics MUST be equal.
- An approximate numeric `a` matches an expected value `e` if
  `|a - e| <= 1e-9 * max(|a|, |e|, 1)`. This is a relative tolerance of 1e-9,
  with an absolute tolerance of 1e-9 near zero.

The rule for approximate numerics is provisional. How to compare
floating-point results across engines is an open question: the size of the
tolerance, the tolerance near zero, and how to match rows as multisets when
values match only within the tolerance. Until it is settled, cases SHOULD use
exact results.

Running the tests
-----------------

DuckDB needs no extensions. To run all reference queries:

```sh
cat compliance/query/scott/scott.sql \
  compliance/query/cases/*/reference.duckdb.sql | duckdb
```

To show the result of a reference query above the result of its
`expected.sql`:

```sh
CASE=compliance/query/cases/a01_grand_total
cat compliance/query/scott/scott.sql \
  "$CASE/reference.duckdb.sql" "$CASE/expected.sql" | duckdb
```

To validate the model:

```sh
uv run validation/validate.py compliance/query/scott/scott_model.ossie.yaml
```

Adding a case
-------------

- Add a directory under `cases/` with the three files. Name it with the
  lowercase case ID, with a two-digit number, and a short name, for example
  `a02_group_by`. Use the ID prefix `A` for one relation and `B` for joins.
- Do not change `scott/scott.sql`. Add any new metric to
  `scott/scott_model.ossie.yaml` in the same pull request, and validate the
  model.
- Copy `expected.sql` from the output of `reference.duckdb.sql`. Do not
  calculate it by hand.
- Cases SHOULD NOT depend on other implementation-defined behavior. Document
  any such dependency in the header of `query.ossie.sql`.

Future work
-----------

The cases can move to the `.ost` test format
([#501](https://github.com/apache/ossie/issues/501)) and use the view expander
([#502](https://github.com/apache/ossie/issues/502)) to run Ossie queries
directly.
