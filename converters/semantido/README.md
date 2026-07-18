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

# semantido ⇄ Apache Ossie converter

Converts between [semantido](https://github.com/hikarilabs/semantido) — a
code-native semantic layer authored as decorators on SQLAlchemy models and
Apache Ossie semantic model documents.

Unlike file-to-file converters, semantido's source of truth is live Python:
the forward direction imports a module of decorated models, syncs the
semantic layer, and emits Ossie YAML through the typed `apache-ossie`
objects. The reverse direction is code generation: it produces a Python
module of `@semantic_table`-decorated models from an Ossie document.

## Usage

```bash
# decorated SQLAlchemy models -> Ossie YAML
ossie-semantido semantido-to-osi \
    -m models.[SQLAlchemy model] -p ./my_project \
    -n [model name] -o [model].osi.yaml

# Ossie YAML -> generated semantido model code
ossie-semantido osi-to-semantido \
    -i [model].osi.yaml -o generated_model_[model name].py
```

## Mapping

| semantido                                 | Ossie                             | Notes                                                |
|-------------------------------------------|-----------------------------------|------------------------------------------------------|
| decorated table                           | `datasets[]`                      | `source` from schema-qualified table name            |
| column                                    | `fields[]`                        | expression emitted as `ANSI_SQL` column reference    |
| `description` / `<col>_description`       | `description`                     |                                                      |
| `business_context`, `application_context` | dataset `ai_context.instructions` |                                                      |
| `synonyms` / `<col>_synonyms`             | `ai_context.synonyms`             |                                                      |
| `<col>_sample_values`                     | field `ai_context.examples`       |                                                      |
| `time_dimension=` + `<col>_time_grain`    | field `dimension.is_time`         | grain preserved in extensions                        |
| FK relationships                          | `relationships[]`                 | join condition parsed to `from_columns`/`to_columns` |
| `sql_filters`                             | dataset `custom_extensions`       | no Ossie core field (see below)                      |
| `<col>_privacy_level`                     | field `custom_extensions`         | no Ossie core field (see below)                      |

### Metadata carried in `custom_extensions`

semantido captures runtime-governance metadata that has no core-spec home
in Ossie today: per-field privacy classification, default SQL filters /
row-level security fragments, and time-dimension grain. These are preserved
losslessly under `vendor_name: SEMANTIDO` with `data` as a serialized JSON
string, so no information is lost in interchange — but tools without
semantido awareness will not interpret them.

### Known lossy conversions

Conversions report structured `ConverterIssue`s (mirroring `ossie_dbt`):
Ossie metrics and non-ANSI dialect expressions have no semantido
equivalent and are dropped with a recorded issue; generated code lists
them in a TODO block for review.

## Tests

The test fixture is an EMIR (European Market Infrastructure Regulation)
trade-reporting model — a regulated-industry schema whose semantics
(role-bridge fan-out, signed vs. unsigned amount conventions,
notional/valuation ambiguity) demonstrate why interchange must carry
business meaning, not just structure.

```bash
uv run pytest tests/
```
