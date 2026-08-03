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

# Apache Ossie <-> Cube converter

Bidirectional, offline conversion between an [Apache Ossie](https://github.com/apache/ossie)
semantic model and a [Cube](https://cube.dev/docs/product/data-modeling/overview)
data model. No Cube deployment, API token, or network access required.

A Cube data model is a *directory* of YAML files rather than a single document, so
this converter maps one Ossie YAML document to/from the Cube model layout:

```
model/cubes/<name>.yml      # one per Ossie dataset
model/views/<name>.yml      # the view the Ossie model maps to
```

Import accepts any layout: `cubes:` and `views:` may live in any `.yml`/`.yaml`
file at any depth, several per file, and original file paths are preserved through
a round trip.

- **Import** (`ossie-cube import`): Cube files -> Ossie. Cube features Ossie has
  no native field for are preserved in `custom_extensions[CUBE]`, so
  **Cube -> Ossie -> Cube is lossless**.
- **Export** (`ossie-cube export`): Ossie -> Cube files. Ossie features with no
  Cube slot are parked under `meta.ossie` rather than dropped -- Cube has a `meta`
  field at every level -- so **Ossie -> Cube -> Ossie is lossless too**.

Any input that breaks a [requirement](#requirements) **raises a
`ConversionError`** -- the converter never silently drops a field or produces an
invalid result. Losses it *can* absorb are returned as structured
[issues](#conversion-issues) rather than printed and forgotten.

## Installation

```bash
pip install apache-ossie-cube        # once published to PyPI
# or, from a checkout of this directory:
pip install -e .
```

Runtime dependencies are `PyYAML` and `sqlglot` (already a runtime dependency of
the dbt and NVIDIA GSF converters, used here to locate the aggregate calls inside a
composite metric). Python 3.11+.

## Usage

### Command line

```bash
ossie-cube import -i model/ [-o model.yaml] [--name my_model] [--view sales]
                            [--strict-fanout]
ossie-cube export -i model.yaml -o model/ [--dialect SNOWFLAKE] [--base-cube orders]
```

`import` accepts a model directory (walked recursively), individual files, or any
mix of several — so converting part of a model does not mean assembling a directory
first:

```bash
ossie-cube import -i model/                                   # the whole model
ossie-cube import -i model/cubes/orders.yml                   # one file
ossie-cube import -i model/cubes/orders.yml model/views/*.yml # a subset
```

Cube itself has a single model root (`CUBEJS_SCHEMA_PATH` is one path), so pointing
at that root is the idiomatic whole-project case. With several paths, files are keyed
relative to their common parent directory — which is what decides where `export`
writes them back — and the single-directory and single-file cases are keyed exactly
as they would be alone.

With no `-o`, `import` writes the Ossie YAML to stdout; `export` always needs `-o` (a
directory). Issues always go to stderr, so stdout stays pipeable. `--view` picks
which view's name/description/AI context map onto the Ossie model when the input
holds several; `--name` overrides the model name. `--base-cube` picks the cube a
*generated* view is rooted at, and is only consulted for a hand-authored Ossie model
with no stashed views.

**A view on its own is not a model.** A Cube view projects members from cubes and
defines none of its own, so passing only `views/sales.yml` is refused -- with an
error naming the cubes it references, so you know which files to add. Include the
cube files (or point `-i` at the model directory).

### Python API

```python
from ossie_cube import convert_cube_to_ossie, convert_ossie_to_cube

ossie_yaml, issues = convert_cube_to_ossie(files)     # {relative filename: YAML str}
files, issues = convert_ossie_to_cube(ossie_yaml)     # -> {relative filename: YAML str}
for issue in issues:
    print(issue)
```

## Mapping

Each row maps in both directions; the **Notes** flag where a behavior is specific
to **import** (Cube -> Ossie) or **export** (Ossie -> Cube).

| Apache Ossie | Cube | Notes |
|---|---|---|
| `semantic_model` | a **view** | Cube users are view-first, and Cube's agent reads `meta.ai_context` only from views and members -- so the view, not any cube, is the model boundary. |
| `semantic_model.name` | view name | Import: the mapped view's name (override with `--name`). |
| `model.description` / `ai_context.instructions` | view `description` / `meta.ai_context` | Import: taken from the sole view, or `--view`. |
| dataset | `cubes[]` entry in `model/cubes/<name>.yml` | Import: a non-canonical original path is stashed and restored on export. |
| `dataset.source` (dotted) | `sql_table` | Passed through verbatim; Cube interpolates it straight into `FROM`, so no catalog/schema split is needed. |
| `dataset.source` (`SELECT ...`) | `sql` | Cube requires exactly one of `sql` / `sql_table`. |
| `dataset.description` | cube `description` | |
| `dataset.ai_context` | cube `meta.ai_context` | Preserved for the round trip, but **inert in Cube** -- its agent ignores cube-level `ai_context`. Recorded as an issue. |
| `dataset.primary_key` | dimension(s) with `primary_key: true` | Composite = several. Export marks a dimension only when it is **scalar** — a single source column — since `primary_key: true` declares that dimension's own `sql` to be the key; a computed dimension or a merged `geo` one would declare the wrong thing even if its name matches. Anything left uncovered becomes a `public: false` scalar dimension, suffixed (`id_pk`) if the obvious name is taken. |
| `dataset.unique_keys` | `meta.ossie.unique_keys` | No native Cube slot; parked rather than dropped. |
| field | `dimensions[]` entry | Export: a name that is not a valid Cube identifier is sanitized; a case-insensitive collision is an error, never a silent merge. |
| `field.expression` | dimension `sql` | Dataset-scoped, so `{CUBE}.col` <-> `col`. Export emits `{CUBE}.column` for a raw column and `{CUBE.member}` for a declared member, and never spells the cube's own name (which would break under `extends`). |
| `field.datatype` | dimension `type` (**required**) | `String`->`string`, `Boolean`->`boolean`, `Date`/`Time`/`DateTime`/`DateTimeTz`->`time`, `Integer`/`Decimal`/`Float`->`number`, `Opaque`->`string`. Import maps back, choosing `Decimal` for `number` -- Cube collapses three Ossie types into one, so any single answer is a guess, and a stated datatype is what another converter can act on. Export parks the exact one in `meta.ossie.datatype`, which import prefers when present, so `Integer` and `Float` still survive a round trip. |
| `field.dimension.is_time` | `type: time` | Import sets `is_time: true` for a time dimension. |
| `field.label` / `description` | dimension `title` / `description` | |
| `field.ai_context.instructions` | dimension `meta.ai_context` | Cube's documented AI-only context field. |
| — | `type: geo` dimension | An Ossie field holds one expression and a geo dimension has two, so it **splits** into `<name>_latitude` / `<name>_longitude` (`Float`). Reconstruction data rides on the latitude half. See [Geo dimensions](#geo-dimensions). |
| relationship | `joins[]` on a cube | `many_to_one` on cube A -> `from: A`(many), `to: B`(one). `one_to_many` is flipped so Ossie's `from` is the many side; the declared side and type are stashed so export restores the original. |
| `from_columns` / `to_columns` | join `sql` | Only an AND-chain of equalities between two member references maps. Anything else (non-equi, range, literal, third cube) is preserved verbatim in the stash. |
| metric | `measures[]` on the cube its expression references | Import hoists cube-scoped measures to the model level, qualifying a colliding name as `<cube>__<measure>` and stashing the original name and owning cube. |
| `SUM`/`AVG`/`MIN`/`MAX(x)` | `type: sum`/`avg`/`min`/`max` + `sql` | |
| `COUNT(DISTINCT x)` | `type: count_distinct` | |
| `APPROX_COUNT_DISTINCT(x)` | `type: count_distinct_approx` | Cube resolves the warehouse-specific function itself. |
| `COUNT(DISTINCT <pk>)` | bare `type: count` | See [Fan-out](#fan-out) -- the primary key is load-bearing here. |
| several aggregates in one expression | one `public: false` measure per aggregate + a `type: number` measure referencing them | Each part is declared on the cube its own operand reads, so Cube corrects row multiplication per aggregate rather than once for the whole expression. The parts carry `meta.ossie.part_of`, and import skips them and inlines their SQL back through the references -- recovering the original expression exactly. |
| anything else | `type: number` (calculated) | A `{other_measure}` reference is **inlined**, because that is what Cube itself does; Ossie has no metric-to-metric reference. |
| — | measure `filters` | Folded into `CASE WHEN … THEN … END` inside the aggregate, exactly as Cube's own `applyMeasureFilters` renders it. |
| `metric.datatype` | — | Import emits `Integer` for the count family, whose result type Cube does know, and omits it otherwise. |
| `metric.description` / `ai_context` | measure `description` / `meta.ai_context` | |
| `custom_extensions[CUBE]` | everything Cube-only | Import stashes; export restores -- keeping `Cube -> Ossie -> Cube` lossless. |
| foreign-vendor `custom_extensions` | `meta.ossie.custom_extensions` | Parked so a multi-vendor Ossie model survives the round trip. |

**Stashed on import** (and restored on export): the views verbatim (minus the
natively mapped description/AI context), the mapped view's identity, original file
paths, cube extras (`title`, `sql_alias`, `data_source`, `public`, `refresh_key`,
`segments`, `pre_aggregations`, `hierarchies`, `access_policy`, `calendar`, ...),
dimension extras (`format`, `currency`, `granularities`, `case`, `sub_query`,
`order`, `aliases`, `meta`, ...), measure extras and any non-reconstructible
measure, joins with no Ossie form, Jinja-templated members, and files with no
Ossie form (`.js`/`.ts` models, non-model YAML).

**Expression dialects**: Cube SQL is the SQL of the model's data source, and the
Ossie dialect enum has no `CUBE` entry -- so import emits `ANSI_SQL`, and export
prefers `ANSI_SQL` with `--dialect` prepending a warehouse dialect (e.g.
`SNOWFLAKE` for a Snowflake-backed Cube model).

## Fan-out

This is the one place where Cube carries semantics an Ossie expression cannot, and
it is handled deliberately rather than papered over.

When a cube sits on the multiplied side of a join, Cube does **not** aggregate over
the flattened join. It builds `SELECT DISTINCT <primary key> FROM <join>`, joins
that key set back to the measure's own cube, and aggregates there -- so each source
row is counted once. If the measures themselves span cubes that fan out, Cube
refuses the query outright. Correctness comes from a *runtime rewrite keyed on
declared primary keys*, and a static SQL string has no way to inherit it.

So the converter emits the fan-out-safe form wherever one exists, and refuses to
emit a silently-wrong one:

| Cube measure | Ossie expression | Safe under fan-out? |
|---|---|---|
| bare `count` | `COUNT(DISTINCT <pk>)` | **Yes, exactly.** Cube renders `count(pk)` normally and `count(distinct pk)` when multiplied; `COUNT(DISTINCT pk)` equals both. A composite key is concatenated with `CAST` + `CONCAT`, as Cube does. |
| `count_distinct` | `COUNT(DISTINCT x)` | Yes, inherently |
| `count_distinct_approx` | `APPROX_COUNT_DISTINCT(x)` | Yes, inherently |
| `min` / `max` | `MIN(x)` / `MAX(x)` | Yes -- idempotent under duplication |
| `sum`, `avg`, `count` + `sql` | `SUM(x)`, `AVG(x)`, `COUNT(x)` | **No** |

Only the last row is at risk, and only when its own cube is the `to` (one) side of
a relationship in the model. The converter computes that from the Ossie graph and
**records a `FANOUT_UNSAFE_METRIC` issue** naming the metric, the dataset and the
relationship responsible -- refusing a whole model over one such metric would leave
the spoke on the other side with nothing. Pass `--strict-fanout` to refuse instead,
mirroring Cube's own refusal.

The issue is reported to the caller, not written into the Ossie model: the spec has
no additivity declaration to write it into (see below), and a `custom_extensions`
entry would only give every other converter something to warn about and discard.

Because a bare `count` maps through the primary key, a cube carrying one **must**
declare `primary_key: true` on a dimension; its absence is an error, not a
different number.

Going the other way, an Ossie metric combining several aggregates is **decomposed**
rather than emitted as one calculated measure, so Cube's correction applies to each
aggregate on its own cube:

```yaml
# Ossie                                         # Cube
SUM(store_sales.amount)                         store_sales: clv_part_1 (sum, public: false)
  / COUNT(DISTINCT customer.id)                 customer:    clv_part_2 (count, public: false)
                                                store_sales: clv = {CUBE.clv_part_1}
                                                                 / {customer.clv_part_2}
```

A single aggregate reading two datasets cannot be split this way and still lands on
one cube.

> Ossie has no additivity or grain declaration to record this properly -- dbt's
> `non_additive_dimension` is the nearest precedent, and this repo's dbt converter
> already loses the same information. Worth raising on `dev@`.

## Geo dimensions

A Cube `type: geo` dimension carries two SQL expressions where an Ossie field carries one, so it splits on import:

```yaml
# Cube                                  # Ossie
- name: home                            - name: home_latitude   (expression: lat)
  type: geo                             - name: home_longitude  (expression: lon)
  latitude:  { sql: "{CUBE}.lat" }
  longitude: { sql: "{CUBE}.lon" }
```

Export merges the halves back into the single geo dimension, so the round trip is exact.

The half names exist **only in Ossie** — Cube has neither a column nor a member called `home_latitude`. So when an Ossie metric or field expression references a half, export substitutes the half's own SQL rather than emitting a reference Cube cannot resolve:

```
AVG(users.home_latitude)                    ->  sql: AVG({CUBE}.lat)
AVG(users.home_latitude) - MIN(orders.amt)  ->  sql: AVG({users}.lat) - MIN({CUBE.amt})
```

`{CUBE}` means "the cube this is declared on", so an inlined snippet is requalified to name its original cube when it crosses into another cube's SQL.

One documented normalization follows: after a round trip such a metric names the column the half actually reads (`users.lat`) rather than the Ossie-only field name (`users.home_latitude`). Same reference, and it is the form Cube can express.

## Onward conversion

Ossie is a hub, so the useful question is not only whether `Cube → Ossie → Cube`
round-trips but whether the Ossie model then reaches the other spokes. Two things
matter in practice.

**Keep Cube-only detail out of `custom_extensions`.** Converters that do not read
foreign extensions warn about and discard every one, so anything placed there is
noise to them. This converter therefore stashes only what is genuinely Cube-specific
— segments, pre-aggregations, hierarchies, view curation, geo reconstruction — and
maps everything else natively. On the TPC-DS model that is 7 stash entries rather
than 41, and 2 Databricks warnings rather than 32.

**Qualify your `sql_table`.** Cube accepts `orders` or `public.orders`, but the
Databricks, Snowflake and NVIDIA GSF converters all require a three-part
`catalog.schema.table` and reject anything shorter:

```
Error: Dataset 'orders': source 'public.orders' must be a 3-part catalog.schema.table
Error: Dataset 'orders' source must resolve to database.schema.table
Error: Source 'public.orders' must be a fully qualified db.schema.table or a subquery
```

Import reports this as `SOURCE_NOT_FULLY_QUALIFIED` rather than guessing a catalog
name, so it surfaces where the Ossie document is produced instead of three hops later.

## Conversion issues

`convert_cube_to_ossie` returns `(yaml, IssueLog)`. Each issue carries a type, the
element it concerns, and a detail string.

| Issue type | Meaning |
|---|---|
| `FANOUT_UNSAFE_METRIC` | A non-idempotent aggregate on a dataset the graph fans out; see [Fan-out](#fan-out) |
| `MULTI_STAGE_MEASURE_PARKED` | A `multi_stage` measure (`group_by`/`reduce_by`/`time_shift`/`rank`) renders as a window function over another grain, so it gets no `metrics` entry — the original is preserved verbatim in the dataset's stash and restored on export |
| `CUBE_LEVEL_AI_CONTEXT_INERT` | Cube's agent ignores cube-level `meta.ai_context` |
| `GEO_DIMENSION_SPLIT` | A `type: geo` dimension became two Ossie fields |
| `TEMPLATED_FILE_SKIPPED` | Jinja templating anywhere in a file, or a `.js`/`.ts` model file. Detected per file, as Cube's own tooling does, so the file is preserved whole rather than half-converted |
| `NO_USABLE_DIALECT` | Export: no `ANSI_SQL` or preferred-dialect expression |
| `SOURCE_NOT_FULLY_QUALIFIED` | A `sql_table` shorter than `catalog.schema.table`. Valid Cube and nothing is lost, but the Databricks, Snowflake and NVIDIA GSF converters reject such a source, so the model cannot convert onward — see [Onward conversion](#onward-conversion) |
| `PARKED_IN_META` | Preserved in the stash or under `meta.ossie` — invisible to Cube, but intact through a round trip |
| `DROPPED_NO_CUBE_EQUIVALENT` | **Gone from the output.** Cube has nowhere to hold it and it cannot be parked: relationship `ai_context` (a Cube join entry has no `meta`), a `dimension.is_time` role or opt-out that Cube expresses only through `type`, and the second and later `semantic_model` entries |
| `APPROXIMATED` | Emitted, but not an exact equivalent: a value Cube requires and Ossie does not carry (so the converter chose one), or a construct rendered in the nearest form Cube has |

These three are kept distinct on purpose. A caller gating on issue types has to be
able to tell "preserved but unreadable by Cube" from "actually lost" from "emitted,
but asserting slightly more than the input did".

## Requirements

Conversion raises a `ConversionError` (rather than guessing or emitting something
invalid) when an input breaks one of these:

- a cube has neither or both of `sql` / `sql_table` (Cube requires exactly one);
- a cube uses `extends` -- resolving it means reproducing Cube's definition-merge
  semantics exactly, so it is refused rather than half-applied;
- a bare `type: count` measure's cube declares no primary key;
- a join names a cube that is not in the model, or an unknown `relationship`;
- a measure has an unknown `type`, or a measure reference cycle;
- two cubes, two views, or two derived metric names collide;
- a dimension has an unknown `type`, or a `geo` dimension is missing
  `latitude.sql` / `longitude.sql`;
- the model carries foreign-vendor `custom_extensions` but no view is mapped, so
  there is nowhere to park them (re-import with `--view <name>`); model-level
  metadata rides on the view representing the model, and picking one arbitrarily
  would not survive a re-import;
- there are no convertible cubes at all; the input YAML is malformed.

## Notes and limitations

- **YAML data models only.** `.js`/`.ts` models and Jinja-templated YAML are
  preserved verbatim for the round trip but no cube inside them is converted --
  matching what Cube's own `CubeSchemaConverter` does for the Rollup Designer.
- **camelCase is normalized.** Cube accepts `sqlTable` and `sql_table` alike;
  import normalizes to snake_case and export always emits snake_case, so a
  camelCase source file comes back snake_cased.
- A filter or computed operand written with bare column names (rather than
  `{CUBE}.col`) cannot be qualified into `dataset.column` form, so it is emitted
  as-is. Cube's own idiom uses the reference form, which converts fully.
- View curation (`prefix`, `alias`, `includes`/`excludes`, `folders`,
  `default_filters`, `view_group`) is stash-and-restore only; Ossie field names
  are always *cube* member names, so prefixed view members never leak into them.
- `type: switch` dimensions, `hierarchies`, `pre_aggregations`, `access_policy`,
  and multiple `data_source`s have no Ossie semantics and round-trip via the stash.

## Development

```bash
uv sync
uv run pytest
```

Example-based unit tests per direction, CLI
behavior tests, fixture round-trip tests (including the
[TPC-DS model](../../examples/tpcds_semantic_model.yaml) the converter guide asks
for as a baseline), core-spec JSON Schema validation of every emitted Ossie
document, and Hypothesis property-based round-trip tests over generated Cube
models -- which fall back to a seeded sweep when `hypothesis` is unavailable, so
the properties still run.

## Future effort

Both the Apache Ossie specification and Cube's data model are still evolving. As
either side adds or changes fields, this converter will be updated to track them.
Known next steps: offline `extends` resolution, `.js`/`.ts` model support (which
needs Cube's own transpiler, so most likely a Cube-side exporter feeding this
converter), and a first-class Ossie representation for measure additivity so the
fan-out caveat can be recorded in the model instead of an issue log.
