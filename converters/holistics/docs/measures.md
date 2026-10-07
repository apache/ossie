<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# Measures

A measure is the one AML construct where two properties compose into one Ossie expression. `aggregation_type` names the
aggregate, and `definition` gives the body.

## Naming

A measure belongs to a model. An Ossie metric sits at the document root, where one namespace covers the whole
document, so two models with a `total_quantity` each would collide. The converter therefore names a metric
`<dataset>__<measure>`: `order_items.total_quantity` becomes the metric `order_items__total_quantity`. A
dataset-level AML metric keeps its own name, because it was already document-wide.

The fixture needs this. `ecommerce_orders` has a measure `count_orders` and the dataset has a metric
`count_orders`, and the two become `ecommerce_orders__count_orders` and `count_orders`.

`__` rather than `.` because a metric reference inside an expression is the metric's own name, and the
sibling converters that face the same collision spell it the same way: Cube writes `orders__count`, and Omni
writes `<view>__<measure>`. A field reference stays dotted, as in `cities.id`, because fields are namespaced
per dataset and the shipped examples qualify them that way.

| `aggregation_type` | Meaning | Ossie expression |
|---|---|---|
| `sum`, `count`, `count distinct`, `min`, `max`, `average`, `median` | The aggregate wraps the body | `SUM(<resolved body>)` and so on |
| `custom` | The body already aggregates | the resolved body, unchanged |

Worked examples from the fixture.

`order_items.total_quantity` has `aggregation_type: sum` and the body `{{ #SOURCE.quantity }}`. The body is a bare source
column, so it resolves to `quantity`, and the metric expression is `SUM(quantity)` under `--sql-dialect`.

`order_items.custom_total_quantity` has `aggregation_type: custom` and the body `sum({{ #SOURCE.quantity }})`. The aggregate
is already in the body, so the converter wraps nothing and emits `sum(quantity)`.

`ecommerce_orders.last_created_at` has `aggregation_type: max` and the body `{{ created_at }}`. That is a sibling reference,
not a source column. `created_at` is a dimension in the same model, so it becomes a field in the same dataset and the metric expression is `MAX(ecommerce_orders.created_at)`.

`cities.downstream_measure` has `aggregation_type: custom` and the body `{{ upstream_measure }} + 1`. `upstream_measure` is a measure in the same model, so it becomes a metric and the expression is `cities__upstream_measure + 1`. `upstream_measure` itself is `COUNT({{ id }})`, whose sibling `id` is `{{ #SOURCE.id }}`, so it becomes `COUNT(cities.id)`.
Referencing rather than inlining means the converter never walks that chain, so a reference cycle in AML cannot make it recurse.

Referencing handles a case inlining could not. A measure whose body names another measure,
under a named `aggregation_type`, would inline to `SUM(COUNT(x))`, which no warehouse accepts. As a reference it is
`SUM(cities__upstream_measure)`, a metric naming a metric, which the semantic layer resolves rather than the warehouse.

`core-spec/expression_language.md` lists "Column and Metric references" among the supported constructs. The
converter emits the dataset-qualified form the shipped examples use for a field, and the metric's own name for
a metric, and raises a WARNING naming the metric so a consumer can check the spelling against its own
resolver.

A measure written in `@aql` travels under `HOLISTICS_AQL` whatever its `aggregation_type` says, because the body already contains its own aggregation.


Part of the [Apache Ossie Holistics converter](../README.md). See also [the fixture](fixture.md), [the mapping](mapping.md), [expression translation](expressions.md), [the HOLISTICS payload](vendor-payload.md), [Ossie to AML](reverse.md), [Ossie SQL to AQL](aql.md) and [limitations](limitations.md).
