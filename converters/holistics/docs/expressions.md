<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# Expression translation

AML writes a field body in one of two languages, named by the heredoc.

| Heredoc | What it holds | Ossie dialect |
|---|---|---|
| `@sql` | Holistics SQL: warehouse SQL with `{{ ... }}` interpolation | the `--sql-dialect` argument |
| `@aql` | AQL, the Holistics analytical query language | `HOLISTICS_AQL` |

The converter substitutes the interpolation and passes the rest of each body through as written. It does not
translate AQL into SQL, and it does not re-render warehouse SQL from one dialect into another.

## Why AQL travels untranslated

AQL is not SQL. `count(ecommerce_orders.id) | where(...) | of_all()` is relationship-aware, so no SQL
rendering of it is correct without the dataset's whole relationship graph.

The specification defines `dialects[]` as `{dialect, expression}` pairs, so a value a converter cannot
translate still travels, tagged with the language it is valid in. `HOLISTICS_AQL` is a member of the
`Dialect` enumeration, registered the way `THOUGHTSPOT` was in
[#351](https://github.com/apache/ossie/pull/351). An AQL body is therefore carried rather than stashed, and
every field keeps the `expression` that Ossie requires.

## Interpolation

A `@sql` body interpolates through `{{ ... }}`.

| Form | Names | Becomes |
|---|---|---|
| `{{ #SOURCE.column }}` | a column of this model's relation | the column name |
| `{{ field }}` | another dimension of the same model | `<dataset>.<field>` |
| `{{ field }}` where `field` is a measure | a measure of the same model | the metric's own name |

A measure is the one reference that is not dataset-qualified, because a measure becomes a root-level Ossie
metric rather than a field inside a dataset. See [measures](measures.md#naming).

References chain. `cities.downstream_measure` is `{{ upstream_measure }} + 1`, and `upstream_measure` is
`COUNT({{ id }})`.

## What the converter emits

| AML definition | Ossie expression | Example |
|---|---|---|
| absent | the column sharing the field's name | `ecommerce_orders.id` becomes `id` |
| exactly one interpolation | what that interpolation resolves to | `{{ #SOURCE.quantity }}` becomes `quantity` |
| anything else | the body, with every interpolation resolved | `CAST({{ #SOURCE.created_at }} AS DATE)` becomes `CAST(created_at AS DATE)` |
| `@aql` | the body, unchanged | |

Every `@sql` row takes the `--sql-dialect` tag, and the `@aql` row takes `HOLISTICS_AQL`. Holistics SQL is
warehouse SQL throughout, so one dialect covers all of them and a reader needs no rule for which field
carries which tag.

A bare reference runs on any warehouse. A body with SQL around it runs on the one it was written for, and
`ecommerce_users.signup_month` shows why: it is `DATE_TRUNC({{ #SOURCE.created_at }}, month)`, which is
BigQuery argument order.

### An omitted definition

An omitted `definition` is a default rather than a missing value. AML fills it with
`{{ #SOURCE.<field name> }}`, the source column sharing the field's name.

- Only a dimension may omit it. A measure must carry one, and the compiler says so rather than defaulting:
  `Property 'definition' is missing on element but required on type 'Measure'`.
- The default is the same in a `TableModel` and a `QueryModel`. A `QueryModel` resolves it against the
  columns its query returns rather than against a table.
- Omitting it is common. Most dimensions in the Holistics AMQL test corpus do.

### Why a sibling reference is qualified

A `{{ field }}` names another AML field in the same model, and that field becomes an Ossie field in the same
dataset, so the reference travels as `dataset.field` in a dimension body and a measure body alike.

- `ecommerce_users.age` is `extract(year from age({{ birth_date }}))` and becomes
  `extract(year from age(ecommerce_users.birth_date))`.
- `ecommerce_orders.last_created_at` is `{{ created_at }}` under `aggregation_type: max` and becomes
  `MAX(ecommerce_orders.created_at)`.

A bare name would be right only by accident. An AML field usually shares its name with the column it reads,
which makes `birth_date` look like it would resolve on its own, and AML does not require the two to match.
`dimension created_at { definition: @sql {{ #SOURCE.sign_up_at }} }` is a field named `created_at` over a
column named `sign_up_at`, so a bare `created_at` would name a column that does not exist.

The shipped examples read the same way. `customer.customer_full_name` in
`examples/tpcds_semantic_model.yaml` is `c_first_name || ' ' || c_last_name`, where the field `c_first_name`
has the expression `c_first_name`, so the bare name could be either. The metric expressions settle it, and
they qualify: `SUM(store_sales.ss_ext_sales_price) / COUNT(DISTINCT customer.c_customer_sk)`.

Referencing also beats inlining. Inlining would copy the referenced field's body into every dependent
expression, so one warehouse-specific body spreads across many and an edit to the referenced field stops
reaching them. The specification points the same way: its "Not Supported in Expressions" table answers both
subqueries and CTEs with "use field references instead".

## Where the dialect comes from

A `@sql` body is the warehouse's SQL, and AML records nothing that says which warehouse. `data_source_name`
is the closest thing to it, and it is an instance-local connection name chosen by whoever created the
connection.

`to-ossie` therefore requires `--sql-dialect`. Its value is one of `ANSI_SQL`, `OSSIE_SQL_2026`, `BIGQUERY`,
`DATABRICKS` or `SNOWFLAKE`, the Ossie dialects sqlglot can parse. The reverse path parses the expression to
restore its field references, so a dialect outside that set would convert in one direction only.

Part of the [Apache Ossie Holistics converter](../README.md). See also [the fixture](fixture.md), [the mapping](mapping.md), [measures](measures.md), [the HOLISTICS payload](vendor-payload.md), [Ossie to AML](reverse.md), [Ossie SQL to AQL](aql.md) and [limitations](limitations.md).
