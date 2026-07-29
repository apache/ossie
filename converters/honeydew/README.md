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

# Ossie ↔ Honeydew Converter

Bidirectional converter between [Ossie](../../core-spec/spec.md) semantic models and [Honeydew](https://honeydew.ai/docs) workspace YAML.

## Overview

| Direction | Input | Output |
|-----------|-------|--------|
| `ossie-to-honeydew` | Single Ossie YAML file | Honeydew workspace directory |
| `honeydew-to-ossie` | Honeydew workspace directory | Single Ossie YAML file |

### Ossie → Honeydew mapping

| Ossie concept | Honeydew concept |
|-------------|-----------------|
| `semantic_model.name` | `workspace.yml name` |
| `dataset` | Entity + dataset files under `schema/<entity>/` |
| `dataset.source` | `dataset.sql` |
| `dataset.primary_key` | `entity.keys` |
| Simple column field | Source Attribute (`dataset.attributes` entry) |
| Computed field expression | Calculated Attribute (`calculated_attribute` YAML) |
| `field.ai_context` | AI Metadata on the attribute or entity |
| `relationship` (from → to) | `entity.relations` on the "from" entity (`rel_type: many-to-one`) |
| `metric` | `metric` YAML (assigned to entity by expression parse) |

### Honeydew → Ossie mapping

| Honeydew concept | Ossie concept |
|-----------------|-------------|
| `workspace.name` | `semantic_model.name` |
| Entity + primary dataset | `dataset` |
| `entity.keys` | `dataset.primary_key` (and `dataset.unique_keys`) |
| `dataset.attributes` (columns) | `fields` with `ANSI_SQL` expression = column name |
| `calculated_attribute` SQL | `fields` with `ANSI_SQL` expression + `HONEYDEW` custom extension |
| `entity.relations` (`many-to-one`) | `relationship` with `from` = this entity |
| `entity.relations` (`one-to-many`) | `relationship` with `from` = target entity |
| `metric.sql` | `metric` expression in `ANSI_SQL` dialect |

## Requirements

- Python 3.12+
- PyYAML 6.0+

## Setup

```bash
uv sync
```

## Usage

```bash
# Ossie YAML → Honeydew workspace directory
uv run honeydew-ossie ossie-to-honeydew -i input.yaml -o output_dir/

# Honeydew workspace directory → Ossie YAML
uv run honeydew-ossie honeydew-to-ossie -i workspace_dir/ -o output.yaml
```

## Tests

```bash
uv run pytest
```

## Limitations

- **One source dataset per entity**: Honeydew entities can have multiple source dataset files; the converter always generates exactly one, because an Ossie `dataset` block describes a single table or SQL query.
- **Datatype inference**: Ossie fields have no explicit datatype; the converter infers Honeydew datatypes from the `dimension.is_time` flag (`timestamp`) and the presence/absence of the `dimension` key (`string` vs `number`).
- **Honeydew SQL expressions**: Calculated attributes and metrics use Honeydew's `entity.attribute` reference syntax. These are exported as `ANSI_SQL` dialect expressions in Ossie; they remain valid for round-tripping but may not run on other databases without adaptation.
- **Perspectives and domains**: Not converted (no Ossie equivalent).
- **Connection expressions** (`connection_expr`): Preserved in `HONEYDEW` custom extensions on the Ossie relationship and restored on the return trip.
- **`ai_context`**: Ossie `ai_context` fields (synonyms, instructions) are stored in Honeydew `metadata` for round-trip recovery. Instructions are also merged into `description` for human readability.
- **`unique_keys`**: A Honeydew entity's `keys` uniquely identify its rows — Honeydew enforces this and validates that relations join to those keys — so they are emitted as the Ossie dataset's `primary_key` *and* a `unique_keys` entry. This surfaces the join-target cardinality for Ossie consumers (e.g. a many-to-one relation's `to_columns` are always covered by the target's `unique_keys`). A unique key identical to the primary key is not stored back in Honeydew `metadata` on the return trip, so `Honeydew → Ossie → Honeydew` stays clean; `Ossie → Honeydew → Ossie` normalizes by surfacing the primary key as a unique key.
