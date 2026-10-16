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
their Auto Ontology source group. A SQL attribute that is a filter,
`SELECT * FROM t WHERE <condition>`, becomes a `Boolean` field holding its
condition, which is true for exactly the rows the filter selects; its original
SQL is preserved, so it returns to Auto Ontology unchanged. Custom analyses
remain global by becoming model-level Ossie metrics. Relationships are
recovered from joins, then physical foreign keys, then semantic foreign keys.

Auto Ontology names are display text, such as `Order Line Item`, while Ossie
names are identifiers that tools write unquoted, as an ontology mapping does in
`dataset.field`. Each dataset, field, metric and relationship is therefore
named in lowercase words joined by underscores, such as `order_line_item`. A
name that SQL reserves gets a trailing underscore (`from_`), one that would
start with a digit is prefixed with what it names (`field_2024_total`), and one
that would collide with another in the same namespace gets a number
(`customer_id_2`). Where the identifier does not spell out the original name,
because it dropped punctuation or had to be told apart, the original is kept as
a synonym in `ai_context`. Expressions lifted out of Auto Ontology SQL refer to
datasets by these names too. Every item keeps its own name on the way back.

Auto Ontology term names are unique across every database it catalogs, so it
renames a term whose name another database already uses to
`Name (database.schema.table)`. An Ossie document is its own namespace, so the
dataset drops that suffix again wherever the plain name is free in the
document, and the term keeps its full name on the way back. Auto Ontology also
gives a foreign-key column no attribute, since the foreign key links the term
instead; each key and relationship column that no field exposes is exported as
a field named after the column, marked so that it does not become an attribute
on the way back.

Primary keys, unique keys and relationships name the fields that read their
columns, which is how ontology tools look them up. On the way back, a key or
relationship column that names a field of its dataset stands for the column
that field reads; any other is the physical column itself, as earlier exports
and other tools write it.

### Ontology and mapping

Auto Ontology's terms are business concepts, its attributes are properties of
those concepts, and its foreign keys link one concept to another. The `import`
command can also write them as an Ossie
[ontology](../../ontology/ontology.md) and a
[mapping document](../../ontology/ontology.md#mapping-documents) that
populates the ontology from the semantic model it writes:

```bash
uv run ossie-nvidia-auto-ontology import \
  --input tpcds.auto_ontology.yaml \
  --output tpcds.semantic_model.yaml \
  --ontology-output tpcds.ontology.yaml \
  --mapping-output tpcds.mapping.yaml
```

```python
from ossie_nvidia_auto_ontology import convert_auto_ontology_to_ossie_ontology

ontology_yaml, mapping_yaml = convert_auto_ontology_to_ossie_ontology(
    auto_ontology_yaml, model_name="tpcds"
)
```

The two outputs are written together. The mapping references both other
documents by name and, from the CLI, by a path relative to itself.

Tools that do not read mapping documents yet read the mapping embedded in the
ontology instead, a form the specification deprecates but still accepts for
them. `--embedded-ontology-output` writes the ontology that way, the mapping
carrying the semantic model whole, on its own or alongside the other outputs:

```bash
uv run ossie-nvidia-auto-ontology import \
  --input tpcds.auto_ontology.yaml \
  --embedded-ontology-output tpcds.ontology.embedded.yaml
```

```python
from ossie_nvidia_auto_ontology import (
    convert_auto_ontology_to_embedded_ossie_ontology,
)

ontology_yaml = convert_auto_ontology_to_embedded_ossie_ontology(
    auto_ontology_yaml, model_name="tpcds"
)
```

The ontology and mapping are read off the semantic model as follows:

| Semantic model | Ontology | Mapping |
|---|---|---|
| Dataset | Entity concept, named in PascalCase | One concept mapping |
| Primary key, else the first unique key | `identify_by` | Object mappings: one expression for a single-field key, referent mappings otherwise |
| Field with a portable `datatype` | `ManyToOne` relationship to the built-in value type, or `OneToOne` when the field alone identifies the concept | Link mapping from the field |
| Relationship | `ManyToOne` relationship to the referenced concept | Link mapping to the referenced concept's objects |

A foreign-key column is the link to another concept, so it does not also become
a value of its own concept. Auto Ontology records no verbalizations, so each
relationship is verbalized from its field name, such as
`{Customer} has account balance {Decimal}`. Mapping expressions quote a name
only where SQL could not read it bare, so `customer.account_balance`. A field whose type is unknown, or
has no built-in value type (`Time`, `Opaque`), is left out rather than guessed.
A concept whose dataset has no key, or whose key columns are not all
identifying fields or links, has nothing to name its objects by: it is declared
in the ontology but not mapped.

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
fictional fields to the Auto Ontology schema.

Auto Ontology records uniqueness per column, from a sample of each table's
rows, so it is evidence rather than a constraint: an amount, a timestamp or a
free-text comment is often distinct across a sample without being a key. On
Auto Ontology → Ossie, a sampled-unique column therefore becomes a unique key
only when a relationship references it, which already treats it as a key, and
not when the primary key already states the same key. Composite unique keys,
and single-column ones no relationship references, cannot be reconstructed
after Auto Ontology → Ossie.

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
preservation, multiple databases, relationships, the ontology and mapping
documents and their official validation, input validation, and CLI behavior.
