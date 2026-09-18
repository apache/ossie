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

# Apache Ossie Hologres Converter

Converts between Apache Ossie semantic models and [Alibaba Cloud Hologres](https://www.alibabacloud.com/product/hologres)
Semantic Views, available in Hologres V5.0.0 and later.

A Hologres Semantic View declares tables, their relationships, business dimensions and
metrics as a queryable database object. Queries name dimensions and `AGG(metric)` instead
of repeating joins and aggregations, and the engine aggregates each metric within its own
join subtree so a one-to-many join cannot inflate a total.

The two directions are deliberately asymmetric, because Hologres publishes and consumes
its Semantic View definitions in different formats:

- **Export (Ossie -> Hologres)** produces `CREATE SEMANTIC VIEW` **SQL DDL text**.
  Hologres has no YAML import function, so the DDL is the only way to create a
  Semantic View.
- **Import (Hologres -> Ossie)** consumes the **`model_yaml`** that Hologres publishes
  for every Semantic View in the `hologres.hg_semantic_view_properties` system table.

## Installation

```bash
uv sync
```

## Usage

### Command line

Export an Apache Ossie model to DDL and run it:

```bash
ossie-hologres export -i model.yaml -o view.sql
psql -h <endpoint> -p 80 -U <user> -d <db> -f view.sql
```

Import an existing Semantic View back into Apache Ossie. Hologres publishes the
structured model for every Semantic View in a system table:

```bash
psql -h <endpoint> -p 80 -U <user> -d <db> -At -c \
  "SELECT property_value FROM hologres.hg_semantic_view_properties
   WHERE schema_name = current_schema()
     AND view_name = 'sales_sv' AND property_key = 'model_yaml';" > model_yaml.yaml

ossie-hologres import -i model_yaml.yaml -o model.yaml
```

Export options:

| Option | Purpose |
|--------|---------|
| `--schema` | Schema for the view, and a default for datasets whose `source` has none. Never overrides a schema already written into a `source`. |
| `--database` | Assert the database the dataset sources belong to. |
| `--drop-if-exists` | Prefix a `DROP SEMANTIC VIEW IF EXISTS`. Hologres has no `CREATE OR REPLACE` or `ALTER`, so this is how a definition is changed. |
| `--metric-owner METRIC=DATASET` | Name the table a metric belongs to, for metrics whose expression has no qualified column to infer it from (`COUNT(*)`). Repeatable. |
| `--skip-unsupported-metrics` | Warn about and skip metrics with no Semantic View form instead of failing. |

### Python API

```python
from ossie_hologres import convert_ossie_to_semantic_view, convert_semantic_view_to_ossie

ddl = convert_ossie_to_semantic_view(ossie_yaml, schema="public")
ossie_yaml = convert_semantic_view_to_ossie(model_yaml)
```

## Mapping

| Apache Ossie | Hologres Semantic View |
|--------------|------------------------|
| document root `name` | `view_name` |
| document root `description` | view-level `COMMENT` |
| `dataset.name` | `TABLES` alias |
| `dataset.source` | the `[schema.]table` in `TABLES` |
| `dataset.primary_key` | `PRIMARY KEY (...)` |
| `dataset.fields[]` | `DIMENSIONS <alias>.<name> AS <expr>` |
| `field.description` | dimension `COMMENT` |
| `metrics[]` | `METRICS <owner>.<name> AS <agg>` |
| `metric.description` | metric `COMMENT` |
| `relationships[]` | `RELATIONSHIPS <name> AS <from>(cols) REFERENCES <to>(cols)` |

Three mappings are worth explaining.

**Dataset names are table aliases.** On import the Hologres alias (`o`, `c`) becomes the
Ossie dataset name, because the alias is what every dimension, metric and relationship
references. That also makes Hologres' own `sum(o.amount)` already correct as an Ossie
dataset-qualified metric expression.

**Relationship direction carries cardinality.** Hologres records
`relationship_type: many_to_one` and nothing else. Ossie already encodes the same fact in
the direction of a relationship -- `from` is the many side holding the foreign key, `to`
is the one side holding the primary key -- so the type is derived rather than stored.
Getting the direction backwards makes Hologres treat a join as one-to-many and aggregate
a metric more than once, so it matters.

**Metric ownership.** Ossie metrics are model-level; Hologres namespaces each metric under
the table it aggregates. The owner is inferred from the metric expression's column
references. `COUNT(*)` references none, so it needs an explicit owner via a
`custom_extensions` entry or `--metric-owner`; the converter refuses to guess, because
picking the wrong owner silently changes the number under a fan-out join.

**Expressions are read and written as `ANSI_SQL` only.** This converter introduces no
dialect token of its own, so it needs no change to the core spec. Hologres is
PostgreSQL-compatible, but that alone does not justify one: the portable spelling is
nearly always available and sqlglot normalizes to it, so `x::text` becomes
`CAST(x AS TEXT)` in both directions.

For the PostgreSQL-only syntax that does remain -- `j -> 'k'`, `s ~ 'pattern'`, the
1-based `arr[1]` -- the `ANSI_SQL` label is not strictly accurate. It is still the better
trade. A converter that looks for an `ANSI_SQL` expression and finds none *drops the
field* (see `converters/databricks`), so over-labelling loses data silently, whereas an
optimistic label at worst surfaces as a SQL error on the target engine. Deciding this per
expression was tried and abandoned: sqlglot's default dialect is not ANSI SQL, so any such
test mislabels in both directions -- passing `ILIKE` as portable while flagging the
standard `SUBSTRING`, `EXTRACT` and `DATE_TRUNC` as vendor-specific.

## Requirements

Hologres V5.0.0 or later, with `hg_enable_semantic_view_query` on (the default). Confirm
with:

```sql
SELECT hg_version();
```

## Limitations

These come from Hologres itself, not the converter. The converter reports each one with
the offending field named rather than emitting DDL the server will reject.

### Definitions

| Constraint | Effect |
|------------|--------|
| A definition expression must be row-level over a **single** table | A dimension or metric spanning two datasets is rejected |
| Aggregates are limited to `count` / `sum` / `avg` / `min` / `max` | `stddev`, `percentile_cont` and friends are rejected |
| No derived, ratio or filtered metrics | `SUM(a) / COUNT(*)` is rejected; compute it in the query layer |
| A `REFERENCES` target must be the target table's `PRIMARY KEY` | A relationship whose `to_columns` are not the target's key is rejected. A matching `unique_keys` entry is promoted with a warning |
| No window functions, subqueries, `VOLATILE` or set-returning functions | Structurally detectable cases are rejected here; the rest are rejected by Hologres at `CREATE` time |
| A top-level operator in a definition must be parenthesised | Handled automatically: `a \|\| b` is emitted as `(a \|\| b)` |
| Dimension and metric names are referenced bare in queries | Names must be unique across the whole view. Ossie only requires field names to be unique per dataset, so a collision across datasets is rejected rather than silently renamed |
| A Semantic View cannot span databases | Datasets naming different databases are rejected |
| No `CREATE OR REPLACE` or `ALTER SEMANTIC VIEW` | Use `--drop-if-exists` to recreate |

### Information not preserved on export

Hologres offers exactly one annotation slot: `COMMENT`, on the view, each dimension and
each metric. `description` maps there, and a string-form `ai_context` is folded in.
Everything else is reported as a warning rather than dropped silently:
`unique_keys` (unless promoted), `datatype`, `dimension.is_time`, `label`, object-form
`ai_context` (synonyms, instructions, examples), `dataset.description` (the `TABLES`
clause takes no comment), `relationship.ai_context`, and non-`HOLOGRES` vendor
`custom_extensions`.

### Round-trip fidelity

A full loop is lossless for everything Hologres stores:

```
Ossie -> DDL -> Hologres -> model_yaml -> Ossie
```

Two caveats. Expressions come back **normalized, not byte-identical**: sqlglot upper-cases
function names and rewrites `x::text` as `CAST(x AS TEXT)`, and Hologres re-renders
operator expressions with its own parenthesisation and explicit casts. And a `source` is
always rebuilt as a full three-part `database.schema.table` from Hologres' `base_table`,
so a two-part or unqualified `source` gains the missing parts.

## Development

```bash
uv sync
uv run pytest
```

### Live tests

A `CREATE SEMANTIC VIEW` statement can only really be validated by a Hologres server, so
the suite includes end-to-end tests that create a view, query it, and read the model back.
They are skipped unless the connection environment variables are set, which keeps CI and a
plain `uv run pytest` hermetic:

```bash
export HOLOGRES_HOST=<endpoint>
export HOLOGRES_PORT=80
export HOLOGRES_USER='BASIC$account'   # single quotes: the $ is literal
export HOLOGRES_PASSWORD='<password>'
export HOLOGRES_DB=<database>

uv sync --group live
uv run pytest -m live -v
```

The tests create everything inside an `ossie_hologres_it` schema and drop it afterwards.
Credentials are read only from the environment; none are stored in this repository.

The `live` dependency group holds the PostgreSQL driver and is excluded from
`default-groups`, so CI never installs a database driver it cannot use.

### Fixtures

The `tests/fixtures` pairs are not hand-written. Each `*_semantic_view.sql` was executed
against a real Hologres 5.0.0 instance and the resulting view queried, and each
`*_model_yaml.yaml` is that instance's own readback of the corresponding DDL. A live test
re-checks the readback against the committed fixture, so if Hologres changes the shape it
emits, the suite says so instead of quietly testing a stale format.

## Future effort

- Import from `ddl_text` as an alternative to `model_yaml`, for views created before
  `model_yaml` was populated.
- Revisit derived and ratio metrics if Hologres gains support for them.
- Map `ai_context` synonyms if Hologres gains an AI annotation surface.
