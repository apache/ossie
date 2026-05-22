# SPEC.md — `osi_python` Implementation Specification

**Version:** 0.3 (post-implementation)
**Status:** Active
**Authoritative standard:** [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md) (`osi_version: "0.1"`)
**Expression language:** [`../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md`](../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md) (`OSI_SQL_2026` is the default dialect)
**Algebra contract:** [`docs/JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md)
**Conformance vectors:** [`../../compliance/foundation-v0.1/DATA_TESTS.md`](../../compliance/foundation-v0.1/DATA_TESTS.md) (`T-NNN` test catalog) — referenced from `Proposed_OSI_Semantics.md` Appendix B.
**Compliance test suite:** [`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/) (separate top-level project; see §11.1 of the Foundation spec).
**Infrastructure & quality contract:** [`INFRA.md`](INFRA.md)

This document defines what the implementation builds and the contracts each
component must satisfy. When this document disagrees with `specs/`, `specs/`
wins; update this document to match.

> **Cleanliness over backwards compatibility.** `osi_python` has never
> shipped a release. Every change MUST prefer a clean end state over
> preserving any current behaviour, name, error code, file layout, or
> public API. No deprecation shims. No legacy aliases. No compat flags.
> Names change to match the updated spec; old names are deleted in the same
> commit. The only exception is `E_DEFERRED_KEY_REJECTED`, which is the
> spec-mandated parse-time rejection of a recognised-but-deferred key.

---

## Table of Contents

1. [Project Goals](#1-project-goals)
2. [What is in scope (Foundation)](#2-what-is-in-scope-foundation)
3. [What is out of scope (deferred)](#3-what-is-out-of-scope-deferred)
4. [Architecture](#4-architecture)
5. [The algebra](#5-the-algebra)
6. [Expression handling](#6-expression-handling)
7. [Error discipline](#7-error-discipline)
8. [Test strategy](#8-test-strategy)
9. [Glossary](#9-glossary)

---

## 1. Project Goals

`osi_python` is a **reference implementation** of Open Semantic Interchange,
targeting the [Foundation tier](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
— a deliberately smaller standard — with three hard commitments:

1. **Algebraic correctness is provable.** Every compiler transformation
   is expressible as a composition of operators from a closed algebra with
   explicit preconditions and grain contracts. Correctness reduces to
   correctness of the algebra; the algebra is checked with property-based
   tests and guarded with mutation testing. See [`docs/JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md).
2. **Failure is explicit.** Any semantics the compiler cannot compile
   correctly raise a typed `OSIError` whose `error.code` is a value from
   Appendix C of the Foundation spec. Silent wrong SQL is the single worst
   possible outcome and is designed out.
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

- Feature parity with any prior implementation. The Foundation is the point.
- Highest performance. Correctness and legibility first.
- Multiple planner shapes. One planner, one algebra, one plan type.
- Backwards compatibility with any earlier `osi_python` surface.

---

## 2. What is in scope (Foundation)

Authoritative definition lives in [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md).
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
[`../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md`](../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md).
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
`NOT EXISTS_IN`, `ATTR`, `UNSAFE`, `AGG`, `GRAIN_AGG`. The `GROUPS`
frame mode and parameterized window frame bounds are also deferred per D-032.

### 2.6 Compliance levels

The Foundation defines two levels; `osi_python` targets Level 2.

| Level | Meaning |
|:---|:---|
| L1 (Parse) | YAML parses into a valid `SemanticModel`. |
| L2 (Plan + Render) | Any valid model + Foundation query produces a deterministic plan and compiles to correct SQL on at least one dialect (DuckDB for correctness, Snowflake/BigQuery for portability). Per-engine determinism (D-014) MUST hold; cross-engine SQL determinism is NOT required. |

The canonical compliance vectors live in
[`../../compliance/foundation-v0.1/DATA_TESTS.md`](../../compliance/foundation-v0.1/DATA_TESTS.md) and the runnable suite in
[`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/).

---

## 3. What is out of scope (deferred)

Authoritative deferred-features list: §10 of `../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`.
Features deferred from Foundation v0.1 are enumerated in `Proposed_OSI_Semantics.md §10`.

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
- **Semi-join filter form** (`EXISTS_IN` / `NOT EXISTS_IN`).
- **Window-function extensions** beyond the Foundation: `GROUPS` frame
  mode, parameterized frame bounds, ordered-set aggregates with
  `WITHIN GROUP`, windowed-metric composition.
- **Named filters** (reusable boolean expressions referenced by name).
- **Multi-hop bridge resolution** (more than one bridge between the same
  two endpoints).
- **Symmetric aggregates** (Looker-style hash trick).

Using any of these in a YAML model or query MUST raise
`E_DEFERRED_KEY_REJECTED` at parse time (D-009). The codebase contains
**no** partial plumbing for these features.

---

## 4. Architecture

Full contract in [`ARCHITECTURE.md`](ARCHITECTURE.md) — three-layer pipeline,
one-way information flow, numbered invariants, and the where-to-add-things
decision tree.

---

## 5. The algebra

### 5.1 Stated as a proof obligation

> **Every transformation of a calculation is a total, pure, deterministic
> function on an immutable `CalculationState`. The nine operators of
> [`docs/JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md) are the complete set
> of transformations. A plan is a sequence of those operators. If an
> operator's precondition cannot be proved at plan-build time, the planner
> raises a typed `OSIError` and builds no plan.**

Operator signatures, grain contracts, the twelve universal laws, and their
property-test and mutation-test mapping live in
[`docs/JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md) and
[`docs/ALGEBRA_LAWS.md`](docs/ALGEBRA_LAWS.md).

---

## 6. Expression handling

The Foundation embeds SQL expressions inside field/metric/filter
definitions. The default dialect is `OSI_SQL_2026`
([`../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md`](../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md)). The
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

### 6.1 Per-dialect expression form (D-021)

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

### 6.2 Expression subset enforcement

At parse time, a visitor rejects any AST node not in the allowed subset.
Removed function names (`EXISTS_IN`, `NOT EXISTS_IN`, `ATTR`, `UNSAFE`,
`AGG`, `GRAIN_AGG`) raise `E_DEFERRED_KEY_REJECTED` or
`E_UNKNOWN_FUNCTION`. `GROUPS` frame mode and parameterized window
frame bounds raise `E_DEFERRED_FRAME_MODE` per D-032.

---

## 7. Error discipline

The authoritative catalog is **Appendix C of
[`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)**.
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

## 8. Test strategy

Five layers (unit, property, golden, e2e, compliance) plus mutation testing.
Full strategy in [`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md).
Per-module mutation thresholds and CI gates in [`INFRA.md §1.1`](INFRA.md).
Every D-NNN row in Appendix B has at least one `T-NNN` test vector in
`DATA_TESTS.md` and a runnable case in `../../compliance/foundation-v0.1/`.

---

## 9. Glossary

- **Algebra** — the nine pure operators over `CalculationState` defined
  in [`docs/JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md).
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
  defined in [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
  (`osi_version: "0.1"`).
- **Deferred** — a feature in §10 of the Foundation spec that is out of
  scope; raises `E_DEFERRED_KEY_REJECTED` at parse time.
- **Conformance Decision (D-NNN)** — a numbered row in Appendix B of the
  Foundation spec; each is a small contract paired with a test shape.
- **Test Vector (T-NNN)** — a runnable witness for a `D-NNN`, defined in
  [`../../compliance/foundation-v0.1/DATA_TESTS.md`](../../compliance/foundation-v0.1/DATA_TESTS.md) and shipped under
  [`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/).
- **Golden test** — a snapshot test whose expected output is a file on
  disk, refreshed only by explicit command.
- **Reference interpreter** — the deliberately-naive pandas implementation
  of the Foundation semantics, used by equivalence laws.
