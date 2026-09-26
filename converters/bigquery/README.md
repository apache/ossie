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

# Ossie to LookML (BigQuery) Converter

Converts Apache Ossie YAML semantic models to [LookML](https://cloud.google.com/looker/docs/what-is-lookml), the semantic modeling language for Looker — Google Cloud's semantic layer over BigQuery. Pure offline conversion — no BigQuery or Looker connection required.

> **Note:** This converter is under active development. It handles common cases but has not been thoroughly tested against all edge cases — use with caution in production.

## Setup

```bash
uv sync
```

## Usage

```bash
uv run ossie-bigquery -i input.yaml -o output.lkml
```

## Tests

```bash
uv run pytest
```

## Mapping

| Ossie construct | LookML output |
|-----------------|---------------|
| `semantic_model` | A single LookML document with a descriptive header |
| `dataset` | `view` with `sql_table_name` (3-part sources are backtick-quoted as `` `project.dataset.table` ``; subquery sources become a `derived_table`) |
| `field` | `dimension` |
| `field` with `dimension.is_time: true` | `dimension_group` with `type: time` and standard timeframes |
| `metric` | `measure` (`type: number`) |
| `relationship` | `explore` on the `from` dataset with a `join` (`relationship: many_to_one`, `left_outer`); composite keys produce an `AND`-joined `sql_on` |

## Dialect selection

For each field and metric the converter prefers the `BIGQUERY` dialect expression and falls back to `ANSI_SQL`, consistent with the dialect-selection contract in [`../README.md`](../README.md). If neither is present, the field/metric is skipped with a warning.

The `BIGQUERY` dialect value requires the dialect-enum addition tracked in the companion "Add BIGQUERY to the Dialect enum" change. The converter itself runs on `ANSI_SQL`-only models with no dependency on that change — see the canonical [TPC-DS example](../../examples/tpcds_semantic_model.yaml), which the test suite converts as-is.

## Limitations

- **Data types**: Ossie fields carry no column data type, so dimensions are emitted as `type: string`. Numeric and key fields may need type refinement after generation.
- **Measures**: Ossie metric expressions do not encode an aggregate type, so measures are emitted as `type: number` with the expression supplied verbatim. Model-level metrics are emitted into the first view; cross-dataset measures may need manual placement within an `explore`.
- **Computed expressions**: Bare column references are qualified as `${TABLE}.column`. Computed expressions are emitted verbatim with a warning so column qualification can be confirmed.
- **Dropped constructs**: `custom_extensions`, `label`, and relationship-level `ai_context` have no direct LookML counterpart and are dropped with warnings.
