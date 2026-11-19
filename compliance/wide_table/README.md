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

Wide table compliance tests
===========================

Compliance tests for the
[Wide Table Query Interface](../../core-spec/wide_table_semantics.md)
(Layer 3). Cases use one of the fixtures below. The `retail` data and its
expected results come from the examples of the specification: Section 1.3,
Section 5.2, Section 6.1, and Appendix B. Appendix B.3 gives no rows, so the
`stores` data is chosen for its cases, and their expected results follow from
it.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are to be
interpreted as described in BCP 14
([RFC 2119](https://www.rfc-editor.org/rfc/rfc2119),
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)) when they appear in
uppercase.

Layout
------

| Path | Contents |
|------|----------|
| [`retail/retail.sql`](retail/retail.sql) | Schema and data: customers, orders, returns, visits, and tickets (Appendix B.1, B.2, B.4, B.5) |
| [`retail/retail_model.ossie.yaml`](retail/retail_model.ossie.yaml) | Ossie model over `retail.sql` |
| [`stores/stores.sql`](stores/stores.sql) | Schema and data: store_sales, store_returns, item, date_dim, store, and customers (Appendix B.3; unmatched home rows) |
| [`stores/stores_model.ossie.yaml`](stores/stores_model.ossie.yaml) | Ossie model over `stores.sql` with one path from `store_sales` to `date_dim` |
| [`stores/stores_ambiguous_model.ossie.yaml`](stores/stores_ambiguous_model.ossie.yaml) | Same as `stores_model`, plus `store` to `date_dim` through `closing_date_id` |
| `cases/<case>/query.json` | Query under test, in the JSON binding of Section 3.7 |
| `cases/<case>/expected.txt` | Which fixture to load, and the expected result or error as `ost` prints it |
| `cases/<case>/query.ossie.sql` | For a case that returns rows: the Layer 2 query of its Section 5 lowering |
| [`cases/README.md`](cases/README.md) | What each case shows |

Cases `a*` return rows. Cases `e*` raise an error. `e01` to `e19` follow the
order of the table in Section 8, and `e20` and `e21` test that order. `e22` to
`e25` use the `stores` fixtures and follow the same order; `e24` and `e25`
cover `E_AMBIGUOUS_PATH` (Section 8, row 17).

A case passes when an implementation loads the fixture named in
`expected.txt`, submits `query.json`, and either returns a result equal to
the expected rows (see "Comparing results") or raises the expected error.

Fixtures
--------

Each `expected.txt` names its fixture in a comment line, `# fixture: <name>`.
The fixtures are:

| Fixture | SQL | Model |
|---------|-----|-------|
| `retail` | [`retail/retail.sql`](retail/retail.sql) | [`retail/retail_model.ossie.yaml`](retail/retail_model.ossie.yaml) |
| `stores` | [`stores/stores.sql`](stores/stores.sql) | [`stores/stores_model.ossie.yaml`](stores/stores_model.ossie.yaml) |
| `stores_ambiguous` | [`stores/stores.sql`](stores/stores.sql) | [`stores/stores_ambiguous_model.ossie.yaml`](stores/stores_ambiguous_model.ossie.yaml) |

`retail` matches Appendix B.1–B.2 and B.4–B.5: every order, return, and visit
matches a customer, so no NULL groups arise from unmatched home rows.

`stores` matches Appendix B.3 without the closing-date relationship. Sale SS4
has a NULL `customer_id`, and customer C3 has a NULL `region`, so a `LEFT JOIN`
places both amounts in the NULL region group (Section 5.4; Section 6, S1). An
`INNER JOIN` would drop SS4.

`stores_ambiguous` adds `store` to `date_dim` through `closing_date_id`, so
`store_sales` has two paths to `date_dim`. Cases `e24` and `e25` use this
fixture; `a19` and `a20` are the same queries on `stores` and succeed. `a21`
also uses this fixture and succeeds: `E_AMBIGUOUS_PATH` applies only to paths
between datasets that the lowering joins.

Case files
----------

`query.json` is the query as a consumer submits it. It has only the parts of
Section 3.2.

`expected.txt` is the expected result, in the output format of the
[`ost`](../../ost/docs/ost.md) test runner. Lines that start with `#` are
comments; the line `# fixture: <name>` names the fixture to load. The file is
not an `.ost` script: it has the output of a command, without the command.

- For a case that returns rows, a table: a header with the output names of
  Section 3.3 in query order, and a line for each row.
- For a case that raises an error, the line `> error: ` followed by the code
  from Section 8 that the engine MUST raise.

```
+--------+---------------+
| region | total_revenue |
+--------+---------------+
| EAST   |           350 |
| WEST   |           300 |
+--------+---------------+
```

`query.ossie.sql` is the Layer 2 query of the Section 5 lowering of
`query.json`, in the SQL of the
[Relational Query Interface](../../core-spec/relational_semantics.md). It
refers to each dataset as `<model name>.<dataset name>`, as
[`../query/README.md`](../query/README.md) does, with the model of the case's
fixture. Run on that fixture, it returns the rows of `expected.txt`. Where a
branch reads one dataset without a join, as in `a08`, a plain aggregate stands
for the `AGG()` of a component of a model-scoped metric. Section 5 fixes the
datasets, joins, filters, groupings, and metric evaluation of the lowering, not
its SQL text, so a lowering may differ from the file and still conform. Cases
`e*` raise an error instead of returning rows, so they have no
`query.ossie.sql`.

[`cases/README.md`](cases/README.md) describes each case: its `Title`,
`Question`, `Behavior`, and `Spec`. It lists the cases by fixture.

For codes 13, 14, and 16, Section 8 also requires the error to identify the
dimension or the filter, and a metric or the anchor that does not reach its
dataset. `Behavior` names them. How an engine reports them depends on its
binding, so the cases check only the code.

Result semantics
----------------

The rules of [`../query/README.md`](../query/README.md), "Result semantics",
apply. For example, `SUM` over no rows is NULL and `COUNT(*)` over no rows is
0, which gives the empty-input values of Section 5.2.

Comparing results
-----------------

Results are compared as in [`../query/README.md`](../query/README.md),
"Comparing results". Rows have no defined order (Section 3.6). In
`expected.txt`:

- The table writes down the expected values, and a result matches by value:
  `350.00` matches `350`. Column widths and padding carry no meaning.
- A number is an exact numeric, unless its column is a metric that returns an
  approximate numeric. Among these cases only `revenue_per_customer` is one
  (`162.5` in `a08_model_metric_components`), because its expression casts the
  numerator to DOUBLE.
- An empty cell is SQL NULL. An empty string is written `""`.

Future work
-----------

The cases can move to `.ost` scripts when the `ost` runner can load these
fixtures and submit a Layer 3 query. Three points are open until then:

- `ost` compares the text of a cell exactly, so `350.00` does not match `350`.
  The rules above compare values.
- `ost` compares an error by its message. The cases check only the code.
- A table does not say whether a number is exact or approximate. The rule
  above takes it from the model.
