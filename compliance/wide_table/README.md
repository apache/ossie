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
| `<case>.ost` | A case, as a script for the [`ost`](../../ost/docs/ost.md) test runner |

Cases `a*` return rows. Cases `e*` raise an error. `e01` to `e19` follow the
order of the table in Section 8, and `e20` and `e21` test that order. `e22` to
`e25` use the `stores` data and follow the same order; `e24` and `e25` cover
`E_AMBIGUOUS_PATH` (Section 8, row 17).

A case passes when an implementation loads the case's data and model,
submits its query, and either returns a result equal to the expected rows
(see "Comparing results") or raises the error with the expected code.

Fixtures
--------

A case loads its data with `connect` and its model with `modelFile`:

| Data | Model | Cases |
|------|-------|-------|
| [`retail/retail.sql`](retail/retail.sql) | [`retail/retail_model.ossie.yaml`](retail/retail_model.ossie.yaml) | `a01` to `a15`, `e01` to `e21` |
| [`stores/stores.sql`](stores/stores.sql) | [`stores/stores_model.ossie.yaml`](stores/stores_model.ossie.yaml) | `a16` to `a20`, `e22`, `e23` |
| [`stores/stores.sql`](stores/stores.sql) | [`stores/stores_ambiguous_model.ossie.yaml`](stores/stores_ambiguous_model.ossie.yaml) | `a21`, `e24`, `e25` |

The `retail` data matches Appendix B.1–B.2 and B.4–B.5: every order, return,
and visit matches a customer, so no NULL groups arise from unmatched home rows.

The `stores` data matches Appendix B.3 without the closing-date relationship.
Sale SS4 has a NULL `customer_id`, and customer C3 has a NULL `region`, so a
`LEFT JOIN` places both amounts in the NULL region group (Section 5.4;
Section 6, S1). An `INNER JOIN` would drop SS4.

`stores_ambiguous_model` adds `store` to `date_dim` through `closing_date_id`,
so `store_sales` has two paths to `date_dim`. Cases `e24` and `e25` use this
model; `a19` and `a20` are the same queries on `stores_model` and succeed.
`a21` also uses this model and succeeds: `E_AMBIGUOUS_PATH` applies only to
paths between datasets that the lowering joins.

Scripts
-------

Each case is a script, such as
[`a01_shared_dimension.ost`](a01_shared_dimension.ost). To run the scripts,
from the root of the repository:

```
uv run --project ost ost -d compliance compliance/wide_table
```

A script loads the case's data and model, and sets `doubleDigits` to 2, as the
scripts of [`../query/`](../query/README.md) do, so `162.5` in `a08` is
written `162.50`. A comment block describes the case: its title, the question
that the query asks in English, the rule under test and why the result has its
values, and the sections of the specification that define the result. For
codes 13, 14, and 16, Section 8 also requires the error to identify the
dimension or the filter, and a metric or the anchor that does not reach its
dataset; the comment names them.

The argument of `executeLower` is the query under test, in the JSON binding of
Section 3.7. `executeLower` lowers the query to a Layer 2 query by Section 5,
expands that query with the view expander of `ost`, and executes the
expansion. Its expected output is one of:

- For a case that returns rows, the lowering, between `{_|` and `|_}`, and
  the expected result: a table with a header of the output names of
  Section 3.3 in query order, and a line for each row. The lowering is not
  normative: Section 5 fixes the datasets, joins, filters, groupings, and
  metric evaluation of the lowering, not its SQL text, so another lowering can
  differ and still conform. Each component of a model-scoped metric is a
  measure of its home dataset with a name of its own, such as `c1` and `c2` in
  `a08`, so the lowering evaluates it with `AGG()` (Section 5.2). A comment at
  the start of the lowering defines each such measure.
- For a case that raises an error, `> error: `, the code from Section 8 that
  the engine MUST raise, and the message of the `ost` lowering. The case
  requires only the code.

A case that `ost` cannot run yet is preceded by `skip`, with the URL of the
issue that tracks the gap. `ost` does not run it, and keeps its expected
output as written. A pull request that fixes the issue removes the `skip`, and
the script passes.

Result semantics
----------------

The rules of [`../query/README.md`](../query/README.md), "Result semantics",
apply. For example, `SUM` over no rows is NULL and `COUNT(*)` over no rows is
0, which gives the empty-input values of Section 5.2.

Comparing results
-----------------

Results are compared as in [`../query/README.md`](../query/README.md),
"Comparing results". Rows have no defined order (Section 3.6), and types are
not compared, so `350.00` from an implementation matches `350`. `ost` itself
is stricter: it compares the text of each cell, so a script that expects
`350.00` in `a05` fails.

- A number is an exact numeric, unless its column is a metric that returns an
  approximate numeric. Among these cases only `revenue_per_customer` is one
  (`162.50` in `a08_model_metric_components`), because its expression casts the
  numerator to DOUBLE.
- An empty cell is SQL NULL. An empty string is written `""`.
