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
| `expected.sql` | The expected result of the Ossie query, as a standard SQL query over literals. |
| `reference.duckdb.sql` | A DuckDB query over the base tables in `scott/scott.sql`. Its result is equal to the result of `expected.sql`. |

The part of the file name before `.sql` gives the SQL dialect of the file:
`.ossie.sql` for an Ossie query, and `.duckdb.sql` for DuckDB SQL. A file name
without a dialect part, such as `expected.sql`, is standard SQL (ISO/IEC 9075).

An implementation passes a case if it loads the data and the model, accepts
`query.ossie.sql`, and returns a result that is equal to the result of
`expected.sql` by the rules in "Comparison of results". The implementation does
not use `reference.duckdb.sql`.

See [`cases/a01_grand_total/`](cases/a01_grand_total/) for an example.

The key words "MUST", "MUST NOT", "SHOULD", "SHOULD NOT", and "MAY" in the rest
of this document are to be interpreted as described in BCP 14
([RFC 2119](https://www.rfc-editor.org/rfc/rfc2119),
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)) when, and only when, they
appear in all capitals, as shown here.

Case files
----------

`query.ossie.sql` starts with a header in SQL comments: the case ID and
title, one or two sentences about the behavior under test, and the spec
sections (for example, `Spec: 6, 7.2`). The query follows the header. A
harness can submit the file as it is.

`expected.sql` is one query of this form:

```sql
SELECT *
FROM (VALUES (29025.00, 14)) AS expected (sum_sal, emp_count);
```

- The column names and their order MUST be the same as in `query.ossie.sql`.
- The values MUST be literals of standard SQL: `NULL`, character string
  literals (`'SALES'`), exact numeric literals (`29025.00`), date and time
  literals (`DATE '1981-12-03'`), and `TRUE` or `FALSE`. Approximate numeric
  literals (`2.5E3`) MUST NOT be used.
- If every value of a column is NULL, the first row MUST give the type with
  `CAST(NULL AS <type>)`, because a bare NULL has no type.
- If the expected result has no rows, `expected.sql` MUST have one row that
  gives the types, and it MUST end with `WHERE 1 = 0`.
- If `query.ossie.sql` has `ORDER BY`, the rows MUST be in that order.

`reference.duckdb.sql` is non-normative. It helps to derive `expected.sql` and
to check it again. The spec is normative: if the reference query and the spec
disagree, the case is wrong, and it MUST be corrected to follow the spec. The
reference query uses the same column names as `query.ossie.sql`.

- For a case on one relation, the reference query is the Ossie query with each
  `AGG(m)` replaced by the expression of `m`, and each model relation replaced
  by its source table.
- For a case with joins, the reference query applies the reference evaluation
  strategy of Section 9 of the spec. It removes join duplicates by row identity
  (the dataset's `primary_key`), and then it aggregates.

Run
---

DuckDB needs no extensions. To run all reference queries:

```sh
cat compliance/query/scott/scott.sql \
  compliance/query/cases/*/reference.duckdb.sql | duckdb
```

To check one case, compare the result of `reference.duckdb.sql` with the result
of `expected.sql`. The command prints the number of rows that are different;
for a correct case, it is 0:

```sh
CASE=compliance/query/cases/a01_grand_total
duckdb -c ".read compliance/query/scott/scott.sql" \
  -c "SET VARIABLE r = (SELECT content FROM read_text('$CASE/reference.duckdb.sql'))" \
  -c "SET VARIABLE e = (SELECT content FROM read_text('$CASE/expected.sql'))" \
  -c "SELECT count(*) AS differences FROM (
        (FROM query(getvariable('r')) EXCEPT ALL FROM query(getvariable('e')))
        UNION ALL
        (FROM query(getvariable('e')) EXCEPT ALL FROM query(getvariable('r'))))"
```

This command does not compare row order. For a case with `ORDER BY`, also
compare the order of the rows.

To check the model:

```sh
uv run validation/validate.py compliance/query/scott/scott_model.ossie.yaml
```

Semantics of the expected results
---------------------------------

The expected results use standard SQL semantics (ISO/IEC 9075) for aggregates
and for scalar operators. Section 8.1 of the spec refers to the "host
dialect". For these tests, the host dialect is standard SQL. For example, `SUM`
over no rows gives NULL, and `COUNT` over no rows gives 0.

If the defaults of an engine are different (for example, if `SUM` over no rows
gives 0), the engine MUST run in a mode with standard SQL semantics.

Every expected value is exact, and the comparison has no tolerance. Thus a
case MUST NOT have a result column whose type is approximate numeric (`REAL`,
`DOUBLE PRECISION`, or `FLOAT`), or whose scale the SQL standard leaves
implementation-defined. Examples of the second kind are `AVG` over an exact
numeric and the division of two exact numerics. For this reason, the model has
no `AVG` metric.

Comparison of results
---------------------

The actual result is the result of `query.ossie.sql`. The expected result is
the result of `expected.sql`.

- The two results MUST have the same number of columns. Columns are compared by
  position. Column names MUST be equal without regard to case.
- The two results MUST be equal as bags (multisets): each row MUST occur the
  same number of times in both results.
- Two values are equal if both are NULL, or if neither is NULL and the SQL `=`
  operator gives TRUE. This is `IS NOT DISTINCT FROM`. Data types are not
  compared, so `29025.00` is equal to `29025`.
- If `query.ossie.sql` has `ORDER BY`, the rows MUST also be in the same order.
  The `ORDER BY` of a case MUST give one order only: its sort key MUST be
  unique in the result.

In SQL, `(actual EXCEPT ALL expected) UNION ALL (expected EXCEPT ALL actual)`
gives no rows if and only if the two results are equal as bags.

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
- Copy the values in `expected.sql` from the output of `reference.duckdb.sql`.
  Do not calculate them by hand. Check the case with the command in "Run". If
  possible, confirm the result with a second, independent query. For a case
  with joins, the second query can write the input set of each measure
  directly from Section 8.1, for example with `EXISTS` over the join
  condition.
- A case SHOULD NOT depend on other implementation-defined behavior. If a
  result does depend on it, say so in the header of `query.ossie.sql`.

Future work
-----------

These tests are a light version of the `.ost` test format
([#501](https://github.com/apache/ossie/issues/501)) and the view expander
([#502](https://github.com/apache/ossie/issues/502)). When those are
available, each case can become an `.ost` test, and a runner can check the
Ossie query directly.
