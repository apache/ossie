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
(Layer 3). All cases use the same data and Ossie model. The data and the
expected results come from the examples of the specification: Section 1.3,
Section 5.2, Section 6.1, and Appendix B.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are to be
interpreted as described in BCP 14
([RFC 2119](https://www.rfc-editor.org/rfc/rfc2119),
[RFC 8174](https://www.rfc-editor.org/rfc/rfc8174)) when they appear in
uppercase.

Layout
------

| Path | Contents |
|------|----------|
| [`retail/retail.sql`](retail/retail.sql) | Schema and data: customers, orders, returns, visits, and tickets |
| [`retail/retail_model.ossie.yaml`](retail/retail_model.ossie.yaml) | Ossie model with relationships and metrics |
| `cases/<case>/query.json` | Query under test, in the JSON binding of Section 3.7 |
| `cases/<case>/expected.json` | Expected result or error |

Cases `a*` return rows. Cases `e*` raise an error; they follow the order of
the table in Section 8, and `e20` and `e21` test that order.

A case passes when an implementation loads the data and the model, submits
`query.json`, and either returns a result equal to the expected rows (see
"Comparing results") or raises the expected error.

Case files
----------

`query.json` is the query as a consumer submits it. It has only the parts of
Section 3.2.

`expected.json` describes the case and its result:

- `title`: what the case shows.
- `question`: what the query asks, in English. It helps a reader; the query is
  the ground truth.
- `behavior`: the rule under test, and why the result has its values.
- `spec`: the sections of the specification that define the result.
- `columns` and `rows`: the expected result, with the output names of Section
  3.3 in query order. Or `error`: the code from Section 8 that the engine
  MUST raise.

```json
{
  "title": "Groups come from the metric's dataset",
  "question": "What is the order amount by customer region?",
  "behavior": "One branch from orders. NORTH has no orders, so it is not a group.",
  "spec": ["5.4", "6.1"],
  "columns": ["region", "total_revenue"],
  "rows": [
    ["EAST", 350],
    ["WEST", 300]
  ]
}
```

For codes 13, 14, and 16, Section 8 also requires the error to identify the
dimension or the filter, and a metric or the anchor that does not reach its
dataset. `behavior` names them. How an engine reports them depends on its
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
`expected.json`:

- A JSON number without a fraction or an exponent is an exact numeric. Any
  other JSON number is an approximate numeric.
- A JSON `null` is SQL NULL.
