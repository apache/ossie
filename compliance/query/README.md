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
| `<case>.ost` | A case, as a script for the [`ost`](../../ost/docs/ost.md) test runner |

Each case is a script ([#501](https://github.com/apache/ossie/issues/501)),
for example [`a01_grand_total.ost`](a01_grand_total.ost). A case passes when
an implementation loads the data and the model, runs the case's query,
and returns a result equal to the case's expected result (see "Comparing
results").

The cases refer to each dataset by the name `<model name>.<dataset name>`, for
example `scott_model.emp`.

Cases
-----

A script starts with a header comment, then three commands that set up the
case:

- `connect("scott")` loads the SCOTT data, which `ost` has as its `scott`
  fixture, with the same values as `scott/scott.sql`.
- `set("doubleDigits", 2)` rounds each approximate numeric in a table to 2
  decimal places (see "Expected results").
- `modelFile("scott_model", "scott/scott_model.ossie.yaml")` loads the model.

Then comes the case. It has a comment:

- the case ID and a title that states what the case shows;
- a `Question:` line that tells in English what the query asks;
- the behavior under test, and why the result has its values;
- the spec sections (for example, `Spec: 6, 7.2`).

The question helps a reader. It is not an input of the test: the query is the
ground truth.

After the comment, `executeExpand(...)` holds the query under test. Its expected
output has two parts:

1. **The expansion**, between `{_|` and `|_}`: an equivalent DuckDB query
   over the datasets' source tables in schema `scott`, as the view expander of `ost`
   ([#502](https://github.com/apache/ossie/issues/502)) writes it.
2. **The expected result**: the table that executing the expansion returns.

The query and the expected result are normative. The expansion is not:
another implementation can evaluate the query in any way that gives the same
result. If the expansion or its result disagrees with the spec, the spec is
correct, and the case MUST be fixed.

For one relation, the expansion replaces each `AGG(m)` with the expression of
`m`, and each model relation with its source table. For joins, it follows the
reference evaluation strategy of Section 9: within each output group, it
discards rows whose `primary_key` is NULL, keeps one row per `primary_key`,
and then aggregates. A group with no remaining rows takes the empty-input
value (`NULL` for `SUM`, `0` for `COUNT`). The expansion has this shape:

1. The CTE `joined` is the `FROM`, `JOIN`, and `WHERE` of the query.
   For each measure, it also selects the `primary_key` of the measure's
   relation (`e_k` for alias `e`) and the arguments of the aggregate.
2. The CTE `spine` has one row for each group: `SELECT DISTINCT` over the
   grouping columns. A query without `GROUP BY` has one group and no `spine`.
3. For each measure, a correlated subquery selects the rows of `joined` in the
   group, discards the rows whose key is NULL, keeps one row per key
   (`GROUP BY` the key), and then applies the aggregate.

Most scripts have one case. Some scripts have more: cases that use the
datasets `pet` and `bonus` go in the script of the case they vary. B5a is in
`b05_manager_counted_once.ost`, B8a and B8b in
`b08_many_to_many_chasm_trap.ost`, and each of B9a to B13a in the script of
B9 to B13. The scripts of B9 to B13 and of B14 to B17 also start with a
comment that describes their group.

Expected results
----------------

The expected result is a table, as `ost` prints it. For example, the result
of case A2:

```
+--------+----------+-----------+
| deptno | sum_sal  | emp_count |
+--------+----------+-----------+
|     10 |  8750.00 |         3 |
|     20 | 10875.00 |         5 |
|     30 |  9400.00 |         6 |
+--------+----------+-----------+
```

- The header has the column names of the query, in its order.
- There is a line for each row. If the query has `ORDER BY`, the rows
  are in that order.
- An empty cell is SQL NULL. A string that `ost` cannot write plainly, such as
  the empty string, is in double quotes (see the `ost` manual).
- A number is an exact numeric, unless its column is an approximate numeric:
  `AVG` over `DOUBLE` (such as the metric `avg_sal`), or a division with an
  operand cast to `DOUBLE`. Cases A5, A9, A12, and B14 to B17 have
  approximate columns. Their values are rounded to 2 decimal places,
  such as `2073.21` for `2.073214285714286E3`.

Result semantics
----------------

Expected results use standard SQL (ISO/IEC 9075) semantics. For these tests,
the host dialect of Section 8.1 is standard SQL. For example, `SUM` over no
rows is NULL, and `COUNT` over no rows is 0. An implementation with other
defaults MUST run the tests with standard SQL semantics.

A case MUST NOT return an exact numeric whose scale the standard leaves
implementation-defined, such as `AVG` over an exact numeric or the division of
two exact numerics. For `AVG` over `DECIMAL(7, 2)`, an implementation can
return a scale of 2, which differs from the true value. To use `AVG`, cast its
argument to an approximate type of the Ossie expression language, for example
`AVG(CAST(sal AS DOUBLE))`. A case MUST NOT return NaN or an infinity.

Comparing results
-----------------

- Columns are matched by position. The column counts MUST be equal. Column
  names are compared case-insensitively, so the implementation's own column
  names are not required to match exactly.
- Rows MUST match as multisets, including the number of duplicates.
- If the query has `ORDER BY`, the row order MUST also match, and the
  sort key MUST order the result uniquely.
- Two values match if both are NULL, or if neither is NULL and they are equal.
  Types are not compared: `29025.00` matches `29025`.
- Exact numerics MUST be equal.
- An approximate numeric matches an expected value if, rounded to 2 decimal
  places (halves away from zero), it is equal to the expected value.

The rule for approximate numerics is provisional. How to compare
floating-point results across engines is an open question: the size of the
tolerance, the tolerance near zero, and how to match rows as multisets when
values match only within the tolerance. Until it is settled, cases SHOULD use
exact results.

`ost` itself is stricter than these rules: it compares the text of each cell,
without its padding, so `29025` does not match `29025.00`.

Running the tests
-----------------

To run all cases, from the root of the repository:

```sh
uv run --project ost ost -d compliance compliance/query
```

or `make compliance-query`. To run one case:

```sh
uv run --project ost ost -d compliance compliance/query/a01_grand_total.ost
```

For each script, `ost` writes a regenerated script, with the actual output,
under `compliance/target/ost`. If a script fails, `ost` prints a `diff -u`
command that compares the script with its regenerated script.

To validate the model:

```sh
uv run validation/validate.py compliance/query/scott/scott_model.ossie.yaml
```

Adding a case
-------------

- Add a script `<case>.ost`. Name it with the lowercase case ID, with a
  two-digit number, and a short name that says what the case shows, for
  example `a09_rollup_not_average_of_averages`. Use the ID prefix `A` for one
  relation and `B` for joins. Start it with the header and the three setup
  commands of an existing script.
- Do not change the values in `scott/scott.sql`: the expected results of all
  cases depend on them. A new table, such as `pet`, may be added, to both
  `scott/scott.sql` and the `scott` fixture of `ost`
  (`ost/src/ossie_ost/fixtures/scott.sql`). Add any new dataset or metric to
  `scott/scott_model.ossie.yaml` in the same pull request, and validate the
  model.
- Write the comment and `executeExpand(...)` without output, and run `ost`
  with `--update`, which writes the expansion and the result into the script.
  Do not write them by hand.
- Check the result independently of the expander, which is under test: by
  hand, or with a plain query over the source tables. Explain in the comment
  why the result has its values.
- Cases SHOULD NOT depend on other implementation-defined behavior. Document
  any such dependency in the case's comment.
