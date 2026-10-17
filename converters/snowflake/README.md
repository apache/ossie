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

# Apache Ossie ↔ Snowflake Converter

Converts between Ossie YAML semantic models and [Snowflake Cortex Analyst](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst) semantic model YAML, in both directions. Pure offline conversion — no Snowflake connection required.

> **Note:** This converter is under active development. It handles common cases but has not been thoroughly tested against all edge cases — use with caution in production.

Ossie documents contain one model directly at the root, with `version`, `name`,
`datasets`, and optional model properties. Legacy `semantic_model` wrappers
(arrays or objects) are rejected.

## Setup

```bash
uv sync
```

## Usage

```bash
# Snowflake Cortex Analyst YAML -> Ossie YAML
uv run ossie-snowflake snowflake-to-ossie -i cortex_analyst.yaml -o semantic_model.yaml

# Ossie YAML -> Snowflake Cortex Analyst YAML
uv run ossie-snowflake ossie-to-snowflake -i semantic_model.yaml -o cortex_analyst.yaml
```

The pre-subcommand spelling stays valid and means `ossie-to-snowflake`:

```bash
uv run ossie-snowflake -i semantic_model.yaml -o cortex_analyst.yaml
```

## Data Type Mapping

The exporter maps the optional logical `datatype` on Ossie fields to Snowflake
`data_type` as follows, and the importer reads the same table in reverse:

| Ossie | Snowflake |
|---|---|
| `String` | `VARCHAR` |
| `Integer` | `NUMBER(38,0)` |
| `Decimal` | `NUMBER` |
| `Float` | `FLOAT` |
| `Boolean` | `BOOLEAN` |
| `Date` | `DATE` |
| `Time` | `TIME` |
| `DateTime` | `TIMESTAMP_NTZ` |
| `DateTimeTz` | `TIMESTAMP_TZ` |

An omitted datatype remains unspecified. `Opaque` has no portable Snowflake
mapping, so the exporter omits `data_type` and emits a warning.

On import, a Snowflake type with no Ossie counterpart (`VARIANT`, `OBJECT`,
`ARRAY`, `MAP`, `BINARY`, `VARBINARY`, `GEOGRAPHY`, `GEOMETRY`, `VECTOR`,
`FILE`) becomes `Opaque`, and the concrete Snowflake type is recorded in the
field's `SNOWFLAKE` `custom_extensions` entry so the mapping stays reversible.

## Import Mapping

Snowflake properties fall into three groups on the way in:

| Snowflake | Ossie |
|---|---|
| `tables[].base_table` | `datasets[].source` (dotted identifier, or the `definition` subquery as written) |
| `tables[].dimensions` / `time_dimensions` | `fields[]` with a `dimension` block |
| `tables[].facts` | `fields[]` with no `dimension` block |
| `tables[].metrics` | lifted to model-level `metrics[]` |
| `synonyms`, `custom_instructions` | `ai_context` |
| a column's `unique` flag | the dataset's `unique_keys` |
| `relationships[].left_table` / `right_table` | `relationships[].from` / `to` |
| `sample_values`, `cortex_search_service`, `is_enum`, `filters`, `verified_queries`, `module_custom_instructions` | `custom_extensions` (vendor `SNOWFLAKE`) |
| `tags`, `labels`, `access_modifier`, a relationship's `join_type` / `relationship_type` | dropped with a warning |

Expressions that are provably standard SQL are labelled `ANSI_SQL`; anything
this converter cannot vouch for keeps the `SNOWFLAKE` label and warns. The
distinction matters because every exporter reads its own dialect, then
`ANSI_SQL`, then `OSSIE_SQL_2026`, and drops any field labelled with none of
them — so a `SNOWFLAKE`-only label removes the field from every other spoke.

## Tests

```bash
uv run pytest
```

## Limitations

Ossie has model-level metrics only, so a Snowflake table's own metrics are
lifted to the model on import. A table metric resolves against its own table, so
lifting qualifies its column references with that table -- `SUM(amount)` on
`orders` becomes `SUM(orders.amount)` -- and the rest of the expression is kept
verbatim. Two tables may each scope a metric under the same name; the model
cannot, so the importer warns when lifting creates a collision.

Some Ossie concepts (e.g., `ai_context` on relationships) do not have a native counterpart in the Snowflake semantic model. These are dropped during conversion and the converter will emit warnings so you know what was left behind.

Snowflake metric result types are inferred from their expressions, so Ossie
metric `datatype` values are not emitted as `data_type` properties.
