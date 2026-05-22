---
name: compiler-best-practices-review
description: Review or design code against compiler engineering best practices — phase boundaries, IR purity, total functions, closed operator algebras, pass ordering, deterministic codegen, error taxonomy, fail-fast preconditions. Use when adding a planner phase, a new IR node, a transform, or any code that participates in the parsing → planning → codegen pipeline as a transformation.
---

# Compiler best-practices review

The reference implementation is a compiler. This skill is the playbook
for verifying that every change preserves the compiler-engineering
properties — phase isolation, IR purity, total transformations,
deterministic output, fail-fast on invariant violation — and for
designing new transformations so those properties hold by construction.

## 1. Purpose

Ensure every change preserves the properties that make the compiler
correct, testable, and explainable: phases are isolated, IRs are
immutable, operators are total, transformations are pure functions,
and the same inputs always produce the same outputs.

## 2. When to use it (Review)

Apply when the change:

- Adds, removes, or reorders a planner phase
  (`preprocess` / `classify` / `resolve` / `plan` / `bridge` / `nested`
  / `composites` / `mn` / `home_grain`).
- Adds a new operator to `planning/algebra/operations.py` or relaxes a
  precondition on an existing one.
- Adds a new `PlanStep` payload type, a new `PlanOperation` enum value,
  or a new field on an existing payload.
- Modifies `codegen/transpiler.py`, `codegen/cte_optimizer.py`, or
  `codegen/dialect.py`.
- Adds new "metadata" on a plan step or state that downstream code
  reads.
- Changes the order in which validation, classification, or resolution
  happens.

## 3. When to use it (Design)

Apply *before* writing code when you are:

- Designing a new planner phase or splitting an existing one.
- Designing a new IR node (a new payload type, a new state field).
- Deciding what data a transformation consumes vs produces.
- Wondering whether a check belongs in parsing, planning, or codegen.
- Considering an "optimisation" that crosses a phase boundary
  (e.g. codegen consults the model). Usually the answer is *don't*;
  the question is whether the missing data should be on the plan step.

## 4. Methodology

1. **Locate the phase.** Compile the change against the planner phase
   ordering in `ARCHITECTURE.md §3` and `JOIN_ALGEBRA.md §7`. Confirm
   the change lives in a single phase; reject phase-spanning changes
   unless the abstraction is genuinely cross-cutting (e.g. `prefixes.py`
   is consulted by both planning and codegen because it produces names;
   that's the only acceptable cross-cut).
2. **Audit IR purity.** The IR types (`CalculationState`, `Column`,
   `PlanStep`, `QueryPlan`, `PlanPayload` subclasses) are frozen
   dataclasses. Every transformation returns a *new* IR value; nothing
   mutates an input. Verify the change does not add a setter, an
   `update_in_place`, or a `__post_init__` that mutates fields.
3. **Audit totality.** Every operator is `(state, args) → state`. The
   "no exceptions" version of total means: any precondition violation
   raises a typed `OSIError` *before* any state mutation could happen.
   No half-built states, no `Optional[State]` returns, no `None`-means-
   failure.
4. **Audit determinism.** The plan and SQL must be byte-identical for
   the same inputs. Confirm new code uses `prefixes.py` for synthetic
   names, sorted iteration over sets / dicts, and no hash-order
   dependence.
5. **Audit error taxonomy.** Every raised exception is `OSIError` (or
   subclass) with a code in `osi.errors.ErrorCode`. No `assert` in
   `src/`; no `raise Exception(...)`. The error message names the
   spec-level concept that failed, not the implementation detail.
6. **Audit pass ordering.** If the change adds a new phase, confirm:
   - Phases run in a fixed, declared order in `planner.py`.
   - Each phase consumes the output of an earlier phase and produces
     the input of a later phase — no back-edges.
   - The phase is testable in isolation (a unit test that runs only
     the new phase).

## 5. Checklists

### 5.1 Phase isolation

- [ ] Parsing does no planning; planning does no codegen; codegen does
      no semantic decisions (`ARCHITECTURE.md §1.1`).
- [ ] No phase reaches back into a previous phase's mutable state.
- [ ] Each phase's input and output types are concrete, frozen.

### 5.2 IR purity

- [ ] No new mutable IR type. Every new payload is frozen.
- [ ] No new top-level field on `CalculationState`, `Column`,
      `PlanStep`, or `QueryPlan` unless it carries information the
      *next* phase needs and is provably immutable.
- [ ] No `Optional` on IR fields used for "I'll fill it in later."
      Either populate at construction or compute it on demand.

### 5.3 Total transformations

- [ ] Every operator signature is `(state, args) → state` (or
      `(...) → state` for `source(...)`).
- [ ] Every precondition is checked *before* the transformation
      starts; failure raises `OSIError`.
- [ ] No operator returns `None` to signal failure.
- [ ] No operator catches its own exception and tries to recover.

### 5.4 Deterministic codegen

- [ ] New synthetic names come from `prefixes.py` counters.
- [ ] No new hash-order iteration over a `set` or `dict` in `planning/`
      / `codegen/` paths that determine output.
- [ ] No `random`, no `time.time()`, no `os.environ` access during
      planning or codegen.

### 5.5 Error taxonomy

- [ ] Every new failure has a typed `OSIError` and a code.
- [ ] The code is in Appendix C or in `_IMPLEMENTATION_EXTENSIONS`.
- [ ] The code's catalog row in `docs/ERROR_CODES.md` and
      `diagnostics/error_catalog.py` is updated in the same PR.
- [ ] No `raise Exception` / `raise ValueError` / `raise RuntimeError`
      in `src/`.
- [ ] No `assert` in `src/` (only valid as a "compiler bug, cannot
      continue" signal; prefer
      `raise OSIError(ErrorCode.E_INTERNAL_INVARIANT, ...)`).

### 5.6 Pass ordering and composability

- [ ] A new phase has a single entry function that takes prior output
      and returns the next IR.
- [ ] The phase is exercised by at least one unit test in isolation
      (not just through the end-to-end `Planner.plan`).
- [ ] If the phase rewrites the IR, the rewrite is idempotent (applying
      twice = applying once). Add a property test if not already
      enforced.

## 6. Triage rule: prefer deterministic enforcement

Findings from this skill walk a strict hierarchy. Apply this rule
whether you're using the skill to review existing code or to design new
code:

1. **Convert to a deterministic check** — property test, drift test,
   arch-test, mypy rule, lint rule. Preferred. Never regresses;
   applies to every future change automatically.
2. **Sharpen this skill's checklist** — if the finding revealed a
   missing angle, update this `SKILL.md` so future runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update `ARCHITECTURE.md` / `JOIN_ALGEBRA.md`
   / `ALGEBRA_LAWS.md` / `INFRA.md` and add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

The same rule, in its design-time framing: before writing code that
establishes a boundary or invariant, ask "can I add a deterministic
check that locks this in before the code lands?" If yes, write the
check in the same PR.

## 7. Existing deterministic checks this skill should leverage

| Check | What it enforces | Source |
|:--|:--|:--|
| `import-linter` contracts | Phase isolation at the import edge | `pyproject.toml` |
| `tests/properties/test_algebra_purity.py` | Operators do not mutate inputs | source |
| `tests/properties/test_algebra_totality.py` | Operators return a state or raise typed | source |
| `tests/properties/test_algebra_determinism.py` | Same inputs ⇒ same plan | source |
| `tests/properties/test_grain_closure.py` | Grain coarsens monotonically | source |
| `tests/properties/test_aggregate_idempotent.py` | Aggregate is idempotent over identity grouping | source |
| `tests/properties/test_project_idempotent.py` | Project is idempotent | source |
| `tests/properties/test_filter_commute.py` | Filter / aggregate commute laws hold | source |
| `tests/properties/test_merge_associative.py` | Merge is associative | source |
| `tests/properties/test_error_taxonomy.py` | Algebra raises only `OSIError` | source |
| `tests/properties/test_frozensql_canonical.py` | `FrozenSQL` equality is canonical-form-based | source |
| `tests/unit/test_operator_enum_sync.py` | `PlanOperation` enum and operator registry stay in sync | source |
| `mutmut` on `src/osi/planning/algebra/` ≥ 90% | Operators are tested against their stated laws | `INFRA.md §1.1` |

## 8. Example output format

```markdown
## CC-001 New phase reads the model directly instead of the plan

- **Severity**: P1 (phase boundary leak)
- **Location**: `impl/python/src/osi/planning/planner_X.py:88`
- **Finding**: The new `expand_role_playing_dim` phase imports
  `SemanticModel` and looks up the role-playing dimension by name on
  the model — but the prior phase already attached the resolved role
  to the plan step. The phase should consume the prior phase's output,
  not the model.
- **Triage**:
  1. (Deterministic) — Add a unit test that runs
     `expand_role_playing_dim` against a synthetic `PlanStep` with no
     `SemanticModel` in scope; the call must succeed. Lands in this PR.
  2. (Skill) — Add a "phases consume the prior phase's IR, not the
     model" bullet to §5.1.
  3. (Code) — Drop the `SemanticModel` import from this phase; pass
     the attached role-playing record on the step. Lands in this PR.
- **Invariants touched**: ARCHITECTURE.md §6.7.
```

## 9. Anti-patterns

- A new phase that reaches into the model "just for one lookup."
  Either attach the lookup result to the prior phase's output, or move
  the lookup into the prior phase.
- A planner phase that mutates the plan in place to "save an allocation."
  Mutation forfeits determinism, replayability, and the property tests.
- A new operator with a precondition checked *after* the transformation.
  Always before. Half-states are unobservable but bug-bearing.
- `assert` in `src/` to "guard an invariant." Use
  `raise OSIError(ErrorCode.E_INTERNAL_INVARIANT, ...)` so it survives
  `-O`, registers in the error taxonomy, and lands in the catalog.
- A second planner ("SimplePlanner") that "skips the bridge analysis"
  for "fast" queries. There is one planner (invariant 14). New rules
  are phases inside the existing planner.

## See also

- [`../../impl/python/ARCHITECTURE.md`](../../impl/python/ARCHITECTURE.md) §3, §5, §6.
- [`../../impl/python/docs/JOIN_ALGEBRA.md`](../../impl/python/docs/JOIN_ALGEBRA.md) — operator specification.
- [`../../impl/python/docs/ALGEBRA_LAWS.md`](../../impl/python/docs/ALGEBRA_LAWS.md) — machine-checked laws.
- [`bi-best-practices-review/SKILL.md`](../bi-best-practices-review/SKILL.md) — sister skill on grain / fan-out / bridge correctness.
- [`database-best-practices-review/SKILL.md`](../database-best-practices-review/SKILL.md) — sister skill on the codegen / SQL emission side.
