# ARCHITECTURE.md — `osi_python` Architectural Contract

This document is the architectural contract. It is the source of truth for
*where things live*, *what each layer may do*, and *what must always be
true*. Read this before adding code that spans more than one package.

The guiding principle:

> **The compiler is a closed, pure algebra over an immutable semantic
> model. Every transformation is a total function from state to state;
> every generated SQL statement is a deterministic projection of a plan.
> The algebra is the hard boundary of correctness.**

If a proposed change breaks that sentence, it is the wrong change.

---

## Table of Contents

1. [Three-layer pipeline](#1-three-layer-pipeline)
2. [Layer 1 — Parsing](#2-layer-1--parsing)
3. [Layer 2 — Planning](#3-layer-2--planning)
4. [Layer 3 — Codegen](#4-layer-3--codegen)
5. [The closed algebra](#5-the-closed-algebra)
6. [Architectural invariants](#6-architectural-invariants)
7. [Error discipline](#7-error-discipline)
8. [Where to add things](#8-where-to-add-things)
9. [Canonical entry points](#9-canonical-entry-points)

---

## 1. Three-layer pipeline

The compiler is strictly three layers. Each layer has a single output
type, sees only the layer above it, and has no opinion about the layer
below it.

```
  YAML  ──(parse)──▶  SemanticModel  ──(plan)──▶  QueryPlan  ──(render)──▶  SQL
        Layer 1                     Layer 2                    Layer 3
        parsing/                    planning/                  codegen/
```

| Layer | Package | Input | Output | Job |
|:---|:---|:---|:---|:---|
| **1. Parsing** | `osi.parsing` | YAML path / string | `SemanticModel`, `Namespace`, `RelationshipGraph` | Load and validate declarations. Produce a typed, frozen, self-consistent model. Reject deferred features with `E1105`. |
| **2. Planning** | `osi.planning` | `SemanticModel` + `SemanticQuery` | `QueryPlan` (ordered tuple of `PlanStep`s, each wrapping a closed-algebra operator over an immutable `CalculationState`) | Decide *how* to compute the query. No SQL. |
| **3. Codegen** | `osi.codegen` | `QueryPlan` + dialect | SQL string | Render the plan to dialect-specific SQL via SQLGlot AST. No semantics. |

A shared `osi.diagnostics` package reads the same artifacts (model +
plan) to produce `describe` / `explain` / `resolve` output. It never
mutates them.

### 1.1 The one-way information flow

Information only travels **down** the pipeline. This is non-negotiable.

- Codegen **must not** open the YAML, consult the namespace, or reach
  back into `SemanticModel`. Every fact it needs has to be on the plan.
- Planning **must not** emit SQL. It may *parse* SQL fragments with
  SQLGlot to analyse dependencies, but it never decides how the final
  SQL will look.
- Parsing **must not** know about plans, algebra, or dialects. It only
  validates that the YAML describes a legal Foundation model.

Violation is enforced by `import-linter` contracts declared in
`pyproject.toml` and checked in CI (`INFRA.md §1.2`).

---

## 2. Layer 1 — Parsing

**Contract.** Turn a YAML file into an immutable, validated
`SemanticModel` that every later layer can trust without re-checking.

### 2.1 Responsibilities

1. Strict pydantic schema parsing (`parsing/models.py`), `extra="forbid"`.
2. Cross-reference validation: metric-referenced fields exist, relationship
   endpoints are real datasets with real fields, primary keys are declared
   where required.
3. Deferred-feature detection: any use of `FIXED`/`INCLUDE`/`EXCLUDE`/`TABLE`
   grain modes, filter-context properties (`reset`, filter.expression on
   metrics), window functions in expressions, non-equijoin conditions,
   grouping sets, pivot, or any other deferred feature raises `E1105
   RESERVED_FOR_DEFERRED`.
4. Circular-reference detection for metric arithmetic (metric depending
   on itself through composition).
5. Name-resolution index construction (`parsing/namespace.py`).
6. Relationship graph construction (`parsing/graph.py`).
7. Identifier normalization via `osi.common.identifiers.normalize_identifier`.

### 2.2 Non-responsibilities

Parsing knows nothing about queries, plans, dialects, or SQL. It does
not expand metric arithmetic, infer sources, or simplify expressions. It
also does not construct `CalculationState` — only the algebra does that.

### 2.3 Key exports

- `osi.parsing.parse_semantic_model(path: str | Path) -> SemanticModel`
- `osi.parsing.SemanticModel`, `Dataset`, `Metric`, `Relationship`,
  `Namespace`, `RelationshipGraph`
- `osi.parsing.reserved_names.OSI_RESERVED_NAMES` — the set of
  OSI-grammar keywords (`GRAIN`, `FILTER`, `QUERY_FILTER`) that user
  identifiers may not collide with (D-019, enforced in
  `parsing/validation.py`).
- `osi.errors.OSIValidationError`

---

## 3. Layer 2 — Planning

**Contract.** Given a frozen `SemanticModel` and a typed `SemanticQuery`,
produce a deterministic `QueryPlan` that computes the query using only
closed algebra operations.

### 3.1 Responsibilities

1. Resolve names (`planning/resolve.py`, via `Namespace`).
2. Classify measures by their source dataset and grain-compatible groups.
3. Expand metric arithmetic (Foundation arithmetic only, no grain
   inheritance).
4. Classify filters — row-level vs semi-join vs post-aggregate-having —
   in `planning/classify.py`.
5. Resolve join paths between datasets via `RelationshipGraph`
   (`planning/joins.py`). Infer cardinality from declared keys; surface
   `E3003 AMBIGUOUS_CARDINALITY` when inference is not possible.
6. Emit a sequence of algebra operator applications whose final
   `CalculationState` matches the requested grain and columns.

### 3.2 Non-responsibilities

Planning never writes SQL strings, chooses CTE shapes, inlines subqueries,
or picks identifier quoting. It does not touch the database. It does not
parse the YAML.

### 3.3 Core types

| Type | Module | Role |
|:---|:---|:---|
| `PlannerContext` | `planner_context.py` | Frozen bundle of `(model, namespace, graph, analyzer)`. The only way deeper modules see the model. |
| `CalculationState` | `algebra/state.py` | Immutable `(grain, columns, provenance)`. The sole currency of the algebra. |
| `QueryPlan` | `plan.py` | Ordered tuple of `PlanStep`s referencing `CalculationState`s. The hand-off to codegen. |
| `PlanStep` | `plan.py` | A single operator application + inputs + outputs + annotations for codegen. |
| `Planner` | `planner.py` | The single planner. Input: `SemanticQuery`. Output: `QueryPlan`. |

### 3.4 Module map

```
src/osi/planning/
  algebra/
    state.py            # CalculationState, Column
    operations.py       # the 9 operators (§5)
    grain.py            # symbolic grain helpers (for tests)
  plan.py               # QueryPlan, PlanStep, PlanOperation enum
  planner_context.py    # PlannerContext
  planner.py            # Planner.plan(query) — the composer
  planner_scalar.py     # scalar (Fields-only) query composer
  planner_bridge.py     # M:N bridge resolution, distinct-bridge dedup,
                        # nested-aggregate-over-bridge plans (D-022, D-026)
  planner_nested.py     # nested cross-grain aggregate planner (D-020, D-024)
  planner_composites.py # composite metric (formula) planning
  planner_mn.py         # multi-fact / many-to-many helpers
  home_grain.py         # implicit home-grain rewrite via correlated
                        # subqueries (D-003, D-015)
  windows.py            # window-function rules (D-028..D-032)
  classify.py           # filter classification
  joins.py              # path resolution, cardinality inference
  resolve.py            # name resolution against Namespace
  prefixes.py           # deterministic synthetic-column / CTE names
  preprocess.py         # query-level rewrites prior to planning
  steps.py              # step-builder helpers used by all composers
```

A few of these modules grew past the 600-LOC informal cap during
Foundation v0.1 (notably `planner_bridge.py` after S-19/S-23 and
`planner.py` after S-21). They are flagged for refactor proposals in the
S-26 retro; the recommended split is described there.

### 3.5 The composer's shape

`Planner.plan(query)` never invents algebra operators; it only composes
them. See the pseudocode in `specs/JOIN_ALGEBRA.md §7`. Practical rules:

- Each helper returns a `CalculationState` (not SQL, not a plan, not a
  tuple of metadata).
- Helpers receive `ctx: PlannerContext` and never import the model
  directly.
- Helpers that could fail their preconditions catch the `AlgebraError`
  raised by the operator and re-raise as an `E3xxx` with additional
  context (dataset, field, query position).

---

## 4. Layer 3 — Codegen

**Contract.** Walk a `QueryPlan` and return a SQL string for the
requested dialect. Correctness means "same plan + same dialect ⇒
byte-identical SQL."

### 4.1 Responsibilities

1. Translate each `PlanStep` to SQLGlot AST nodes (`codegen/transpiler.py`).
2. Wire nodes into a CTE chain per the plan's DAG structure.
3. Apply dialect-specific transforms (`codegen/dialect.py`).
4. Apply post-build optimizations — CTE inlining, chaining, folding,
   deduplication (`codegen/cte_optimizer.py`).
5. Render via `sqlglot.Expression.sql(dialect=...)`.

### 4.2 Non-responsibilities

Codegen never decides *what* to compute. It never reads the model or
the namespace. It never normalizes grain, picks join paths, or classifies
filters. If it is tempted to, the plan is missing information — extend
`PlanStep`.

### 4.3 Module map

```
src/osi/codegen/
  transpiler.py   # PlanStep → SQLGlot AST
  dialect.py      # dialect-specific transforms (ANSI / DuckDB / Snowflake)
  cte_optimizer.py # post-build AST transforms
  types.py        # CTEName + other codegen NewTypes
```

---

## 5. The closed algebra

The algebra is the heart of the system. Full specification in
[`specs/JOIN_ALGEBRA.md`](specs/JOIN_ALGEBRA.md); companion document with
machine-checked laws in [`docs/ALGEBRA_LAWS.md`](docs/ALGEBRA_LAWS.md).

### 5.1 Closure properties

1. **Total.** Every operator is `(CalculationState, args) → CalculationState`.
   There are no nullable returns or out-parameters.
2. **Pure.** No I/O, no globals, no mutation of inputs. All inputs are
   frozen; all outputs are new frozen values.
3. **Deterministic.** The same inputs produce the same output, including
   column ordering and generated column names (driven by
   `planning/prefixes.py`).
4. **Fail-fast.** Preconditions are checked before any transformation. A
   violation raises a typed `OSIError` with an error code — never a
   silent mis-compute.
5. **Grain-safe by construction.** An operator that would break grain
   safety (coarsening violation, fan trap, chasm trap, explosion-unsafe
   aggregation) is rejected with a specific `E3xxx`/`E4xxx` code.

### 5.2 Operations and grain contracts

Full table in [`specs/JOIN_ALGEBRA.md §3`](specs/JOIN_ALGEBRA.md#3-operators).
Summary:

| Operator | Grain effect |
|:---|:---|
| `source(dataset)` | init from dataset's primary key |
| `filter(state, pred)` | preserve |
| `enrich(parent, child, keys, join_type)` | preserve parent grain |
| `aggregate(state, grain, aggs)` | coarsen to `grain` |
| `project(state, cols)` | preserve |
| `add_columns(state, defs)` | preserve |
| `merge(left, right, on)` | preserve (grains must match) |
| `filtering_join(state, rhs, keys, mode)` | preserve |
| `broadcast(state, scalar)` | preserve |

Each rule is enforced at call time in `planning/algebra/operations.py`.
If you're about to write "unless", stop — extend the rule, don't skip
it.

### 5.3 Why this matters

The closed algebra is what gives the system these properties for free:

- **Explainability.** `diagnostics.explain(plan)` walks the plan and
  prints the grain at every step, because every step declares its grain.
- **Optimizability.** CTE folding in `codegen/cte_optimizer.py` is safe
  because it operates on an AST whose semantics were pinned down before
  codegen.
- **Dialect portability.** A new dialect is a new transpiler; the plan
  is dialect-free.
- **Test determinism.** Property tests compare states structurally;
  golden tests compare plans and SQL byte-for-byte; E2E tests execute
  against DuckDB. All three reproduce.

Breaking the closure — even "just once, for perf" — forfeits all of the
above simultaneously.

---

## 6. Architectural invariants

Numbered so tests and reviews can cite them. These are enforced across
the codebase; a PR that violates one should not merge.

### Algebra purity

1. **Closed state.** The only way to obtain a `CalculationState` is from
   `source(...)` or an algebra operator in `planning/algebra/operations.py`.
   Never construct one by hand outside the algebra module.
2. **Immutability.** `SemanticModel`, `Namespace`, `RelationshipGraph`,
   `PlannerContext`, `CalculationState`, `Column`, `QueryPlan`, and
   `PlanStep` are frozen dataclasses. Transformations return new values.
3. **Pure functions.** Algebra operators are free of I/O, randomness,
   clocks, and global state. Any exception is a typed `OSIError`.
4. **Determinism.** Same `(model, query, dialect)` ⇒ identical
   `QueryPlan` and identical SQL byte-for-byte. Column names, CTE
   names, and ordering are driven by `prefixes.py` counters, not by
   hashing or insertion order.
5. **Grain tracking.** Every `CalculationState` carries an explicit
   `grain: frozenset[Identifier]`. An operator that cannot prove grain
   safety raises a typed error; it never "tries its best".

### Layer discipline

6. **One-way flow.** Codegen imports from `planning` and `common`, never
   from `parsing`. Planning imports from `parsing` and `common`, never
   from `codegen`. Parsing imports only from `common`. Enforced by
   `import-linter`.
7. **`PlannerContext` is the only model handle in planning.** Sub-modules
   receive `ctx: PlannerContext` and never import the semantic model
   directly.
8. **Facades stay consistent.** `planning/__init__.py` re-exports the
   public surface. Any addition / rename in a sub-module updates the
   facade in the same PR.

### Correctness over cleverness

9. **No silent wrong SQL.** Any semantics we cannot compile correctly
   raise a typed `OSIError` with a specific code. Returning plausibly
   wrong SQL is always worse than raising.
10. **SQL composition via AST only.** Compose, combine, and transform
    SQL expressions with SQLGlot AST nodes. Never by string
    concatenation, f-strings, or regex.
11. **Identifier safety.** All identifier comparisons go through
    `normalize_identifier()` in `osi.common.identifiers`. Raw `==` on
    identifier strings is a bug. Lint flags it.
12. **Column prefixes live in one place.** All synthetic column and CTE
    names come from `planning/prefixes.py`. Never hardcode a prefix
    literal; adding one elsewhere breaks determinism and test stability.

### Foundation discipline

13. **No deferred-feature plumbing.** The codebase contains no partial
    support for `FIXED` / `INCLUDE` / `EXCLUDE` / `TABLE` grain, filter
    context (`reset`, per-metric filter expressions), window functions,
    grouping sets, pivot, non-equijoins, ASOF, or semi-additive. All
    raise `E1105 RESERVED_FOR_DEFERRED` at parse time. Adding
    plumbing for any of these in anticipation of a future feature is
    forbidden; file a proposal and wait for ratification.
14. **One planner.** `osi.planning.Planner` is the single planner. No
    `SimplePlanner`, no `FastPathPlanner`, no variants.

### Model extension

15. **Relationships are declared, not inferred.** Join paths come from
    `RelationshipGraph`. Planner code never synthesizes a join that
    isn't in the graph. If no path exists, planning fails with `E2004 UNREACHABLE_DATASET`.
16. **Cardinality requires declared keys.** When cardinality cannot be
    inferred from PK/UK declarations, parsing raises
    `E3003 AMBIGUOUS_CARDINALITY`. The planner never guesses.

---

## 7. Error discipline

See [`docs/ERROR_CODES.md`](docs/ERROR_CODES.md) for the full catalog.

- All errors inherit from `osi.errors.OSIError` and carry a stable code.
- Errors carry enough context — dataset, field, grain, suggestion — to
  be actionable without reading the source.
- Tests assert on error codes, not on message text.
- `tests/properties/test_error_taxonomy.py` enforces globally that every
  raised exception is an `OSIError` subclass with a known code.

---

## 8. Where to add things

| You want to … | Put it in … | Because … |
|:---|:---|:---|
| Support a new YAML field in the Foundation | `parsing/models.py` + `parsing/validation.py` | Parsing is the single gate into the model. |
| Support a new metric idiom | `planning/planner.py` (composition) + possibly `planning/algebra/operations.py` | The algebra defines what's computable; compositions define how metrics compose. |
| Add a new filter shape | `planning/classify.py` + possibly `planning/algebra/operations.py` | Filter classification is how the planner routes filters to operators. |
| Support a new SQL dialect | `codegen/dialect.py` + a transpiler variant | Dialects are a render-time concern. |
| Improve SQL shape (fewer CTEs, better inlining) | `codegen/cte_optimizer.py` | Post-build AST transforms on the rendered tree only. |
| Improve explainability | `diagnostics/` | Read-only over model + plan. |
| Implement a previously-deferred feature | First: propose in `specs/`, get sign-off, move the spec out of `specs/deferred/`. Then: parser, planner, possibly a new algebra operator, tests across all four layers. | A deferred feature is not "opt-in"; it's an additive spec change. |

If a task pulls you across two layers, that's a signal to rethink the
abstraction, not to make the layers leaky.

---

## 9. Canonical entry points

| Goal | Entry point |
|:---|:---|
| Parse a model | `osi.parsing.parse_semantic_model(path)` |
| Build a query plan | `osi.planning.Planner(model).plan(query)` |
| Render SQL | `osi.codegen.render(plan, dialect="duckdb")` |
| End-to-end CLI | `osi_cli.py` (`load`, `describe`, `sql`, `explain`, `run`) |
| Diagnostics CLI | `python -m osi describe \| explain \| resolve \| compile \| explain-code` |
| Look up an error code | `osi.diagnostics.error_catalog.explain_error(code)` or `osi explain-code <CODE>` |
| End-to-end Python example | `examples/run_example.py` |
| Algebra deep-dive | [`specs/JOIN_ALGEBRA.md`](specs/JOIN_ALGEBRA.md) · [`docs/ALGEBRA_LAWS.md`](docs/ALGEBRA_LAWS.md) |
| Foundation standard | [`specs/Proposed_OSI_Semantics.md`](specs/Proposed_OSI_Semantics.md) |
