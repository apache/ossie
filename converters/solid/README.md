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

# Apache Ossie Solid Converter

Bidirectional, offline conversion between an [Apache Ossie](https://github.com/apache/ossie)
semantic model and a [Solid](https://www.getsolid.ai/) semantic model YAML export. No
Solid connection required.

- **Import** (`ossie-solid import`): Solid → Apache Ossie. Solid features Apache Ossie
  has no native field for are preserved in `custom_extensions[SOLID]`, so
  `Solid → Apache Ossie → Solid` is lossless.
- **Export** (`ossie-solid export`): Apache Ossie → Solid, in the key order
  Solid's own exporter uses.

Solid is an AI analytics platform whose semantic layer is authored against a warehouse
catalog and consumed by its text-to-SQL agents. Its export is the YAML you get from
**Download Solid Semantic Model YAML**, and the same document Solid stores as a semantic
layer version.

On **export** (Apache Ossie → Solid), Apache Ossie constructs with no Solid slot —
`unique_keys`, field `label`, computed dimensions, metric `datatype`, relationship
`ai_context`, foreign-vendor `custom_extensions`, `ai_context.examples` below the model
level — are **dropped with a warning**. On
**import** (Solid → Apache Ossie), Solid-only features are instead **preserved** in
`custom_extensions[SOLID]`. Any input that breaks a [requirement](#requirements)
**raises a `ConversionError`** — the converter never silently drops a field or produces
an invalid result.

## Installation

```bash
pip install apache-ossie-solid        # once published to PyPI
```

Or, from a checkout of this directory:

```bash
pip install -e .
```

Runtime dependencies are `PyYAML` and `sqlglot`. Python 3.11+.

## Usage

### Command line

```bash
ossie-solid import -i solid_model.yaml -o model.yaml [--dialect SNOWFLAKE]
```

```bash
ossie-solid export -i model.yaml -o solid_model.yaml [--dialect SNOWFLAKE]
```

With no `-o`, output goes to stdout; warnings always go to stderr. `--name` overrides the
model name in either direction. `--dialect` is described under
[Dialect resolution](#dialect-resolution).

### Python API

```python
from ossie_solid import convert_solid_to_ossie, convert_ossie_to_solid

ossie_yaml = convert_solid_to_ossie(solid_yaml_str)                      # or dialect="SNOWFLAKE"
solid_yaml = convert_ossie_to_solid(ossie_yaml_str, model_name="sales")
```

## Dialect resolution

**Solid's YAML does not record which warehouse a model came from.** The source system is
held on the asset row in Solid's own database and is dropped when the YAML is rendered,
but Apache Ossie requires a dialect on every expression. The converter resolves one in
this order:

1. **`--dialect`**, when given. Always wins.
2. **Inference from the column type vocabulary.** Solid copies each column's `type` out
   of the catalog verbatim, so the names identify the warehouse: `NUMBER`/`TEXT`/
   `VARIANT` mean Snowflake, `LONG`/`MAP`/`BIGINT` mean Databricks, `INT64`/`FLOAT64`/
   `BOOL` mean BigQuery. Only names unique to one warehouse vote; shared ones
   (`STRING`, `BOOLEAN`, `DATE`, `TIMESTAMP_NTZ`) are ignored.
3. **`ANSI_SQL`**, with a warning, when nothing votes or two warehouses tie.

The resolved dialect is recorded in `custom_extensions[SOLID]`, so an export reads
expressions back in the dialect they were written in without being told again.

On export, `--dialect` selects which expression dialect to *read*; without it the
converter uses the dialect recorded at import, then the single non-`ANSI_SQL` dialect the
model's expressions use. A field with neither the chosen dialect nor `ANSI_SQL` raises.

**Only SQL dialects are read.** Apache Ossie's dialect enum also covers expression
languages that are not SQL — `MDX`, `TABLEAU`, `MAQL` — which this converter cannot
parse, qualify or unqualify. One of those never becomes the resolved dialect: the model
is reported, and each expression's `ANSI_SQL` form is read instead. An expression
offering only a non-SQL dialect raises rather than passing a formula Solid cannot
execute into a SQL field. (This is what lets GoodData's `MAQL`-and-`ANSI_SQL` models
convert on their SQL side.)

## Mapping

Each row maps in both directions; the **Notes** flag where a behavior is specific to
**import** (Solid → Apache Ossie) or **export** (Apache Ossie → Solid).

### Model

| Apache Ossie | Solid | Notes |
|---|---|---|
| `semantic_model[0].name` | `semantic_model.name` | Solid holds one model per file; export warns if the document has more. |
| `description` | `model_llm_description` | |
| `ai_context.instructions` | `business_context.custom_instructions` | Import resolves Solid's `@<assetlink …>` markup to the display names it carries and keeps the tagged original in the stash, so an export restores the live catalog references. |
| `ai_context.examples` | `business_context.business_questions` | |
| `custom_extensions[SOLID]` | `business_context.model_description`, `example_queries`, `benchmark_questions` | No Apache Ossie core equivalent. |

### Datasets

| Apache Ossie | Solid | Notes |
|---|---|---|
| `dataset.source` | `tables[].name` | Solid names a table by its `catalog.schema.table`. |
| `dataset.name` | — | Derived on import from the last part of the FQN, widening to `schema_table` and then the whole FQN if a shorter form is taken. Apache Ossie dataset names double as expression qualifiers, so a dotted name is not usable. |
| `description` | `description` | Solid's AI-written description. |
| `ai_context.instructions` | `manual_description` | Solid's human-written annotation. |
| `ai_context.synonyms` | `synonyms` | |
| `primary_key` (array) | `primary_key` (scalar) | Solid joins a composite key with `", "` into one scalar; import splits it, export rejoins it. |
| `custom_extensions[SOLID]` | `quality_rank`, `indexes` | No Apache Ossie core equivalent. Export emits `quality_rank` even when unknown, as Solid does. |

### Fields

| Apache Ossie | Solid | Notes |
|---|---|---|
| `fields[]` | `dimensions[]` + `facts[]` | Solid splits columns two ways; Apache Ossie has one list. Import records the split as the presence of the `dimension` block, and export reads it back. An Apache Ossie model that carries no `dimension` block anywhere is split the way Solid itself does it — by data type. |
| `dimension.is_time` | — | Set explicitly on import from the resolved `datatype`, rather than left to the spec's default, so a consumer that does not implement the default still reads the same role. |
| `expression.dialects[]` | the column's own name | A Solid column's expression is a bare reference to itself; a name needing quoting is quoted for the resolved dialect. |
| `datatype` | `type` | See [Data types](#data-types). |
| `description` | `description` | |
| `ai_context.instructions` | `manual_description` | |
| `ai_context.synonyms` | `synonyms` | |
| `custom_extensions[SOLID]` | `type` (raw), `sample_values` | The raw warehouse type is always kept, since the portable `datatype` is lossy. |

A Solid **fact may be a table-scoped metric** rather than a catalog column — it carries an
`expression` and no `type`. Import keeps the expression and marks the field
`role: metric` in the stash; export re-emits it as an expression-only fact.

### Relationships

| Apache Ossie | Solid | Notes |
|---|---|---|
| `from` / `to` | `left_table` / `right_table` | See [Cardinality](#cardinality). |
| `from_columns` / `to_columns` | `join_keys.left` / `join_keys.right` | Positional; import rejects a length mismatch. |
| `name` | — | Solid relationships are unnamed. Import generates `{from}_to_{to}`, suffixed if that collides. |
| `ai_context` | — | A Solid relationship is a table pair and its join keys, with no free text. Export warns — except for the one-to-one note import itself writes, which is this converter's own marker and means nothing to Solid. |
| `custom_extensions[SOLID]` | — | Records the original left/right orientation and position, so an export reproduces Solid's ordering. |

### Metrics

| Apache Ossie | Solid | Notes |
|---|---|---|
| `metrics[]` | `metrics[]` | Solid metrics are already model-level. |
| `expression.dialects[]` | `expression` + `tables[]` | See [Metric expressions](#metric-expressions). |
| `description` | `description` | |
| `datatype` | — | Solid types a metric by evaluating its formula against the warehouse, so there is no slot for a declared result type. Export warns. |
| `ai_context.synonyms` | `synonyms` | |
| `custom_extensions[SOLID]` | `tables[]` | Stashed only when the qualified expression does not already name the same datasets — for a cross-table metric, or one referencing no column at all (`COUNT(*)`). |

## Cardinality

Apache Ossie encodes cardinality through direction — `from` is the many side, `to` the one
side — but a Solid relationship is an undirected pair of column lists. Import recovers the
direction from the primary keys:

| Condition | Result |
|---|---|
| The right table's primary key is exactly its join columns | `from` = left, `to` = right |
| The left table's primary key is exactly its join columns | `from` = right, `to` = left (recorded as `flipped`) |
| Both | One-to-one: direction is arbitrary, and a note is added to the relationship's `ai_context` |
| Neither | `from` = left, with a warning — Solid records no cardinality and its `unique_keys` are not exported |

## Metric expressions

Solid stores a metric formula against **bare** column names and records the owning table
separately; `is_valid_metric_formula` in solid-server actively strips alias prefixes
before saving. Apache Ossie expects a metric to be self-contained, qualifying each column
with its dataset. So:

- **Import** prefixes each bare column with its dataset when `tables[]` names exactly one
  table (`SUM(ss_ext_sales_price)` → `SUM(store_sales.ss_ext_sales_price)`).
- **Export** strips those qualifiers back out.

Both edits are a **surgical splice**, not a re-render. Each column is located by
sqlglot's tokenizer, which reports byte offsets and keeps string literals and quoted
identifiers in token types of their own, and the qualifier is inserted or removed at those
offsets. Everything else is left byte-for-byte intact — round-tripping a parsed tree
through `Expression.sql()` would canonicalize as it generates, turning
`CAST(x AS FLOAT)` into `CAST(x AS DOUBLE)` and `EXTRACT(year FROM d)` into
`DATE_PART(YEAR, d)`, and a converter has no business rewriting SQL it was only asked to
qualify.

The parser is still used as a cross-check: it decides which names are genuinely bare
column references, and a token scan that disagrees with it means some occurrence is
something else. On any disagreement — an unparseable expression, or a column also named
`year` in `EXTRACT(year FROM …)` — the expression is left **exactly as written** and a
warning is emitted. An unqualified metric is a far smaller problem than a corrupted one.

**A cross-table metric cannot be qualified.** Solid's `tables[]` may hold more than one
table, and the alias-to-table binding lives in `metric.column_ids`, which its YAML export
does not carry. Those metrics keep their bare column names, keep `tables[]` in the stash,
and emit a warning.

## Data types

Solid stores the **raw** warehouse type string verbatim (`NUMBER(38,0)`, `TEXT`, `INT64`,
`MAP<STRING, STRING>`), so mapping onto Apache Ossie's portable `datatype` is lossy —
`NUMBER(38,2)`, `NUMERIC` and `DECIMAL` all collapse to `Decimal`. The raw string is
therefore always kept in the stash and is what an export re-emits; the derived table below
is only used for an Apache Ossie model that has no stash to read.

| Warehouse type | Apache Ossie `datatype` |
|---|---|
| `STRING`, `TEXT`, `VARCHAR`, `CHAR` | `String` |
| `INT`, `BIGINT`, `LONG`, `INT64`, `TINYINT` | `Integer` |
| `NUMBER(p,0)`, `NUMERIC(p,0)`, `DECIMAL(p,0)` | `Integer` |
| `NUMBER(p,s>0)`, `NUMERIC`, `DECIMAL`, `BIGNUMERIC` | `Decimal` |
| `FLOAT`, `FLOAT64`, `DOUBLE`, `REAL` | `Float` |
| `BOOLEAN`, `BOOL` | `Boolean` |
| `DATE` / `TIME` | `Date` / `Time` |
| `TIMESTAMP_NTZ`, `DATETIME` | `DateTime` |
| `TIMESTAMP_TZ`, `TIMESTAMP_LTZ` | `DateTimeTz` |
| `TIMESTAMP` | `DateTime` on Snowflake, `DateTimeTz` on Databricks and BigQuery |
| `VARIANT`, `OBJECT`, `ARRAY`, `MAP`, `STRUCT`, `BINARY`, `GEOGRAPHY` | `Opaque` |
| anything else | omitted, with a warning |

A declared scale of `0` is read as an integer, which is what keeps Snowflake ids and
counts — stored as `NUMBER(38,0)` — from being typed `Decimal`.

## Requirements

Conversion raises a `ConversionError` (rather than guessing or emitting something invalid)
when an input breaks one of these:

- the Apache Ossie `version` is neither `0.2.0.dev0` nor `0.1.1` (export) — see
  [Spec versions](#spec-versions);
- the input YAML is malformed, or its root is not a mapping;
- a Solid model has no `tables`, or an Apache Ossie model has no `datasets` — the spec
  requires at least one dataset;
- a relationship's `from_columns`/`to_columns` differ in length, or name a dataset that
  is not declared;
- `--dialect` names a dialect outside `ANSI_SQL`, `SNOWFLAKE`, `DATABRICKS`, `BIGQUERY`;
- a field or metric has neither the selected dialect nor `ANSI_SQL` (export);
- the recorded `custom_extensions[SOLID]` dialect is not one of the four above — which
  only a hand-edited stash can produce (export).

## Spec versions

Output is always written as **`0.2.0.dev0`**, the current draft. On export, a document
declaring either `0.2.0.dev0` or **`0.1.1`** is read; anything else raises.

`0.1.1` is accepted because it is the only *released* spec version, so it is what models
in the wild — and several of this repository's own converter fixtures — declare. As a
document, a `0.1.1` model is a `0.2.0.dev0` model minus three purely additive changes:
`datatype` on `Field` and `Metric`, `BIGQUERY` in the dialect enum, and a free-form
rather than enumerated `vendor_name`. Nothing in a `0.1.1` document is therefore invalid
under `0.2.0.dev0`, and no separate read path is needed. Only the absence of `datatype`
is visible to this converter, and a `0.2.0.dev0` model that simply omits it — as most do
— is already handled the same way. Reading a `0.1.1` document warns, naming that gap.

## Known differences

- **An empty description is normalized away.** solid-server renders a whitespace-only
  `manual_description` as an empty block scalar, which parses back as `''`. That carries
  no information, so the converter drops it rather than round-tripping the emptiness.
- **`unique_keys` cannot survive an export.** Solid's YAML has no unique-key field; a
  single unique key identical to the primary key is dropped silently, any other is
  dropped with a warning.
- **A Solid model carries no `unique_keys` to import**, so a re-imported model relies on
  `primary_key` alone to determine relationship cardinality.

## Development

```bash
uv sync && uv run pytest
```

The suite covers both directions against three Solid fixtures — the repository's
[TPC-DS model](../../examples/tpcds_semantic_model.yaml) expressed as a Solid export, plus
a Databricks and a BigQuery model — and validates every converted document against the
[Apache Ossie JSON Schema](../../core-spec/ossie-schema.json).

`tests/test_cross_vendor.py` covers the case the round-trip tests cannot: an Apache Ossie
model produced by *another vendor's* converter, which carries no `custom_extensions[SOLID]`
stash, no `dimension` blocks, usually no `datatype`, and metrics written against bare
column names. It sweeps every Ossie fixture the other converters in this repository ship,
asserting that each converts to a well-formed Solid model that re-imports schema-valid.
That sweep deliberately pins **no** warning counts, because those fixtures belong to other
converters and each converter's CI is path-filtered to its own directory — a pin here would
break on a main-branch push from a PR that never ran this suite. The exact interop gaps are
pinned instead against
[`tests/fixtures/foreign_ossie.yaml`](tests/fixtures/foreign_ossie.yaml), a fixture this
converter owns that reproduces the same constructs.

To regenerate the expected-output fixture after an intentional change:

```bash
uv run ossie-solid import -i tests/fixtures/tpcds_solid.yaml -o tests/fixtures/tpcds_ossie.yaml
```

then re-add the license header at the top of the file.

## Future effort

Both the Apache Ossie specification and Solid's semantic model are still evolving, and
this converter will be updated to track them. Several gaps are worth naming.

Three of these are **open interop decisions**, pinned as such in
`tests/test_cross_vendor.py` rather than resolved. Each is a judgement about what Solid
should do with a foreign model, not a defect in the transform:

- **A column with no `datatype` gets an empty Solid `type`.** `datatype` is optional in
  the spec and most converters omit it, so this is the largest single gap when importing
  a foreign model — see the counts in `test_the_documented_interop_gaps_are_exactly_these`.
  It cannot be closed offline: Solid types its columns from the warehouse catalog, and
  the honest fix is to reconcile against the live catalog at import time rather than to
  guess here. What the converter owes that flow is a machine-readable list of what needs
  reconciling, which today it only reports as warnings.
- **A field renamed relative to its column loses the column it reads.** Given
  `name: ticket_number` over `expression: ss_ticket_number`, the alias survives and the
  real column does not, so the Solid model names a column the warehouse does not have.
  Emitting the underlying name and keeping the alias as a synonym would be truer, but it
  changes which identifier downstream Solid consumers see and so needs a product call.
- **A metric written against bare column names reaches Solid with `tables: []`.** Only a
  dataset-qualified reference identifies its owner. Resolving bare names against each
  dataset's declared fields would fix most cases; it is inference, so it is deliberately
  not done silently.

And two are **format-level**:

- **Verified queries have no home in the core spec.** Solid's `example_queries` and
  `benchmark_questions` are currently stashed, but the same construct appears across the
  ecosystem — Snowflake Cortex `verified_queries`, WisdomAI reviewed queries — and looks
  like a candidate for a core field rather than N vendor extensions.
- **Solid's export does not record its source warehouse**, which is why the dialect has to
  be inferred. Adding it to the export would make the format self-describing; the
  converter would read it first and keep inference only as a fallback for existing files.
