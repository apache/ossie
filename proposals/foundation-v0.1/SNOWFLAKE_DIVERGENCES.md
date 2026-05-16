# Snowflake Divergences — Foundation Design Choices

This document catalogs **intentional Foundation design divergences** from
Snowflake Semantic Views. Each entry captures a place where Snowflake's
behavior is defensible (not a bug) but the Foundation deliberately picks a
different rule, plus the rationale for the choice.

## What this document is

A reference for OSI authors, implementers, and porting tools. Every entry
records:

- The Snowflake behavior, with citation to Snowflake's public docs.
- The OSI Foundation behavior, with citation to the relevant spec section.
- The rationale for the divergence — typically "follow the cross-vendor
  majority" or "preserve a Foundation-only correctness contract."
- Porting consequences in both directions (OSI → Snowflake, Snowflake → OSI).

## What this document is **not**

| Out of scope | Lives in |
|:---|:---|
| Snowflake **bugs** the Foundation prevents (e.g., compound-window-bypasses-`WHERE`) | `Proposed_OSI_Semantics.md` §12.A.2; `docs/ERRATA_ALIGNMENT.md` |
| SQL **surface-syntax** alignment (clause names, parser grammar) | `SQL_INTERFACE.md` §10.6, §12 |
| Snowflake errata catalog with test pointers | `docs/ERRATA_ALIGNMENT.md` |
| Looker / Tableau / dbt-semantic-layer alignment | `Proposed_OSI_Semantics.md` §12.B, §12.C, §12.E (or future divergence docs per vendor) |

A divergence entry here is a **stable** Foundation decision. If a Snowflake
behavior is under debate or might change, it belongs in
`Proposed_OSI_Semantics.md §12.A` as part of the still-evolving
alignment discussion, not here. Promote to this catalog when the decision is
final.

## Relationship to spec sections

- `Proposed_OSI_Semantics.md §12.A.1` ("Convergence Already
  Achieved") — places where OSI and Snowflake produce equivalent results.
  Each entry in this divergence catalog corresponds to a "One known
  divergence" callout inside §12.A.1 or a row in §12.A.2 that has resolved
  toward "defensible design choice, not bug" rather than "Snowflake bug
  Foundation resolves."
- `Proposed_OSI_Semantics.md §12.A.2` ("Differences OSI Would
  Resolve") — Snowflake bugs we explicitly prevent. Different category;
  not catalogued here.

## Identifier scheme

Each divergence has an `SD-NNN` identifier (Snowflake Divergence #N) that's
stable across spec revisions. New entries get the next available number;
never reuse retired numbers. The numbering is independent from the
`D-NNN` conformance-decision IDs in `Proposed_OSI_Semantics.md`
Appendix B.

## Adding a new divergence

When you discover or settle a new divergence:

1. Append a new `SD-NNN` section below.
2. Cite Snowflake's public documentation for the Snowflake behavior.
3. Cite the relevant `Proposed_OSI_Semantics.md` section (with a
   `D-NNN` conformance-decision pointer if one exists).
4. Spell out both porting directions explicitly.
5. Add a "logged: YYYY-MM-DD" marker so we can see when each decision was
   made.
6. If the spec text in §12.A inlines a brief note about this divergence,
   add a cross-reference from §12.A → this entry.

---

## Catalog

### SD-1 — Cross-grain single-step aggregates accepted

**Logged:** 2026-05-12 · **Revised:** 2026-05-12 (added M:N `COUNT(DISTINCT)` divergence) · **Spec anchor:** `Proposed_OSI_Semantics.md` §4.5 form (1), §6.11.3, Appendix B D-020, D-022, D-027 · **Conformance decision:** D-020 (with D-022 / D-027 / §6.11.3 for the M:N case)

**Snowflake behavior.** A metric expression that references a row-level
expression at higher granularity than the metric's home dataset MUST use
nested aggregation. Single-step cross-grain expressions are rejected as
invalid. From the [Snowflake Semantic View validation rules][snowflake-vr],
§"Rules for aggregate-level expressions (metrics)":

> Higher granularity references: When referring to row-level expressions at
> higher granularity, a metric must use nested aggregation. For example,
> `customer.average_order_value` must use `AVG(SUM(orders.o_totalprice))`
> because `orders` is at higher granularity than `customer`.

This applies to all aggregate categories — distributive, algebraic, holistic
— and to all relationship types (`1 : N`, `N : N`).

**OSI Foundation behavior.** A metric expression MAY aggregate a higher-grain
referenced dataset over a `1 : N` edge **single-step**; the interpretation is
**standard SQL semantics** (the engine joins the higher-grain rows through
the relationship path and aggregates them at the query's grain, each
higher-grain row contributing once per output group, satisfying §6.1
Semantic 2). This applies to every aggregate category. The user MAY still
write explicit nested aggregation to obtain the alternative
"per-home-row-first" interpretation; the two forms agree numerically for
distributive aggregates and differ for non-distributive aggregates.

Cross-grain aggregates over an **`N : N`** edge follow `D-026` / `D-027`:
the bridge plan materialises the unique `(measure-home-row, group-key)`
row set and aggregates over it in a single pass, regardless of aggregate
category. Distributive (`SUM`), algebraic (`AVG`, `STDDEV`), and holistic
aggregates (`MEDIAN`, `COUNT(DISTINCT)`) are all accepted bare. This is
the heavy-side-weighted single-step analogue of the `1 : N` rule above.
The "per-home-row-first" interpretation requires the explicit nested
form `AGG(AGG(...))`, which is **deferred** to §10's grain-aware-functions
proposal and currently raises `E_NESTED_AGGREGATION_DEFERRED`.

**Second divergence — every aggregate category over M:N.** Snowflake's
higher-grain nesting rule applies to **every** aggregate category (including
`SUM`, `AVG`, `COUNT(DISTINCT)`) over an N:N edge — a metric like
`groups.distinct_customers = COUNT(DISTINCT customers.id)` over an
`actors ↔ memberships ↔ groups` bridge would have to be written as a
nested form (e.g., `COUNT(DISTINCT(COUNT(DISTINCT customers.id)))` or by
collapsing the inner step into a field on the bridge). The Foundation
accepts the single-step bare form for every category: the §6.8.1 bridge
plan's distinct `(home, group-key)` materialisation is a single-pass
aggregate that is well-defined for every category (per D-027). For
**distributive** and **`COUNT(DISTINCT)`** aggregates the two engines'
plans give the **same number** — the divergence is only in *what the user
is required to write*. For **non-distributive** aggregates (`AVG`,
`MEDIAN`, `STDDEV`) the two engines pick different default interpretations:
the Foundation's bare form is the bridge-dedup answer (heavy-side-weighted,
analogous to the 1:N rule); Snowflake's required nested form is the
per-home-row-first answer. They give different numbers. The Foundation's
per-home-row-first answer requires the nested form `AGG(AGG(...))`, which
is deferred to §10.

**Rationale.** Three of four major BI tools accept single-step cross-grain
with standard-SQL semantics:

- **Looker** — symmetric aggregates make `type: sum/avg/count_distinct/median`
  Just Work across fanout joins ([Looker measure types docs][looker-mt]).
- **Tableau** — relationships use "smart aggregations" where measures
  aggregate to the source table's level of detail then combine via the
  relationship ([Tableau relationships docs][tableau-rel]).
- **dbt-semantic-layer** — MetricFlow auto-joins normalized schemas and
  aggregates at the requested grain ([dbt metrics overview][dbt-metrics]).

Snowflake is the strict outlier. The Foundation follows the majority for
1:N reaches because (a) the single-step interpretation is unambiguous
standard SQL, (b) it matches user expectation from every other major BI
tool, and (c) for distributive aggregates the single-step and two-step
forms are numerically identical, so there is no determinism cost.

**Porting consequences.**

- **OSI → Snowflake.** A model that uses single-step cross-grain aggregates
  needs a mechanical rewrite to add an outer aggregate:
  - `customers.total_orders = SUM(orders.amount)` →
    `customers.total_orders = SUM(SUM(orders.amount))` (semantic-equivalent;
    distributive aggregate, both forms give the same number).
  - `customers.avg_order = AVG(orders.amount)` →
    `customers.avg_order = AVG(SUM(orders.amount))` — but **note**: this is
    **not semantically equivalent**. Snowflake's `AVG(SUM(...))` is the
    per-home-row-first interpretation (average of per-customer totals);
    OSI's single-step `AVG(orders.amount)` is the standard-SQL
    "average of all orders" answer. A porting tool MUST surface this
    choice to the user for every non-distributive cross-grain aggregate.
  - `groups.distinct_customers = COUNT(DISTINCT customers.id)` over an M:N
    bridge ⇒ MUST be expressed in Snowflake's nested-aggregation form (e.g.,
    by pre-defining a field on the bridge that collapses to the
    `(group, customer)` set, then `COUNT(DISTINCT customer_id)` over that
    field). Same number as the OSI single-step form; pure surface rewrite.

- **Snowflake → OSI.** Distributive aggregates (`SUM(SUM(...))`) and
  `COUNT(DISTINCT)` ports unchanged numerically — strip the outer aggregate
  to get the equivalent OSI single-step form, or leave it nested (the OSI
  nested form raises `E_NESTED_AGGREGATION_DEFERRED` until §10, so the
  port-time rewrite is to drop the outer aggregate). Non-distributive
  aggregates (`AVG(AVG(...))`, `MEDIAN(MEDIAN(...))`) over an N:N edge
  ports with a **semantic gap**: the Snowflake nested form gives the
  per-home-row-first answer; OSI's single-step bare form gives the
  bridge-dedup heavy-side-weighted answer. A porting tool MUST surface
  this choice — the Foundation does not yet have a surface for the
  per-home-row-first interpretation (waiting for §10's nested form).

[snowflake-vr]: https://docs.snowflake.com/en/user-guide/views-semantic/validation-rules
[looker-mt]: https://cloud.google.com/looker/docs/reference/param-measure-types
[tableau-rel]: https://help.tableau.com/current/pro/desktop/en-gb/datasource_dont_be_scared_calcs.htm
[dbt-metrics]: https://docs.getdbt.com/docs/build/metrics-overview.md

---

### SD-2 — `ORDER BY` NULL placement: Spark / Databricks divergence

**Logged:** 2026-05-12 · **Revised:** 2026-05-13 (high-end-NULL convention; Snowflake is no longer divergent) · **Spec anchor:** `Proposed_OSI_Semantics.md` §5.1 "Common clause semantics", §6.10.2, Appendix B D-029 · **Conformance decision:** D-029

**Foundation behavior.** Every `ORDER BY <expr>` — outer or inside `OVER
(...)` — has a defined NULL placement. If the user omits `NULLS FIRST | NULLS
LAST`, the Foundation default is **`NULLS LAST` for `ASC`** and **`NULLS
FIRST` for `DESC`** — i.e., NULL is treated as a high-end value that lands
at whichever end the maximum lands at. The Foundation does NOT reject
unspecified-NULL ordering; engines accept the model and MUST guarantee the
**resolved row order** on every supported dialect, emitting the explicit
clause whenever the dialect's native default would produce a different
order. When the resolved clause matches the dialect default the explicit
clause MAY be elided (both forms produce identical row orders).

This convention preserves the **symmetry property** that flipping
`ASC ↔ DESC` flips NULL placement — so a "top-10 by revenue → flip to
bottom-10" UI flip moves the NULL-revenue rows to the top, since they *are*
the worst values by any reasonable interpretation of "missing revenue."
A user who wants every NULL pinned to a specific end regardless of
direction MUST write the explicit clause.

**Snowflake behavior.** Snowflake's out-of-the-box default is the same
high-end-NULL convention (`ASC NULLS LAST` / `DESC NULLS FIRST`), driven
by the session-level `DEFAULT_NULL_ORDERING = LAST` parameter. **Snowflake
is no longer a divergence target for this rule.** The OSI compiler may
elide the explicit `NULLS …` clause on Snowflake compilation because the
resolved row order matches the native default — both forms produce
identical row orders. If a Snowflake account has set
`DEFAULT_NULL_ORDERING = FIRST`, the *Snowflake-native* default disagrees
with the OSI default in the symmetric way; the OSI compiler then emits
the explicit clause on Snowflake too, restoring the resolved row order.

**Spark / Databricks behavior — the surviving divergence.** Spark / Databricks
treat NULL as a *low-end* value: `ASC NULLS FIRST` / `DESC NULLS LAST`.
This is the **opposite** convention from Snowflake (and from the
SQL:2003 default of "NULLs compare-greater than non-NULLs"). A model
that omits the explicit `NULLS …` clause and is run against a
Spark / Databricks engine **without** the OSI compiler will return the
opposite NULL placement from what the Foundation specifies. With the OSI
compiler in the loop, the compiled SQL emits the explicit `NULLS FIRST`
(for `DESC`) or `NULLS LAST` (for `ASC`) clause on Spark/Databricks
because the dialect's native default would otherwise produce the opposite
order; Spark then produces the Foundation row order.

**Porting consequences.**

- **OSI → Snowflake.** The OSI compiler may elide the `NULLS …` clause on
  Snowflake (native default agrees). Emitted-SQL row order matches
  Snowflake-native row order under default account settings.
- **OSI → Spark / Databricks.** The OSI-compiled SQL carries the explicit
  `NULLS …` clause; Spark accepts it and the row order matches the
  Foundation contract. A model author who runs the *unrewritten* user
  expression directly in Spark (bypassing the OSI compiler) will see the
  opposite NULL placement — the OSI compiler is the determinism boundary.
- **Snowflake → OSI.** A query that relied on Snowflake's
  `DEFAULT_NULL_ORDERING = LAST` default needs no rewrite — both sides
  agree.
- **Spark / Databricks → OSI.** A query that relied on Spark's native
  `ASC NULLS FIRST` / `DESC NULLS LAST` ordering MUST add the explicit
  `NULLS FIRST` (for ASC) or `NULLS LAST` (for DESC) to preserve the
  original row order under OSI.

**Why high-end NULL?** Three reasons in priority order:

1. **Symmetry under direction flip.** Pinning NULLs to a single end (e.g.
   "always last") breaks the most natural BI mental model: "top-N → flip
   to bottom-N" should bring the worst-case rows (which include the NULL
   values) into the visible region. The high-end convention keeps that
   property.
2. **Standards alignment.** SQL:2003 defines NULLs as compare-greater
   than non-NULLs. The high-end convention follows this, matching
   Snowflake, PostgreSQL, and Oracle out-of-the-box. Spark / Databricks
   is the lone outlier among the major analytics engines.
3. **Reduced compiled-SQL noise on the most-used warehouse.** Snowflake's
   native default already matches, so the explicit clause emitted by the
   OSI compiler is a *redundant safety annotation* on Snowflake rather
   than a *behaviour-changing override* — exactly the semantic level a
   determinism guarantee should carry.

---

### SD-3 — `QUALIFY` not supported

**Logged:** 2026-05-12 · **Spec anchor:** `Proposed_OSI_Semantics.md` §6.10.1, §12.D · **Conformance decision:** D-028

**Snowflake behavior.** Snowflake supports the `QUALIFY` clause as a
post-window filter (Snowflake-extension SQL, not ANSI). `QUALIFY` lets a
user filter rows based on the value of a window function in the same query
without wrapping the query in a subquery — e.g., top-N within a group via
`QUALIFY ROW_NUMBER() OVER (PARTITION BY g ORDER BY x) <= N`.

**OSI Foundation behavior.** No `QUALIFY` clause. The same top-N-within-group
shape is expressed by defining a windowed field on the home dataset and
filtering on it in `Where` (§6.10.4):

```yaml
# field on orders
- name: order_rank
  expression: ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY order_date NULLS LAST)

# query
Where: orders.order_rank = 1
```

**Rationale.** `QUALIFY` is not portable across major engines: Postgres,
MySQL, Oracle, and SQL Server do not support it. Adopting it would make
OSI dialect-restricted. The Foundation's windowed-field-in-`Where` pattern
is universal and follows from the existing §6.10.4 grain-interaction rules.

**Porting consequences.**

- **OSI → Snowflake.** A Snowflake codegen layer MAY rewrite the
  windowed-field-in-`Where` pattern to `QUALIFY` for performance, but is
  not required to.
- **Snowflake → OSI.** A `QUALIFY` clause MUST be rewritten as a windowed
  field with the filter in `Where`. Mechanical conversion.

---

### SD-4 — `GROUPS` frame mode deferred

**Logged:** 2026-05-12 · **Spec anchor:** `Proposed_OSI_Semantics.md` §6.10.6, §10 · **Conformance decision:** D-032

**Snowflake behavior.** Snowflake does **not** support the `GROUPS` frame
mode in window functions (only `ROWS` and `RANGE`).

**OSI Foundation behavior.** `GROUPS` frame mode is deferred (§10). The
Foundation supports `ROWS` and `RANGE` only.

**Rationale.** Despite the surface alignment, this is listed here because
the Foundation's reason for deferral is different: `GROUPS` is supported in
Postgres 11+, Oracle 12+, and DuckDB, but not in Snowflake, BigQuery,
Databricks, SQL Server, or MySQL. The Foundation defers it because of
broad-dialect portability, not because Snowflake lacks it. If Snowflake
later adopts `GROUPS`, the Foundation may revisit independently.

**Porting consequences.** None today; both engines reject `GROUPS`.

---

### SD-5 — Parameterized window frame bounds deferred

**Logged:** 2026-05-12 · **Spec anchor:** `Proposed_OSI_Semantics.md` §10; `SQL_EXPRESSION_SUBSET.md` §"Window Functions" · **Conformance decision:** D-032

**Snowflake behavior.** Snowflake requires window frame bounds (e.g., the
`n` in `ROWS BETWEEN n PRECEDING AND CURRENT ROW`) to be **constant integer
literals**. Bind parameters and runtime expressions are not supported in
the frame clause.

**OSI Foundation behavior.** Same — frame bounds MUST be integer literals
or `UNBOUNDED PRECEDING/FOLLOWING` / `CURRENT ROW`. Parameterized frame
bounds (e.g., `ROWS BETWEEN :lookback_frame PRECEDING AND CURRENT ROW`) are
deferred (§10).

**Rationale.** Surface-aligned, but the Foundation's deferred-feature
proposal (`SQL_EXPRESSION_SUBSET.md` §"Window Functions") explicitly
flags parameterized frame bounds as a planned future divergence from
Snowflake. When that proposal lands, OSI will accept parameterized frame
bounds while Snowflake continues to reject them. Tracking now so the
divergence is easy to surface when the proposal is adopted.

**Porting consequences.** None today; both engines reject parameterized
bounds. When OSI adds them under the deferred proposal:

- **OSI → Snowflake.** A model that uses parameterized frame bounds will
  need the engine to either (a) constant-fold the bound at SQL-generation
  time when the binding is known, or (b) raise an explicit
  `E_DIALECT_UNSUPPORTED` error pointing to the parameter.
- **Snowflake → OSI.** No change needed.
