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

# Apache Ossie ↔ NVIDIA Auto Ontology Converter

Offline conversion between Apache Ossie YAML and NVIDIA Auto Ontology's native
`AutoOntologyModelDocument` YAML contract. Conversion itself does not require
Auto Ontology, a database, or network access.

Auto Ontology was previously named Generative Semantic Fabric (GSF). Ossie
files written under the old name carry an `NVIDIA_GSF` custom extension; the
converter still reads it, and writes `NVIDIA_AUTO_ONTOLOGY` from now on.

Ossie input and output use one model per document, with `name`, `datasets`,
`relationships`, and `metrics` directly at the root beside `version`. Migrate
legacy `semantic_model` wrappers before conversion; see the
[format migration guidance](../../core-spec/spec.md#migrating-earlier-document-shapes).

## Mapping

| Apache Ossie | Native Auto Ontology model document |
|---|---|
| Dataset source | `data_layer.databases[].schemas[].tables[]` |
| Dataset field backed by one column | `semantic_layer.terms[].columns_attributes[]` |
| Computed dataset field | `semantic_layer.sql_attributes.manual[]` |
| Model-level metric | `semantic_layer.custom_analyses[]` |
| Field `datatype` | physical `type` on the catalog column behind the field |
| Relationship | data-layer `joins` and `foreign_keys`, plus `semantic_fks` when possible |
| Dataset | term that `represents` exactly one catalog table |

The generated root contains exactly `data_layer`, `semantic_layer`, and
`zones`. It does not contain a converter-specific version or model envelope.
Catalog columns are collected from fields, primary and unique keys,
relationships, and SQL column references. Stable UUIDv5 identifiers make
repeated Ossie exports deterministic. Any Ossie `0.2.x` version is accepted on
input, including `.dev` releases; output is written as the spec version the
converter targets.

## Setup

```bash
cd converters/nvidia
uv sync
```

## Ossie → Auto Ontology

```bash
uv run ossie-nvidia-auto-ontology export \
  --input ../../examples/tpcds_semantic_model.yaml \
  --output tpcds.auto_ontology.yaml \
  --database-name tpcds
```

`--database-name` supplies the database for `schema.table` sources. Fully
qualified `database.schema.table` sources do not require it. One document may
contain multiple databases.

```python
from ossie_nvidia_auto_ontology import convert_ossie_to_auto_ontology

auto_ontology_yaml = convert_ossie_to_auto_ontology(ossie_yaml, database_name="tpcds")
```

## Auto Ontology → Ossie

```bash
uv run ossie-nvidia-auto-ontology import \
  --input tpcds.auto_ontology.yaml \
  --output semantic_model.yaml \
  --name tpcds
```

`--name` overrides the Ossie model name. Without it, the converter uses the
single catalog database name when there is one, otherwise
`auto_ontology_model`.

```python
from ossie_nvidia_auto_ontology import convert_auto_ontology_to_ossie

ossie_yaml = convert_auto_ontology_to_ossie(auto_ontology_yaml, model_name="tpcds")
```

This converter subset currently accepts only Auto Ontology terms that represent
exactly one table, because one Ossie dataset cannot represent several physical
tables. Several terms may represent the same table and become distinct Ossie
datasets sharing one source. SQL attributes become Ossie fields regardless of
their Auto Ontology source group. Custom analyses remain global by becoming
model-level Ossie metrics. Relationships are recovered from joins, then
physical foreign keys, then semantic foreign keys.

Every column pair of an Ossie relationship must match at once, so only pairs
that form one key share a relationship. A join keeps its own columns together.
Auto Ontology stores a foreign key, physical or semantic, one column pair at a
time, without the constraint it belongs to, so the foreign keys between two
tables, and those of a join that names no columns, are grouped as follows:

- pairs covering the target's composite primary key exactly once are that key;
- pairs that reference the same target column are independent links, because
  one key never references a column twice, so each becomes its own
  relationship;
- otherwise the pairs stay together as one key, since nothing shows they are
  independent.

A semantic foreign key that repeats a pair an earlier relationship between the
same datasets already has adds nothing. A relationship that only partly
overlaps an earlier one is kept whole rather than trimmed, since dropping its
shared pairs would loosen its join.

This cannot tell every case apart. Two independent links to different target
columns of the same table are kept as one relationship, and when links that
share a target column are mixed with other pairs, all of them are split, as
which pairs would belong together is unknown.

## Importing the model into Auto Ontology

Start Auto Ontology, then send the native document to its REST API:

```bash
curl --fail-with-body \
  -X POST \
  'http://127.0.0.1:3001/api/model/import?replace=true&embed=true' \
  -H 'Content-Type: application/x-yaml' \
  --data-binary @tpcds.auto_ontology.yaml
```

The endpoint also accepts a multipart upload in a `file` field.

The target Auto Ontology instance must already have a connection configured for
each database named in the document. Auto Ontology validates every imported SQL
attribute against that connection's dialect, so importing into an instance with
no matching connection fails. A database's `dialect` is likewise derived from
the live connection rather than stored on import, so it is exported for
information only and does not survive an Auto Ontology → Auto Ontology cycle.

## Fidelity and unavoidable losses

When converting Auto Ontology to Ossie, the converter records the native
document in an `NVIDIA_AUTO_ONTOLOGY` custom extension. A direct
Auto Ontology → Ossie → Auto Ontology cycle can therefore reuse live
identifiers and preserve catalog properties, SQL source groups, SQL text,
`sql_column_is`, relationships, and zones. Ossie-origin entities use
deterministic IDs when no preserved native ID is available. Current Ossie
expressions and relationships remain authoritative: preserved SQL and native
relationship records are reused only when they still correspond to the Ossie
entities or are outside the represented Ossie catalog scope.

That extension holds the whole native document, so an Ossie file produced from
Auto Ontology carries a full copy of the Auto Ontology catalog alongside the
model derived from it. This is a deliberate trade of size for round-trip
fidelity: it is what lets an Auto Ontology → Ossie → Auto Ontology cycle keep
live identifiers, and it means the Ossie output of a large catalog is bulky and
not meant to be reviewed by hand. Converting Ossie → Auto Ontology from a
hand-written Ossie file, which has no such extension, is unaffected.

SQL is parsed with sqlglot across a list of candidate dialects rather than a
single one, because preserved Auto Ontology SQL carries whatever dialect its
connection reported. SQL that no candidate can parse is treated as opaque: it
is still carried through verbatim, and only the parse-derived enrichment
(discovering which tables and columns an expression touches) is skipped, so a
model that imported cleanly can always be exported again. On
Auto Ontology → Ossie, expressions are labelled with the source connection's
dialect when Ossie names it (`SNOWFLAKE`, `DATABRICKS`, `BIGQUERY`) and
`ANSI_SQL` otherwise.

For an Ossie-origin model there is no Auto Ontology catalog to check against,
so physical columns are synthesized from the identifiers in each expression.
Date-part keywords are excluded, but only in the unit argument of a recognized
date function, so a column genuinely named `day` or `month` is kept everywhere
else. A unit passed to a date function the converter does not recognize is
still synthesized as a column; this is inherent to deriving a catalog from SQL
text and does not apply once an Auto Ontology catalog is present, since a
catalog sourced from Auto Ontology is authoritative and never widened.

The Auto Ontology contract has no semantic-model envelope, `ai_context`,
dimensions, synonyms, Ossie custom-extension storage, or expression-dialect
variants. Those values cannot be represented in a native Auto Ontology document
and are unavoidably lost on Ossie → Auto Ontology. Auto Ontology joins also
have no relationship name, so Auto Ontology → Ossie synthesizes one from the
relationship's own columns, `<from>_<from_columns>_to_<to>`, such as
`orders_customer_id_to_customers`. It does not depend on other relationships,
so adding a link never renames an existing one. The converter never adds
fictional fields to the Auto Ontology schema. Auto Ontology records uniqueness
per column, so Ossie composite unique keys cannot be reconstructed after
Auto Ontology → Ossie; only single-column unique keys survive.

Ossie's `datatype` maps to and from the physical type on an Auto Ontology
catalog column, for fields backed by a single column. Auto Ontology → Ossie
reduces the physical type to Ossie's logical vocabulary, so `NUMBER(38,0)`
becomes `Integer`, `NUMBER(12,2)` becomes `Decimal`, and a type Ossie cannot
name, such as Snowflake's `VARIANT`, becomes `Opaque` as the spec prescribes.
Ossie → Auto Ontology writes a canonical physical type for a column the Ossie
model introduces, and never overrides a type preserved from a real
Auto Ontology catalog, since Auto Ontology reports what the connection actually
holds. The two mappings are inverses, so a declared `datatype` survives a full
cycle.

A computed field or a metric has no single column behind it and Auto Ontology
stores no type for either, so their `datatype` is not carried. `Opaque` is not
written back, because it names a type outside the vocabulary and there is no
physical type worth inventing from it.

## Tests

```bash
uv run pytest
```

The suite checks the exact native root shape, deterministic and resolvable
IDs, official Ossie validation, semantic round trips, native metadata
preservation, multiple databases, relationships, input validation, and CLI
behavior.
