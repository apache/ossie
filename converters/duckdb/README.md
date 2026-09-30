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

# Apache Ossie ↔ DuckDB Converter

Bidirectional converter between DuckDB databases and the Apache Ossie
semantic model.

- **Export** (Ossie → DuckDB): renders a semantic model as a DuckDB SQL
  script — one view per dataset, one `metric_<name>` view per metric (with
  joins derived from the declared relationships), and `COMMENT ON`
  statements carrying the descriptions into the DuckDB catalog.
- **Import** (DuckDB → Ossie): reads a database's tables, views, columns,
  constraints, and comments and emits a valid Ossie YAML document.

## Usage

```bash
# Ossie model -> DuckDB SQL script
ossie-duckdb export -i model.yaml -o model.sql
ossie-duckdb export -i model.yaml --view-schema semantic   # views in their own schema

# DuckDB database -> Ossie model
ossie-duckdb import -i analytics.duckdb -o model.yaml
ossie-duckdb import -i md:my_database --schema gold --name gold_model
```

Or from Python:

```python
from ossie_duckdb import convert_osi_to_duckdb, convert_duckdb_to_osi_yaml

sql = convert_osi_to_duckdb(open("model.yaml").read(), view_schema="semantic")

import duckdb
conn = duckdb.connect("analytics.duckdb")
osi_yaml = convert_duckdb_to_osi_yaml(conn, schema="main")
```

## Mapping

| Ossie construct | DuckDB construct |
|-----------------|------------------|
| Dataset | `CREATE OR REPLACE VIEW <name>` over the dataset `source` |
| Field | View column (`<expression> AS <name>`) |
| Field / dataset / metric `description` | `COMMENT ON COLUMN` / `COMMENT ON VIEW` |
| Metric | `CREATE OR REPLACE VIEW metric_<name>`, joins derived from relationships |
| Relationship | `LEFT JOIN` in metric views (export); `FOREIGN KEY` constraint (import) |
| `primary_key` / `unique_keys` | `PRIMARY KEY` / `UNIQUE` constraints (import) |

Expression dialect selection on export prefers `DUCKDB`, falls back to
`ANSI_SQL` with a warning, and fails when neither is present.

## MotherDuck

The import side accepts anything `duckdb.connect` accepts, including
MotherDuck `md:` connection strings and already-open connections, so
hosted databases work the same way local files do.

## Limitations

- Metric dataset references are detected as unquoted `dataset.field`
  identifiers; quoted dataset names in metric expressions are not resolved.
- Metric joins require the referenced datasets to be connected through the
  model's declared relationships, and the join columns must be exposed as
  fields on datasets that declare an explicit field list.
- Join direction is `LEFT JOIN` from the many (`from`) side; Ossie does not
  yet declare cardinality or join type, so other join semantics are not
  representable.
- `ai_context` and `custom_extensions` have no DuckDB catalog equivalent and
  are not carried into the SQL output.
- Import emits datasets, fields, keys, and relationships; it does not infer
  metrics.

## Development

```bash
cd converters/duckdb
uv sync
uv run pytest
uv run ruff check
```
