# Limitations and design tradeoffs

What `converters/sigma` does not map onto a portable Ossie concept, and why. Every
item here is reported at runtime as a `ConverterIssue`, never dropped silently.

## Presentation and governance state is preserved, not modeled

A Sigma table element carries `filters`, `folders`, `order`, `sort`, `summary`,
`groupings`, `columnSecurities`, `visibleAsSource`, per-column `hidden`, and per-metric
`isHighlighted`/`format`/`timeline`. None of these describe the *shape* of a semantic
model — they describe how Sigma displays it and who may see it — and Ossie has no
equivalent for any of them.

All of it is preserved verbatim under a `native` key in the owning object's
`custom_extensions` (`vendor_name: SIGMA`) and restored unchanged on export, matching
how the Databricks, Omni, and Orion Belt converters handle vendor-only features. The
residue is captured **by subtraction** — everything the converter does not explicitly
map — so a future `schemaVersion` that adds fields still round-trips rather than
silently losing them. Element `filters` additionally raise `FILTER_NOT_MODELED`.

## Only `kind: table` elements are modeled

The data model spec defines table elements; the API docs list input tables, Python
elements, UI elements, and custom functions as unsupported programmatically. Any
element with another `kind` is preserved verbatim at the model level under
`non_table_elements` with an `UNSUPPORTED_ELEMENT_KIND` issue. This is a defensive
path, not an expected one.

## Non-warehouse-table sources have no `OssieDataset.source`

`source.kind` may be `warehouse-table`, `sql`, `table`, `data-model`, `join`, or
`union`. Only the first is a `database.schema.table` location, which is what
`OssieDataset.source` is defined to hold. The other five get a readable marker
(`sql:<connectionId>`, `join:<elementId>`, ...) plus a `DERIVED_ELEMENT_NOT_MODELED`
issue; the full native `source` block lives in `custom_extensions`, so export
reproduces it exactly. An Ossie document that never came from Sigma can only ever
produce a `warehouse-table` source, since that is all a location string implies.

## Join keys use two addressing schemes

Sigma addresses a relationship key either by the element's own column id or by a raw
`inode-<file>/<PHYSICAL_COLUMN>` reference straight to the warehouse column, bypassing
the modeled column list. The converter resolves both to a modeled field name where it
can, records `RELATIONSHIP_COLUMN_UNRESOLVED` where it cannot, and **always** keeps the
raw `keys` in `custom_extensions`, so Sigma → Ossie → Sigma is exact either way.

Unsolved: a document authored by another tool has no raw keys to fall back on, so
export must synthesize key ids from field names. That works when every joined field is
a modeled column, but cannot recreate a key pointing at a physical column the element
never redefined.

A raw physical reference resolves case-insensitively against the element's own plain
columns (including ones self-qualified with the element's own physical table name,
e.g. `[EVENTS/ORG_ID]` on the `EVENTS` element), but not against a column qualified
with a genuinely different table name — that is a cross-table reference, not a
physical column of this element. Two columns whose names differ only by case (e.g.
`[amount]` and `[Amount]`) make the reference ambiguous; it is treated as unresolved
rather than silently guessing.

## Every Sigma object needs an id

A table element, column, metric, or relationship missing an `id` is dropped from the
conversion with a `MISSING_ID` issue rather than given a synthetic one: the Sigma Data
Model API requires every one of these to have an id anyway, and other parts of the
same spec may reference an id this converter never sees the referencing side of.

## Formula coverage is bounded by what Sigma puts in the formula

`ossie_sigma.sigma_formula` parses Sigma's formula language and translates it through a
sqlglot expression tree, covering nested calls, all operators, literals, and ~30
functions across aggregation, conditional, string, and date categories.

Table calculations (`RunningSum`, `Rank`, `Lag`, ...) resolve their partition/order
context from UI configuration rather than from arguments, so nothing in the formula
string can produce correct SQL. These are reported `EXPRESSION_NOT_TRANSLATABLE` and
carry a `SIGMA` dialect entry only. Every formula, translatable or not, is preserved
verbatim in that `SIGMA` entry, so nothing is ever lost on the way in.

Because the intermediate representation is a sqlglot tree rather than SQL text,
emitting a warehouse dialect instead of ANSI is a `dialect=` argument
(`sigma_formula.to_sql`). The converter currently emits `ANSI_SQL` only: Sigma formulas
are warehouse-agnostic, so the spec gives no signal about which vendor dialect would be
more useful, and the table-calculation gap above is unaffected either way.

## Untranslatable expressions are omitted on export, not approximated

`formula` is required on every Sigma column and metric, and the data model API
validates the whole document before applying any of it — so one placeholder formula
fails the entire upload, not one field. When neither a `SIGMA` dialect entry nor a
translatable `ANSI_SQL` one is available, the column or metric is **omitted** with an
`EXPRESSION_NOT_TRANSLATABLE` issue naming it.

## Cross-dataset metrics have no Sigma equivalent

A Sigma metric is scoped to exactly one element; an `OssieMetric` is model-level and may
span datasets via relationships. Sigma → Ossie always promotes cleanly (the owning
`element_id` is preserved). Ossie → Sigma places a metric by its preserved
`element_id`, or, failing that, by the single dataset its ANSI SQL unambiguously
qualifies. A metric that references several datasets or none is dropped with
`CROSS_DATASET_METRIC_DROPPED`.

## Column formats carry only a coarse datatype

The spec has no column datatype — only a display `format`, with two documented kinds,
`number` and `date`. So Sigma → Ossie can infer no more than `Decimal`/`DateTime`, any
other kind becomes `Opaque` (`OPAQUE_DATATYPE`), and a column with no format correctly
gets no `datatype` at all. Ossie → Sigma emits only those two kinds; `String`,
`Boolean`, `Time`, and `Opaque` produce no `format` key, because an invented `kind`
would be rejected for the whole document. The full native format object is always
preserved, so display detail (`formatString`, `currencySymbol`, ...) survives.

## Synthesized ids

Sigma element/column/relationship ids are load-bearing — controls, other data models,
and materializations reference them — so the converter never invents an id for an
object that has one. Native ids ride in `custom_extensions` and are reused verbatim.
Objects originating outside Sigma get a `uuid5` of a fixed namespace plus their
dataset/field path: deterministic across runs, processes, and machines (pinned by
`test_synthesized_ids_are_stable_across_processes`), but not pre-registered with
Sigma's backend.

## One semantic model per document

Sigma data models are single models; `OssieDocument.semantic_model` is a list. Only
`semantic_model[0]` is converted, with `EXTRA_MODEL_DROPPED` naming how many were
dropped. An empty `semantic_model` list has no model to convert at all; rather than
raising an opaque `IndexError`, `OssieToSigmaConverter` raises a `ConverterError`.
