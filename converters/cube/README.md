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
| `semantic_model` | a **view** | Cube users are view-first, and Cube's agent reads `meta.ai_context` only from views and members -- so the view, not any cube, is the model boundary. A Cube model need not contain a view, though, and one with several need not say which is the model; when no view can carry the model's metadata it is parked on the alphabetically first cube under `meta.ossie.model` and import reads it back from there. |
| `semantic_model.name` | view name | Import: the mapped view's name (override with `--name`). Export: whenever the emitted view cannot carry the name exactly, the original is recorded in `meta.ossie.model_name` and import hands that back instead of the view's. Three causes — the name is not a valid Cube identifier (`Sales Model` → view `sales_model`); a `--name` override the stashed view does not match; or the name is also a dataset's, since Cube keeps cubes and views in one namespace and would refuse a model with two members of one name (what a Databricks metric view over a same-named table produces), so the generated view becomes `<name>_view`. |
| `model.description` / `ai_context.instructions` | view `description` / `meta.ai_context` | Import: taken from the sole view, or `--view`; or from `meta.ossie.model` on a cube when no view is mapped. |
| dataset | `cubes[]` entry in `model/cubes/<name>.yml` | Import: a non-canonical original path is stashed and restored on export. |
| `dataset.source` (dotted) | `sql_table` | Passed through verbatim; Cube interpolates it straight into `FROM`, so no catalog/schema split is needed. |
| `dataset.source` (`SELECT ...`) | `sql` | Cube requires exactly one of `sql` / `sql_table`. |
| `dataset.description` | cube `description` | |
| `dataset.ai_context` | cube `meta.ai_context` | Preserved for the round trip, but **inert in Cube** -- its agent ignores cube-level `ai_context`. Recorded as an issue. |
| `dataset.primary_key` | dimension(s) with `primary_key: true` | Composite = several. Ossie names the key by *column* while Cube marks a *dimension*, and the two differ whenever the dimension carrying the key is not named after its column — so the column list is recorded (`meta.ossie.primary_key`) when it cannot be read back off the dimensions. It is what the rebuilt `COUNT(DISTINCT …)` uses too, which otherwise named a member the Ossie model does not have. A Cube key can be an *expression*, and then the only name Ossie can carry is the dimension's -- which the Ossie document alone cannot tell apart from a column name afterwards, so import records it (`computed_primary_key`) and export puts `primary_key: true` back on that dimension instead of synthesizing one that reads a column of that name. Export marks a dimension only when it is **scalar** — a single source column — since `primary_key: true` declares that dimension's own `sql` to be the key; a computed dimension or a merged `geo` one would declare the wrong thing even if its name matches. Anything left uncovered becomes a `public: false` scalar dimension, suffixed (`id_pk`) if the obvious name is taken. |
| `dataset.unique_keys` | `meta.ossie.unique_keys` | No native Cube slot, so parked — and used as the Cube primary key when the dataset declares none, recorded as `meta.ossie.key_from_unique_keys` so re-import does not hand back a `primary_key` the model never declared. Cube refuses a cube that declares a join without one, and several source formats have no primary-key concept: a Databricks metric view does not. A dataset with a relationship and neither is reported, naming Cube's requirement, since nothing can be invented. |
| field | `dimensions[]` entry | Export: a name that is not a valid Cube identifier is sanitized; a case-insensitive collision is an error, never a silent merge. |
| `field.expression` | dimension `sql` | Dataset-scoped, so `{CUBE}.col` <-> `col`. Export emits `{CUBE.member}` for a declared member and `{CUBE}.column` for a raw column — including every bare column in a **computed** expression (`c_first_name \|\| ' ' \|\| c_last_name` → `{CUBE}.c_first_name \|\| ' ' \|\| {CUBE}.c_last_name`, parser-based): Cube interpolates the sql verbatim into generated queries, so an unqualified column is ambiguous once the cube is joined, and `{CUBE}` is the reference Cube's own documentation recommends. A single-column dimension keeps the bare `sql: column` form Cube models conventionally use. The cube's own name is never spelled out (which would break under `extends`). |
| `field.datatype` | dimension `type` (**required**) | `String`->`string`, `Boolean`->`boolean`, `Date`/`Time`/`DateTime`/`DateTimeTz`->`time`, `Integer`/`Decimal`/`Float`->`number`, `Opaque`->`string`. Import maps back, choosing `Decimal` for `number` -- Cube collapses three Ossie types into one, so any single answer is a guess, and a stated datatype is what another converter can act on. Export parks the exact one in `meta.ossie.datatype`, which import prefers when present, so `Integer` and `Float` still survive a round trip. |
| `field.dimension` | a `dimensions[]` entry | Import always emits the block, because a Cube `dimensions:` entry *is* a dimension and the block's absence is what other converters read as "not one" — the Snowflake converter classifies a field without it as a fact regardless of datatype, so omitting it made every non-time dimension a Cortex Analyst fact. Left empty for a non-time dimension, so the consumer applies the spec's default rather than this converter asserting `is_time: false`. An Ossie field that had *no* block is recorded (`meta.ossie.no_role`) and gets none back: it was a fact and stays one. |
| `field.dimension.is_time` | `type: time` | Import sets `is_time: true` for a time dimension. A field carrying `is_time` but *no* `datatype` records the absence (`meta.ossie.untyped`), since the spec says not to infer a scalar type from `is_time` alone — otherwise `type: time` would come back asserting `DateTime`. |
| `field.label` / `description` | dimension `title` / `description` | |
| `field.ai_context.instructions` | dimension `meta.ai_context` | Cube's documented AI-only context field. |
| `field.expression` (`CASE WHEN …`) | dimension `case` | A Cube `case` dimension carries conditions instead of `sql` (Cube rejects both together), so it has no column to name. It maps to a real Ossie `CASE WHEN … THEN … ELSE … END`: a string `label` becomes a SQL literal, the `{sql: …}` form becomes that expression. The `case` block still rides in the stash, so export restores the Cube form exactly and drops the generated `sql`. |
| — | `type: switch` dimension | Maps to `String` like an ordinary dimension, and `String` maps back to `string` -- so the Cube type is recorded in the stash, or the dimension would return as a plain string one carrying an orphaned `case` block. |
| — | `sub_query: true` dimension | The sql references a *measure* through a correlated subquery, which an Ossie field expression has no form for. Emitting the flattened reference claimed a column no dataset has — text that reads as valid SQL and computes nothing anywhere — so the dimension is **parked whole** with a `PARKED_IN_META` issue (the same protocol `switch` dimensions use) and restored verbatim at its position. The aggregate itself still reaches the model: the referenced measure is hoisted as a metric like any other. |
| `metric.datatype` | `meta.ossie.datatype` | Cube has no field for a measure's result type. Import infers one for the count family (whose result type does not depend on the operand) and reads a parked one otherwise, so a `Decimal` sum survives. |
| several relationships between the same two datasets | — | **Refused.** A cube's `joins` are keyed by target, so Cube holds one join per target; emitting two does not fail but silently keeps the last, and every query through the lost relationship then joins on the surviving predicate. Model the second path as its own dataset. |
| relationship `custom_extensions` | cube `meta.ossie.join_extensions` | A Cube join entry takes only name/sql/relationship, so a relationship's foreign-vendor extensions ride on the declaring cube keyed by join target. |
| — | `type: geo` dimension | An Ossie field holds one expression and a geo dimension has two, so it **splits** into `<name>_latitude` / `<name>_longitude` (`Float`). Reconstruction data rides on the latitude half. See [Geo dimensions](#geo-dimensions). |
| relationship | `joins[]` on a cube | `many_to_one` on cube A -> `from: A`(many), `to: B`(one). `one_to_many` is flipped so Ossie's `from` is the many side; the declared side and type are stashed so export restores the original. |
| `from_columns` / `to_columns` | join `sql` | Only an AND-chain of equalities mapping to **physical columns of that dataset** converts. `{CUBE}.user_id` is already one; `{CUBE.user_key}` names a *member*, so it resolves to the column that member reads (`user_id`), following a chain of member references to its end with cycle detection. Everything else preserves the whole join verbatim in the stash rather than describing it wrongly: a member reading an expression (`CONCAT(...)`), a `case`/`switch` dimension (which reads no column at all), an alias belonging to another cube (`{users}.region_id`), or a clause that is not a two-column equality. |
| metric | `measures[]` on the cube its expression references | Import hoists cube-scoped measures to the model level, qualifying a colliding name as `<cube>__<measure>` and stashing the original name. The owning cube is stashed only when export would not derive it — from the sole cube the expression's dataset references, metric references and attributed fields point at, or (when they span several) from the model's base cube, the FK sink a generated view is rooted at. Collision is judged on the **normalized** identifier, since Ossie's are case-insensitive: `orders.revenue` and `users.Revenue` are one name in the model-level namespace and both get qualified. The emitted name keeps its original spelling. |
| `SUM`/`AVG`/`MIN`/`MAX(x)` | `type: sum`/`avg`/`min`/`max` + `sql` | |
| `COUNT(DISTINCT x)` | `type: count_distinct` | |
| `APPROX_COUNT_DISTINCT(x)` | `type: count_distinct_approx` | Cube resolves the warehouse-specific function itself. |
| `COUNT(DISTINCT <pk>)` | bare `type: count` | See [Fan-out](#fan-out) -- the primary key is load-bearing here. |
| one aggregate inside a larger expression | `type: number` (calculated) | Deliberately not decomposed: Cube applies its row-multiplication correction to a calculated measure just as it does to a structured one. `SUM({CUBE}.amount) / 100` and the same split into a hidden `type: sum` plus a ratio generate *identical* SQL under fan-out. Splitting would add a hidden member and buy nothing. |
| several aggregates **reading different cubes** | one `public: false` measure per aggregate + a `type: number` measure referencing them | Each part is declared on the cube its own operand reads, so Cube corrects row multiplication per aggregate rather than once for the whole expression. The parts carry `meta.ossie.part_of`, and import skips them and inlines their SQL back through the references -- recovering the original expression exactly. A Cube-authored *inline* cross-cube composite is **normalized** into this shape on its first round trip (a documented normalization: the decomposed form is the fan-out-correct one, and the second cycle is a fixed point) -- so it carries no stash, rather than a rendered copy of its own SQL. A composite whose aggregates all read one cube is *not* split: hidden parts would buy nothing there, and it round-trips verbatim. |
| `{other_measure}` reference | metric reference by name | The expression language lists **Metric references** among its supported constructs: a bare identifier in a model-level metric expression resolves in the metric namespace. So `{total_amount} / {count}` becomes `total_amount / orders__count` — the referenced metrics' *Ossie names* (qualified where the measure name collides) — and export renders a bare metric name back as `{measure}` on the same cube or `{cube.measure}` across cubes. Inlining instead rendered a copy of every referenced definition into the dependent, which is the metric drift a shared model exists to prevent. Because bare identifiers are metric references at the model level, import qualifies raw columns in measure SQL (`SUM(amount * 2)` → `SUM(orders.amount * 2)`), parser-based. Inlining still happens where a reference cannot: a generated decomposition part (which produces no metric), a windowed dependency (both park), or a metric whose name cannot stand as a bare identifier (a SQL keyword) — the original spelling then rides in the stash. A reference cycle is refused in both directions, as Cube itself refuses it. |
| anything else | `type: number` (calculated) | The whole expression rides as the measure's `sql`, regenerated -- not stashed -- whenever every reference is spelled the way export re-emits it (which includes cross-cube member references such as `{customer.c_customer_sk}`). |
| `AGG(CASE WHEN (…) THEN … END)` | measure `filters` | Folded into `CASE WHEN … THEN … END` inside the aggregate, exactly as Cube's own `applyMeasureFilters` renders it — and **unfolded back** into structured `filters` on export, so a filtered measure travels with *no stash at all*. Only the exact canonical fold unfolds, verified by refolding; a hand-written CASE with an ELSE, unparenthesized conditions, or an operand that is itself a CASE stays one expression (and in the last case the original `sql`/`filters` spellings ride in the stash, since the fold is not invertible there). |
| declared `type` the expression would not regenerate | `type` stash entry | A `type: number` measure whose sql is a single aggregate, or a `count_distinct` declared over the primary key, computes the same value as another Cube spelling — classification would emit that other spelling, so the declared type is recorded and the measure comes back written the way it was written. |
| Cube-only measure keys (`format`, `drill_members`, `public`, …) | flat stash entries | The same protocol dimensions use. Import used to stash a *copy of the whole measure* whenever any extra key was present, which duplicated the `sql`/`type`/`filters` the expression already carries; now only the keys the expression cannot carry ride along. |
| `metric.datatype` | — | Import emits `Integer` for the count family, whose result type Cube does know, and reads a parked one otherwise. |
| `metric.description` / `ai_context` | measure `description` / `meta.ai_context` | |
| `custom_extensions[CUBE]` | everything Cube-only | Import stashes; export restores -- keeping `Cube -> Ossie -> Cube` lossless. |
| foreign-vendor `custom_extensions` | `meta.ossie.custom_extensions` | Parked so a multi-vendor Ossie model survives the round trip. |

**Stashed on import** (and restored on export): the views verbatim (minus the
natively mapped description/AI context) -- unless the sole view is exactly the one
export generates for a hand-authored model, which is derivable by construction and
therefore regenerated rather than recorded -- the mapped view's identity, original file
paths, cube extras (`title`, `sql_alias`, `data_source`, `public`, `refresh_key`,
`segments`, `pre_aggregations`, `hierarchies`, `access_policy`, `calendar`, ...),
dimension extras (`format`, `currency`, `granularities`, `case`, `order`,
`aliases`, `meta`, ...), dimensions with no Ossie form parked whole at their
positions (`switch`, `sub_query`), measure extras (flat, same protocol) plus any
`sql`/`filters`/`type` spelling regeneration would not reproduce, joins with no
Ossie form, Jinja-templated members, and files with no Ossie form (`.js`/`.ts`
models, non-model YAML).

**Identifier case**: Ossie regular (unquoted) identifiers are case-insensitive — the
core spec's *normalized* form upper-cases them and strips quotes from quoted ones — so
`orders.AMOUNT` addresses the field `amount`. Lookups use that form, and what is
emitted is the canonical **Cube** spelling (for the target cube's members too, not just
its own), because Cube's own member resolution *is* case-sensitive. Matching exactly, as
this converter first did, emitted `{CUBE}.AMOUNT`: a raw column that bypasses the
member's expression, so a metric silently aggregated the wrong thing.

A **quoted** identifier is a name, not a string literal, so it is parsed rather than
skipped — and the spec's table decides what it matches: `orders."AMOUNT"` is the field
`amount` (force-matched to the normalized case), while `orders."Amount"` is not, and
stays a raw quoted column.

**Expression dialects**: Cube SQL is the SQL of the model's data source, and the
Ossie dialect enum has no `CUBE` entry -- so import emits `ANSI_SQL`, and export
prefers `ANSI_SQL` with `--dialect` prepending a warehouse dialect (e.g.
`SNOWFLAKE` for a Snowflake-backed Cube model).

Failing both, export falls back to the **first** dialect on offer that is warehouse SQL
(`SNOWFLAKE`, `DATABRICKS`, `BIGQUERY`), records which one in `meta.ossie.dialect`, and
reports it. The record matters: without it re-import would label vendor-specific SQL as
`ANSI_SQL` and mislead the next converter.

Cube holds one `sql` per member, so an expression offering **several** dialects cannot
keep its alternatives natively — the whole expression object is parked and restored, since
nothing less brings them back. This is what
makes another converter's output usable: everything the Databricks converter emits is
`DATABRICKS` with no ANSI alternative, and requiring ANSI dropped every field and metric
— producing an *empty* Cube model, which Cube compiles, so nothing downstream noticed.
`MDX`, `TABLEAU` and `MAQL` are query or calculation languages rather than warehouse SQL,
so those still drop.

**Braces are escaped in free text.** Cube compiles *every* string in a YAML model as
a Python f-string (`f"<sql>"` in `YamlCompiler`; only the handful of boolean-ish keys
in the compiler's `nonStringFields` are exempt), so an unescaped `{` in a description,
an AI context, or a parked JSON blob is read as an interpolation and **the whole model
fails to compile**. Export writes `\{` / `\}`, which is Cube's escape for a literal
brace; import undoes it. Content restored from a Cube stash is left byte-identical --
it was written for Cube already. Only strings sourced from Ossie are escaped.

**String literals** are handled asymmetrically, on purpose. Cube compiles a YAML
`sql` value as a Python f-string (`f"<sql>"` in `YamlCompiler`), so `{CUBE}.col`
interpolates *anywhere* in the value -- SQL's own quotes mean nothing to it. So on
import a reference inside a literal is a real reference and is translated, while on
export nothing is rewritten inside a literal: emitting `{CUBE.col}` there would make
Cube replace the literal's own text with a column reference. The same rule decides
which dataset a metric belongs to, so a name mentioned only inside a literal does not
attribute the metric or make it look cross-dataset.

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
| `number` (calculated) containing one of those | the expression verbatim | **No** — and judged on the *resolved expression*, not the measure type: `SUM({CUBE}.ltv) / 100` is a `number` measure whose value is still a sum. |

Safety is judged on the **fully inlined expression, per aggregate, per dataset** — not
on the measure's Cube type, not on the emitted expression (whose metric references hide
the aggregates they stand for), and not on the cube it is declared on. All three
shortcuts were wrong: a calculated `type: number` measure's type says nothing about the
aggregates inside it, and the cube a measure is declared on is not necessarily the one
an aggregate inside it *reads*. `SUM(users.ltv) / SUM(orders.amount)` sits on `orders`
while `users` is the fanned-out side. The idempotent set is an **allowlist** — `MIN`, `MAX`, `APPROX_COUNT_DISTINCT`,
`BOOL_OR`/`BOOL_AND`/`BIT_OR`/`BIT_AND`, and *any* aggregate over a `DISTINCT` set (which
collapses duplicates before the aggregate sees them, so `SUM(DISTINCT x)` is as safe as
`COUNT(DISTINCT x)`) — because the set of aggregate functions is open-ended, and listing
the unsafe ones silently declared `STDDEV`, `MEDIAN` and `ARRAY_AGG` safe.

Attribution walks the parse tree rather than matching aggregate names in text, and counts
three shapes as one aggregate: an ordinary call, an *ordered-set* one (`PERCENTILE_CONT(…)
WITHIN GROUP (ORDER BY x)`, whose value-bearing column sits on the wrapper), and a call
SQL parsing does not model at all (`LISTAGG`, `APPROX_PERCENTILE`). The last of those may
equally be a scalar UDF, so treating it as an aggregate over-reports — the cheaper error,
since the default is to warn rather than refuse. Qualified and unqualified operands are
counted independently, because one aggregate can read both.

The TPC-DS fixture carries a real example: `store_productivity` is
`SUM(store_sales.ss_ext_sales_price) / NULLIF(SUM(store.s_number_employees), 0)`, and
summing a `store` column across a fanning join inflates it. Cube corrects that at query
time; a static expression cannot, so it is reported.

Only the last row is at risk, and only when the dataset the aggregate reads is the `to`
(one) side of a relationship in the model. The converter computes that from the Ossie
graph and
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

Going the other way, an Ossie metric combining several aggregates **that read
different cubes** is **decomposed** rather than emitted as one calculated measure,
so Cube's correction applies to each aggregate on its own cube (a composite whose
aggregates all read one cube stays a single calculated measure -- splitting it
would key the correction on the same cube either way):

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

Export merges the halves back into the single geo dimension, so the round trip is
exact. The stash records only `of` and `part` -- the structure Ossie cannot carry.
The half's SQL rides along only when the field's expression would not regenerate it
(a raw column such as `{CUBE}.lat` is exactly what the expression already says).

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
— segments, pre-aggregations, hierarchies, view curation, geo reconstruction, and
spellings regeneration would not reproduce — and everything derivable from the
document itself (a measure's `sql`, `type` and `filters`, the cube a single-dataset
metric belongs on) is derived rather than recorded. On the TPC-DS model that is 2
stash entries rather than 41, and 2 Databricks warnings rather than 32.

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

### Measuring it

Both claims above are measurements, so they are reproducible:

```bash
uv run tools/interop_matrix.py                       # the committed TPC-DS fixture
uv run tools/interop_matrix.py path/to/cube/model    # any Cube model directory
uv run tools/interop_matrix.py --spokes omni --keep  # one spoke, keep its output
```

It converts a Cube model to Ossie, checks that intermediate against the repo's own
`validation/validate.py`, then hands it to every other converter and reports what
each made of it:

```
model:  converters/cube/tests/fixtures/tpcds_cube
Ossie:  539 lines, 7 CUBE stash entries
issues: 5x CUBE_LEVEL_AI_CONTEXT_INERT
spec:   valid (validation/validate.py)

spoke        result  warns  foreign   note
----------------------------------------------------------------------------
databricks   OK         22        2
dbt          FAIL        0        0   AttributeError: 'PydanticSemanticManifes
gooddata     OK          0        0
gsf          OK          0        0
honeydew     OK          0        0
omni         OK         15        7
orionbelt    OK          2        0
snowflake    OK          7        7
wisdom       OK         47        7
polaris      --                       Java converter, needs Maven
salesforce   --                       Java converter, needs Maven
```

`foreign` counts warnings that name a `custom_extensions` vendor — the cost this
converter imposes on the others by stashing, and the number to watch when deciding
whether something belongs in a stash at all. The dbt `FAIL` is unrelated to this
converter: its CLI crashes on every input, including this repo's own examples
([#296](https://github.com/apache/ossie/issues/296)).

Each spoke runs in its own `uv` environment, so the first run resolves that
converter's dependencies; the script is stdlib-only and needs none of its own. Nothing
about it is Cube-specific except the first hop — if it is useful repo-wide it belongs
somewhere like `compliance/`, which is a question for `dev@`.

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
- two members of one cube share a name, or an Ossie field and metric map to the same
  Cube member -- Cube keeps one namespace per cube for dimensions, measures and
  segments alike ("orders cube: revenue defined more than once"), and emitting the
  clash produced a model Cube refuses to compile and an Ossie document the spec's own
  validator rejects for a duplicate field name;
- a stashed file path is absolute or escapes the output directory: the stash is part
  of the input document, so a path in it is untrusted;
- a stashed extra file would overwrite a generated cube or view file: those restore
  verbatim, so one landing on a generated path would replace a converted model with
  arbitrary text;
- a cube uses `extends` -- resolving it means reproducing Cube's definition-merge
  semantics exactly, so it is refused rather than half-applied;
- a bare `type: count` measure's cube declares no primary key;
- a join names a cube that is not in the model, or an unknown `relationship`;
- a measure has an unknown `type`, or a measure reference cycle;
- two cubes, two views, or two derived metric names collide;
- a dimension has an unknown `type`, or a `geo` dimension is missing
  `latitude.sql` / `longitude.sql`;
- the model carries foreign-vendor `custom_extensions` but no view is mapped, so
  there is nowhere to park them (re-import with `--view <name>`). The model's own
  name, description and AI context are carried on a cube in this case; foreign
  extensions are not, because import restores those only from the mapped view --
  so this refuses loudly rather than dropping them;
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

Example-based unit tests per direction, CLI behavior tests, fixture round-trip tests
(including the [TPC-DS model](../../examples/tpcds_semantic_model.yaml) the converter
guide asks for as a baseline), a **feature matrix** of one fixture per Cube data-model
feature, core-spec validation of every emitted Ossie document, and Hypothesis
property-based round-trip tests **from both ends** -- which fall back to a seeded
sweep when `hypothesis` is unavailable, so the properties still run.

Generating from Cube only proves things about models that came *out* of a Cube file, and
therefore carry a stash. A hand-authored Ossie model has none, so every key the exporter
writes is one it chose rather than restored -- which is the harder direction, and where
review findings kept landing. So there is a second generator for that direction, drawing
composite metrics (the decomposition path), mixed-case and quoted references, computed
fields, and join keys in all three reference forms. It asserts the generated document is
spec-valid, that `Ossie -> Cube -> Ossie` preserves every metric and field expression
modulo the identifier-case canonicalization, and -- when a Cube checkout is available --
that Cube compiles the export.

It draws the shapes a converter's output has and hand-written test models do not: a
warehouse dialect with no ANSI alternative, `unique_keys` in place of `primary_key`, and
fields with no `dimension` role. Each is a place where export has to make a choice Cube
requires and then be able to undo it, and the property compares dialects, keys and roles
rather than expressions alone -- reverting any one of those three provenance records fails
it. It found a defect on its first run: a generated view over
an ordinary star schema, where the fact's `users_id` foreign key collides with `users.id`
once prefixed.

`tests/fixtures/features/` holds the feature matrix: `case`/`switch` dimensions,
custom granularities, presentation and masking metadata, measure variants
(`rolling_window`, `multi_stage`, `time_shift`, filters, `drill_members`),
hierarchies, segments, pre-aggregations, access policies, view curation, `sub_query`
dimensions and a computed primary key. Each fixture is a *valid Cube model* — verified
by compiling it — and each is asked the same four questions: does it convert, is the
Ossie spec-valid, does `Cube -> Ossie -> Cube` reproduce it, does Cube still compile
the result. Adding a feature means adding a fixture; the four assertions come for
free. The layout follows Cube's own suite, which keeps a fixture per feature.

### Gates beyond the assertions

Two checks the YAML assertions cannot replace, both wired into `pytest`:

**The spec's own validator** runs over every Ossie document the suite produces —
including the ones Hypothesis generates — not just the committed fixtures. It checks
what a field-level assertion structurally cannot: unique names across the document,
relationship references that resolve, and every expression parseable as SQL. It is
imported in-process from `validation/validate.py`, so it costs nothing per document.

**Cube itself** compiles every fixture and every converted model:

```bash
OSSIE_CUBE_REPO=~/src/cube uv run pytest          # runs the compile gate too
OSSIE_CUBE_REPO=~/src/cube node tools/cube_compile.js model/cubes/*.yml
```

This is the only check that can answer "would Cube load this?", and it earns its
keep: Cube compiles every string in a model as a Python f-string, resolves every
member reference, and enforces one member namespace per cube — so a model can
round-trip through Ossie byte-for-byte and still be one Cube refuses. Four defects
were found by asking, including an exported model that failed to compile at all and a
generated view whose `id` members collided. It needs a built Cube checkout and skips
without one, so it gates local and release runs rather than CI.

`tools/interop_matrix.py` checks the other half of the job — whether the Ossie this
converter emits is any use to the other spokes. It is not part of `pytest`, because
it drives the other converters' environments rather than this one's. See
[Measuring it](#measuring-it).

## Future effort

Both the Apache Ossie specification and Cube's data model are still evolving. As
either side adds or changes fields, this converter will be updated to track them.
Known next steps: offline `extends` resolution, `.js`/`.ts` model support (which
needs Cube's own transpiler, so most likely a Cube-side exporter feeding this
converter), and a first-class Ossie representation for measure additivity so the
fan-out caveat can be recorded in the model instead of an issue log.
