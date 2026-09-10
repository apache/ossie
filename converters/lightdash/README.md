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

# Apache Ossie <> Lightdash converter

Bidirectional converter between Ossie documents and
[Lightdash](https://github.com/lightdash/lightdash) semantic definitions.
Lightdash reads its semantic layer from dbt `schema.yml` files: dimensions and
metrics are declared per column (and per model) under `meta`. This converter
translates between that shape and Ossie.

- **Export** (`ossie_to_lightdash`): Ossie document → a Lightdash project.
  The default output is Lightdash's own dbt-free model files (`type: model`,
  `sql_from`, typed dimensions), which `lightdash deploy` reads as they are;
  `--format dbt-meta` produces one dbt `schema.yml` with Lightdash `meta`
  blocks instead, for projects that keep their definitions in dbt.
- **Import** (`lightdash_to_ossie`): a Lightdash project → an Ossie document,
  for teams adopting Ossie as the source of truth for definitions they already
  maintain in Lightdash. Both Lightdash shapes are read: dbt `schema.yml`
  files with Lightdash `meta`, and Lightdash's own dbt-free model files.

Lightdash-only attributes travel in `custom_extensions` under the registered
vendor token `LIGHTDASH`, so Lightdash → Ossie → Lightdash restores the
project exactly while every other consumer works from the core vocabulary.

## Installation

The core `apache-ossie` package is not on PyPI yet, so install both from git
(no checkout needed; `pipx` works the same way):

```bash
pip install "apache-ossie @ git+https://github.com/apache/ossie@main#subdirectory=python"
pip install "apache-ossie-lightdash @ git+https://github.com/apache/ossie@main#subdirectory=converters/lightdash"
```

From a checkout of this directory, `uv sync` (or `pip install -e .`) does the
same and picks up the in-repo core package. Once both packages are published,
`pip install apache-ossie-lightdash` is all that is needed. Python 3.11+.

## Usage

```
# Ossie -> a deployable Lightdash project (no dbt needed)
ossie-lightdash export -i semantic_model.yaml -o my-project --dialect BIGQUERY
cd my-project && lightdash deploy

# Ossie -> one dbt schema.yml with Lightdash meta, for a dbt project
ossie-lightdash export -i semantic_model.yaml -o schema.yml --format dbt-meta --dialect BIGQUERY [--meta-under-config]

# Lightdash dbt meta -> Ossie (a schema file, or a whole dbt project directory)
ossie-lightdash import -i path/to/dbt -o semantic_model.json --database analytics_db --schema marts --dialect BIGQUERY \
  --catalog path/to/dbt/target/catalog.json
```

`--catalog` takes the `catalog.json` that `dbt docs generate` writes: the
warehouse's real column types fill in `datatype` for every column without an
authored `type` (authored types win), reduced to Ossie's vocabulary
(`INT64` → `Integer`, `NUMBER(12,2)` → `Decimal`, `TIMESTAMP_TZ` →
`DateTimeTz`, ...). Lightdash learns most of its types from the warehouse
rather than from YAML, so without a catalog most fields leave untyped. A
model the catalog does not know is reported (`CATALOG_MODEL_MISSING`), which
is also how a stale catalog shows.

The default export writes `my-project/lightdash/models/<model>.yml`, one file
per dataset, and a starter `my-project/lightdash.config.yml` whose
`warehouse.type` is derived from `--dialect` (`BIGQUERY`, `SNOWFLAKE`,
`DATABRICKS`) or given with `--warehouse`; an existing config is left alone.
Each dataset's `source` becomes the model's `sql_from` verbatim, a table
reference or a query.

`-i/--input` and `-o/--output` follow the other converters; the two
positional forms (`export in out`) work too. Issues (anything lost or
approximated, see below) are printed to stderr as `[ISSUE_TYPE] element`.

### Python API

```python
from ossie import OssieDialect
from ossie_lightdash import LightdashToOssieConverter, OssieToLightdashConverter

result = LightdashToOssieConverter(OssieDialect.BIGQUERY).convert(
    schema_yml, database="analytics_db", schema="marts"
)
result.output   # OssieDocument
result.issues   # [ConverterIssue(issue_type, element_name), ...]

models = OssieToLightdashConverter(OssieDialect.BIGQUERY).convert_models(document)
models.output    # [{"type": "model", "name": ..., "sql_from": ..., "dimensions": [...]}, ...]

exported = OssieToLightdashConverter(OssieDialect.BIGQUERY).convert(document)
exported.output  # {"version": 2, "models": [...]}  (dbt-meta flavour)
```

## Mapping

The table describes the dbt-meta flavour; the model-file flavour is the same
mapping with three differences: `dataset.source` is the model's `sql_from`,
every dimension carries its own `type` and `sql` (a field without a datatype
gets `string` with a `DIMENSION_TYPE_DEFAULTED` issue), and model meta that
the dbt flavour has to stash (`sql_filter`, `group_details`,
`default_time_dimension`, ...) are ordinary top-level keys of the model file.

| Ossie | Lightdash (dbt meta) |
| ----- | -------------------- |
| `dataset` | dbt model (`name` = table part of `source`) |
| `dataset.source` | assembled on import from `--database` / `--schema` / model name |
| `dataset.primary_key` | model `meta.primary_key` (a single key exports as a string, a composite key as a list) |
| `ai_context` on datasets, fields and metrics | `ai_hint` on models, dimensions and metrics; a multi-line instruction is a list of hints, and the synonyms / examples of the structured form are rendered as extra hints on export |
| `field` with `dimension` | a column: every dbt column is a Lightdash dimension by default, so a dimension with nothing else to say is a plain column entry with no `meta` |
| `field` without `dimension` (measure-only) | `columns[].meta.dimension.hidden: true` — a hidden column is the closest Lightdash comes to a field that is not for grouping; it keeps its `type`, `label` and `ai_hint` |
| `field.datatype` | `meta.dimension.type` (`String`→`string`, `Integer`/`Decimal`/`Float`→`number`, `Date`→`date`, `DateTime`/`DateTimeTz`→`timestamp`, `Boolean`→`boolean`, `Time`/`Opaque`→`string`); on import `number` maps back to `Decimal` |
| `field.dimension.is_time` | `time_intervals: OFF` ↔ an explicit `is_time: false` on a temporal column; otherwise not carried — a non-temporal time axis (e.g. a year stored as `Integer`) has no Lightdash equivalent |
| `field.label` / `.description` | `meta.dimension.label` / column `description` |
| `field.expression` (≠ column name) | `meta.dimension.sql` (`dataset.col` ↔ `${TABLE}.col`) |
| `metric.name` | Lightdash scopes metric names per model, Ossie per semantic model, so the Ossie name is Lightdash's own field id `<model>_<metric>` (`orders_total_amount`); the bare name is stashed in the extension and restored on export. An Ossie metric with no stash exports under its name minus a `<model>_` prefix, if it has one |
| `metric.datatype` | derived on import: `Integer` for counts, `Decimal` for numeric aggregates over a `number` column, the column's type for `min`/`max`, the declared type for `boolean`/`string`/`date`/`timestamp` metrics |
| `metric` that is one aggregation over a column (`SUM(ds.col)`, `COUNT(DISTINCT ds.col)`, `SUM(DISTINCT ds.col)`, `PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY ds.col)`, ...) | column-level `meta.metrics.<name>` with a typed metric (`sum`, `count_distinct`, `sum_distinct`, `percentile` + `percentile: 90`, ...) |
| `metric` that is one aggregation over any other expression (`AVG(CASE WHEN ds.status = 'done' THEN 1 ELSE 0 END)`) | model-level `meta.metrics.<name>` with the typed metric and the operand as `sql` |
| `metric` with any other expression | model-level `meta.metrics.<name>` with `type: number` + `sql`, on the dataset that joins every other dataset the expression references (`${joined_model.col}`) |
| `relationship` | `meta.joins` (`sql_on` built from / parsed into column pairs, `relationship: many-to-one` unless a stashed one says otherwise); the `alias` and other join attributes (`relationship`, `type`, `fields`, ...) travel in the relationship's `lightdash` extension, and a dataset joined more than once from the same model is aliased on export |
| a join Ossie cannot reproduce — chained through another joined model (`${projects.org_id} = ${organizations.org_id}` on the `queries` explore), an expression join (`LOWER(a) = b`), extra conditions | the column pairs still become relationships (a chained pair derives the edge between the two models it names, unless that model declares it itself); the join is stashed verbatim on the dataset's `lightdash` extension and restored on export, replacing the generated join to the same target and alias. Pair order and side order (`${T.y} = ${M.x}`) are not semantic and need no stash |
| model meta without Ossie vocabulary (`label`, `hidden`, `sql_filter`, `group_details`, `default_time_dimension`, `required_filters`, `order_fields_by`, ...) and column meta outside `dimension` / `metrics` (`additional_dimensions`, ...) | stashed on the dataset's / field's `lightdash` extension (the latter under `column_meta`) and restored on export; invisible to other consumers |
| bare column names in Lightdash SQL (`SUM(budget_use)`) | qualified with the dataset when they name one of the model's columns (outside string literals, not when called as a function); the hosting model is also stashed as `model` so an expression that names no dataset can still be placed on export |
| `${TABLE}.col`, `${col}`, `${other_model.col}` in Lightdash SQL | `dataset.col` / `other_model.col`; `${alias.col}` is flattened onto the aliased model, `${metric}` is replaced by that metric's expression |
| Lightdash presentation attributes (`label`, `format`, `round`, `compact`, `group_label`, `hidden`, ...) | `custom_extensions` with `vendor_name: LIGHTDASH` (the registered vendor token; the lowercase name of earlier documents is still read); on export the extension data is overlaid onto the generated definition (structural keys — `sql`/`label` on dimensions, `sql`/`description` on metrics and `type`/`percentile` on metrics whose expression is a recognised aggregation, `join`/`sql_on` on joins — are protected and cannot be overridden) |

## Dialects

Lightdash SQL is written for the project's warehouse, so `import --dialect`
labels the emitted expressions with that warehouse's Ossie dialect
(`BIGQUERY`, `SNOWFLAKE`, `DATABRICKS`); warehouses without an Ossie dialect
(Postgres, Redshift, ...) keep the default `ANSI_SQL`. `export --dialect`
prefers that dialect, falls back to `ANSI_SQL`, and takes the first available
dialect with a `DIALECT_UNAVAILABLE` issue when an expression offers neither.

## Input shape

`import` reads two shapes, mixed freely: dbt schema files (`models:` and
`seeds:` entries; seeds are tables to Lightdash too) and Lightdash model files
(`type: model`, `sql_from`, a `dimensions:` list, the format `export` writes).
Point it at a file or at a project root and it walks `models/`, `seeds/`,
`lightdash/models/` and the rest in sorted order, ignoring `target/`,
`dbt_packages/`, virtualenvs and `dbt_project.yml`. A model file's `sql_from`
(or a dbt model's `meta.sql_from`) is the dataset `source` verbatim, so
`--database` / `--schema` only apply to models that do not name their own
relation. A join whose target is not among them is skipped
with a `JOIN_TARGET_UNKNOWN` issue, since Ossie relationships may only
reference datasets in the document.

## Where the meta lives

dbt 1.10+ places `meta` under `config:`; Lightdash reads both the top-level
`meta` and `config.meta` and lets `config.meta` win, and so does the import
direction. Export writes top-level `meta` by default; pass
`--meta-under-config` to emit the `config.meta` placement instead.

## Recommended source shape for dbt-native flows

If the Ossie documents are also consumed by dbt's native OSI parsing, prefer
importing **without** `--database` (i.e. `schema.table` sources): the database
is usually environment-dependent in dbt projects, and a database-less source
keeps one document valid across environments (see
[dbt-core#15649](https://github.com/dbt-labs/dbt-core/issues/15649)).
Omitting `--schema` as well is reported as a `SOURCE_UNQUALIFIED` issue.

## Fidelity and unavoidable losses

The two models disagree in five places. Each disagreement is handled the same
way: carry what has vocabulary on both sides, keep the rest on the Lightdash
side of the document, and report whatever another consumer of the document
will not see.

**Scope of metric names.** Lightdash scopes metric names per model, Ossie per
semantic model, and every real Lightdash project has a `count` on several
models. The Ossie name is therefore Lightdash's own field id,
`<model>_<metric>`, always rather than only on collision: a metric's name must
not change because a metric was added to a different model, and the field id
is what Lightdash users already see in its API and URLs. The bare name is
stashed and restored, so Lightdash → Ossie → Lightdash is exact; an Ossie
document not written by Lightdash sees its metric names normalised once
(`total_sales` on `store_sales` becomes `store_sales_total_sales`).

**What a dimension is.** Every dbt column is a Lightdash dimension unless it
is hidden; an Ossie field is a dimension only when it says so. `hidden: true`
and "no `dimension`" are the two ends of one mapping. The cost is that a
hidden column cannot carry Ossie's `is_time` role, and Lightdash has no way to
say "a dimension that is not for grouping but is not a measure either"; neither
side has such a thing today.

**Joins versus relationships.** Ossie's relationships form one graph. A
Lightdash explore is a base model plus an explicit list of joins, which may
reach a table through another one, join on an expression, or add conditions.
The graph part becomes relationships (a chained join derives the edge between
the two models it names); the explore part is stashed verbatim on the dataset
and restored, so the explore comes back exactly while other consumers see the
graph. Because Lightdash never resolves `${other.column}` transitively, a
metric spanning datasets is hosted on the model that joins every referenced
dataset directly, and dropped with an issue when no such model exists.

**Query-time evaluation.** Lightdash evaluates project parameters, user
attributes and Liquid templating when a query runs. No downstream consumer
can, so a dimension or metric whose SQL depends on them is skipped on import
and reported rather than shipped as SQL that is not SQL. Metric-to-metric
references are inlined for the same reason: Ossie metrics cannot reference
each other.

**Types.** Lightdash's `number` covers Ossie's `Integer`, `Decimal` and
`Float`, and its `timestamp` covers `DateTime` and `DateTimeTz`, so datatypes
authored in YAML round-trip by category rather than by exact member. A column
with no authored `type` leaves without a datatype unless `--catalog` supplies
the warehouse's, in which case the exact member is known; Lightdash itself
learns those types from the warehouse, not from the YAML.

### Kept for Lightdash only

Stashed in the `LIGHTDASH` extension and restored on export (as `meta` in the
dbt flavour, as top-level keys in the model-file flavour), invisible to other
consumers: presentation attributes of dimensions and metrics (`format`,
`round`, `compact`, `groups`, `urls`, `show_underlying_values`, ...); model
meta without Ossie vocabulary (`label`, `hidden`, `group_details`,
`default_time_dimension`, `order_fields_by`, ...); column meta outside
`dimension` / `metrics` (`additional_dimensions`); join attributes (`alias`,
`type`, `fields`, ...); and joins Ossie cannot reproduce.

Two of these change query results rather than presentation and are therefore
reported: a metric's `filters` (`METRIC_FILTER_NOT_PORTABLE` — the Ossie
expression is the unfiltered aggregate) and a model's `sql_filter` /
`sql_where` / `required_filters` (`ROW_FILTER_NOT_PORTABLE` — the Ossie
dataset is unrestricted). Encoding metric filters as `CASE WHEN` and
`sql_filter` as a query `source` is the planned fix.

### Approximated

- A dataset joined more than once is referenced through its first join when
  an expression names it (`date_dim.year` → `${date_dim.year}`, not the
  aliased second join); a `${alias.column}` reference is flattened onto the
  joined dataset with an `ALIAS_REFERENCE_FLATTENED` issue.
- A name that still collides after qualification (model `orders` + metric
  `x_total` versus model `orders_x` + metric `total`) is suffixed with a
  `METRIC_NAME_COLLISION` issue.
- `ai_context` synonyms and examples of the structured form are rendered as
  extra `ai_hint` lines on export; on import `ai_hint` becomes a plain
  instruction string.
- A single-column `primary_key` exports as a string, a composite one as a list.

### Not carried

- `unique_keys` — Lightdash has no corresponding concept.
- `ai_context` and custom extensions on relationships.
- `dataset.name` when it differs from the source table name: the dbt model is
  named after the table part of `source`.
- Relationships with mismatched `from_columns` / `to_columns` lengths
  (`RELATIONSHIP_COLUMNS_MISMATCHED`), and relationships to datasets missing
  from the document.
- Custom extensions of other vendors (`FOREIGN_EXTENSION_IGNORED`); they
  remain untouched in the Ossie document.
- Documents are emitted at the in-repo spec version; dbt-core 1.12's native
  Ossie parsing accepts `0.1.0` / `0.1.1` only.

## Issues

Every loss or approximation is reported as a `ConverterIssue`: on import
`JOIN_STASHED` / `JOIN_SQL_UNPARSED` (a join kept for Lightdash only),
`JOIN_TARGET_UNKNOWN`, `METRIC_FILTER_NOT_PORTABLE`, `ROW_FILTER_NOT_PORTABLE`,
`EXPRESSION_NOT_PORTABLE`, `METRIC_REFERENCE_INLINED`,
`ALIAS_REFERENCE_FLATTENED`, `METRIC_NAME_COLLISION`, `SOURCE_UNQUALIFIED`,
`METRIC_SQL_MISSING`; on export `CROSS_DATASET_METRIC_DROPPED`,
`FIELD_REFERENCE_UNJOINED`, `TIME_ROLE_NOT_REPRESENTABLE`,
`DIMENSION_TYPE_DEFAULTED`, `COLUMN_META_NOT_REPRESENTABLE`, `CATALOG_MODEL_MISSING`, `DIALECT_UNAVAILABLE`,
`RELATIONSHIP_COLUMNS_MISMATCHED`, `EXTENSION_DATA_INVALID`,
`FOREIGN_EXTENSION_IGNORED`.

## Development

```
uv sync
uv run pytest
```

The exported TPC-DS project compiles with `lightdash compile` (5 explores, 0
errors). Beyond the unit tests and the TPC-DS round trip, the converter is
exercised against real Lightdash projects (the public jaffle-shop demo and two
production projects on BigQuery): every model's meta and every join survive
Lightdash → Ossie → Lightdash, every uniquely named metric returns with its
expression unchanged, and the documents pass `validation/validate.py`.
