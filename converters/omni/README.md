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

# OSI <-> Omni converter

Bidirectional, offline conversion between an OSI semantic model and
[Omni](https://docs.omni.co/modeling) semantic model files. No Omni connection
required.

An Omni model is a *directory* of YAML files rather than a single document, so
this converter maps one OSI YAML document to/from the Omni model layout:

```
model.yaml                  # model-wide config (restored verbatim from a prior import)
relationships.yaml          # top-level list of join definitions
views/<name>.view.yaml      # one per OSI dataset
topics/<name>.topic.yaml    # one generated topic per OSI model (or restored originals)
```

Import also accepts the layout Omni's API/IDE emits (`omni models yaml-get`,
git sync): view/topic files in per-schema or arbitrary folders
(`DELIGHTED/response.view`), bare `.view`/`.topic` suffixes, and
schema-qualified view names (`delighted__response`) taken from each file's
`# Reference this view as ...` header (falling back to the file's basename).
Original file paths are preserved through a round trip.

- **Export** (`osi-omni export`): OSI -> Omni files. Datasets become views,
  relationships become `relationships.yaml` joins, metrics become measures on
  the view they reference, and the model becomes a topic (rooted at the
  fact/FK-sink dataset, or `--base-view`).
- **Import** (`osi-omni import`): Omni files -> OSI. Omni features OSI has no
  native field for are preserved in `custom_extensions[OMNI]`, so
  **Omni -> OSI -> Omni is lossless**.

On **export** (OSI -> Omni), OSI features with no Omni slot -- `unique_keys`,
relationship `ai_context`, dataset-level `ai_context` synonyms, model-level
`ai_context` synonyms/examples, foreign-vendor `custom_extensions`, fields and
metrics without a usable dialect -- are **dropped with a warning**. On
**import** (Omni -> OSI), Omni-only features (formats, timeframes, hidden/tags,
topic curation, the model file, ...) are instead **preserved** in
`custom_extensions[OMNI]`. Any input that breaks a
[requirement](#requirements) **raises a `ConversionError`** -- the converter
never silently drops a field or produces an invalid result.

## Installation

```bash
pip install osi-omni          # once published to PyPI
# or, from a checkout of this directory:
pip install -e .
```

The only runtime dependency is `PyYAML`. Python 3.9+.

## Usage

### Command line

```bash
osi-omni export -i model.yaml -o omni_model/ [--base-view orders] [--dialect SNOWFLAKE]
osi-omni import -i omni_model/ [-o model.yaml] [--name my_model] [--topic orders]
```

`export` writes the Omni files into the `-o` directory. `import` reads a model
directory (the [local-editor / git layout](https://docs.omni.co/guides/modeling/local-development),
`.yaml`-suffixed or bare `.view`/`.topic` names both work); with no `-o` the
OSI YAML goes to stdout. `--base-view` picks the dataset the generated topic is
rooted at (default: the FK-sink dataset). `--topic` picks which topic's
description/AI context map onto the OSI model when there are several.

### Python API

```python
from osi_omni import convert_osi_to_omni, convert_omni_to_osi

files = convert_osi_to_omni(osi_yaml_str)          # -> {relative filename: YAML str}
osi_yaml = convert_omni_to_osi(files)              # {relative filename: YAML str} -> str
```

## Mapping

Each row maps in both directions; the **Notes** flag where a behavior is
specific to **export** (OSI -> Omni) or **import** (Omni -> OSI).

| OSI | Omni | Notes |
|---|---|---|
| `semantic_model.name` | topic file name | Import: the mapped topic's name (override with `--name`). |
| `model.description` / `ai_context.instructions` | topic `description` / `ai_context` | Import: taken from the sole topic, or `--topic`. |
| dataset | `views/<name>.view.yaml` | Import: a stashed original path (`DELIGHTED/response.view`) is restored on export. |
| `dataset.source` `catalog.schema.table` / `schema.table` | view `catalog` + `schema` + `table_name` | `table_name` left implicit when it matches the file name; a part that is not a plain identifier is double-quoted (`"Omni Views".upload`). |
| `dataset.source` `SELECT ...` | view `sql:` | A SQL-defined view. |
| `dataset.description` / `ai_context.instructions` | view `description` / `ai_context` | |
| `dataset.primary_key` (single) | `primary_key: true` on the matching dimension | Export: a key column no field covers becomes a hidden dimension. |
| `dataset.primary_key` (composite) | view `custom_compound_primary_key_sql` | Import: `${view.field}` entries resolve to plain field names (original list stashed); multiple `primary_key: true` dimensions also form a composite key. |
| field | dimension | Export: an already-valid Omni identifier (incl. `_fivetran_id`, `..._day_`, camelCase) passes through; anything else sanitizes to `[a-z][a-z0-9_]*`. A case-insensitive collision is an error. |
| `field.expression` | dimension `sql` | Export: a bare column named like the field emits `{}` (the schema-layer default); import translates `${field}`/`${view.field}`/`${TABLE}.col` references and stashes the original `sql`. |
| `field.dimension.is_time` | dimension `timeframes` | Export: the Omni default list; import stashes the exact list. |
| `field.label` / `description` | `label` / `description` | |
| `field.ai_context` synonyms / instructions | `synonyms` / `ai_context` | |
| relationship | `relationships.yaml` entry | `from`(many) -> `join_from_view`, `to`(one) -> `join_to_view`, columns -> `on_sql` equi-join. Declared `join_type`/`relationship_type` (even Omni defaults) and any `on_sql` the rebuild would not reproduce (aliases, `and` casing, spacing) are stashed verbatim. |
| `relationship.name` | -- | Regenerated as `<from>_to_<to>` on import (suffixed `_2`, `_3`, ... when several joins share a view pair). |
| metric | measure on the view its expression references (else the base view) | `AGG(view.field)` <-> `aggregate_type` + `sql: ${field}`; `COUNT(*)` <-> `aggregate_type: count`; anything else <-> a raw-`sql` measure. |
| `metric.description` / `ai_context` | measure `description` / `synonyms` / `ai_context` | |
| `custom_extensions[OMNI]` | everything Omni-only | Import stashes; export restores -- keeping `Omni -> OSI -> Omni` lossless. |

**Stashed on import** (and restored on export): the model file (verbatim),
topics (verbatim, minus the natively-mapped description/AI context), original
file paths, view extras (`label`, `hidden`, `tags`, view-level `filters:`,
...), dimension extras (`format`, `group_label`, exact `timeframes`, original
`sql`, ...), present-but-empty metadata (`description: ''`), join extras
(declared `join_type`/`relationship_type`, `reversible`, `where_sql`, aliases,
non-canonical `on_sql`), joins OSI cannot represent (non-equi/range joins,
joins touching a query or extends-only view -- restored at their original
positions), fields/measures whose sql uses Omni template (`{{...}}`) syntax,
non-reconstructible measures (filtered, `percentile`/`list`/`*_distinct_on`,
raw-SQL), and files with no OSI form (query views, extends-only views,
unrecognized files).

**Expression dialects**: Omni SQL is the SQL of the model's database
connection, and the OSI dialect enum has no `OMNI` entry -- so import emits
`ANSI_SQL` expressions, and export prefers `ANSI_SQL` with `--dialect`
prepending a warehouse dialect (e.g. `SNOWFLAKE` for a Snowflake-backed Omni
model). A field/metric with neither is dropped with a warning.

## Requirements

Conversion raises a `ConversionError` (rather than guessing or emitting
something invalid) when an input breaks one of these:

- a dataset `source` has no schema part (Omni views require `schema`);
- the relationship graph gives no unambiguous base view for the generated topic
  (multiple FK sinks or a cycle) and `--base-view` is not given;
- two names sanitize to the same Omni identifier, case-insensitively (never
  silently merged); two view files resolve to the same canonical view name;
- a measure has an unknown `aggregate_type`; an import directory has no
  convertible view files; the input YAML is malformed.

## Notes and limitations

- Exported `on_sql` references columns as `${view.column}`. Omni resolves these
  against the schema layer, which auto-generates a dimension per physical
  column, so the reference is valid even when the OSI model declares no field
  for the column.
- `dimension: {is_time: false}` is equivalent to omitting `dimension` and is
  normalized away on a round trip.
- One generated topic per OSI model; multi-path (aliased) join fan-out and
  Omni query views, extends-only views, non-equi joins, composite topics,
  access grants/filters, and templated filters are stash-and-restore only (no
  OSI semantics).
- A model containing *only* query/extends views (e.g. a pure GA4-export model)
  has nothing to convert and is rejected.
- OSI metric order is regrouped by view on import (order is not semantic).

## Development

```bash
pip install -e ".[dev]"
python3 -m pytest tests/
```

Example-based unit tests plus Hypothesis property-based round-trip tests
(`test_roundtrip_properties.py`, which fall back to a seeded-random sweep if
`hypothesis` is not installed).

## Future effort

Both the OSI specification and Omni's model YAML are still evolving. As either
side adds or changes fields, this converter will be updated to track them --
extending the mapping and coverage in both directions (query views, composite
topics, measure filters as first-class OSI once the spec grows a slot for
them) to keep the conversion current.
