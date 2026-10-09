<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# Ossie SQL to AQL

AML gives a metric that spans datasets one body type, `@aql`. So `to-aml` renders its Ossie SQL as AQL, one
construct at a time. Along with rendering a portable body into `--sql-dialect`, this is the only place the
converter rewrites an expression rather than carrying it.

## Why a metric is translated

An AQL expression can name fields from several models. A `@sql` expression can name only fields of the model
it sits in. Naming another model's field from a `@sql` body fails with
`Cannot refer to external field in TableModel/QueryModel`.

A metric whose expression names two datasets therefore has to be AQL, in either place AML allows one.

| Where it is written | Body it accepts |
|---|---|
| a dataset-level `metric` | `@aql` only. The compiler rejects `@sql` there with `Incompatible property 'definition' of 'Metric'` |
| a model-level `measure` | `@sql` or `@aql`, and a `@sql` one names only this model's fields |

`customer_lifetime_value` in `examples/tpcds_semantic_model.yaml` is
`SUM(store_sales.ss_ext_sales_price) / COUNT(DISTINCT customer.c_customer_sk)` and becomes
`sum(store_sales.ss_ext_sales_price) / count_distinct(customer.c_customer_sk)`.

## What translation changes in the models

AQL resolves a reference differently from Ossie SQL, so rendering a metric can need two changes to the models it
names. The converter reports each one.

| What the Ossie SQL does | What the converter writes | Why |
|---|---|---|
| reads a column no dataset declares as a field | a hidden `dimension` on that model over the column of that name | AQL resolves against declared fields only, answering ``Field `subtotal` not found in model `orders` `` where Ossie SQL resolves against the table |
| sums or averages a field with no Ossie `datatype` | `type: 'number'` on that dimension | the fallback is `text`, and AQL then answers ``The `sum` function expects `Dimension(Number)`, but got `Text` `` |

In the second row the metric is the only evidence the field is a number, because the Ossie document
never said so.

## A name that is another metric

An Ossie SQL body may name a metric where a column would go. AQL resolves both, and spells them differently.

| The metric named | The AQL written |
|---|---|
| a dataset-level `metric` | `quantity_per_customer`, the bare name |
| a `measure` on a model | `items.items_quantity`, qualified by the model holding it |

[Ossie to AML](reverse.md#a-metric-that-names-another-metric) covers which home a metric lands in, which is
what picks the row.

## A window function depends on how the metric is queried

A window function computes over a set of rows, and the query decides which rows those are. The two AML
homes handle that differently.

**On a model, as a `@sql` measure.** AML carries the body as written, and it compiles. The warehouse SQL it
generates is valid only for a query that groups by the columns in the frame.
`SUM(SUM(q)) OVER (ORDER BY oid ...)` queried alongside `oid` gives:

```sql
SELECT oid, SUM(SUM(quantity)) OVER (ORDER BY oid ...) FROM t GROUP BY 1
```

Queried on its own, as a grand total, it gives the same expression with no `GROUP BY`, which the warehouse
rejects. Holistics checks none of this at conversion time, so the converter raises a WARNING naming the
columns the frame reads.

**On the dataset, as a `metric`.** The body has to be AQL, and the next section is why that cannot be
written mechanically.

## A window function cannot be translated

Even though AQL has window functions, this converter cannot output one.

The two languages take the frame from different places. Ossie SQL carries the partition and the ordering in the
expression, inside the `OVER` clause. AQL takes both from the grain of the query being run.

Translating one into the other means deciding which query grain a given `OVER` clause corresponds to. The
Ossie specification defines no semantics for how a window function behaves when queried, so nothing says
which grain that is. Picking one changes what the metric computes.

The ERROR therefore records a limit of this converter. AQL can express the metric, so write the AQL by hand
to carry one of these over.

`brand_rank_in_store` in `examples/tpcds_semantic_model.yaml` is one of these:
`RANK() OVER (PARTITION BY store.s_store_sk ORDER BY SUM(store_sales.ss_ext_sales_price) DESC)`.

AQL's window functions are listed under Window Function in the
[AQL function reference](https://docs.holistics.io/reference/aql/functions), and
[`rank`](https://docs.holistics.io/reference/aql/rank) shows one in full.

## The function table

Most Ossie SQL functions keep their name in AQL, lowercased. `SUM(x)` becomes `sum(x)`, `ROUND(x, 2)` becomes
`round(x, 2)`, `SAFE_DIVIDE(a, b)` becomes `safe_divide(a, b)`. 45 of the table's 51 entries work that way,
so the table is worth reading only for the ones that do not.

| Ossie SQL | AQL |
|---|---|
| `LENGTH` | `len` |
| `POWER` | `pow` |
| `STDDEV` | `stdev` |
| `STDDEV_POP` | `stdevp` |
| `VARIANCE` | `var` |
| `VARIANCE_POP` | `varp` |
| `COUNT(DISTINCT x)` | `count_distinct(x)` |

The last is not a rename but a reshape. Ossie SQL puts `DISTINCT` inside the argument list and AQL puts it in the
function name, so `COUNT` reads as either `count` or `count_distinct` depending on what it wraps.

The table itself comes from parsing every name AQL registers and reading which node sqlglot produced, rather
than from matching names by eye.

The table leaves out some names. What happens to one depends on whether the original call can be read back
out of the source text, which [the passthrough](#the-passthrough) explains.

**A name that is one sqlglot node with several spellings.** `LPAD` and `RPAD` are both `Pad`, `LTRIM` and
`RTRIM` are both `Trim`, `LOG` and `LOG10` are both `Log`. The node says which shape was parsed, not which
name was written, so rendering from it would turn `LPAD(s, 5)` into `PAD(s, 5)`. The node's offset points at
the name in the source, so the passthrough recovers it.

**A name whose arguments sqlglot rewrote.** `LOG10(x)` gains a base argument the source never had, and
`DATE_TRUNC('month', d)` has its unit normalized to `'MONTH'`. Either way the argument carries no offset.
Dropping the invented base is right and dropping the normalized unit is not, and an absent offset does not
say which case it is, so both raise an ERROR.

**A name whose arguments mean something else in AQL.** Ossie SQL `SUBSTRING(s, start, len)` and `STRPOS(s, sub)`
do not line up with AQL's `mid` and `find`, so neither is in the table. The passthrough carries `STRPOS`
under its own name with the warehouse's own meaning. `SUBSTRING`'s node carries no offset, so the converter
raises an ERROR for it.

## The passthrough

A function with no AQL name becomes `sql_number('NAME', arg, ...)`, AQL's raw-SQL call, which hands the
warehouse the original call with AQL-resolved arguments.

```
MY_WAREHOUSE_UDF(COUNT(users.id), 3)  ->  sql_number('MY_WAREHOUSE_UDF', count(users.id), 3)
LPAD(users.full_name, 5)              ->  sql_number('LPAD', users.full_name, 5)
```

The name and the argument order come from the source text rather than from sqlglot, which names a node
after the shape it parsed. A function sqlglot does not model keeps its name and arguments in an `Anonymous`
node already. For one it does model, the node carries the offset of the name token and each argument carries
its own, so the converter reads both back out of the source.

Every argument has to carry an offset for that to work. An argument sqlglot synthesized or normalized has
none, and the converter raises an ERROR rather than rebuild the call without it.

The family is typed by return value: `sql_number`, `sql_text`, `sql_date`, `sql_datetime` and
`sql_truefalse`. A metric body is numeric, so the converter uses `sql_number`.

## Reading it back

`tests/fixtures/aql/translation.ossie.yaml` holds one metric for each path through this document.

| Metric | Path |
|---|---|
| `quantity_per_customer` | aggregates with AQL names |
| `rounded_ratio` | scalar functions with AQL names |
| `untyped_total` | a field with no Ossie `datatype` |
| `borrowed_column_total` | a column no dataset declares as a field |
| `passthrough_call` | a function with no AQL name |
| `windowed_rank` | a window function, dropped with an ERROR |
| `unmapped_function` | a call sqlglot rewrote, dropped with an ERROR |

`tests/test_snapshots.py` pins each result by metric name, and runs the generated AML through
`holistics aml validate`, which type-checks the AQL rather than only parsing the file.

Part of the [Apache Ossie Holistics converter](../README.md). See also [the fixture](fixture.md), [the mapping](mapping.md), [expression translation](expressions.md), [measures](measures.md), [the HOLISTICS payload](vendor-payload.md), [Ossie to AML](reverse.md) and [limitations](limitations.md).
