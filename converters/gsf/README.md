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

# Apache Ossie ↔ NVIDIA GSF Converter

Offline conversion between Apache Ossie YAML and the standalone semantic-model
YAML format supported by [NVIDIA GSF](https://github.com/NVIDIA/GSF). No GSF,
Neo4j, database, or network connection is required.

## Mapping

| Apache Ossie | Standalone GSF YAML |
|---|---|
| Semantic model | `model` |
| Dataset | `terms[]` |
| Physical field | `column_attributes[]` |
| Computed field | `sql_attributes[]` with `kind: field` |
| Metric | `sql_attributes[]` with `kind: metric` |
| Relationship | `semantic_foreign_keys[]` |
| Expression dialects | `expressions[]` |
| Dataset source | Term `source` mapping |

Computed fields and metrics include generated full SQL and explicit
`table_refs`, which lets GSF validate and attach them to its ingested catalog.

## Setup

```bash
cd converters/gsf
uv sync
```

## Ossie → GSF

```bash
uv run ossie-gsf export \
  --input ../../examples/tpcds_semantic_model.yaml \
  --output tpcds.gsf.yaml \
  --database-name tpcds
```

`--database-name` supplies the database for sources written as `schema.table`.
Fully qualified `database.schema.table` sources do not require it.

Python:

```python
from ossie_gsf import convert_ossie_to_gsf

gsf_yaml = convert_ossie_to_gsf(
    ossie_yaml,
    database_name="tpcds",
)
```

## GSF → Ossie

```bash
uv run ossie-gsf import \
  --input tpcds.gsf.yaml \
  --output semantic_model.yaml
```

Use `--name` to override the exported Ossie semantic-model name.

Python:

```python
from ossie_gsf import convert_gsf_to_ossie

ossie_yaml = convert_gsf_to_ossie(gsf_yaml)
```

## Loading the converted model into GSF

Run the native GSF importer from the GSF repository:

```bash
uv run python -m gsf.semantic import \
  --database-name tpcds \
  --input tpcds.gsf.yaml
```

GSF resolves the file against its existing catalog, writes the graph
transactionally, validates SQL, and refreshes semantic embeddings.

## Conversion behavior and limitations

- Apache Ossie `0.2.0.dev0` and GSF model-file `1.0` are supported.
- One semantic model is converted per document.
- GSF term sources must resolve to physical `database.schema.table` names.
- Simple field expressions become `ColumnAttribute` mappings. Other field
  expressions become SQL attributes.
- Multi-dataset metric SQL is joined through declared Ossie relationships.
  Disconnected datasets fail conversion instead of producing a cross join.
- GSF SQL attribute names are global, so duplicate computed-field or metric
  names fail conversion.
- Native GSF SQL attributes with `kind: attribute` have no unambiguous Ossie
  field/metric equivalent and fail GSF → Ossie conversion.
- Metrics are attached to the first referenced dataset. Unqualified metrics in
  multi-dataset models need a `GSF` custom extension containing
  `{"term": "dataset_name"}`.
- Expression dialect variants are preserved in the standalone GSF file.
- Ossie custom extensions and GSF metadata are preserved through metadata and
  `GSF` custom-extension payloads.

## Tests

```bash
uv run pytest
```

The suite includes a checked-in Ossie/GSF fixture pair, bidirectional
round-trip checks, validation failures, CLI coverage, and verification of
generated Ossie YAML with the repository's official validator.
