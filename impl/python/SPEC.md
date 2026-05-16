# SPEC.md — `osi_python` Implementation Specification

**Version:** 0.2 (Updated-Foundation rollout)
**Status:** Active — sprint roadmap in §11 below
**Authoritative standard:** [`specs/Proposed_OSI_Semantics.md`](specs/Proposed_OSI_Semantics.md) (`osi_version: "0.1"`)
**Expression language:** [`specs/SQL_EXPRESSION_SUBSET.md`](specs/SQL_EXPRESSION_SUBSET.md) (`OSI_SQL_2026` is the default dialect)
**Algebra contract:** [`specs/JOIN_ALGEBRA.md`](specs/JOIN_ALGEBRA.md)
**Conformance vectors:** [`specs/DATA_TESTS.md`](specs/DATA_TESTS.md) (`T-NNN` test catalog) — referenced from `Proposed_OSI_Semantics.md` Appendix B.
**Compliance test suite:** [`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/) (separate top-level project; see §11.1 of the Foundation spec).
**Infrastructure & quality contract:** [`INFRA.md`](INFRA.md)

This document defines what we are building, the phased path to get there,
and the contracts each component must satisfy. It is the PM's source of
truth. When this document disagrees with `specs/`, `specs/` wins; update
this document to match.

> **Cleanliness over backwards compatibility.** `osi_python` has never
> shipped a release. Every sprint below MUST prefer a clean end state
> over preserving any current behaviour, name, error code, file layout,
> or public API. No deprecation shims. No legacy aliases. No compat
> flags. Names change to match the updated spec; old names are deleted
> in the same sprint. This rule mirrors `INFRA.md` `[I-DEC-2]`'s
> "never add a legacy alias" stance and extends it to error codes,
> public types, YAML keys, dialect names, planner outputs, and tests.
> The only exception is `E_DEFERRED_KEY_REJECTED`, which is the
> spec-mandated parse-time rejection of a recognised-but-deferred key.

---

## Table of Contents

1. [Project Goals](#1-project-goals)
2. [What is in scope (Foundation)](#2-what-is-in-scope-foundation)
3. [What is out of scope (deferred)](#3-what-is-out-of-scope-deferred)
4. [Architecture at a glance](#4-architecture-at-a-glance)
5. [The algebra is the hard boundary](#5-the-algebra-is-the-hard-boundary)
6. [Component contracts](#6-component-contracts)
7. [Expression handling](#7-expression-handling)
8. [Error discipline](#8-error-discipline)
9. [Test strategy (summary)](#9-test-strategy-summary)
10. [Lessons from `osi_impl` and how we apply them](#10-lessons-from-osi_impl-and-how-we-apply-them)
11. [Implementation phases — Updated-Foundation Sprint Roadmap](#11-implementation-phases--updated-foundation-sprint-roadmap)
12. [Open questions](#12-open-questions)
13. [Glossary](#13-glossary)

---

## 1. Project Goals

`osi_python` is a **second reference implementation** of Open Semantic
Interchange. It is NOT a rewrite of `osi_impl`. Its purpose is to
implement the [Foundation](specs/Proposed_OSI_Semantics.md) — a deliberately
smaller standard — with three hard commitments that the first
implementation only partially delivered:

1. **Algebraic correctness is provable.** Every compiler transformation
   is expressible as a composition of operators from a closed algebra with
   explicit preconditions and grain contracts. Correctness reduces to
   correctness of the algebra; the algebra is checked with property-based
   tests and guarded with mutation testing. See [`specs/JOIN_ALGEBRA.md`](specs/JOIN_ALGEBRA.md).
2. **Failure is explicit.** Any semantics the compiler cannot compile
   correctly raise a typed `OSIError` whose `error.code` is a value from
   Appendix C of [`specs/Proposed_OSI_Semantics.md`](specs/Proposed_OSI_Semantics.md).
   Silent wrong SQL is the single worst possible outcome and is designed out.
3. **The Foundation stays thin.** Deferred features (§3 below) raise
   `E_DEFERRED_KEY_REJECTED` at parse time. The codebase contains no
   speculative plumbing for them.

### 1.1 Design priorities, in order

| # | Priority | What it means in practice |
|:--:|:---|:---|
| 1 | **Provable correctness** | The algebra is closed, pure, and total. Property tests assert universal laws; mutation tests prove the tests actually check. Every conformance decision in `Proposed_OSI_Semantics.md` Appendix B has at least one `T-NNN` vector in `DATA_TESTS.md` and a runnable case in `../../compliance/foundation-v0.1/`. |
| 2 | **Legibility** | Code reads like a textbook. `src/osi/planning/algebra/operations.py` is the first file a new contributor reads; it should be ~400 LOC, not 4000. Hard cap: no file in `src/osi/` > 600 LOC. |
| 3 | **Compiler discipline** | Three layers (`parsing`, `planning`, `codegen`) with one-way information flow and typed boundaries. |
| 4 | **Explainability** | `diagnostics.explain(plan)` emits one line per algebra op with grain, inputs, outputs. New errors land in the explainer the same sprint they land in the planner. |
| 5 | **Portability** | `codegen` is a pure projection; adding a dialect is a new transpiler, not a plan change. `OSI_SQL_2026` is the default; per-dialect expression form (`{ dialects: [{dialect, expression}, …] }`) is supported per D-021. |

### 1.2 Non-goals

- Feature parity with `osi_impl`. The Foundation is the point. If
  `osi_impl` can do something the Foundation does not, that's a
  `specs/deferred/` item.
- Highest performance. Correctness and legibility first; optimize the
  bottlenecks the profiler surfaces.
- Multiple planner shapes. One planner, one algebra, one plan type.
- Backwards compatibility with any earlier `osi_python` surface. See
  the cleanliness clause in the document header.

---

## 2. What is in scope (Foundation)

Authoritative definition lives in [`specs/Proposed_OSI_Semantics.md`](specs/Proposed_OSI_Semantics.md).
The Foundation declares `osi_version: "0.1"`. Summary for this SPEC:

### 2.1 Semantic model

- **Datasets** (§4.2): logical tables with declared `primary_key` (and
  optional `unique_keys`), fields, and an optional list of dataset-scoped
  metrics.
- **Relationships** (§4.4): equijoin only, single-column or composite.
  Each declares `from` (many side) and `to` (one side). Cardinality is
  inferred from PK / UK declarations (§6.4). `referential_integrity`,
  `condition`, `asof`, `range`, and `using_relationships` are deferred
  (§3 / D-009) and rejected with `E_DEFERRED_KEY_REJECTED`.
- **Fields** (§4.3): each field has a `name` and an `expression`. The
  expression MAY be scalar, an aggregate (table-scoped metric — see
  **implicit home-grain aggregation** below), a window expression, or a
  boolean form of any of those. There is no `role:` keyword and no grain
  override; routing is by *resolved expression shape* (D-005).
- **Implicit home-grain aggregation** (§4.3.1, D-003): when a field's
  body references columns or fields from a higher-grain related dataset
  via a `1 : N` edge, the aggregate is implicitly evaluated at the home
  dataset's grain. The result is a per-home-row scalar at definition time
  and does not change with the consuming query. This is what lets
  `customers.lifetime_value = SUM(orders.amount)` resolve to a per-customer
  scalar without an explicit `grain:` keyword.
- **Metrics** (§4.5): named aggregate expressions. Three forms:
  (1) single-step cross-grain aggregation over `1 : N` (D-020); (2)
  composition over other metric names (no grain inheritance — the
  windowed-metric composition case is rejected with
  `E_WINDOWED_METRIC_COMPOSITION` per D-031); (3) constant. Cross-grain
  aggregation over `N : N` is governed by D-026 / D-027: the bridge plan
  is a single-pass aggregate over the unique `(measure-home-row,
  group-key)` row set, and is accepted bare for every aggregate category
  (distributive, algebraic, holistic). The "per-home-row-first"
  interpretation requires the nested form `AGG(AGG(...))` and is deferred
  to §10 (`E_NESTED_AGGREGATION_DEFERRED`).
- **Parameters**: typed query-time values with defaults; literals in
  expressions.
- **Namespacing** (§4.6, D-006 / D-018 / D-019): bare references resolve
  to the global namespace; dataset-scoped names use `dataset.field`.
  Reserved names (`GRAIN`, `FILTER`, `QUERY_FILTER`) cannot be used as
  user identifiers.

### 2.2 Query model — two shapes

The Foundation distinguishes **aggregation queries** from **scalar
queries** (§5.1, D-010 / D-011).

| Shape | Clauses | Rule |
|:---|:---|:---|
| **Aggregation query** | `Dimensions`, `Measures`, `Where`, `Having`, `Order By`, `Limit` | Result cardinality is exactly `DISTINCT(Dimensions)`; empty `Dimensions` ⇒ exactly one row (the empty grain). All measures resolve at the query grain (D-002). |
| **Scalar query** | `Fields`, `Where`, `Order By`, `Limit` | Row-level projection. A bare metric reference inside `Fields` ⇒ `E_AGGREGATE_IN_SCALAR_QUERY`. A scalar query whose join path replicates home-dataset rows ⇒ `E_FAN_OUT_IN_SCALAR_QUERY` (D-023). |

A query that mixes the two shapes (`Fields` set together with
`Dimensions` or `Measures`) ⇒ `E_MIXED_QUERY_SHAPE` (D-010).

Predicate placement is by resolved expression shape (D-005, D-012):

| Predicate site | Allowed | Otherwise |
|:---|:---|:---|
| `Where` | row-level scalars; home-grain scalars (incl. boolean home-grain scalars produced by implicit home-grain aggregation) | aggregate at the *query* grain ⇒ `E_AGGREGATE_IN_WHERE`; mixed levels ⇒ `E_MIXED_PREDICATE_LEVEL` |
| `Having` | aggregates resolved at the query grain | pure row-level predicate ⇒ `E_NON_AGGREGATE_IN_HAVING`; mixed levels ⇒ `E_MIXED_PREDICATE_LEVEL` |

`ORDER BY <expr>` (outer or inside `OVER (...)`) without an explicit
`NULLS FIRST` / `NULLS LAST` resolves to the Foundation default
**`NULLS LAST` for `ASC` and `NULLS FIRST` for `DESC`** — the SQL:2003
"NULLs are high-end" convention. Flipping `ASC ↔ DESC` flips NULL
placement (the symmetry property). Engines guarantee the resolved row
order on every supported dialect by emitting the explicit clause whenever
the dialect's native default would otherwise produce a different order;
when the resolved clause matches the dialect default it MAY be elided
(both forms produce identical row orders on that dialect) (D-029).

### 2.3 Join semantics

- **Cardinality inference** (§6.4) from declared `primary_key` and
  `unique_keys`.
- **Default join types** (§6.6, D-001 / D-004):
  - `N : 1` enrichment ⇒ `LEFT` (orphan facts surface as `NULL` group keys).
  - Multi-fact composition on shared dimensions with **incompatible
    fact roots** ⇒ `FULL OUTER` stitch.
  - Scalar grand totals (no shared grain) ⇒ `CROSS JOIN` of pre-aggregated
    1-row scalars.
  - Per-metric `joins.type` overrides are deferred (D-008).
- **Safety** (§6.7): aggregate-before-join, fan-out safety, and chasm-trap
  safety are preconditions on algebra operators.
- **M:N resolution** (§6.8): bridge dataset (§6.8.1, D-026 — materialize
  distinct `(fact, group-key)`) or shared-dimension stitch (§6.8.2). No
  bridge and no stitch ⇒ `E3012_MN_NO_SAFE_REWRITE`. Two unrelated facts
  with no shared dimension ⇒ `E3013_NO_STITCHING_DIMENSION`. The
  semi-join filter form (`EXISTS_IN`) is deferred.
- **Path resolution** (§6.9, D-018): unique path used; ambiguity ⇒
  `E_AMBIGUOUS_PATH`; no path ⇒ `E_NO_PATH`.

### 2.4 Window functions

Standard SQL window functions are part of the Foundation (§6.10):

- **Catalog** (D-028): ranking (`ROW_NUMBER`, `RANK`, `DENSE_RANK`,
  `NTILE`, `PERCENT_RANK`, `CUME_DIST`), navigation (`LAG`, `LEAD`,
  `FIRST_VALUE`, `LAST_VALUE`, `NTH_VALUE`), and aggregate-windows.
  Allowed in `Measures`, `Fields`, `Order By`, `Having`, and any field /
  metric `expression`. Inside `Where` ⇒ `E_WINDOW_IN_WHERE`.
- **Pre-fan-out** (D-030): a window's home dataset MUST run over the
  pre-fan-out row set; engines materialize home-grain rows before
  applying the window. If no safe rewrite is available ⇒
  `E_WINDOW_OVER_FANOUT_REWRITE`.
- **Composition** (D-031): a metric that references another metric whose
  body contains a window ⇒ `E_WINDOWED_METRIC_COMPOSITION`. Direct use
  of a windowed metric in `Measures` is allowed.
- **Frame modes** (D-032): `ROWS` and `RANGE` with integer-literal bounds
  only. `GROUPS` and parameterized bounds ⇒ `E_DEFERRED_FRAME_MODE`.

### 2.5 SQL subset

The expression language is defined normatively in
[`specs/SQL_EXPRESSION_SUBSET.md`](specs/SQL_EXPRESSION_SUBSET.md).
The default dialect for un-annotated expressions is **`OSI_SQL_2026`**
(D-021). Field and metric `expression` slots accept either a bare string
in the default dialect or the structured per-dialect form
`{ dialects: [{ dialect, expression }, …] }`.

Required surface:

- ANSI SQL:2003 Core scalar ops, `CASE`, `COALESCE`, `CAST`.
- Aggregations: `SUM`, `COUNT`, `COUNT(*)` (required — see D-016 — engines
  that historically reject `COUNT(*)` MUST provide a transparent
  rewrite), `COUNT(DISTINCT)`, `MIN`, `MAX`, `AVG`.
- Standard SQL window functions (see §2.4 above).
- Empty / NULL aggregate behaviour follows standard SQL (D-033):
  `COUNT` family ⇒ `0`; `SUM`, `AVG`, `MIN`, `MAX`, etc. ⇒ `NULL`.
  Models that prefer `0` MUST declare it per-metric (`COALESCE(SUM(...), 0)`).

Removed from the Foundation surface (rejected with
`E_DEFERRED_KEY_REJECTED` or `E_UNKNOWN_FUNCTION`): `EXISTS_IN`,
`NOT EXISTS_IN`, `ATTR`, `UNSAFE`, `AGG`, `GRAIN_AGG`. These are not
expression keywords — they were OSI-specific helpers in the old
implementation. The `GROUPS` frame mode and parameterized window frame
bounds are also deferred per D-032.

### 2.6 Compliance levels

The Foundation defines two levels; `osi_python` targets Level 2.

| Level | Meaning |
|:---|:---|
| L1 (Parse) | YAML parses into a valid `SemanticModel`. |
| L2 (Plan + Render) | Any valid model + Foundation query produces a deterministic plan and compiles to correct SQL on at least one dialect (DuckDB for correctness, Snowflake/BigQuery for portability). Per-engine determinism (D-014) MUST hold; cross-engine SQL determinism is NOT required. |

The canonical compliance vectors live in
[`specs/DATA_TESTS.md`](specs/DATA_TESTS.md) and the runnable suite in
[`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/).

---

## 3. What is out of scope (deferred)

Authoritative deferred-features list: §10 of `specs/Proposed_OSI_Semantics.md`.
Design archive: [`specs/deferred/`](specs/deferred/).

Summary:

- **Explicit grain overrides** (`FIXED` / `INCLUDE` / `EXCLUDE` / explicit
  `TABLE`). *Implicit home-grain aggregation* is in scope (§4.3); the
  explicit `grain:` keyword is not.
- **Filter context propagation** (`reset`, `filter.expression` on metrics).
- **Metric composition with grain or filter inheritance** (windowed-metric
  composition specifically rejected via D-031).
- **Model-level `natural_grain`** declaration.
- **Path disambiguation** (`using_relationships`) and **per-metric
  `joins.{type, using_relationships}` overrides** (D-008).
- **Non-equijoin / ASOF / Range** relationships (`condition`, `asof`,
  `range`, `cardinality`).
- **Referential-integrity-driven INNER promotion**
  (`from_all_rows_match`, `to_all_rows_match`, `referential_integrity:`
  on relationships). The Foundation does NOT carry RI plumbing today.
- **Semi-additive measures**.
- **Grouping sets / ROLLUP / CUBE / PIVOT**.
- **Semi-join filter form** (`EXISTS_IN` / `NOT EXISTS_IN`) — a separate
  proposal will pin the surface.
- **Window-function extensions** beyond the Foundation: `GROUPS` frame
  mode, parameterized frame bounds, ordered-set aggregates with
  `WITHIN GROUP`, windowed-metric composition.
- **Named filters** (reusable boolean expressions referenced by name).
- **Multi-hop bridge resolution** (more than one bridge between the same
  two endpoints).
- **Symmetric aggregates** (Looker-style hash trick) — a future codegen
  optimization, not a correctness mechanism.

Using any of these in a YAML model or query MUST raise
`E_DEFERRED_KEY_REJECTED` at parse time (D-009). The codebase contains
**no** partial plumbing for these features.

---

## 4. Architecture at a glance

Full contract in [`ARCHITECTURE.md`](ARCHITECTURE.md). Summary diagram:

```
┌─────────────────┐     ┌──────────────────────┐     ┌────────────────────┐     ┌──────────┐
│   YAML file     │ ──▶ │  osi.parsing         │ ──▶ │  osi.planning      │ ──▶ │ osi.codegen │ ──▶ SQL
│   + SemanticQuery │     │  → SemanticModel     │     │  → QueryPlan       │     │  → rendered  │
└─────────────────┘     │  (immutable,         │     │  (sequence of      │     │    SQL       │
                        │   schema-validated)  │     │   algebra ops over │     │              │
                        └──────────────────────┘     │   CalculationState)│     └──────────────┘
                                                     └──────────┬─────────┘
                                                                │
                                                                ▼
                                                      ┌──────────────────┐
                                                      │ osi.diagnostics  │
                                                      │ read-only view   │
                                                      │ over model + plan│
                                                      └──────────────────┘
```

**One-way information flow.**

- `codegen` imports from `planning` and `common`. Never `parsing`.
- `planning` imports from `parsing` and `common`. Never `codegen`.
- `parsing` imports only from `common` and external libraries.

A lint rule in `INFRA.md §1.2` enforces this with import-linter.

---

## 5. The algebra is the hard boundary

This is what `osi_python` does differently from `osi_impl` most
deliberately.

### 5.1 Stated as a proof obligation

> **Every transformation of a calculation is a total, pure, deterministic
> function on an immutable `CalculationState`. The nine operators of
> [`specs/JOIN_ALGEBRA.md`](specs/JOIN_ALGEBRA.md) are the complete set
> of transformations. A plan is a sequence of those operators. If an
> operator's precondition cannot be proved at plan-build time, the planner
> raises a typed `OSIError` and builds no plan.**

### 5.2 The nine operators

Full signatures and grain contracts in [`specs/JOIN_ALGEBRA.md §3`](specs/JOIN_ALGEBRA.md#3-operators):

| Operator | Grain effect | Preconditions |
|:---|:---|:---|
| `source` | init from `dataset.primary_key` | dataset has PK |
| `filter` | preserve | predicate deps ⊆ state columns; no aggregates |
| `enrich` | preserve (N:1 join) | declared cardinality N:1; keys ⊆ left grain |
| `aggregate` | coarsen to target | target ⊆ source grain; holistic aggs only at final grain; fan-out safety |
| `project` | preserve | columns ⊆ state columns; grain ⊆ columns |
| `add_columns` | preserve | no aggregates; deps ⊆ state columns |
| `merge` | preserve | equal grains; disjoint non-grain columns |
| `filtering_join` | preserve | semi/anti; no columns added |
| `broadcast` | preserve | rhs grain == ∅; column names disjoint |

### 5.3 The laws

Twelve universal laws (totality, purity, determinism, grain closure,
idempotences, commutativities, associativities, safety rules). Each law
is stated in [`specs/JOIN_ALGEBRA.md §4`](specs/JOIN_ALGEBRA.md#4-laws)
and checked by a Hypothesis property test under `tests/properties/`. See
[`docs/ALGEBRA_LAWS.md`](docs/ALGEBRA_LAWS.md) for the mapping from law
to test to mutation-testing target.

### 5.4 Why this is "proof-through-tests", not paper proof

A paper proof of an algebra the size of this one is feasible but slow to
maintain. We substitute:

- **Universally-quantified Hypothesis tests** for each law, with `max_examples=500`
  default and generation strategies that cover the algebraic structure
  (not just handpicked fixtures).
- **Mutation testing** on `src/osi/planning/algebra/` with a `≥ 90%` score
  target, enforced in CI. A mutation that survives every test means a
  law is not actually being checked; that's an actionable gap.
- **A reference interpreter** (see [`docs/ALGEBRA_LAWS.md §3`](docs/ALGEBRA_LAWS.md#3-reference-interpreter))
  written in pandas, deliberately naive, used by equivalence laws to
  compare SQL-compiled results to semantic ground truth on generated
  fixtures.

The combination gives us confidence equivalent to a proof for the shapes
we care about, and keeps working as the algebra evolves. When a law
cannot be expressed as a Hypothesis property, that's a signal the law is
unclear — either reformulate it or reject it.

---

## 6. Component contracts

The table-of-contents of every module's responsibilities. Full detail in
[`ARCHITECTURE.md`](ARCHITECTURE.md) §§ 2–4.

### 6.1 `osi.parsing` — Layer 1

**Inputs.** YAML path or string.
**Outputs.** Frozen `SemanticModel`, `Namespace`, `RelationshipGraph`.

**Responsibilities.**

1. Strict pydantic schema validation (`parsing/models.py`).
2. Cross-reference validation — every relationship references real
   datasets/fields, every metric references real fields, no circular
   metric composition, no deferred-feature key present (raise
   `E_DEFERRED_KEY_REJECTED`).
3. Identifier normalization through `osi.common.identifiers.normalize`.
4. Namespace construction (`parsing/namespace.py`) per §4.6 / D-006.
5. Relationship graph construction (`parsing/graph.py`).

**Non-responsibilities.** Parsing does not expand metric compositions,
infer sources, simplify expressions, or touch the algebra. It produces a
model that the planner can trust without re-validating.

### 6.2 `osi.planning` — Layer 2

**Inputs.** `SemanticModel` + `SemanticQuery`.
**Outputs.** `QueryPlan` — a frozen tuple of `PlanStep`s, each bundling
an operator, its arguments, and the resulting `CalculationState`.

**Responsibilities.**

1. Branch on query shape (Aggregation vs Scalar) per §5.1.
2. Classify each predicate by *resolved expression shape* (D-005); raise
   `E_AGGREGATE_IN_WHERE` / `E_NON_AGGREGATE_IN_HAVING` /
   `E_MIXED_PREDICATE_LEVEL` as appropriate.
3. Expand metric references (no composition with grain inheritance —
   that's deferred).
4. Resolve join paths via `RelationshipGraph`; default `LEFT` for
   `N : 1` enrichment, `FULL OUTER` stitch for incompatible-root
   multi-fact, `CROSS JOIN` for scalar grand totals (D-001 / D-004).
5. Resolve M:N traversals via bridge (§6.8.1) or shared-dim stitch
   (§6.8.2); raise `E3012` / `E3013` if neither applies.
6. Realise implicit home-grain aggregation for cross-grain field bodies
   (§4.3, D-003 / D-015).
7. Emit a sequence of algebra operator applications whose final state
   matches the query's projection at the query's grain.

**Non-responsibilities.** Planning emits no SQL strings. It does not
parse YAML, touch the database, or know about dialects.

**Key sub-modules.**

- `planning/algebra/` — state, operators, laws (the load-bearing module).
- `planning/planner.py` — the composer.
- `planning/classify.py` — predicate-shape classification.
- `planning/joins.py` — join path resolution and cardinality inference.
- `planning/prefixes.py` — deterministic synthetic-column and CTE names.

### 6.3 `osi.codegen` — Layer 3

**Inputs.** `QueryPlan` + dialect name.
**Outputs.** SQL string.

**Responsibilities.**

1. Translate each `PlanStep` to a SQLGlot AST node.
2. Wire nodes into a CTE chain per plan structure.
3. Apply dialect-specific transforms (`OSI_SQL_2026` is the default).
4. Render via `sqlglot.Expression.sql(dialect=...)`.
5. Resolve every `ORDER BY` (outer or inside `OVER (...)`) to the
   Foundation default — `NULLS LAST` for `ASC`, `NULLS FIRST` for `DESC`
   — when the user does not specify, and emit the explicit clause
   whenever the dialect's native default would produce a different row
   order. When the resolved clause matches the dialect default, the
   explicit clause MAY be elided (both forms produce identical row
   orders) (D-029).

**Non-responsibilities.** Codegen never reads the semantic model or
namespace; never classifies filters; never picks join paths. If it is
tempted to, the plan is missing information — extend `PlanStep`.

### 6.4 `osi.diagnostics`

Read-only projection of model + plan into human-readable form. Entry
points:

- `describe(model)` — render the semantic model as a table.
- `explain(plan)` — render the plan as a per-step grain/column trace.
  Lists every error code from Appendix C that the plan can raise at this
  point.
- `resolve(query, model)` — show which datasets, relationships, and
  fields the query will touch.

Never mutates inputs.

### 6.5 `osi.common`

Shared primitives:

- `identifiers.py` — `Identifier` NewType, normalization, validation.
- `sql_expr.py` — thin wrappers over SQLGlot for frozen/comparable
  expressions.
- `types.py` — `DimensionSet`, `CTEName`, other NewTypes that turn up
  in multiple layers.

---

## 7. Expression handling

The Foundation embeds SQL expressions inside field/metric/filter
definitions. The default dialect is `OSI_SQL_2026`
([`specs/SQL_EXPRESSION_SUBSET.md`](specs/SQL_EXPRESSION_SUBSET.md)). The
compiler's expression handling follows three rules:

1. **All expression manipulation goes through SQLGlot ASTs.** Raw-string
   concatenation, f-strings, and regex-on-SQL are banned project-wide.
2. **Expressions are frozen on parse.** The pydantic validator parses each
   expression string with `sqlglot.parse_one(...)` against the declared
   dialect (default `OSI_SQL_2026`) and stores the resulting AST.
   Downstream code reads it; nothing mutates it.
3. **Dependency analysis is pure.** `osi.common.sql_expr.dependencies(expr)`
   walks the AST and returns `frozenset[Identifier]`; no state, no side
   effects.

### 7.1 Per-dialect expression form (D-021)

An `expression` slot accepts either a bare string in the model's default
dialect or the structured object form:

```yaml
expression:
  dialects:
    - dialect: OSI_SQL_2026
      expression: "amount * 1.1"
    - dialect: SNOWFLAKE
      expression: "amount * 1.1::FLOAT"
```

The structured form is normatively defined in
`SQL_EXPRESSION_SUBSET.md`. Engines that recognize neither dialect in a
`dialects:` array MUST reject the model with a clear error.

### 7.2 Expression subset enforcement

At parse time, a visitor rejects any AST node not in the allowed subset.
Removed function names (`EXISTS_IN`, `NOT EXISTS_IN`, `ATTR`, `UNSAFE`,
`AGG`, `GRAIN_AGG`) raise `E_DEFERRED_KEY_REJECTED` or
`E_UNKNOWN_FUNCTION`. `GROUPS` frame mode and parameterized window
frame bounds raise `E_DEFERRED_FRAME_MODE` per D-032.

---

## 8. Error discipline

The authoritative catalog is **Appendix C of
[`specs/Proposed_OSI_Semantics.md`](specs/Proposed_OSI_Semantics.md)**.
[`docs/ERROR_CODES.md`](docs/ERROR_CODES.md) is the implementation-side
mirror that names the Python `ErrorCode` enum members one-for-one with
the appendix.

- All errors inherit from `osi.errors.OSIError`.
- Every error carries a stable `code: ErrorCode` from Appendix C and a
  `context` dict with dataset/field/expression information for diagnostics.
- Tests assert on `error.code`, never on message text.
- The algebra raises only the subset of `E_*` codes whose anchor
  §-reference falls inside the algebra's responsibility (`E_UNSAFE_REAGGREGATION`,
  `E_AMBIGUOUS_NESTED_AGGREGATION_GRAIN`,
  `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`, `E_AMBIGUOUS_MEASURE_GRAIN`).
  The planner raises predicate-routing and shape errors
  (`E_AGGREGATE_IN_WHERE`, `E_NON_AGGREGATE_IN_HAVING`,
  `E_MIXED_PREDICATE_LEVEL`, `E_MIXED_QUERY_SHAPE`,
  `E_AGGREGATE_IN_SCALAR_QUERY`, `E_FAN_OUT_IN_SCALAR_QUERY`,
  `E_EMPTY_AGGREGATION_QUERY`, `E_EMPTY_SCALAR_QUERY`, the M:N family
  `E3012` / `E3013`, the path family `E_AMBIGUOUS_PATH` / `E_NO_PATH`,
  the namespace family `E_NAME_COLLISION` / `E_NAME_NOT_FOUND`, and the
  window-placement codes `E_WINDOW_IN_WHERE`,
  `E_WINDOW_OVER_FANOUT_REWRITE`, `E_WINDOWED_METRIC_COMPOSITION`).
  Codegen raises only `E_DEFERRED_FRAME_MODE` (when a deferred frame
  pattern survives parsing — defence in depth) and dialect-specific
  emission errors.
- A property test (`tests/properties/test_error_taxonomy.py`) asserts
  that every exception raised anywhere in the compiler is an `OSIError`
  with a code from Appendix C. Catching a bare `Exception` in `src/` is
  a lint error. Adding a code outside Appendix C is a lint error.

---

## 9. Test strategy (summary)

Full strategy in [`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md).

Five layers, all required for every feature:

| Layer | Checks |
|:---|:---|
| **Unit** | Happy path and preconditions of each function. |
| **Property** | Universal laws of the algebra (see [`docs/ALGEBRA_LAWS.md`](docs/ALGEBRA_LAWS.md)). |
| **Golden** | Exact `QueryPlan` + exact SQL per `(query, dialect)` pair for a curated corpus. |
| **E2E** | DuckDB-executed row comparisons against hand-rolled references. |
| **Compliance** | The new external suite at [`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/). Each `T-NNN` case is a `(model, query, expected_outcome)` triple keyed to a `D-NNN` from Appendix B. Tests assert on `error.code` or row-set; never on plan shape or SQL string. |

Plus mutation testing with per-module thresholds in [`INFRA.md §1.1`](INFRA.md).

---

## 10. Lessons from `osi_impl` and how we apply them

Each row is a thing we did well in `osi_impl` or a thing we wish we had
done differently, and what that means for `osi_python`.

| What we learned | `osi_python` policy |
|:---|:---|
| **Three-layer separation with one-way imports works.** It survived large refactors. | Keep the separation; enforce imports via `import-linter` in CI (`INFRA.md §1.2`). |
| **Closed algebra with grain on every state works.** `osi_impl` only belatedly made this rigorous. | Start with the algebra. `src/osi/planning/algebra/` is the first module written; it has property tests from sprint 1. |
| **Deprecated aliases and legacy aliases are bug magnets.** (`osi_impl` I-DEC-2 deleted ~900 LOC of dead code late.) | Never add a legacy alias. If a name changes, change every callsite. The cleanliness clause at the top of this document is the project-wide form. |
| **Multiple planners drift.** (`osi_impl` had three.) | One `Planner` class, one `SemanticQuery` input, one `QueryPlan` output. No `SimplePlanner` fast path. |
| **A 4000-LOC planner is unreviewable.** (`planner_lod.py`.) | Hard cap: no file in `src/osi/` > 600 LOC. Split by responsibility (see §6.2 sub-modules). |
| **Deferred-feature plumbing leaks.** (`osi_impl` carries LOD enums and filter-reset scaffolding in the core.) | Zero plumbing for deferred features. `E_DEFERRED_KEY_REJECTED` at parse time. |
| **`LODPlanner` is a misnomer when LOD is deferred.** | Name is `Planner` full stop. Input is `SemanticQuery`, not `LODQuery`. |
| **Snapshot / determinism tests catch accidental changes.** | Golden tests for every canonical query; `make golden-refresh` is the only way to update them; golden refresh requires explicit PR justification. Per-engine determinism (D-014) is enforced; cross-engine is not. |
| **SQLGlot as the only SQL-manipulation tool is load-bearing.** | Same policy. `INFRA.md §1.3` bans `f"{...} IN ({...})"`; CI greps for `f"{.*}SELECT\\b"` and fails if it matches. |
| **Cursor rules + skills help contributors.** | Port the planner-feature skill and write a new `add-new-operator-to-algebra` skill tuned to the Foundation. The new sprint workflow lives in `.cursor/skills/osi-compliance-sprint/SKILL.md`. |
| **Per-project venv with strict mypy beats shared tooling.** | Same. `impl/python/.venv`, own `Makefile`, own `.pre-commit-config.yaml`. |
| **Mutation testing was always "planned".** (`osi_impl` I-9.) | Mutation testing is **in from day one**, starting with the algebra module. Per-module thresholds in `INFRA.md §1.1`. |
| **Typed identifiers help but only if threaded through from day one.** (`osi_impl` I-5/I-7 showed retrofit is painful.) | `Identifier`, `CTEName`, `DimensionSet`, `ExpressionId` are NewType from sprint 1, used in every public signature. |
| **Errors are easy to add, hard to dedupe.** (`osi_impl` grew E2011 with comma-separated meanings.) | One concept = one code. Every code lives in Appendix C of `Proposed_OSI_Semantics.md`. |
| **Expression handling via AST from day one.** | Pydantic validators parse expressions on load against `OSI_SQL_2026`; stored AST is frozen; dependency analysis is a pure AST walk. |
| **Error-on-unknown is better than warn-on-unknown.** | Unknown fields in YAML are a parse error, not a warning. The pydantic `extra="forbid"` policy applies. Unknown but recognised-deferred keys raise `E_DEFERRED_KEY_REJECTED`. |

---

## 11. Implementation phases — Updated-Foundation Sprint Roadmap

Each sprint follows the per-sprint workflow defined in
`.cursor/skills/osi-compliance-sprint/SKILL.md` (multi-agent simulated
plan → implement → review → tester deep pass → compliance run → retro).
Sprint IDs are stable. Periodic tech-debt sprints (S-1, S-6, S-11, S-15)
are SPEC-anchored under `INFRA.md §3` with one infrastructure item per
sprint; each tech-debt sprint MUST run `mutmut` on the modules touched
by the previous feature sprints, surface surviving mutants, and fill the
gaps before exiting.

### 11.0 Pre-sprint scaffolding (read-only on `src/`)

| Sprint | Title | Anchor | Notes |
|:---|:---|:---|:---|
| **S-A** | Spec-doc + roadmap landing | §1.1 of updated spec, §10, Appendix B/C | Renames `Proposed_OSI_Semantics_updated.md` → `Proposed_OSI_Semantics.md` and `SQL_EXPRESSION_SUBSET_updated.md` → `SQL_EXPRESSION_SUBSET.md`; deletes the old files. Rewrites `SPEC.md` (this file), `INFRA.md`, `AGENTS.md`, `CLAUDE.md` references in the same commit. No "deprecated" notes. |
| **S-B** | New compliance suite scaffold + delete the old one | §11.1, `DATA_TESTS.md` | Lands `compliance/foundation-v0.1/` (README, SPEC, `pyproject.toml`, `conformance.yaml`, `proposals.yaml`, `decisions.yaml`, `adapters/`, `datasets/f_*`, empty `tests/` tree). Reuses harness from `compliance/harness` via path dep. Deletes `impl/python/tests/compliance/` so we have exactly one compliance harness. No `src/` changes. |
| **S-C** | Compliance suite tests v1 (T-001 … T-033) | All `D-NNN` | Encodes `DATA_TESTS.md §4` as runnable cases; one negative test per `E_DEFERRED_KEY_REJECTED` family. |
| **S-D** | Baseline compliance run + gap report | All | Runs S-C against current `osi_python`; emits `results/baseline_<date>.md`. **No fixes yet.** Every red row must be cited by exactly one sprint's exit criterion. |
| **S-E** | Differential / edge-case audit + extra tests | All `D-NNN`, `INFRA §1.1` | Cross-references every sprint S-1 … S-17 against the v1 catalog and the cross-implementation drift checklist (NULL ordering, integer/decimal precision, division-by-zero, empty-aggregate, time-zone/date arithmetic, collation/case, large-N determinism, M:N de-dup, nested-aggregate grain inference, window frame defaults, `OSI_SQL_2026` function semantics). Lands missing `T-NNN` cases as `metadata.yaml + model.yaml + query.json + gold_rows.json` BEFORE S-1 starts. Read-only on `src/`. |

### 11.1 Implementation sprints

| Sprint | Title | Anchor decisions / specs | Notes |
|:---|:---|:---|:---|
| **S-1** | Tech-debt #1 — delete all deferred plumbing | §10, D-009 | **Delete** every reference to `EXISTS_IN` / `NOT EXISTS_IN`, `referential_integrity`, named filters, `role:`, per-metric `joins.{type, using_relationships}`, `ATTR`, `UNSAFE`, `AGG`, `GRAIN_AGG` from `src/`, parser models, codegen, diagnostics, tests, fixtures, examples, and docs. No re-export, no alias module, no warning shim — bare `E_DEFERRED_KEY_REJECTED` at parse time. Mutation pass on every touched module. |
| **S-2** | Two query shapes (Aggregation vs Scalar) | §5.1 / D-010, D-011, D-023 | New `Fields` clause; `E_MIXED_QUERY_SHAPE`; `E_AGGREGATE_IN_SCALAR_QUERY`; `E_FAN_OUT_IN_SCALAR_QUERY`; scalar-query planner branch + codegen. |
| **S-3** | Routing by resolved expression shape | §4.3, §6.3 / D-005, D-012 | Drop `role:`; classify expressions; new predicate-shape errors `E_AGGREGATE_IN_WHERE`, `E_NON_AGGREGATE_IN_HAVING`, `E_MIXED_PREDICATE_LEVEL`. |
| **S-4** | Implicit home-grain aggregation | §4.3 / D-003, D-015 | Field bodies with cross-grain aggregates resolve at home grain; pick one of correlated subquery / `LATERAL` / pre-agg CTE; cover with at least 3 D-015 equivalence golden tests. |
| **S-5** | Single-step + nested cross-grain aggregates | §4.5 / D-020, D-024 | Accept single-step `1:N` cross-grain; reject `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`. |
| **S-6** | Tech-debt #2 — algebra cleanup + mutation gap fill | INFRA §1.1.1, S-3..S-5 churn | Re-establish ≥ 90% mutation on `src/osi/planning/algebra/`; refactor anything > 600 LOC introduced by S-2..S-5. |
| **S-7** | Default join shape rewrite | §6.6 / D-001, D-004 | Single-measure ⇒ `LEFT` (fact→dim) with `NULL`-key bucket. Multi-measure incompatible-root ⇒ `FULL OUTER` stitch. Scalar grand totals ⇒ `CROSS JOIN` of pre-aggregated 1-row scalars. |
| **S-8** | Bridge de-duplication contract | §6.8.1 / D-026 | Bridge plan materializes distinct `(fact, group-key)`; rip out every "tag-fan-out" code path and comment — no compat wording left behind. Port the actor↔movie fixture into the new compliance suite. |
| **S-9** | Bridge-dedup acceptance for every aggregate category + chasm/stitch decomposition safety | §6.8.1 / D-022, D-027 | Bridge plan is single-pass and accepted bare for SUM / AVG / MEDIAN / COUNT(DISTINCT) over an N:N edge (D-027). `E_UNSAFE_REAGGREGATION` narrowed to genuinely-decomposing plans only (§6.7 chasm pre-aggregation, §6.8.2 stitch — D-022). Nested form `AGG(AGG(...))` continues to raise `E_NESTED_AGGREGATION_DEFERRED` until §10. |
| **S-10** | Error-taxonomy + identifier-resolution alignment | §4.6 / D-006, D-018, D-019, Appendix C | Parser raises `E_NAME_COLLISION` / `E_NAME_NOT_FOUND` / `E_AMBIGUOUS_PATH` / `E_NO_PATH`; reserve `GRAIN`, `FILTER`, `QUERY_FILTER`. Every internal `OSIError` code maps 1:1 to Appendix C. |
| **S-11** | Tech-debt #3 — diagnostics + readability | After S-7..S-10 | Refactor planner sub-modules (`classify.py`, `joins.py`) for legibility; ensure `diagnostics.explain` lists the new errors; mutation pass on `classify` / `joins`. |
| **S-12** | Window functions in Foundation | §6.10 / D-028, D-030, D-031, D-032 | Window-in-`Where` rejection; pre-fan-out window materialization; deferred-frame-mode rejection; windowed-metric-composition rejection. |
| **S-13** | NULL-placement default + per-engine determinism | §5.1 / D-029, D-014 | Outer `Order By` + window `OVER (... ORDER BY ...)` resolve unspecified NULL placement to `NULLS LAST` for `ASC` and `NULLS FIRST` for `DESC` (the SQL:2003 high-end-NULL convention); emit explicit clause in compiled SQL. **Amended 2026-05-13** from the original "always `NULLS LAST`" rule, which broke the symmetry property under `ASC ↔ DESC` flips; see `SNOWFLAKE_DIVERGENCES.md` SD-2 and INFRA.md I-57. |
| **S-14** | Empty/NULL aggregate behaviour | §6.11 / D-033 | `COUNT*` ⇒ 0, others ⇒ `NULL`; ensure stitch missing-cells follow standard SQL. |
| **S-15** | Tech-debt #4 — final mutation + property gap fill | INFRA §1.1, §1.1.1 | Run `mutmut` on every planning/codegen module; fill any < 88% gaps with property tests; re-baseline. |
| **S-16** | `OSI_SQL_2026` default dialect surface | §7 of updated spec, `SQL_EXPRESSION_SUBSET.md` | Treat `OSI_SQL_2026` as the default; per-dialect `expression` form (`{ dialects: [...] }`) in parser; D-021. |
| **S-17** | Final compliance pass + xfail clear-out | §11.1 | Re-run new compliance suite end-to-end; root-cause every remaining failure; classify as impl bug / test bug / spec ambiguity. Exit when every D-NNN is `must_pass`. |

The original Phase 0 – Phase 6 ramp (scaffolding, algebra, parsing,
planner, codegen, diagnostics, hardening) has been completed for the
first iteration of the codebase; this roadmap is the next iteration that
brings the implementation in line with the updated Foundation spec. The
underlying module layout (§4–§6) is unchanged.

---

## 12. Open questions

Items known to be under-specified; resolve before exiting the sprint in
which they first matter.

| # | Question | Sprint |
|:--:|:---|:---:|
| Q-1 | Composite-key equijoin coverage in the first M:N pass — already implicit in S-7 / S-8, or a follow-up? | S-7 |
| Q-2 | Which D-015 compilation strategy do we pick (correlated subquery vs `LATERAL` vs pre-agg CTE) for the first land? Pin in the S-4 architect doc. | S-4 |
| Q-3 | How do we represent parameter defaults in golden / compliance files without freezing the current time? | S-13 |
| Q-4 | Which Snowflake dialect features do we commit to supporting for the `S-16` default-dialect cut, vs deferring to a post-rollout dialect sprint? | S-16 |
| Q-5 | Bridge plan's distinct-`(fact, group-key)` materialisation: SQL `DISTINCT` vs an explicit pre-agg CTE keyed on the bridge — which is the "default" emission for D-026? Settle in S-8. | S-8 |

---

## 13. Glossary

- **Algebra** — the nine pure operators over `CalculationState` defined
  in [`specs/JOIN_ALGEBRA.md`](specs/JOIN_ALGEBRA.md).
- **Aggregation query** — a Foundation query shape (§5.1.1): `Dimensions`,
  `Measures`, `Where`, `Having`, `Order By`, `Limit`. Result cardinality
  is `DISTINCT(Dimensions)`.
- **Scalar query** — a Foundation query shape (§5.1.2): `Fields`,
  `Where`, `Order By`, `Limit`. Row-level projection.
- **CalculationState** — the single value flowing through the algebra;
  grain + columns + provenance, frozen.
- **Grain** — the set of dimensions that uniquely identify a row.
- **Home grain (table grain)** — the per-dataset grain; the dataset's
  primary key (or any declared unique key).
- **Implicit home-grain aggregation** — the §4.3 / D-003 rule: a field
  body that aggregates a higher-grain related dataset over `1 : N`
  resolves to a per-home-row scalar, automatically aggregated at the home
  dataset's grain.
- **Fan-out** — a join that creates multiple rows per parent due to a
  many-side cardinality; unsafe for most aggregations without
  pre-aggregation.
- **Chasm trap** — two facts sharing a dimension without a direct
  relationship; resolved by per-fact aggregation + `merge` (D-001 row 3).
- **Foundation** — the subset of OSI this implementation targets;
  defined in [`specs/Proposed_OSI_Semantics.md`](specs/Proposed_OSI_Semantics.md)
  (`osi_version: "0.1"`).
- **Deferred** — a feature in §10 of the Foundation spec that is out of
  scope; raises `E_DEFERRED_KEY_REJECTED` at parse time.
- **Conformance Decision (D-NNN)** — a numbered row in Appendix B of the
  Foundation spec; each is a small contract paired with a test shape.
- **Test Vector (T-NNN)** — a runnable witness for a `D-NNN`, defined in
  [`specs/DATA_TESTS.md`](specs/DATA_TESTS.md) and shipped under
  [`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/).
- **Golden test** — a snapshot test whose expected output is a file on
  disk, refreshed only by explicit command.
- **Reference interpreter** — the deliberately-naive pandas implementation
  of the Foundation semantics, used by equivalence laws.
