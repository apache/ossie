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

Offline conversion between Apache Ossie YAML and NVIDIA GSF's native
`GsfModelDocument` YAML contract. Conversion itself does not require GSF,
Neo4j, a database, or network access.

## Mapping

| Apache Ossie | Native GSF model document |
|---|---|
| Dataset source | `data_layer.databases[].schemas[].tables[]` |
| Dataset field backed by one column | `semantic_layer.terms[].columns_attributes[]` |
| Computed dataset field | `semantic_layer.sql_attributes.manual[]` |
| Model-level metric | `semantic_layer.custom_analyses[]` |
| Relationship | data-layer `joins` and `foreign_keys`, plus `semantic_fks` when possible |
| Dataset | term that `represents` exactly one catalog table |

The generated root contains exactly `data_layer`, `semantic_layer`, and
`zones`. It does not contain a converter-specific version or model envelope.
Catalog columns are collected from fields, primary and unique keys,
relationships, and SQL column references. Stable UUIDv5 identifiers make
repeated Ossie exports deterministic.

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

`--database-name` supplies the database for `schema.table` sources. Fully
qualified `database.schema.table` sources do not require it. One document may
contain multiple databases.

```python
from ossie_gsf import convert_ossie_to_gsf

gsf_yaml = convert_ossie_to_gsf(ossie_yaml, database_name="tpcds")
```

## GSF → Ossie

```bash
uv run ossie-gsf import \
  --input tpcds.gsf.yaml \
  --output semantic_model.yaml \
  --name tpcds
```

`--name` overrides the Ossie model name. Without it, the converter uses the
single catalog database name when there is one, otherwise `gsf_model`.

```python
from ossie_gsf import convert_gsf_to_ossie

ossie_yaml = convert_gsf_to_ossie(gsf_yaml, model_name="tpcds")
```

This converter subset currently accepts only GSF terms that represent exactly
one table, because one Ossie dataset cannot represent several physical tables.
Several terms may represent the same table and become distinct Ossie datasets
sharing one source. SQL attributes become Ossie fields regardless of their GSF
source group. Custom analyses remain global by becoming model-level Ossie
metrics. Relationships are recovered from joins, then physical foreign keys,
then semantic foreign keys.

## Importing the model into GSF

Start GSF, then send the native document to its REST API:

```bash
curl --fail-with-body \
  -X POST \
  'http://127.0.0.1:3001/api/model/import?replace=true&embed=true' \
  -H 'Content-Type: application/x-yaml' \
  --data-binary @tpcds.gsf.yaml
```

The endpoint also accepts a multipart upload in a `file` field.

The target GSF instance must already have a connection configured for each
database named in the document. GSF validates every imported SQL attribute
against that connection's dialect, so importing into an instance with no
matching connection fails. A database's `dialect` is likewise derived from the
live connection rather than stored on import, so it is exported for information
only and does not survive a GSF → GSF cycle.

## Fidelity and unavoidable losses

When converting GSF to Ossie, the converter records the native document in an
`NVIDIA_GSF` custom extension. A direct GSF → Ossie → GSF cycle can therefore
reuse live identifiers and preserve catalog properties, SQL source groups,
SQL text, `sql_column_is`, relationships, and zones. Ossie-origin entities use
deterministic IDs when no preserved native ID is available. Current Ossie
expressions and relationships remain authoritative: preserved SQL and native
relationship records are reused only when they still correspond to the Ossie
entities or are outside the represented Ossie catalog scope.

The GSF contract has no semantic-model envelope, `ai_context`, dimensions,
synonyms, Ossie custom-extension storage, or expression-dialect variants.
Those values cannot be represented in a native GSF document and are
unavoidably lost on Ossie → GSF. GSF joins also have no relationship name, so
GSF → Ossie synthesizes a stable `<from>_to_<to>` name. The converter never
adds fictional fields to the GSF schema. GSF records uniqueness per column, so
Ossie composite unique keys cannot be reconstructed after GSF → Ossie; only
single-column unique keys survive.

## Tests

```bash
uv run pytest
```

The suite checks the exact native root shape, deterministic and resolvable
IDs, official Ossie validation, semantic round trips, native metadata
preservation, multiple databases, relationships, input validation, and CLI
behavior.
