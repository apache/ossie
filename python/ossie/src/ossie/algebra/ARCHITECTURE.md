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

# `ossie.algebra` — the closed algebra

The algebra is the **correctness boundary** of the Ossie reference compiler. A
query is compiled by *composing operators from this package* into a
`CalculationState`; every higher layer (parsing, planning, codegen) is defined
in terms of these operators and this state, and no other way.

Normative semantics live in
[`core-spec/foundational_semantics.md`](../../../../../core-spec/foundational_semantics.md)
§6. This document is the **engineering contract** for the package: what "closed"
means here, and how it is mechanically enforced.

## Why "closed"

The whole point of an algebra is that a *small, fixed* set of operators over a
*single* immutable value type can express every transformation — and that its
properties (grain only coarsens, no fact row is dropped or double-counted, the
same input yields byte-identical output) can be proven once and then relied on
everywhere. New capability is added by **composing existing operators**, not by
adding operators or by hand-building state. That is what keeps the correctness
guarantees total.

## The nine operators

The single carrier value is `CalculationState` (grain + columns + provenance +
unique keys). Every operator takes states/values and returns a **new** state.

| Operator | Module | Role |
|----------|--------|------|
| `source` | `operations.py` | initialize a state from a dataset (sets PK + grain) |
| `filter_` | `operations.py` | apply a row-level predicate (grain unchanged) |
| `enrich` | `operations.py` | N:1 join; preserves parent grain, adds columns |
| `aggregate` | `operations.py` | coarsen to a target grain; add aggregate columns |
| `project` | `operations.py` | keep only named columns (grain preserved) |
| `add_columns` | `composition.py` | derived scalar columns (composite metrics) |
| `merge` | `joins.py` | full-outer, chasm-safe stitch at matching grain |
| `filtering_join` | `joins.py` | semi-/anti-join (**experimental**, flag-gated) |
| `broadcast` | `composition.py` | attach a scalar column (**reserved**) |

`grain.py` provides the pure grain *simulation* (`simulate`, `combine_grains`,
`is_coarser`, the `Step` tags) used to reason about a plan's grain without
materializing states.

## State invariants

`CalculationState.__post_init__` validates these eagerly and raises
`AlgebraError` (never returns a bad state):

- **I-1** `grain ⊆ {c.name for c in columns if c.kind is DIMENSION}`
- **I-2** column names are unique
- **I-5** `grain == ∅` ⇔ scalar (exactly one row)
- **I-6** `column.dependencies ⊆ {names of other columns}`
- **I-8** `provenance` grows only through operators serving a requested expression
- **I-9** every `unique_keys` set is non-empty and a subset of the dimension names

## The rules (and how each is enforced)

1. **Compose operators — never construct state by hand.** `CalculationState`
   is built only inside the operator modules (`operations.py`, `joins.py`,
   `composition.py`). Enforced mechanically by
   [`tests/test_algebra_closure.py`](../../../tests/test_algebra_closure.py)
   (AST scan of the whole `ossie` tree).
2. **Immutability / purity.** States and columns are `@dataclass(frozen=True,
   slots=True)`; operators return fresh values and have no side effects (no I/O,
   time, or randomness). Enforced by `tests/test_algebra_purity.py`.
3. **Totality.** Operators are total over valid inputs: they either return a
   valid state or raise a typed `AlgebraError`. Enforced by
   `tests/test_algebra_totality.py`.
4. **Determinism.** The same inputs produce byte-identical results (foundation
   decision **D-014**). Enforced by `tests/test_algebra_determinism.py`.
5. **Grain closure.** Grain only *coarsens*, and only through `aggregate`.
   Enforced by `tests/test_grain_closure.py`.
6. **Typed errors only.** Failures raise `AlgebraError` with an
   `ErrorCode` — never `assert`, never a bare `Exception`. See
   [`ossie/errors.py`](../errors.py).
7. **No upward imports.** The algebra imports only `ossie.common`,
   `ossie.errors`, the stdlib, and `sqlglot`. Enforced by `import-linter`
   (`make lint-imports`; contract in `pyproject.toml`).

## Scope: necessary, not sufficient

The algebra enforces the *local* safety of each operator application; it is **not**
the whole implementation of the spec's guarantees. Several §6 semantics are owned
by other layers, and a valid algebra composition does not by itself make a query
spec-correct:

- **Semantic 1 (no fact row dropped) and the default join direction.** The algebra
  records an `enrich` `join_type` but does not choose it; emitting `LEFT`
  fact→dim (§6.6) and the NULL-bucket behaviour are the planner's and codegen's
  job.
- **Semantic 4 (no unsafe re-aggregation) is only *partially* guarded here.** The
  algebra rejects the clearest unsafe case — a holistic aggregate over a
  discharged join-RHS value (`E4001`). It does **not** implement the full
  contract: whether an *algebraic* aggregate (AVG, STDDEV) survives a §6.7 chasm
  pre-aggregation or a §6.8.2 stitch, and the resulting `E_UNSAFE_REAGGREGATION`
  verdict, are decided by the **planner** when it chooses the plan shape. The
  algebra has no multi-stage (SUM/COUNT-tracking) decomposition machinery.
- **Runtime/codegen semantics** — NULL-safe stitch equality (§6.6 row 3),
  empty/NULL aggregate results (§6.11), window evaluation and placement (§6.10) —
  are outside the algebra entirely.

In short: the algebra makes *illegal states unrepresentable* and *local fan-out
unsafe*; the planner composes operators into a plan that satisfies the remaining
§6 guarantees, and codegen renders it. Reviewers should not read a green algebra
as a proof of end-to-end spec conformance — that is what the compliance suite is
for.

## How to extend

- **Add an aggregate function / dialect behaviour / metric shape:** express it
  by *composing* the existing operators. This is almost always the answer.
- **Change an operator's contract:** update the operator, its preconditions, its
  laws in the property tests, and this document together — in one change.
- **Add a tenth operator:** this is a deliberate, reviewed spec change. It
  requires a new entry in `test_algebra_closure.py`'s allow-list, a full set of
  property tests (purity, totality, determinism, grain), and a spec update. Do
  not do it to work around a planner limitation.

## Testing bar

The algebra is held to a **mutation score ≥ 90%** (`make mutation`, `mutmut`
over `src/ossie/algebra`). A surviving mutant here is a P0: it means a line of
the correctness core is unprotected by a test. Property tests use `hypothesis`;
see `tests/strategies.py` for the state generators.
