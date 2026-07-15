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

> **Output format:** This converter produces the **legacy** Cortex Analyst semantic model YAML — the stage-based format. It is *not* the newer Snowflake [semantic view YAML](https://docs.snowflake.com/en/user-guide/views-semantic/semantic-view-yaml-spec) (`views-semantic`) that pairs with the `CREATE SEMANTIC VIEW` DDL. The two structures overlap heavily but are distinct Snowflake surfaces. See [Ossie YAML and Snowflake semantic views](#ossie-yaml-and-snowflake-semantic-views) below for how to get to an actual semantic view.

> **Note:** This converter is under active development. It handles common cases but has not been thoroughly tested against all edge cases — use with caution in production.

## Setup

```bash
pip3 install -r requirements.txt
```

## Usage

```bash
python3 src/osi_to_snowflake_yaml_converter.py -i input.yaml -o output.yaml
```

## Tests

```bash
python3 -m pytest tests/
```

## Limitations

Some Ossie concepts (e.g., `ai_context` on relationships) do not have a native counterpart in the Snowflake semantic model. These are dropped during conversion and the converter will emit warnings so you know what was left behind.

## Ossie YAML and Snowflake semantic views

A dedicated open-source Ossie ↔ semantic-view converter would be a welcome future addition. In the meantime, Snowflake can already read and write Ossie YAML natively, so the round-trip works today:

- [`SYSTEM$CREATE_SEMANTIC_VIEW_FROM_OSI_YAML`](https://docs.snowflake.com/en/sql-reference/stored-procedures/system_create_semantic_view_from_osi_yaml) — create a semantic view directly from Ossie YAML (Ossie YAML → semantic view).
- [`SYSTEM$READ_OSI_YAML_FROM_SEMANTIC_VIEW`](https://docs.snowflake.com/en/sql-reference/functions/system_read_osi_yaml_from_semantic_view) — export an existing semantic view to Ossie YAML (semantic view → Ossie YAML).
