<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# Limitations

## Coverage matrix

Every construct the converter does not carry, with its consequence. A construct it drops or changes raises a
structured issue at conversion time, listed under [Issue codes](#issue-codes).

| Construct | Limitation | Consequence |
|---|---|---|
| AQL field and metric bodies | Carried under `HOLISTICS_AQL`, never translated to SQL | A consumer that does not speak AQL cannot evaluate these, though it can read and round-trip them |
| Inactive relationships | `active: false` has no Ossie marker. **WARNING severity**, one issue per relationship | A consumer joins across a relationship that Holistics leaves out by default |
| Params | Runtime inputs with no Ossie equivalent | A `@sql` body that reads a param cannot be resolved statically |
| `number` precision | AML does not distinguish exact from approximate | A consumer reading `Decimal` may infer exactness AML never stated. The stash holds the original AML type |
| Warehouse SQL portability | Emitted under `--sql-dialect`, never rewritten | A document converted for one warehouse does not run on another |
| Dashboards, reports and canvases | Out of scope. Ossie models semantics, not presentation | No loss to the semantic model |
| A query body using Holistics template syntax | Detected and carried unresolved, never resolved. **ERROR severity** | The dataset's `source` is not runnable SQL. A consumer must resolve it through Holistics or drop the dataset |
| `RangeRelationship` and `ManyToManyRelationship` | Neither has an Ossie form. **WARNING severity**, one issue per relationship | Both carry an AQL `match` rather than column pairs, which is why no column mapping exists. The payload is stashed on the document and the reverse path rebuilds it, so a consumer reading Ossie sees the two datasets as unrelated while the round trip keeps the join |
| A relationship `where` filter | A `RelationshipFilter` restricts the join condition. No Ossie equivalent | The join widens to every matching row, so a consumer counts rows Holistics excludes. **WARNING severity** |
| A `QueryModel` becoming a query in `source` | `source` is one string, so a consumer reads the text to tell a query from a table reference. **WARNING severity** | Whether a given target reads it as a query depends on that target. See below |

These are the reverse path's, writing AML from Ossie.

| Construct | Limitation | Consequence |
|---|---|---|
| A metric spanning datasets, carrying Ossie SQL | Rendered as AQL, which is the only body a dataset-level `metric` accepts. **WARNING severity** | The AQL is a construct-for-construct map of the Ossie SQL, so it is worth reading once. See [Ossie SQL to AQL](aql.md) |
| A metric whose Ossie SQL this converter does not write as AQL | A window function, or a construct outside the map. **ERROR severity** | The metric is dropped. AQL may well express it; the converter does not translate it. See [Ossie SQL to AQL](aql.md) |
| A `@sql` body with a window function | The warehouse SQL it generates is valid only for a query that groups by the frame's columns. **WARNING severity** | A grand total over it produces warehouse SQL the database rejects. See [Ossie SQL to AQL](aql.md#a-window-function-depends-on-how-the-metric-is-queried) |
| An expression sqlglot cannot parse, or tagged with a dialect that is not SQL | Its column references cannot be located, so they cannot be written as interpolation. **ERROR severity** | The body is written out unchanged. It compiles, and AQL cannot see which fields it reads, so query rewriting and optimisation lose that link |
| An expression in a warehouse dialect that is not `--sql-dialect` | Translating Ossie SQL between warehouses is out of scope. **ERROR severity** | The body is written unchanged and will not run on the connection. An `ANSI_SQL` or `OSSIE_SQL_2026` body is rendered into the target instead, with a WARNING |
| A qualifier naming no dataset in the document | A table alias from a subquery has no AML form. **ERROR severity** | The reference is left as written, and the body compiles without the field linkage |
| A `@sql` body naming a field of another dataset | A `@sql` expression names only fields of the model it sits in. **ERROR severity** | Holistics rejects it at SQL generation with "Cannot refer to external field in TableModel/QueryModel". An expression that names two models belongs in AQL |
| A field with no `datatype` | AML requires `type` on every dimension. **WARNING severity** | `text` is written. `date_dim.d_quarter_name` in `examples/tpcds_semantic_model.yaml` is one |
| A name AML cannot spell | An Ossie name is any non-empty string. An AML identifier is letters, digits and underscore, with no leading digit. **WARNING severity** | Each illegal character becomes an underscore, so `Flights semantic model` in `examples/flights.semantic_model.yaml` is written `Flights_semantic_model`. Two names that collapse onto one spelling stop the run |
| A semantic model sharing a name with one of its datasets | Ossie keeps the two in separate namespaces and AML has one namespace for `Dataset` and `Model`. **WARNING severity** | The AML dataset takes a `_dataset` suffix. Models and relationships are unaffected, since nothing references it. `converters/cube/tests/fixtures/databricks_ossie.yaml` names both `orders` |
| File layout | The compiled JSON records no file provenance | `AML to Ossie to AML` returns the same models, not the same files. See [Ossie to AML](reverse.md) |
| Comments and property order | Neither survives the forward path | A diff against the original AML shows both, even where nothing semantic changed |


## Issue codes

Every code the converter writes to its issue log, with the severity it carries. The sections above give the
reasoning behind the ones that need it.

`to-ossie`:

| Code | Severity | Raised when |
|---|---|---|
| `HOLISTICS_AQL_NOT_TRANSLATED` | INFO | A body is AQL, carried under `HOLISTICS_AQL` |
| `HOLISTICS_INACTIVE_RELATIONSHIP` | WARNING | `active: false`, which Ossie cannot express |
| `HOLISTICS_PARAM_DROPPED` | WARNING | A model declares params, stashed rather than carried |
| `HOLISTICS_QUERY_SOURCE` | WARNING | A `QueryModel` becomes a query in `source` |
| `HOLISTICS_RELATIONSHIP_FILTER` | WARNING | A relationship carries a `where` filter |
| `HOLISTICS_UNSUPPORTED_RELATIONSHIP` | WARNING | A range or many-to-many relationship, stashed on the document |
| `HOLISTICS_UNKNOWN_DATATYPE` | WARNING | An AML type outside the data type table |
| `HOLISTICS_UNRESOLVED_QUERY_TEMPLATE` | ERROR | A query body uses Holistics template syntax |
| `HOLISTICS_UNKNOWN_AGGREGATION` | ERROR | An `aggregation_type` outside the aggregation table |
| `HOLISTICS_UNRESOLVED_REFERENCE` | ERROR | An interpolation naming nothing in the dataset |

`to-aml`:

| Code | Severity | Raised when |
|---|---|---|
| `HOLISTICS_ASSUMED_TYPE` | WARNING | A field has no `datatype`, so `text` is written |
| `HOLISTICS_NAME_NOT_SPELLABLE_IN_AML` | WARNING | A name holds characters AML identifiers do not |
| `HOLISTICS_DATASET_NAME_COLLIDES_WITH_MODEL` | WARNING | The model name is also a dataset name |
| `HOLISTICS_EXPRESSION_RENDERED_INTO_TARGET_DIALECT` | WARNING | A portable body was rendered into `--sql-dialect` |
| `HOLISTICS_AGGREGATION_NOT_STRIPPED` | WARNING | A stashed aggregation does not wrap the whole expression |
| `HOLISTICS_METRIC_RENDERED_AS_AQL` | WARNING | A cross-dataset metric's Ossie SQL became AQL |
| `HOLISTICS_UNDECLARED_COLUMN_ADDED_AS_DIMENSION` | WARNING | An AQL metric reads a column no dataset declares |
| `HOLISTICS_WINDOW_FUNCTION_NEEDS_A_GROUPING` | WARNING | A `@sql` body has a window function |
| `HOLISTICS_METRIC_SQL_NOT_TRANSLATED_TO_AQL` | ERROR | A construct the converter does not write as AQL |
| `HOLISTICS_CROSS_MODEL_SQL_BODY` | ERROR | A `@sql` body names a field of another dataset, or a metric it cannot spell |
| `HOLISTICS_METRIC_REFERENCE_CYCLE` | ERROR | A metric references itself through another metric |
| `HOLISTICS_UNKNOWN_QUALIFIER` | ERROR | A qualifier naming no dataset in the document |
| `HOLISTICS_UNPARSEABLE_SQL` | ERROR | sqlglot cannot read the body at its declared dialect |
| `HOLISTICS_NO_USABLE_DIALECT` | ERROR | No body in the target dialect and none portable |

## Known limitations

### Name flattening can collide

The converter replaces `::` with `__` to turn an AML module path into a flat Ossie dataset name, so
`reporting::calendar` becomes `reporting__calendar`. A doubled separator is used rather than a single `_` because a
single one collides with any model name that already contains an underscore, which most do. A root model named
`reporting_calendar` would take the name `reporting::calendar` wants.

A collision is still reachable, just rarer. A root model named `reporting__calendar` still takes it, and the
converter fails naming both rather than letting one dataset overwrite the other. The unflattened `__fqn__`
goes to the stash either way, so the reverse path reads the module boundaries from there.

### A query model becomes a query in `source`

A `QueryModel` has no table name, only a Holistics SQL body, so its Ossie `source` is that query. The specification
provides for this. `core-spec/spec.md` describes `source` as a "Reference to underlying physical table/view
(e.g., `database.schema.table`) or query".

`source` is a single string, `{"type": "string", "minLength": 1}`, so a consumer tells a query from a table
reference by reading the text. Several converters do, each with its own test, and they do not all draw the
line in the same place. `(SELECT * FROM public.users)` and `EXEC sp_get_orders` are two spellings where the
answers differ.

So the converter emits a `QueryModel` as a query in `source` and raises a WARNING naming the model. Whether a
given target reads it as a query depends on that target, and a model worth sharing widely is better
materialised as a table in Holistics first.

### Warehouse SQL has no dialect of its own

`Dialect` in `core-spec/ossie-schema.json` is a closed enumeration. Its members
today are `ANSI_SQL`, `SNOWFLAKE`, `MDX`, `TABLEAU`, `DATABRICKS`, `MAQL`, `BIGQUERY`, `SIGMA`, `THOUGHTSPOT`, `DAX`, `HOLISTICS_AQL`, and
`OSSIE_SQL_2026`.

`HOLISTICS_AQL` covers AQL. It does not cover a `@sql` body, because that body is the warehouse's SQL rather than a
Holistics language, and AML does not record which warehouse. A computed `@sql` body takes its dialect from
`--sql-dialect`, which must name an existing member.


Part of the [Apache Ossie Holistics converter](../README.md). See also [the fixture](fixture.md), [the mapping](mapping.md), [expression translation](expressions.md), [measures](measures.md), [the HOLISTICS payload](vendor-payload.md), [Ossie to AML](reverse.md) and [Ossie SQL to AQL](aql.md).
