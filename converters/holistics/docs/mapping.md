<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# Mapping

| AML | Ossie | Notes |
|---|---|---|
| `Dataset` | the semantic model (the document root) | One dataset per document, its fields at the root beside `version` |
| `Dataset.description` | `description` | Falling back to `label` when the dataset declares no description. The unused one goes to the stash |
| `TableModel` | `dataset` with `source` from `table_name` | `table_name` arrives already quoted for the warehouse, for example `` `ecommerce`.`order_items` `` |
| `QueryModel` | `dataset` with `source` derived from the `query` body | The body is not plain SQL and cannot be copied verbatim. See [Query model bodies](#query-model-bodies) |
| model `__fqn__` | `dataset.name` | AML joins module paths with `::`. Ossie names are flat, so each `::` becomes `__` |
| `dimension` | `dataset.fields[]` entry | See [Expression translation](expressions.md) |
| `measure` | `metrics[]` entry named `<dataset>__<measure>` | See [Measures](measures.md#naming) |
| dimension `primary_key: true` | `dataset.primary_key` | AML marks the key on the dimension. Ossie carries it on the dataset |
| `dimension.type` | `field.datatype` | See [Data types](#data-types) |
| `relationship(a.x > b.y, true)` | `relationships[]` entry | `>` is many-to-one. `a` becomes `from`, `b` becomes `to` |
| `relationship(a.x - b.y, true)` | `relationships[]` entry | `-` is one-to-one. Ossie has no one-to-one marker, so the kind goes to the stash |
| dataset-level `dimension` | `dataset.fields[]` entry on the model it names | The AML dimension names its owning model in `model` |
| dataset-level `metric` | `metrics[]` entry | Always AQL in practice, so always tagged `HOLISTICS_AQL` |
| everything else | `custom_extensions[HOLISTICS]` | See [The HOLISTICS payload](vendor-payload.md) |

## Relationships

AML writes a relationship as a pair of field references plus an active flag. Ossie needs a name, and AML gives
none, so the converter synthesizes `<from>_to_<to>`, the spelling Cube, GoodData, NVIDIA and Orion Belt all use
in their Ossie fixtures. A second relationship between the same pair takes `_2`, then `_3`. The reverse path
reads the endpoints from `from`, `to`, `from_columns` and `to_columns`, so the name it was given does not
change the AML it writes.

AML has three relationship types, and only one of them has an Ossie form.

| AML type | Ossie |
|---|---|
| `EqualityRelationship` (`many_to_one`, `one_to_one`) | a `relationships[]` entry |
| `RangeRelationship` (`range`) | none. Its `match` is an AQL condition, not a column pair |
| `ManyToManyRelationship` (`many_to_many`) | none. Ossie relationships run many to one |

An `EqualityRelationship` carries its first column pair in `from` and `to`, and each further pair in a
repeated `on` block. Blocks combine with AND, which is exactly Ossie's positional `from_columns` and
`to_columns`:

```
from: FieldRef { model: 'merchants', field: 'city_id' }        from_columns: [city_id, country_code]
to:   FieldRef { model: 'cities',    field: 'id' }             to_columns:   [id, country_code]
on { from: merchants.country_code  to: cities.country_code }
```

The `relationship(a.x > b.y, true)` shorthand carries one pair only. A composite needs the long
`RelationshipConfig` form, which is what the fixture uses for `merchants` to `cities`.

A relationship endpoint and a model spell the same name two different ways. The endpoint uses dots, as in
`reporting.calendar`, while the model's own `__fqn__` uses colons, as in `reporting::calendar`. The compiler
fills a `__refFqn__` shortcut on some endpoints and leaves it `null` on others, and in the fixture the one it
leaves null names a model inside a module. The converter therefore ignores `__refFqn__` and resolves an
endpoint by replacing each `.` with `::` and looking the result up in `models`.

A relationship may set `active: false`. Holistics does not join across an inactive relationship unless a query asks
for it. Ossie has no inactive marker. Emitting an inactive relationship as an ordinary Ossie relationship would change
which joins a consumer makes, so the converter records the flag in the stash and raises a WARNING for each one.

## Query model bodies

A `TableModel` names a table, so its `source` is that name. A `QueryModel` carries Holistics SQL in `query` instead,
and that body may use Holistics template syntax. The compiler does not resolve it. The body is passed
through, and each referenced model is inlined under `models[]`.

`delivered_orders` in the fixture compiles to this:

```
query:    select {{ #ecommerce_orders.* }}
          from {{ #ecommerce_orders }}
          where {% filter(min_delivery_attempts) %} delivery_attempts {% end %}
models[]: [ecommerce_orders, inlined whole, table_name `ecommerce`.`orders`]
```

| Form | Means |
|---|---|
| `{{ #model }}` | the referenced model's relation |
| `{{ #model.* }}` | every column of the referenced model |
| `{% filter(param) %} ... {% end %}` | a predicate, once a param value arrives |

### What the converter does

A query body that matches none of those forms is plain SQL. It becomes the dataset's `source` unchanged.

A query body that matches any of them is carried unresolved. The converter puts the verbatim body in
`source`, records the forms it found in the stash, and raises an ERROR naming the dataset.

### Why it resolves nothing, including what it could

`models[]` inlines the referenced model with its `table_name`, so `{{ #ecommerce_orders }}` could be resolved
to `` `ecommerce`.`orders` ``. The converter does not do it.

A filter block cannot be resolved at conversion time, because the param value arrives at query time. So a
body can carry both a form the converter could resolve and a form it cannot. Resolving the first leaves a
`source` that reads as SQL and is not, and a reader would have to know which forms this converter handles to
tell. Leaving every form in place keeps the body uniformly unresolved.

Resolving them would also mean a second implementation of Holistics' own resolution rules, kept in step by
hand. That is the same reason the converter does not translate AQL and does not rewrite warehouse SQL. See
[expression translation](expressions.md).

A dbt semantic manifest carries `node_relation.relation_name`, which dbt resolved during its own
compilation, so `ossie_to_msi.py` reads a finished string and the question does not arise there. The
Holistics CLI passes the template through instead.

## Data types

| AML `type` | Ossie `datatype` | `dimension.is_time` |
|---|---|---|
| `text` | `String` | defaults to false |
| `truefalse` | `Boolean` | defaults to false |
| `date` | `Date` | defaults to true |
| `datetime` | `DateTime` | defaults to true |
| `number` | `Decimal` | defaults to false |

`number` is the one lossy row. AML does not say whether a `number` is exact or approximate, so no Ossie member matches it
exactly. `Decimal` is the closest, because the Ossie specification leaves its precision and scale unspecified. The
converter writes the original AML `type` into the stash, so an Ossie document converted back to AML recovers the AML type
regardless of this choice.

`is_time` follows the Ossie default rule for each mapped `datatype`, so the converter writes no explicit `is_time`.


Part of the [Apache Ossie Holistics converter](../README.md). See also [the fixture](fixture.md), [expression translation](expressions.md), [measures](measures.md), [the HOLISTICS payload](vendor-payload.md), [Ossie to AML](reverse.md), [Ossie SQL to AQL](aql.md) and [limitations](limitations.md).
