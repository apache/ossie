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

- **Export** (`osi_to_lightdash`): Ossie document → a dbt `schema.yml`-shaped
  dictionary with Lightdash `meta` blocks, ready to merge into a dbt project.
- **Import** (`lightdash_to_osi`): a Lightdash-flavoured `schema.yml` → an
  Ossie document, as a migration path for teams with an existing installed
  base of Lightdash metrics.

```
ossie-lightdash export semantic_model.yaml schema.yml
ossie-lightdash import schema.yml semantic_model.json --database analytics_db --schema marts
```

## Mapping

| Ossie | Lightdash (dbt meta) |
| ----- | -------------------- |
| `dataset` | dbt model (`name` = table part of `source`) |
| `dataset.source` | assembled on import from `--database` / `--schema` / model name |
| `field` (no `dimension`) | plain column entry |
| `field` with `dimension` | `columns[].meta.dimension` (`is_time` ↔ `type: date/timestamp`; an empty `dimension: {}` marks a categorical dimension) |
| `field.label` / `.description` | `meta.dimension.label` / column `description` |
| `field.expression` (≠ column name) | `meta.dimension.sql` (`dataset.col` ↔ `${TABLE}.col`) |
| `metric` with single-aggregation expression (`SUM(ds.col)`, `COUNT(DISTINCT ds.col)`, ...) | column-level `meta.metrics.<name>` with a typed metric (`sum`, `count_distinct`, ...) |
| `metric` with any other single-dataset expression | model-level `meta.metrics.<name>` with `type: number` + `sql` |
| `relationship` | `meta.joins` (`sql_on` built from / parsed into column pairs) |
| Lightdash presentation attributes (`label`, `format`, `round`, `compact`, `group_label`, `hidden`, `percentile`, ...) | `custom_extensions` with `vendor_name: "lightdash"`; on export the extension data is overlaid onto the generated definition (structural keys — `sql`/`label` on dimensions, `sql`/`description` on metrics — are protected and cannot be overridden) |

Expressions are written under the `ANSI_SQL` dialect. Warehouse-specific
dialects (e.g. `BIGQUERY`) can be added once the surrounding tooling resolves
them.

## Recommended source shape for dbt-native flows

If the Ossie documents are also consumed by dbt's native OSI parsing, prefer
importing **without** `--database` (i.e. `schema.table` sources): the database
is usually environment-dependent in dbt projects, and a database-less source
keeps one document valid across environments (see
[dbt-core#15649](https://github.com/dbt-labs/dbt-core/issues/15649)).
Omitting `--schema` as well is reported as a `SOURCE_UNQUALIFIED` issue.

## Known limitations

- **Cross-dataset metrics are dropped on export** (with a
  `CROSS_DATASET_METRIC_DROPPED` issue): a Lightdash model metric cannot
  reference other tables.
- **Percentile metrics** keep `type` / `percentile` in the `lightdash`
  extension (Ossie expressions cannot express them faithfully) and re-export
  as model-level metrics.
- **`primary_key` / `unique_keys` are not exported** — Lightdash has no
  corresponding concept — and consequently cannot be reconstructed on import.
- **`dataset.name` is not preserved when it differs from the source table
  name**: the dbt model is named after the table part of `source`, and the
  import direction derives dataset names from model names. References inside
  expressions and relationships are rewritten consistently, but a
  name-stable round-trip is not guaranteed.
- **Relationships with mismatched `from_columns` / `to_columns` lengths are
  skipped on export** with a `RELATIONSHIP_COLUMNS_MISMATCHED` issue.
- **`ai_context` is not carried** into Lightdash meta.
- **Model-level Lightdash meta beyond `metrics` and `joins`** (`label`,
  `group_details`, `sql_filter`, `order_fields_by`, column
  `additional_dimensions`, ...) is not carried yet.
- **Standalone Lightdash YAML projects** (Lightdash without dbt) are not
  supported yet; the converter targets the dbt-meta flavour.
- Custom extensions from other vendors are ignored on export (reported as
  `FOREIGN_EXTENSION_IGNORED`); they remain untouched in the Ossie document.
- Documents are emitted at the current in-repo spec version. Note that
  dbt-core 1.12's native OSI parsing accepts spec versions `0.1.0` / `0.1.1`
  only.
