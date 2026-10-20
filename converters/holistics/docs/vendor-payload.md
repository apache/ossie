<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# The HOLISTICS payload

AML carries properties that the Ossie core specification has no field for. The converter stashes each one under a single
`custom_extensions` entry with `vendor_name: HOLISTICS`, attached to the Ossie object it came from.

```yaml
custom_extensions:
- vendor_name: HOLISTICS
  data: '{"_v": 1, "aml_type": "TableModel", "data_source_name": "bigquery_demo"}'
```

`data` is always a single JSON-encoded string, never a nested object, because the core specification requires that. It
carries `_v`, a shape version, currently 1. The converter fails on an unrecognised version rather than misreading a
shape it has never seen. Keys are sorted, so a change to the stash shows up as a diff on the keys that changed.

What goes into the stash:

| AML property | Scope | Why Ossie has no field for it |
|---|---|---|
| `aggregation_type` | metric | Ossie carries the whole aggregate in one expression |
| `hidden` | field, metric | Ossie models semantics, not visibility |
| `format` | field, metric | A display format, not semantics |
| `type` | field, metric | The AML type, kept verbatim so the return trip recovers it |
| query template forms | dataset | Which of the three forms the body uses, so a consumer knows why `source` is not runnable |
| `param` | dataset | A runtime input with no Ossie equivalent |
| `persistence` | dataset | A materialization directive, not semantics |
| `data_source_name` | model, dataset | An instance-local connection name |
| `owner` | model, dataset | An instance-local email address |
| `table_name` | dataset | Kept verbatim, because `source` normalizes the quoting |
| relationship kind | relationship | Ossie has no one-to-one marker |
| `active` | relationship | See [Relationships](mapping.md#relationships) |
| `where` | relationship | A `RelationshipFilter` narrowing the join condition. No Ossie equivalent |
| `direction`, `nullable`, `rlp_propagation` | relationship | No Ossie equivalent |
| `__fqn__` | dataset | The unflattened module path, kept so the return trip restores it |
| `aml_type` | model, dataset | `TableModel`, `QueryModel` or `Dataset`, so the return trip does not have to guess a query from the `source` text |
| `label` | field, metric, dataset | AML requires a label on every dimension and measure, and Ossie's `label` means categorization rather than a display name |
| `model` | metric | The model a measure came from. The reverse path reads this rather than splitting the metric name, which `__` makes ambiguous |
| `aml_name` | metric | The measure's own name, since the metric carries `<dataset>__<measure>` |
| `scope` | field | `dataset` when the field came from a dataset-level AML `dimension`. Without the marker the reverse path would write it back inside a model, where a body spanning two models does not resolve |

A foreign vendor's `custom_extensions` entry on the same object passes through untouched. The `HOLISTICS`
entry appears only on an object that has something to carry.

`distinct_rows` is absent from the table above. It is deprecated in AML, so the converter neither reads it nor carries it.


Part of the [Apache Ossie Holistics converter](../README.md). See also [the fixture](fixture.md), [the mapping](mapping.md), [expression translation](expressions.md), [measures](measures.md), [Ossie to AML](reverse.md), [Ossie SQL to AQL](aql.md) and [limitations](limitations.md).
