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

# Apache Ossie to Snowflake Converter

Converts Ossie YAML semantic models to [Snowflake Cortex Analyst](https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst) semantic model YAML. Pure offline conversion — no Snowflake connection required.

> **Note:** This converter is under active development. It handles common cases but has not been thoroughly tested against all edge cases — use with caution in production.

## Setup

```bash
uv sync
```

## Usage

```bash
uv run ossie-snowflake -i input.yaml -o output.yaml
```

## Data Type Mapping

The exporter maps the optional logical `datatype` on Ossie fields to Snowflake
`data_type` as follows:

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

## Tests

```bash
uv run pytest
```

## Limitations

Some Ossie concepts (e.g., `ai_context` on relationships) do not have a native counterpart in the Snowflake semantic model. These are dropped during conversion and the converter will emit warnings so you know what was left behind.

Snowflake metric result types are inferred from their expressions, so Ossie
metric `datatype` values are not emitted as `data_type` properties.
