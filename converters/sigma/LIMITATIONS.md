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

# Limitations, design tradeoffs, and testing strategy

This document is a deliberately honest accounting of what `converters/sigma` does not
(yet) handle faithfully, why, and what a general-purpose fix would look like versus a
Sigma-specific workaround. It also documents how the converter is tested and gives an
assessment of how this contribution is likely to be received upstream.

## 1. Controls and workbook-level filters are not modeled

Sigma data models can contain `kind: control` elements (date-range pickers, dropdown
filters, etc.) that other parts of a workbook reference. A control has no analog in
the OSI core spec — it isn't a dataset, field, relationship, or metric; it's a
*presentation-layer* input that a downstream Sigma workbook wires to one or more
columns via `filters: [{columnId, source: {elementId}}]`.

**What this converter does:** preserves every control element verbatim, byte-for-byte,
in a model-level `custom_extensions` entry (`vendor_name: SIGMA`, keyed under
`non_table_elements`, alongside the page it lived on). `osi-to-sigma` restores it
unchanged. A `ConverterIssue(CONTROL_ELEMENT_NOT_MODELED)` is always recorded so
callers know a control was round-tripped opaquely rather than actually converted.

**Why not model it in OSI:** a control is fundamentally about Sigma's own UI
(what widget renders, what workbook pages it applies to) — projecting it into the
portable spec would mean adding Sigma-specific concepts to a vendor-neutral format,
which runs against the stated purpose of OSI ("the general spirit of Open Semantic
Interchange should be maintained rather than hacking Sigma-specific functionality").
The same reasoning applies to Sigma's **named/static element filters**
(`element.filters[]`, distinct from control filters) — these are preserved verbatim in
each dataset's `custom_extensions` with a `FILTER_NOT_MODELED` issue, for the same
reason.

**What a real fix looks like:** this is a job for a **Sigma-specific API layer above
the converter**, not the converter itself — e.g. a small adapter that, after an
`osi-to-sigma` conversion, re-applies any previously-captured controls/filters via
Sigma's own workbook/control API calls (which this converter does not call — it only
produces/consumes the data model spec document). That adapter is legitimately
Sigma-specific glue code and does not belong in a hub-and-spoke OSI converter.

## 2. Calculated fields on tables: included, deliberately

Sigma lets every table element define calculated columns (formulas referencing other
columns on the same element) alongside physical passthrough columns. This converter
maps **all** of them — physical and calculated alike — to `OSIField`, using the same
disambiguation OSI already has for any dataset: an `OSIField.expression` is *just an
expression*, whether it happens to be `[TABLE/COL]` (a passthrough) or
`If([Status] = "closed", 1, 0)` (a calculation). There's no reason to exclude
calculated fields from a dataset's `fields[]` — the OSI core spec draws no such
distinction, and doing so would need an invented Sigma-only concept ("calculated vs.
physical field") that has no home in the core spec. Sigma's *metrics* (element-scoped
aggregate formulas) are a different, real distinction — the core spec already has a
separate `OSIMetric` concept for exactly this, and Sigma's metrics map onto it (see
§4 for the promotion nuance).

## 3. Stable ids: preserved-by-default, synthesized-as-fallback

Sigma element, column, and relationship ids are load-bearing — other objects
(controls, other data models' relationships/materializations, deployment policies)
reference them by id, so an export that minted new ids for previously-existing objects
would silently break those references on re-import.

**What this converter does:** never invents an id for anything that already has one.
Every id-bearing Sigma object's native id is preserved verbatim inside that object's
`custom_extensions` (e.g. `{"id": "colOrderId", ...}` on the corresponding `OSIField`),
and `osi-to-sigma` reuses it unchanged. Ids are synthesized — as a deterministic
`uuid5` of a fixed namespace plus the object's dataset/field path — **only** for
objects with no preserved Sigma id, i.e. ones that originate purely in an Ossie
document that was never round-tripped through Sigma. This makes `osi-to-sigma`
deterministic and idempotent (verified by `test_ids_are_deterministic_across_repeated_conversions`),
but a synthesized id is *not* a real Sigma id in the sense of being pre-registered
with Sigma's backend — the first time such a document is actually loaded into Sigma
(via `sigcli data-models spec create`), Sigma's own API, not this converter, is the
source of truth for whether that id is accepted.

## 4. Relationship (join) resolution has a real, documented gap

Sigma relationships address join-key columns in **two different ways** within the
same `keys[]` array: either by the owning element's own column id, or by a raw
`inode-<file>/<PHYSICAL_COLUMN_NAME>` reference straight to the underlying warehouse
table/column — bypassing the element's modeled column list entirely. (This second
form shows up whenever a relationship key is a physical column that was never
explicitly redefined as one of the element's own `columns[]` entries.)

**What this converter does:** resolves both forms to a modeled Ossie field name where
possible (matching the physical column name against each element's own column
formulas), and **always preserves the raw, unresolved `sourceColumnId`/
`targetColumnId` values in the relationship's `custom_extensions`** regardless of
whether resolution succeeded. This means round-trip fidelity (Sigma → OSI → Sigma) is
guaranteed even when the human-readable `from_columns`/`to_columns` names in the OSI
document are only best-effort. When resolution fails, a
`RELATIONSHIP_COLUMN_UNRESOLVED` issue is recorded rather than silently guessing.

**What's genuinely unsolved:** an Ossie document authored by a *different* tool
(e.g. hand-written, or round-tripped through dbt) that gets converted to Sigma has no
such raw reference to fall back on — `osi-to-sigma` must synthesize
`sourceColumnId`/`targetColumnId` values from the field names alone, via each
element's own modeled columns. This works correctly as long as every joined field
exists as a named column, but Sigma's implicit "every warehouse column is
automatically a column, even a hidden/undefined one" behavior means there could be
edge cases (a foreign-tool document referencing a physical column that was deliberately
never redefined) this converter cannot correctly recreate without more information
than the OSI document contains. This is inherent to Sigma's addressing scheme, not
fixable purely within the converter.

## 5. Derived ("child") elements and custom SQL elements

A Sigma element's `source.kind` can be `warehouse-table` (a physical table) or
something else (e.g. `table`, meaning the element is layered on another element
rather than a warehouse table directly). This converter still creates an `OSIDataset`
for a derived element — with `source` set to a synthetic `element:<parent-id>`
marker rather than a `database.schema.table` string — and flags it with
`DERIVED_ELEMENT_NOT_MODELED`, since OSI's `OSIDataset.source` field is documented as
a physical location string and has no first-class "this dataset is derived from
another dataset" relationship concept. The parent element id is preserved in
`custom_extensions` so `osi-to-sigma` reconstructs the exact original `source` block.

Sigma workbooks can also define **custom SQL elements** (a table backed by a
hand-written SQL query rather than a warehouse table reference or a data-model
formula). This converter has not been exercised against that element shape — Sigma's
public data-model spec endpoint was not observed producing one in the data models
inspected during development. If Sigma represents a custom-SQL element with a
`source.kind` other than the two handled here, it will currently be treated like any
other non-`warehouse-table` source (preserved as a derived element with a
`DERIVED_ELEMENT_NOT_MODELED` issue) rather than mapped to something more precise —
this is a gap to close with real fixture data from a workbook that uses one.

## 6. Formula language coverage: real but intentionally bounded

`ossie_sigma.sigma_formula` implements a genuine tokenizer, recursive-descent parser,
and bidirectional ANSI SQL renderer (not a regex classifier) supporting: nested
function calls at arbitrary depth, all comparison/logical/arithmetic operators,
string/number/boolean literals, and roughly 30 Sigma functions across aggregation,
conditional, string, and date categories (see the module docstring and
`core-spec/expression_language.md`'s Sigma column in the Cross-Reference Tool
Mappings tables for the full list).

**Deliberately out of scope:** Sigma's *table calculation* functions (`RowNumber`,
`Rank`, `RunningSum`, `RunningAvg`, `Lag`, `Lead`, etc.) resolve their partition/order
context from **UI configuration** (which pivot table or chart the calculation is
attached to), not from arguments passed in the formula text. There is no way to
recover that context from the formula string alone, so these are correctly identified
as untranslatable — the original Sigma formula is preserved in the `SIGMA` dialect,
but no `ANSI_SQL` dialect entry is produced. This is not a parser limitation; it's a
structural fact about where Sigma stores that information (outside the formula).

**Every formula, translatable or not, is never lost:** the `SIGMA` dialect entry
always carries the original text verbatim, so nothing is silently dropped —
untranslatable formulas simply don't get a second, `ANSI_SQL`-dialect representation.

## 7. Maximizing ANSI SQL vs. vendor-specific dialects

Per the "maximum set of expressions representable as ANSI SQL" goal, the current
implementation targets `ANSI_SQL` only (no Snowflake/Databricks/BigQuery-specific
translation) because Sigma data models are themselves warehouse-agnostic — a formula
like `Sum([Amount])` means the same thing regardless of which connection the element
points to, so there is no Sigma-side signal indicating which vendor SQL dialect would
be more useful to target. A natural follow-up (out of scope for this PR) would be: for
formulas this converter can't express in ANSI SQL, check the target connection's
warehouse type (Snowflake, BigQuery, Databricks — available on the connection, not
surfaced in the data model spec used here) and add a vendor-specific `OSIDialect`
entry using that warehouse's native date/window function syntax, which would recover
some of the table-calculation functions in §6 for warehouses with native window
function support (though the partition/order-context problem remains — Sigma still
doesn't hand that information to the formula).

## 8. Cross-dataset ("model-level") metrics have no Sigma equivalent

A Sigma metric (`element.metrics[]`) is always scoped to exactly one element. An OSI
`OSIMetric`, by contrast, lives at the model level and may reference multiple
datasets via relationships (e.g. a ratio metric like
`SUM(store_sales.ss_ext_sales_price) / COUNT(DISTINCT customer.c_customer_sk)`).

**Sigma → OSI:** every Sigma metric promotes cleanly to an `OSIMetric`, re-qualified
with its owning element's name, with `element_id` preserved in `custom_extensions`
for exact placement on the way back.

**OSI → Sigma:** for a metric with a preserved `element_id` (round-tripped from
Sigma), placement is exact. For a metric with no such extension (authored elsewhere),
the converter inspects the `ANSI_SQL` expression's column qualifiers and attaches the
metric to the single dataset it unambiguously references — but if the expression
spans more than one dataset (or none), there is **no faithful Sigma representation**,
and the metric is dropped with a `CROSS_DATASET_METRIC_DROPPED` issue rather than
silently attached to an arbitrary dataset or silently discarded without a trace. This
is exercised directly by `test_osi_to_sigma.py::test_foreign_origin_document_synthesizes_valid_spec`
against `examples/tpcds_semantic_model.yaml`, which contains exactly this kind of
metric (`customer_lifetime_value`, `store_productivity`).

## 9. `Opaque` and `custom_extensions` usage discipline

- `datatype: Opaque` is used **only** when a Sigma column's `format.kind` has no
  portable Ossie equivalent (observed example: `variant`) — never as a default or
  fallback for "we didn't bother mapping this." Most Sigma columns carry no `format`
  at all in the data models this converter was developed against, and those fields
  correctly get no `datatype` at all (per the core spec: "omit `datatype` when the
  type is unknown or unspecified"), not `Opaque`.
- Every `custom_extensions` entry this converter writes carries only the minimum
  Sigma-specific data needed for round-trip fidelity (native id, page placement,
  folder/order UI grouping, raw relationship keys, unrecognized column format) — it
  is never used as a dumping ground for data that has a proper OSI home (e.g. a
  column's description goes in `OSIField.description`, not `custom_extensions`).
- The one intentional exception is `non_table_elements` (control/unknown-kind
  elements) at the model level, and the full `folders`/`order` UI-grouping metadata
  per dataset — both are genuinely presentation-layer Sigma concepts with no OSI
  equivalent (see §1), so `custom_extensions` is the correct (and only) home for
  them, not a workaround for something OSI should have modeled instead.

## 10. Multiple semantic models per document

Sigma data models are always a single model; `OSIDocument.semantic_model` is a list.
If given a multi-model Ossie document, only `semantic_model[0]` is converted, and a
`ConverterIssue` is recorded naming how many additional models were dropped, rather
than silently ignoring them or guessing which one the caller meant.

## How this converter is tested

- **Unit tests** (`tests/test_sigma_formula.py`) exercise the formula parser/renderer
  directly: every supported function/operator in both directions, plus the
  untranslatable and unparseable cases, independent of the surrounding converter.
- **Fixture-driven directional tests** (`tests/test_sigma_to_osi.py`,
  `tests/test_osi_to_sigma.py`) use two hand-authored, synthetic (non-proprietary)
  Sigma data model fixtures:
  - `fixtureA_sigma.json` — the common path: two related tables, a composite-free
    relationship, model-level metrics, nested calculated-field formulas, and a
    control element.
  - `fixtureB_sigma.json` — the edge cases: a composite (multi-key) relationship
    mixing both column-id and `inode-`-style physical references, an unrecognized
    column format (→ `Opaque`), an untranslatable table-calculation formula
    (`RunningSum`), and a derived (non-warehouse-table) element.
- **Round-trip tests** (`tests/test_roundtrip.py`) assert both fixtures survive
  Sigma → OSI → Sigma **byte-for-byte** (structurally, modulo key order), including
  through the same YAML serialization boundary the CLI uses, and separately assert
  that Sigma → OSI → Sigma → OSI preserves all portable (non-`custom_extensions`)
  content on the second OSI document.
- **Real-world / foreign-origin coverage** (`test_osi_to_sigma.py::test_foreign_origin_document_synthesizes_valid_spec`)
  runs the reverse converter against `examples/tpcds_semantic_model.yaml` — an Ossie
  document that never touched Sigma — to verify the converter produces a valid,
  useful Sigma spec (synthesized ids, ANSI-SQL-to-Sigma-formula reverse translation,
  correct single-dataset metric placement, correct dropping of genuinely
  cross-dataset metrics) even with no `SIGMA` custom extensions to fall back on.
- **Schema validation**: every fixture's `sigma-to-osi` output was checked against
  `core-spec/osi-schema.json` and `validation/validate.py`'s SQL-syntax checker (both
  of which required small, narrowly-scoped fixes as part of this PR — see below).

What is *not* yet covered: a fixture generated from a real production Sigma data model
(only synthetic fixtures are included, deliberately, to avoid embedding any
organization's proprietary schema/business logic in an open-source repository), and
the custom-SQL-element case noted in §5.

## Small fixes bundled with this PR (not Sigma-specific)

While validating this converter's output against the repo's existing tooling, two
pre-existing gaps were found and fixed, since they block *any* converter from
producing schema-valid output that uses features already present in the pydantic
model:

- `core-spec/osi-schema.json` was missing `dialects`/`vendors` as valid root-level
  `OSIDocument` properties, even though `python/src/ossie/models.py`'s `OSIDocument`
  has defined and exported them since before this PR. Added them.
- `validation/validate.py`'s SQL-syntax checker attempted to parse every dialect's
  expression as SQL, including known non-SQL dialects — but its own
  `SKIP_SQL_VALIDATION` set already excluded `MDX`/`TABLEAU`/`MAQL` for exactly this
  reason. `SIGMA` was missing from that set (understandably, since it didn't exist
  before this PR) and has been added alongside the new dialect.

## Assessment: likelihood of upstream approval

Apache Ossie uses a review-then-commit model (per `CONTRIBUTING.md`): merge requires
at least one committer +1 and no unresolved -1, and any change to `core-spec/` itself
carries a higher bar (dev@ discussion, then a `[VOTE]` thread). This PR is mostly
**not** a core-spec change — it adds a new converter under `converters/sigma/`
following the exact structure, tooling (`uv`), and conventions of the most recently
merged converter (NVIDIA GSF, PR #247) and the most actively maintained one (dbt).
The genuinely core-spec-touching pieces are narrow and precedented:

- Adding `SIGMA` to `OSIDialect`/`OSIVendor` in `python/src/ossie/models.py` and the
  corresponding `core-spec/osi-schema.json` enums — the same kind of addition every
  prior converter needed (`TABLEAU`, `DATABRICKS`, `BIGQUERY`, `WISDOM`, etc. all
  entered the enums this way), not a structural spec change.
  It is worth confirming with the community whether such additions require the
  full dev@/VOTE process or have historically been accepted as ordinary PR review —
  the git history suggests the latter, but this PR does not assume that.
- The `dialects`/`vendors` schema-sync fix and the `validate.py` `SIGMA` addition are
  small, mechanical, and justified independently of Sigma (see above).

Reasonable committer concerns to expect, roughly in order of likely weight:

1. **"Why does the relationship resolution need two addressing schemes?"** — this is
   inherent to Sigma's own data model (§4), not a design choice in this converter,
   but it's the single most complex piece of logic here and the one most likely to
   draw close review.
2. **Formula language coverage as a moving target** — Sigma's formula function list
   isn't formally published as a machine-readable grammar (unlike, say, ANSI SQL's
   own grammar), so a committer may reasonably ask how coverage will be
   validated/extended over time. The test suite and the module docstring's function
   table are the answer, but this is worth calling out explicitly in the PR
   description.
3. **Scope of `custom_extensions` for controls** — stashing whole native elements
   verbatim mirrors the GSF converter's precedent (README §"Fidelity and unavoidable
   losses"), but a reviewer unfamiliar with that precedent might initially read it as
   "hacking around" rather than the documented, intentional choice it is; pointing
   reviewers at this LIMITATIONS.md file and the GSF README directly should resolve
   that quickly.
4. **No real-world fixture** — reasonable, and addressed above; a committer may ask
   for one, which would need to come from a community member willing to contribute a
   sanitized example (this PR intentionally does not include one, to avoid
   embedding any organization's data model in the ASF repository).

Net assessment: **likely mergeable with normal review iteration**, on the strength of
following established converter conventions closely and treating limitations as
first-class, tested, and documented rather than papered over — which is exactly what
`converters/README.md`'s own "Writing a Converter" checklist and round-trip fidelity
principles ask for. The main risk to merge speed is committer bandwidth/review
latency (an ASF-standard risk for any PR, not specific to this one), not a structural
objection to the approach.
