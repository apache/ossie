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
| [`cases/`](cases/) | One file for each case. |

All cases use the same data and the same model.

Run
---

Run the data file, then the cases, with DuckDB. DuckDB needs no extensions:

```sh
cat compliance/query/scott/scott.sql compliance/query/cases/*.sql | duckdb
```

To run one case, give only that case file after `scott.sql`.

To check the model:

```sh
uv run validation/validate.py compliance/query/scott/scott_model.ossie.yaml
```

Case format
-----------

Each file in `cases/` has one case with four parts:

1. A header: the case ID and title, one or two sentences about the behavior
   under test, and the spec sections (for example, `Spec: 6, 7.2`).
2. An Ossie query that evaluates measures with `AGG()` (commented out). The
   query refers to the model relations by two-part name, for example
   `scott_model.emp`.
3. A reference query in DuckDB SQL over the base tables (runnable).
   - For a case on one relation, the reference query is the Ossie query with
     each `AGG(m)` replaced by the expression of `m`, and each model relation
     replaced by its source table.
   - For a case with joins, the reference query applies the reference
     evaluation strategy of Section 9 of the spec. It removes join duplicates
     by row identity (the dataset's `primary_key`), and then it aggregates.
4. The expected result (commented out), in this format:
   - Column headers are in uppercase.
   - Numbers are right-aligned. Strings and dates are left-aligned.
   - NULL is shown as an empty cell.
   - Values that are not exact (see "Comparison of results") are rounded to
     2 decimal places.

See [`cases/a01_grand_total.sql`](cases/a01_grand_total.sql) for an example.

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
  some result types. For example, the precision and scale of `AVG` over an
  exact numeric type are implementation-defined.
- Integers, and exact numerics whose scale the standard fixes, must be equal.
  For example, `COUNT(*)`, and `SUM` over `DECIMAL(7, 2)`, which has scale 2.
- Other numeric values (for example, `AVG`, division, and approximate numerics)
  are not exact. An actual value matches if it differs from the expected value
  by 0.01 or less. Thus rounding and truncation to 2 decimal places both match.

Adding a case
-------------

- Put one case in each file in `cases/`. Use the lowercase case ID, with a
  two-digit number, and a short name, for example `a02_group_by.sql`. The
  two-digit number keeps the files in ID order.
- Use the ID prefix `A` for a case on one relation, and `B` for a case with
  joins.
- Use only the data in `scott/scott.sql`. Do not change the data, because the
  expected results of all cases depend on it.
- Use the model in `scott/scott_model.ossie.yaml`. If a case needs a new metric,
  add the metric in the same pull request, and check the model with
  `validation/validate.py`.
- Take the expected result from the output of the reference query. Do not
  calculate it by hand. If possible, confirm it with a second, independent
  query. For a case with joins, the second query can write the input set of
  each measure directly from Section 8.1, for example with `EXISTS` over the
  join condition.
- Prefer queries whose result does not depend on implementation-defined
  behavior. If a result does depend on it, say so in the header.

Future work
-----------

These tests are a light version of the `.ost` test format
([#501](https://github.com/apache/ossie/issues/501)) and the view expander
([#502](https://github.com/apache/ossie/issues/502)). When those are
available, each case can become an `.ost` test, and a runner can check the
Ossie query directly instead of its reference query.
