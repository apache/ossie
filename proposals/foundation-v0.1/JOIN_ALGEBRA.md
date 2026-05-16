# JOIN_ALGEBRA.md — The Closed Algebra

**Status:** Authoritative spec
**Companion:** [`Proposed_OSI_Semantics.md`](Proposed_OSI_Semantics.md) §6 · [`../docs/ALGEBRA_LAWS.md`](../docs/ALGEBRA_LAWS.md) (proofs and property tests)

This document specifies the algebra the planner uses to turn a semantic
query into a deterministic plan. The algebra is the **hard boundary** of
the compiler:

> Every transformation of a calculation is a total, pure, deterministic
> function over an immutable state. Join semantics, aggregation, filtering,
> and projection are expressed by a small closed set of operators with
> explicit preconditions and grain contracts. If an operator's
> precondition is violated, the compiler refuses to build the plan.

Correctness of the compiler reduces to correctness of this algebra. Every
law below is machine-checked: see [`../docs/ALGEBRA_LAWS.md`](../docs/ALGEBRA_LAWS.md) for the
property-based tests that enforce it.

---

## Table of Contents

1. [The State](#1-the-state)
2. [Core Invariants](#2-core-invariants)
3. [Operators](#3-operators)
4. [Laws](#4-laws)
5. [Safety Rules](#5-safety-rules)
6. [What the Algebra Does Not Decide](#6-what-the-algebra-does-not-decide)
7. [How the Planner Uses the Algebra](#7-how-the-planner-uses-the-algebra)
8. [Non-Negotiables](#8-non-negotiables)

---

## 1. The State

The single value flowing through the algebra is `CalculationState`:

```python
@dataclass(frozen=True)
class CalculationState:
    grain: frozenset[Identifier]            # dimensions that uniquely identify a row
    columns: tuple[Column, ...]             # ordered; names are unique
    provenance: frozenset[ExpressionId]     # which requested expressions this state serves
```

```python
@dataclass(frozen=True)
class Column:
    name: Identifier
    expression: SqlExpr                     # frozen sqlglot AST (never a raw string)
    dependencies: frozenset[Identifier]     # names of other columns this one reads
    kind: ColumnKind                        # DIMENSION | FACT | AGGREGATE
    aggregate: AggregateInfo | None         # present iff kind == AGGREGATE
    is_single_valued: bool                  # known constant or scalar-valued across the grain
    from_join_rhs: bool                     # introduced by a join on the many→one side
```

`CalculationState` is frozen. Every operator returns a new state; existing
states are never mutated.

**Why `grain: frozenset[Identifier]`?**
- A frozenset captures set-equality, which is the semantic truth: `{a,b}`
  and `{b,a}` are the same grain.
- An empty grain (`frozenset()`) is the scalar grain — one row per state.
- `grain ⊆ {column.name for column in columns if column.kind == DIMENSION}`
  is an invariant; grain members must be present as dimension columns.

---

## 2. Core Invariants

These hold at every state produced by the algebra. Tests encode each as
a Hypothesis property under `tests/properties/`.

| # | Invariant | Rationale |
|:--:|:---|:---|
| **I-1** | `state.grain ⊆ {c.name for c in state.columns if c.kind == DIMENSION}` | Grain can only reference dimensions that exist. |
| **I-2** | Column names in `state.columns` are unique. | Addressing by name is deterministic. |
| **I-3** | `state` is reachable only by starting from `SOURCE` and applying operators. | No side-door construction. |
| **I-4** | Every `AGGREGATE` column's `aggregate.decomposability` is one of `DISTRIBUTIVE`, `ALGEBRAIC`, `HOLISTIC`. | Re-aggregation and fan-out safety depend on this classification. |
| **I-5** | `grain = frozenset()` implies `len(output_rows) == 1`. | Scalar state has exactly one tuple. |
| **I-6** | `expression.dependencies ⊆ {c.name for c in columns}` for every column. | Column expressions are closed under the current state. |
| **I-7** | `from_join_rhs = True` implies the producing operator was `ENRICH` on the one-side. | Fan-out safety uses this flag. |
| **I-8** | `provenance` strictly grows through operators that contribute to a requested expression, and is unchanged otherwise. | Diagnostics can trace any output back to its request. |

Violating any invariant is a bug in the algebra implementation, not a
valid runtime state.

---

## 3. Operators

The algebra has **nine** operators and no more. Every compiler transformation
must be expressible as a composition of these. If a PR needs a tenth, the
algebra itself is changing and requires a SPEC update.

All operators return a new state or raise `AlgebraError` (a subclass of
`OSIError`, error code `E4xxx`).

### 3.1 `source(dataset) → state`

Initialize a state from a dataset declaration.

| Aspect | Value |
|:---|:---|
| Precondition | `dataset.primary_key` is declared. |
| Resulting grain | `frozenset(dataset.primary_key)` |
| Resulting columns | One column per field declared on the dataset, `kind=DIMENSION` or `FACT` per the field's `role`. `is_single_valued = False`. `from_join_rhs = False`. |
| Provenance | `frozenset()` (set by subsequent operators as they serve expressions). |

### 3.2 `filter(state, predicate) → state`

Apply a row-level boolean predicate.

| Aspect | Value |
|:---|:---|
| Precondition | `predicate.dependencies ⊆ {c.name for c in state.columns}`. Predicate contains no aggregate functions. |
| Grain effect | **Preserved.** Filtering removes rows, not dimensions. |
| Columns effect | Unchanged (same names, same expressions; the filter is structurally represented by the plan step, not by mutating columns). |

### 3.3 `enrich(parent, child, keys, join_type) → state`

N:1 join — bring one-side columns into the many-side state.

| Aspect | Value |
|:---|:---|
| Precondition | `keys.parent ⊆ parent.grain` (or a declared PK of parent). The declared relationship has cardinality `N:1` from parent to child. `join_type ∈ {INNER, LEFT}`. |
| Grain effect | **Preserved** (we joined to the one-side, so no fan-out). |
| Columns effect | Parent columns unchanged. Child's non-key columns appended, each with `from_join_rhs=True`, `is_single_valued=True` (single-valued over the parent grain). |
| Safety | Rejects `N:1` joins that would fan out (any many-side FK appearing more than once per parent PK). Relies on declared cardinality; if cardinality is not declared (neither side has PK/UK match), this operator raises `E3003 AMBIGUOUS_CARDINALITY`. |

### 3.4 `aggregate(state, new_grain, aggregations) → state`

Reduce to a coarser grain.

| Aspect | Value |
|:---|:---|
| Precondition | `new_grain ⊆ state.grain`. Every aggregation's `source_expression.dependencies ⊆ {c.name for c in state.columns}`. No aggregation operates over a column with `from_join_rhs=True` that would require pre-aggregation (see §5 fan-out safety). |
| Grain effect | `new_grain` (may equal `state.grain` — aggregate at same grain is a no-op and is optimized away). |
| Columns effect | Columns = the dimensions in `new_grain` (each preserved from `state.columns`) plus one `AGGREGATE` column per aggregation. |
| Safety | Holistic aggregates (`COUNT DISTINCT`) are rejected for pre-aggregation followed by re-aggregation; they must operate at the final grain. |

### 3.5 `project(state, columns) → state`

Keep only the named columns.

| Aspect | Value |
|:---|:---|
| Precondition | `columns ⊆ {c.name for c in state.columns}`. `state.grain ⊆ columns`. |
| Grain effect | **Preserved.** |
| Columns effect | Only the named columns survive, in the order given. |

### 3.6 `add_columns(state, definitions) → state`

Introduce derived scalar columns.

| Aspect | Value |
|:---|:---|
| Precondition | Each definition's expression has no aggregate functions and `definition.dependencies ⊆ {c.name for c in state.columns}`. New column names do not collide. |
| Grain effect | **Preserved.** |
| Columns effect | Existing columns followed by new columns. Each new column inherits `kind=DIMENSION` if its expression is dimension-pure, else `kind=FACT`. |

### 3.7 `merge(left, right, on) → state`

FULL OUTER join of two states at the same grain (chasm-trap resolution).

| Aspect | Value |
|:---|:---|
| Precondition | `left.grain == right.grain`. `on ⊆ left.grain` and `on ⊆ right.grain` (conventionally `on == left.grain`). Non-grain columns of `left` and `right` are disjoint. |
| Grain effect | **Preserved** — same grain on both sides. |
| Columns effect | Grain dimensions coalesced via `COALESCE(left.d, right.d)`. Non-grain columns unioned positionally (left-then-right). |

### 3.8 `filtering_join(state, rhs, keys, mode) → state`

Semi-join (`mode=SEMI`) or anti-semi-join (`mode=ANTI`). Used for `EXISTS_IN`
/ `NOT EXISTS_IN`.

| Aspect | Value |
|:---|:---|
| Precondition | `keys.lhs ⊆ state.columns`. `keys.rhs ⊆ rhs.columns`. `mode ∈ {SEMI, ANTI}`. |
| Grain effect | **Preserved.** |
| Columns effect | Unchanged (no columns added — that's the point of a filtering join). |

### 3.9 `broadcast(state, scalar) → state`

Attach a scalar value (a state with `grain == frozenset()`) as a new column,
cross-join style.

| Aspect | Value |
|:---|:---|
| Precondition | `scalar.grain == frozenset()`. `scalar` has exactly one output column not already in `state`. |
| Grain effect | **Preserved.** |
| Columns effect | Existing columns followed by the scalar's column, flagged `is_single_valued=True`. |

**That is the entire algebra.** Nothing else is an operator. Every other
transformation — "resolve a metric reference", "pick a join path", "apply a
filter context" — composes these nine.

---

## 4. Laws

Each law has a Hypothesis test under `tests/properties/`. The mutation
testing threshold in `INFRA.md §1.1` is set against the algebra module
specifically because these laws are where silent bugs can hide.

### 4.1 Totality

Every operator is a total function on states that satisfy the stated
precondition. If the precondition fails, it raises a typed error; it never
returns a sentinel value or silently no-ops.

**Test**: `test_algebra_totality.py` — for every operator, Hypothesis
generates (state, args) pairs. Either the precondition passes and a valid
new state is returned, or an `AlgebraError` is raised. No other outcome.

### 4.2 Purity

Every operator is a pure function: no I/O, no clocks, no randomness, no
mutation of inputs.

**Test**: `test_algebra_purity.py` — runs each op twice with the same
inputs and asserts identical outputs. Also checks that the input state
is unchanged (by comparing `id(...)` of column tuples and by deep equality).

### 4.3 Determinism

`same inputs → same output`, including column order and generated column names.

**Test**: `test_algebra_determinism.py` — 1000 random repetitions per op;
output must be byte-identical.

### 4.4 Closure of Grain

For every operator the resulting grain is expressible as a set-theoretic
function of the input grains:

| Operator | Grain function |
|:---|:---|
| `source(d)` | `frozenset(d.primary_key)` |
| `filter` | `state.grain` |
| `enrich` | `parent.grain` |
| `aggregate(_, g, _)` | `g` |
| `project` | `state.grain` |
| `add_columns` | `state.grain` |
| `merge(l, r, _)` | `l.grain == r.grain` (enforced) |
| `filtering_join` | `state.grain` |
| `broadcast` | `state.grain` |

**Law**: for any path of operators, the final grain is deterministically
computable from the operator-argument sequence alone, without executing the
plan.

**Test**: `test_grain_closure.py` — symbolic simulation matches concrete
execution on generated operator chains.

### 4.5 Aggregate Idempotence (at same grain)

`aggregate(state, state.grain, aggs)` is a no-op when `aggs` are identity
re-aggregations (`SUM(x) → SUM(x)` at same grain, etc.).

**Test**: `test_aggregate_idempotent.py` — generates states, wraps in
identity re-aggregation, asserts structural equality.

### 4.6 Filter Commutativity

`filter(filter(s, p1), p2)` produces a state equivalent to `filter(s, p1 AND p2)`.
Two non-overlapping filters may be reordered.

**Test**: `test_filter_commute.py` — asserts equivalence of row
sets when rendered and executed on DuckDB.

### 4.7 Merge Associativity (at same grain)

`merge(merge(a, b), c) ≡ merge(a, merge(b, c))` for disjoint non-grain
column sets and equal grains.

**Test**: `test_merge_associative.py`.

### 4.8 Projection Idempotence

`project(project(s, c1), c2) ≡ project(s, c2)` whenever `c2 ⊆ c1`.

**Test**: `test_project_idempotent.py`.

### 4.9 Enrichment Preserves Parent Rows

`enrich(parent, child, keys, LEFT)` does not change the number of parent
rows — specifically, the projection of the result onto `parent.grain` must
have the same multiset of values as the projection of `parent` onto the
same grain.

**Test**: `test_enrich_preserves_rows.py` — DuckDB-executed; asserts row
counts match exactly over 100 generated fixtures.

### 4.10 Explosion Safety

For any aggregation whose source column has `from_join_rhs=True`, the
planner must have proven that either (a) the join was safely pre-aggregated
(the many-side was reduced to its PK before join), or (b) the aggregation
is a counting aggregation over the join key and the relationship is
declared `N:1`. Otherwise the algebra raises `E4001 EXPLOSION_UNSAFE`.

**Test**: `test_explosion_safety.py` — generates fan-out-prone topologies;
asserts that silent double-counting never occurs, by comparing OSI output
to a hand-rolled pre-aggregate reference.

---

## 5. Safety Rules

The three analytical traps the Foundation defends against, stated as
algebra rules that must hold in every produced plan.

### 5.1 Fan-out Safety

> **Rule.** For any `aggregate(state, g, aggs)`, if any aggregation's source
> expression reads a column with `from_join_rhs=True`, that column must
> have been introduced by a `broadcast` (single-valued) or the many-side
> must have been pre-aggregated via an `aggregate` before the `enrich`.

Concretely: `SUM(orders.amount)` grouped by `customers.region` where
orders and customers are joined N:1 requires a pre-aggregate step on
orders first. This is not a heuristic — it is a precondition on
`aggregate`.

Violating the rule raises `E4001`. See `JOIN_SAFETY.md` in `docs/` for
worked examples.

### 5.2 Chasm-Trap Safety

> **Rule.** When two facts connect through a shared dimension but have no
> direct relationship, they MUST be computed in separate states
> (`source → … → aggregate` per fact) and combined via `merge` on the
> shared dimension. They MUST NOT be joined through a single multi-branch
> state.

Violating the rule raises `E3010`. The planner composition step is the
only place that constructs chasm-safe plans; no other helper may fabricate
a plan that joins two facts directly through their shared dimension.

### 5.3 `enrich` is N:1-only — M:N is the planner's problem

> **Rule.** `enrich` requires the declared relationship to have cardinality
> `N:1` from parent to child. An `N:N` (or `N:?` with missing keys)
> relationship MUST NOT reach `enrich`. Violating this precondition
> raises `E3003 AMBIGUOUS_CARDINALITY`.

This is an *operator-local* precondition, not a blanket rule about M:N
data models. M:N edges are valid model citizens and are resolved by the
planner before any operator is invoked, via one of three routes
(`Proposed_OSI_Semantics.md` §6.5):

| Route | Operator path |
|:---|:---|
| Bridge dataset | `enrich` (bridge → grouping-side) ∘ `aggregate` (dedup to `{measure-PK, grouping-cols}`) ∘ `enrich` (→ measure-side) ∘ `aggregate` (to query grain). The intermediate `aggregate` is the §5.1 pre-aggregation of the bridge, without which the final `aggregate` would read a `from_join_rhs=True` column over a fanned-out parent and raise `E4001`. |
| Stitching dimension | `aggregate` per side, then `merge` (FULL OUTER on shared dims) |
| `EXISTS_IN` filter | `filtering_join` (mode `SEMI` or `ANTI`) |

If none of the three routes applies the planner raises `E3012` or
`E3013` — *before* it ever reaches the algebra. The algebra only ever
sees inputs that are already known to be safe.

`E3011 MN_AGGREGATION_REJECTED` is the **engine-capability opt-out**
code: an engine that does not support M:N at all raises it for every
M:N query. M:N-supporting engines (which `osi_python` is) emit
`E3012` / `E3013` for per-query failures and never raise `E3011` at
the user-facing surface. The algebra-internal use of `E3011` (the
`enrich` precondition raising it on an `N : N` edge) is a planner-
internal signal that the planner translates to the user-facing
`E3012` / `E3013`. See `Proposed_OSI_Semantics.md` §6.5 for the
resolution rules and §11.1 for the compliance suite that pins the
behaviour.

#### 5.3.1 Worked bridge plan (operator-level)

For the query `SUM(movies.gross)` grouped by `actors.height` against the
mini-model M2 in `Proposed_OSI_Semantics.md` Appendix A
(`actors ↔ appearances ↔ movies`), the planner emits this exact operator
sequence:

```
s0 = source(appearances)
        # grain = {actor_id, movie_id}
s1 = enrich(s0, child=actors, keys=[actor_id], join_type=LEFT)
        # grain = {actor_id, movie_id}; adds height (from_join_rhs=True)
s2 = aggregate(s1, new_grain={movie_id, height}, aggregations=[])
        # collapses duplicate appearances per (movie, height).
        # This is the §5.1 pre-aggregation step for the next enrich.
s3 = enrich(s2, child=movies, keys=[movie_id], join_type=LEFT)
        # grain = {movie_id, height}; adds gross (from_join_rhs=True,
        # is_single_valued over the new parent grain)
s4 = aggregate(s3, new_grain={height}, aggregations=[SUM(gross)])
        # grain = {height}; final query-grain aggregate.
```

`s2` is the load-bearing step. Without it, `s4` would be summing `gross`
(a `from_join_rhs=True` column) over an `appearances`-grained state that
has multiple rows per `(movie_id, height)`, and §5.1 fan-out safety would
raise `E4001 EXPLOSION_UNSAFE`. The dedup-via-`aggregate` is the
"pre-aggregate the many-side" pattern §5.1 mandates, applied at the
bridge.

For an explicit nested-aggregation query like `AVG(AVG(movies.gross))`,
the planner instead emits a per-endpoint-first plan
(`enrich(→movies) ∘ aggregate({actor_id}, [AVG(gross)]) ∘ enrich(→actors)
∘ aggregate({height}, [AVG(per_actor_avg)])`). Both shapes are legal
under the algebra; the planner picks one based on whether the user wrote
a bare aggregate (dedup shape) or nested aggregation (per-endpoint
shape).

The compliance suite pins both shapes via `tests/data/DATA_TESTS.md`
entries T-026 and T-027 — observable row-set equivalence, not the SQL
text.

---

## 6. What the Algebra Does Not Decide

The algebra is deliberately agnostic about the following — they are
decided elsewhere:

| Concern | Decided by |
|:---|:---|
| How a metric definition expands into an operator sequence. | `planning/planner.py` (composition). |
| Which relationship path to use when multiple exist. | `planning/joins.py` — calls graph traversal, then hands `enrich` the chosen keys. |
| Filter routing: pre-aggregation vs post-aggregation vs semi-join. | `planning/classify.py` — then expresses its decision by emitting `filter` / `filtering_join`. |
| How CTEs are shaped in the output SQL. | `codegen/` — operates on the plan, not on the algebra. |
| Dialect-specific syntax. | `codegen/dialect.py`. |
| Error message prose. | `osi.errors` — the algebra raises typed errors with codes and context; the error-message module renders them. |

Because the algebra is agnostic, these concerns can be changed
independently without weakening the algebra's guarantees.

---

## 7. How the Planner Uses the Algebra

The planner is a **composer of algebra ops**. Its job is to choose which
operators to apply, in what order, with what arguments. It never invents
new operators.

Pseudocode for the main loop:

```python
def plan(query: SemanticQuery, ctx: PlannerContext) -> QueryPlan:
    branches: list[CalculationState] = []

    for measure_group in classify_measures_by_source(query.measures, ctx):
        s = source(ctx.model.datasets[measure_group.root])
        for rel in ctx.joins.path(measure_group.datasets):
            s = enrich(s, child=ctx.model.datasets[rel.to], keys=rel.keys, join_type=rel.join_type())
        for predicate in classify(query.where).row_level_predicates_for(measure_group):
            s = filter(s, predicate)
        for predicate in classify(query.where).semi_join_predicates_for(measure_group):
            s = filtering_join(s, predicate.rhs_state(ctx), predicate.keys, predicate.mode)
        s = aggregate(s, query.grain, measure_group.aggregations)
        s = add_columns(s, measure_group.post_aggregate_scalars)
        branches.append(s)

    combined = reduce(lambda a, b: merge(a, b, on=query.grain), branches)
    combined = add_columns(combined, query.derived_metric_arithmetic)
    for predicate in classify(query.having).post_aggregate_predicates():
        combined = filter(combined, predicate)
    final = project(combined, query.output_columns)
    return QueryPlan.from_state(final)
```

Everything inside `plan()` is composition. The correctness of `plan()`
reduces to: (a) it picks arguments that satisfy the preconditions of each
operator, and (b) the resulting state matches the query's requested
columns and grain. The algebra itself is trusted.

---

## 8. Non-Negotiables

These are the five commitments that make the algebra load-bearing. None
may be relaxed without a SPEC change and a new decision-log entry in
`INFRA.md §4`.

1. **Immutability.** Every state and every column is frozen. No operator
   mutates its inputs.
2. **Purity.** No I/O, no clocks, no randomness, no global state in any
   operator.
3. **Totality.** Every operator either returns a valid state or raises a
   typed `AlgebraError` with an error code. No `None`, no silent fallback.
4. **Explicit grain contract.** Every operator declares its grain effect,
   checked at call time by a single helper in `algebra/operations.py`.
5. **Single-point entry.** The only way to build a `CalculationState` is
   `source(...)` or an operator applied to an existing state. There is no
   constructor escape hatch.

Tests under `tests/properties/` enforce each commitment as a Hypothesis
property. Mutation testing on `src/osi/planning/algebra/` is where the
quality bar is strictest; see `INFRA.md §1.1`.
