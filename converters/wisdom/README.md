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

# WisdomAI → Apache Ossie Converter

Converts a [WisdomAI](https://wisdom.ai) domain export (format `1.0`, produced by wisdom's
`exportDomain` API) into an Ossie semantic model YAML document.

## Usage

```bash
pip install -e ../../python -e .
ossie-wisdom wisdom-to-osi -i domain-export.json -o semantic_model.yaml
```

Conversion warnings (information loss) are printed to stderr; the output YAML validates
against the [Ossie JSON Schema](../../core-spec/osi-schema.json):

```bash
python ../../validation/validate.py semantic_model.yaml --schema ../../core-spec/osi-schema.json
```

## Field mapping

| Ossie | Wisdom |
|-------|--------|
| `semantic_model[].name` | domain `ref.name` |
| `semantic_model[].description` | domain `description` |
| `semantic_model[].ai_context` | `domainSystemInstructions` + each domain `knowledge[].content` as a bulleted list |
| `datasets[].name` | table `ref.name` |
| `datasets[].source` | table `location.database.schema.dbTable` |
| `datasets[].description` | table `description` |
| `datasets[].primary_key` | table `primaryKey.columns`, else columns flagged `isPrimaryKey` |
| `datasets[].fields[]` | table `columns[]` (expression = bare column name) and `formulas[]` (expression verbatim) |
| `fields[].label` | column/formula `properties.displayName` |
| `fields[].dimension.is_time` | set when `properties.dataType` is `DATE`, `DATETIME`, or `TIMESTAMP` |
| `relationships[]` | domain `relationshipGraph.relationships[]` (see cardinality below) |
| `metrics[]` | every table's `measures[]`, hoisted to the model level |

Expressions are emitted verbatim under the Ossie dialect mapped from the table's connection
(`snowflake → SNOWFLAKE`, `databricks → DATABRICKS`). Connections with any other dialect fall
back to `ANSI_SQL` verbatim with an `UNSUPPORTED_DIALECT` warning.

### Relationship cardinality

Ossie encodes cardinality by direction (`from` = many side, `to` = one side), so wisdom's
`relationshipType` is folded into the edge direction:

| Wisdom `relationshipType` | Ossie |
|---------------------------|-------|
| `MANY_TO_ONE` | `from` = left, `to` = right |
| `ONE_TO_MANY` | `from` = right, `to` = left |
| `ONE_TO_ONE` | `from` = left, plus an `ai_context` note (direction is arbitrary) |
| `MANY_TO_MANY` | `from` = left, plus an `ai_context` note and a `CARDINALITY_LOSS` warning |

Compound join conditions that are an `AND` of equality conditions are flattened into
positional `from_columns`/`to_columns` arrays; any other compound condition (e.g. `OR`)
drops the relationship with a `RELATIONSHIP_DROPPED` warning.

## Known limitations

- One-way only (wisdom → Ossie). No Ossie → wisdom export yet.
- Hidden columns and stale measures are converted anyway (with a `STALE_MEASURE` warning
  for the latter), so the output may reference columns wisdom itself hides from querying.
- Out of scope for now: reviewed queries, recommended questions, synonym sets, row-level
  security config, per-knowledge schema annotations, column enum values, and LLM prompts.

## Development

```bash
pip install -e ../../python -e . pytest
pytest tests/
```
