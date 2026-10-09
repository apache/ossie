<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# Ossie to AML

The reverse path writes AML source text.

```
AML source  --(holistics aml compile)-->  JSON  --(this converter)-->  Ossie YAML
AML source  <---------------------(this converter)------------------  Ossie YAML
```

`holistics aml compile` reads AML source and writes JSON, and it is the only tool that moves between the
two. So AML source is the format a person edits, a repository holds, and the CLI reads back, and generated
JSON would be a file with no reader.

## Output layout

One Ossie document becomes one dataset file plus one model file per dataset:

```
<out-dir>/
  <dataset name>.dataset.aml
  orders.model.aml                      a dataset named orders
  modules/reporting/calendar.model.aml  a dataset named reporting__calendar
```

A dataset name splits on `__` back into a module path, so `reporting__calendar` becomes
`modules/reporting/calendar.model.aml` holding `Model calendar`. When the stash carries the original `__fqn__`,
that wins over splitting, because it records where the module boundaries actually were.

The input file layout is lost on the way in. The compiled JSON records which models a dataset holds and not
which file each came from, so by the time a document reaches Ossie the layout is already gone. AML accepts any
arrangement, so the generator picks one. `AML to Ossie to AML` returns the same models in the generator's
layout.

## Mapping

| Ossie | AML |
|---|---|
| the document root | `Dataset <name> { ... }` |
| `description` | the dataset's `description` |
| `dataset` whose `source` is a dotted reference | `Model <name> { type: 'table' table_name: '<source>' }` |
| `dataset` whose `source` is a query | `Model <name> { type: 'query' query: @sql <source> ;; }` |
| `dataset.name` | the model name, `__` split back into a module path |
| `dataset.fields[]` | `dimension <name> { ... }` |
| `dataset.primary_key` | `primary_key: true` on each named dimension |
| `metrics[]` | a `measure` on one model, or a dataset-level `metric` |
| `relationships[]`, one column pair | `relationship(from.col > to.col, true)` in the dataset block |
| `relationships[]`, composite | the long `RelationshipConfig` form, extra pairs in repeated `on` blocks |
| `custom_extensions[HOLISTICS]` | restores `hidden`, `format`, `type`, `aggregation_type`, `param`, `persistence`, `owner`, `table_name`, the relationship kind and `active` |

## Properties AML requires and Ossie has no field for

Four properties are mandatory in AML. `label` and `type` on every dimension and measure, and
`data_source_name` and `relationships` on the dataset. The compiler says so rather than defaulting:
`Property 'label' is missing on element but required on type 'Dimension'`.

| Property | Where it comes from |
|---|---|
| `label` | the stash, else the name titleized the way AML itself does it, so `created_at` becomes `Created At` |
| `type` | the stash, else `datatype` through the reverse of the type table, else `text` with a WARNING |
| `data_source_name` | the stash, else the `--data-source-name` argument. Without either, the run fails |
| a legal identifier | each illegal character in an Ossie name becomes an underscore, and the rename is reported |
| a dataset name no model holds | AML has one namespace for `Dataset` and `Model`, so a collision moves the dataset to `<name>_dataset` |
| `relationships` | the Ossie `relationships[]`, written as `[]` when there are none |

## Expressions

An Ossie `expression` may carry several dialects. `--sql-dialect` names the warehouse the Holistics
connection reads, and the converter picks against it:

| The expression carries | Written as | Issue |
|---|---|---|
| `HOLISTICS_AQL` | `definition: @aql <expression> ;;`, unchanged | |
| the `--sql-dialect` dialect | `definition: @sql <expression> ;;`, unchanged | |
| `ANSI_SQL` or `OSSIE_SQL_2026` | `@sql`, rendered into `--sql-dialect` by sqlglot | WARNING, showing both texts |
| none of those | `@sql`, written unchanged | ERROR. The body will not run on this connection |

AQL comes first and is exact, because the body came out of AML unchanged and goes back unchanged.

A warehouse dialect that is not the target gets an ERROR rather than a rendering. Turning a `SNOWFLAKE` body
into a BigQuery one is a translation between warehouses, which this converter does not do. `ANSI_SQL` and
`OSSIE_SQL_2026` are the exception, because they are the portable forms the specification provides, and
every sibling converter reads `OSSIE_SQL_2026` as an `ANSI_SQL` equivalent.

A `@sql` body needs every reference written back as Holistics interpolation. A bare column name does compile,
and it is not equivalent: AQL reads the interpolation to learn which field a body depends on, and a body
without it loses the query rewriting and the optimisation that link drives.

| Ossie expression | AML |
|---|---|
| a bare column name | `{{ #SOURCE.column }}` |
| `<this dataset>.<field>` | `{{ field }}` |
| a metric name owned by this model | `{{ <measure name> }}` |
| a reference to a field of another dataset | no `@sql` form, so it is written model-qualified and an ERROR names it. See [Ossie SQL to AQL](aql.md) |

Restoring those references means telling a column apart from a keyword, a function name and a date part. The
converter parses the expression with sqlglot at the dialect the expression declares, takes the character
offsets sqlglot reports for each column it found, and splices the replacement text into the original string.
In BigQuery's `DATE_TRUNC(created_at, month)` sqlglot reads `created_at` as a column and `month` as a date
part, so the result is `DATE_TRUNC({{ #SOURCE.created_at }}, month)`.

The Ossie SQL is never written back out through sqlglot. `DATE_TRUNC(created_at, month)` rendered through the
default dialect returns `DATE_TRUNC(created_at, MONTH)`, which that same default dialect then re-reads with
the unit and the expression exchanged. Splicing on offsets keeps every character outside a replaced span
exactly as it was, so no round trip through the writer can happen.

An expression the parser cannot read, or one tagged with a dialect that is not SQL, raises an ERROR naming
the field. The body is written out unchanged, which compiles and loses the field linkage, and the error says
so.

## Metrics have two possible homes

An Ossie metric sits at the document root and may span datasets. AML offers two places to put one. A stashed
`model` decides it, because it records which model the measure came from. Without one the expression
decides:

- An expression naming exactly one dataset becomes a `measure` on that model.
- An expression naming several, or none, becomes a dataset-level `metric`.
- An expression naming another metric counts the datasets that metric resolves to as its own.

### A metric that names another metric

An Ossie body may name a metric where a column would go. `cities__upstream_measure + 1` in
`tests/fixtures/ecommerce` is one, and the forward path writes that shape every time one AML measure
references another.

Which home the named metric landed in decides where the naming one can go.

| The named metric is | The naming metric becomes |
|---|---|
| a `measure` on the same model | a `measure` on that model, written `{{ items_quantity }}` |
| a `measure` on another model | a dataset-level `metric`, written `items.items_quantity` in AQL |
| a dataset-level `metric` | a dataset-level `metric`, written as the bare name in AQL |

AQL names a measure in any model, and a `@sql` body has no spelling for a dataset-level metric. So the last
two rows move the naming metric onto the dataset, where AQL resolves the reference.

The third bullet above is what puts a metric on the right model. `items_quantity + 1` names no dataset of
its own. Counting only its columns sends it to the dataset, and `items_quantity` is an Ossie SQL name that AQL does
not resolve there, so the metric is dropped.

Two shapes are still an ERROR, and a stash or a qualifier that disagrees with the body produces both:

- A `@sql` body naming a dataset-level metric. A qualifier gives this: `orders.spanning` reads as a field of
  `orders`, which has no such field.
- A measure stashed onto one model, naming a measure of another. Holistics rejects it at SQL generation with
  `Cannot refer to external field in TableModel/QueryModel`.

A metric that names itself through another metric is an ERROR as well. `holistics aml validate` accepts a
cycle, and Holistics inlines one metric into the body of the next, so the pair has no warehouse SQL.

`tests/fixtures/metric_refs` covers each row of the table, and `holistics aml validate` runs over the AML it
generates.

### A cross-dataset metric becomes AQL

A `@sql` expression names only fields of the model it sits in, so a metric naming two datasets is rendered
into AQL whichever of the two places AML allows it. The converter raises a WARNING showing the Ossie SQL and
the AQL side by side. A metric that came from Holistics arrives as `HOLISTICS_AQL` already and is untouched.

A construct the converter does not write as AQL drops the metric with an ERROR. A window function is the one
to expect. [Ossie SQL to AQL](aql.md) covers the function table, the raw-SQL call for a function AQL has no name
for, and the two changes rendering can make to the models a metric names.

`aggregation_type` and the body compose into one Ossie expression on the way out, so the reverse path splits
them apart again.

| Stash | AML written |
|---|---|
| a named `aggregation_type` wrapping the whole expression | that aggregation, and the body from inside the wrapper. `sum` over `SUM(quantity)` gives `aggregation_type: 'sum'` and `{{ #SOURCE.quantity }}` |
| no `aggregation_type` | `custom`, with the expression kept whole. The aggregate already sits inside it |
| a named `aggregation_type` that does not wrap the whole expression | `custom`, with the expression kept whole, and a WARNING. The expression was edited after the forward path wrote it, and aggregating it again would double the aggregate |

TPC-DS takes the second row throughout, because it carries no stash. Its `total_sales` is
`SUM(store_sales.ss_ext_sales_price)` and becomes `definition: @sql SUM({{ ss_ext_sales_price }});;` under
`custom`, where `store_sales` is the model being written so the reference is a sibling. An unqualified
`SUM(ss_ext_sales_price)` would have become `SUM({{ #SOURCE.ss_ext_sales_price }})` instead.

## Verifying the output

The generated AML is checkable without a Holistics account:

```bash
holistics aml validate <out-dir>/<name>.dataset.aml -r <out-dir>
```

That parses every generated file and type-checks the AQL. `tests/test_snapshots.py` runs it over every
fixture and asserts a clean exit, so "the output is valid AML" is measured rather than asserted. CI installs
the CLI, so those tests run there too.

Compiling goes further than validating. `test_the_round_trip_returns_the_same_ossie_document` runs
`holistics aml compile` over the generated AML and converts the result back to Ossie, then compares it with
the document the reverse path started from. For the `ecommerce` fixture the two are byte-identical.

## What does not survive

Beyond the file layout above:

- Comments. The compiled JSON keeps `__doc__` fields but the forward path does not carry them into Ossie.
- Property order inside a block, which the generator chooses.
- Anything the forward path already declared lost. See [limitations](limitations.md).

Part of the [Apache Ossie Holistics converter](../README.md). See also [the fixture](fixture.md), [the mapping](mapping.md), [expression translation](expressions.md), [measures](measures.md), [the HOLISTICS payload](vendor-payload.md) and [limitations](limitations.md).
