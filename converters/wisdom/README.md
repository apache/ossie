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

# WisdomAI ↔ Apache Ossie Converter

Converts between a [WisdomAI](https://wisdom.ai) domain export (format `1.0`, produced by
wisdom's `exportDomain` API and consumed by `importDomain`) and an Ossie semantic model
YAML document.

## Usage

```bash
pip install -e ../../python -e .
ossie-wisdom wisdom-to-ossie -i domain-export.json -o semantic_model.yaml
ossie-wisdom ossie-to-wisdom -i semantic_model.yaml -o domain-export.json
```

Conversion warnings (information loss) are printed to stderr; the output YAML validates
against the [Ossie JSON Schema](../../core-spec/ossie-schema.json):

```bash
python ../../validation/validate.py semantic_model.yaml --schema ../../core-spec/ossie-schema.json
```

## Field mapping

| Ossie | Wisdom |
|-------|--------|
| `name` | domain `ref.name` |
| `description` | domain `description` |
| `ai_context` | `domainSystemInstructions` + each domain `knowledge[].content` as a bulleted list |
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
(`snowflake → SNOWFLAKE`, `databricks → DATABRICKS`, `bigquery → BIGQUERY`). Connections with
any other dialect fall back to `ANSI_SQL` verbatim with an `UNSUPPORTED_DIALECT` warning.

### Relationship cardinality

Ossie encodes cardinality by direction (`from` = many side, `to` = one side), so wisdom's
`relationshipType` is folded into the edge direction:

| Wisdom `relationshipType` | Ossie |
|---------------------------|-------|
| `MANY_TO_ONE` | `from` = left, `to` = right |
| `ONE_TO_MANY` | `from` = right, `to` = left |
| `ONE_TO_ONE` | `from` = left, plus the note `one-to-one relationship` (direction is arbitrary) |
| `MANY_TO_MANY` | `from` = left, plus the note `many-to-many relationship; cardinality is not representable in Ossie` and a `CARDINALITY_LOSS` warning |

Every relationship that is not `MANY_TO_ONE` also gets a `WISDOM` entry in its
`custom_extensions` recording the original type and the `from`/`to` datasets it was
written with, so the export direction can restore the type:

```yaml
custom_extensions:
  - vendor_name: WISDOM
    data: '{"_v": 1, "normalizedEndpoints": ["orders", "customers"], "relationshipType": "ONE_TO_MANY"}'
```

`_v` is the payload's format version. An entry with a version this converter does not
understand is treated as malformed (see below).

The `ONE_TO_ONE` and `MANY_TO_MANY` notes in `ai_context` are for readers only; the
export does not depend on them. `ONE_TO_MANY` gets no note: once flipped, the relationship
is many-to-one as Ossie reads it, and a one-to-many note would contradict that.

Compound join conditions that are an `AND` of equality conditions are flattened into
positional `from_columns`/`to_columns` arrays; any other compound condition (e.g. `OR`)
drops the relationship with a `RELATIONSHIP_DROPPED` warning.

## Ossie → wisdom (export direction)

`ossie-to-wisdom` emits a domain export mirroring wisdom's `exportDomain` JSON, inverting
the mapping above so that wisdom → Ossie → wisdom and Ossie → wisdom → Ossie round-trips
are stable:

- Model `ai_context` splits back into `domainSystemInstructions` (leading text) and one
  knowledge item per `- ` bullet.
- A field whose expression is just its own (possibly quoted) name becomes a column;
  anything else becomes a formula. `dimension.is_time` becomes a `TIMESTAMP` data type
  (wisdom re-derives exact types from the warehouse).
- Relationships become `MANY_TO_ONE` edges (Ossie's `from` is the many side). A relationship
  with a `WISDOM` custom extension written by `wisdom-to-ossie` gets its type back from it:
  `ONE_TO_ONE`, `MANY_TO_MANY`, or `ONE_TO_MANY` with the two sides (and their columns)
  swapped back. Reversing `from`/`to` after import keeps the type, since the relationship
  means the same thing either way. If a recorded dataset is no longer one of the
  relationship's `from`/`to`, or the entry is malformed, the edge stays `MANY_TO_ONE`
  with a `RELATIONSHIP_TYPE_UNRESOLVED` warning. The entries read this way are not reported
  as `CUSTOM_EXTENSION_DROPPED`. Composite keys become compound `AND` join conditions.
- Documents written before the `WISDOM` extension carry the type only as an `ai_context`
  note. When a relationship has no `WISDOM` entry, an `ai_context` that is exactly one of
  the notes (ignoring leading and trailing whitespace), including the older
  `one-to-many relationship`, still restores the type. A note matching the exported type
  is carried by it; any other `ai_context` on a relationship, including a hand-edited
  note, is dropped with an `AI_CONTEXT_DROPPED` warning.
- Metrics attach to the first dataset their expression references (a
  `METRIC_TABLE_UNRESOLVED` warning falls back to the first dataset).
- Connections are per-dialect placeholders (`et-connection-snowflake`, ...) expected to be
  remapped when the domain is imported; all IDs are derived deterministically from names,
  so re-runs produce identical output.
- Not representable in wisdom (dropped with warnings): extra semantic models beyond the
  first, `unique_keys`, `custom_extensions` (other than the relationship entries above), and
  `ai_context` on fields/metrics (plus synonyms/examples anywhere).

## Known limitations

- Hidden columns and stale measures are converted anyway (with a `STALE_MEASURE` warning
  for the latter), so the output may reference columns wisdom itself hides from querying.
- Out of scope for now: reviewed queries, recommended questions, synonym sets, row-level
  security config, per-knowledge schema annotations, column enum values, and LLM prompts.

## Development

```bash
pip install -e ../../python -e . pytest
pytest tests/
```
