---
name: architectural-review
description: Review or design code against the OSI reference implementation's architectural invariants — the three-layer pipeline (parsing → planning → codegen), the closed algebra, one-way information flow, and the numbered invariants in impl/python/ARCHITECTURE.md §6. Use when adding code that crosses layer boundaries, introducing a new module, reviewing a design doc, or evaluating whether a feature can be implemented without breaking the pipeline contract.
---

# Architectural review

The OSI reference implementation is a closed, pure algebra over an
immutable semantic model, organised as a strict three-layer pipeline.
The architectural contract lives in
[`../../impl/python/ARCHITECTURE.md`](../../impl/python/ARCHITECTURE.md);
this skill is the playbook for verifying that contract holds — and for
designing new code so it cannot be broken in the first place.

## 1. Purpose

Ensure every change preserves the three-layer pipeline, the one-way
information flow, the closed algebra, and the numbered invariants in
`ARCHITECTURE.md §6`. Prefer locking new boundaries down with
deterministic checks (import-linter contracts, arch-tests, drift tests)
over relying on review vigilance.

## 2. When to use it (Review)

Apply this skill during code review when the change:

- Adds or modifies a module under `impl/python/src/osi/` (any layer).
- Introduces a new import edge between subpackages.
- Adds a new public function on a layer facade
  (`osi.parsing.__init__`, `osi.planning.__init__`, `osi.codegen.__init__`).
- Touches `planner_context.py`, `algebra/state.py`, `plan.py`, or any
  file mentioned in `ARCHITECTURE.md §3.4`.
- Adds a new algebra operator or relaxes a precondition on an existing
  one.
- Bypasses (or appears to bypass) an `ErrorCode` raise — e.g. catches
  and re-raises silently, or returns `None` instead of raising.
- Adds, removes, or reorders a phase in the planner.

## 3. When to use it (Design)

Apply this skill *before* writing code when you are:

- Designing a new feature that touches more than one layer.
- Sketching a new module or sub-package — to decide which layer it
  belongs in and which existing modules it may import.
- Promoting a deferred feature into the Foundation (see
  [`../../impl/python/CONTRIBUTING.md §8`](../../impl/python/CONTRIBUTING.md)).
- Introducing a new architectural concept (a new IR type, a new
  helper class, a new planner phase).
- Deciding whether a new behaviour should be a new algebra operator or
  a composition of existing ones.

At design time the goal is: *which existing deterministic check would
this design rely on, and which new one should land in the same PR to
lock the new boundary in?*

## 4. Methodology

The same six steps work for both review and design — at design time
they generate the checklist you build into the PR; at review time they
audit the PR you received.

1. **Locate the change on the layer map.** Open
   [`ARCHITECTURE.md §1`](../../impl/python/ARCHITECTURE.md#1-three-layer-pipeline)
   and identify every layer the change touches. If the change crosses
   more than one layer, ask whether the cross-cut is genuinely necessary
   or whether the abstraction needs sharpening (`§8 Where to add things`
   guidance).
2. **Trace the information flow.** Confirm data only moves down the
   pipeline (YAML → SemanticModel → QueryPlan → SQL). Codegen reaches
   *only* into the plan, never back into the model. Planning reaches
   into the model *only* through `PlannerContext`.
3. **Audit the import edges.** Run `import-linter` (`lint-imports` /
   `make lint`); confirm no new edge violates the contracts in
   `pyproject.toml [tool.importlinter]`. Inspect new edges by eye even
   if they pass — the linter only catches forbidden cross-layer imports,
   not in-layer drift.
4. **Verify algebra closure.** If the change touches `planning/algebra/`
   or any module that produces a `CalculationState`, confirm:
   - All new `CalculationState`s are produced by `source(...)` or by an
     algebra operator (never instantiated directly outside the algebra
     package, invariant 1).
   - All new types are `frozen=True` dataclasses (invariant 2).
   - All new operators take `(state, args) → state`, with no exceptions
     other than typed `OSIError` (invariants 3, 4, 9).
5. **Check error discipline.** Every new failure path raises an
   `OSIError` subclass with an `ErrorCode` from Appendix C *or* from
   the implementation-extensions list in `test_appendix_c_drift.py`.
   No bare `Exception`, `ValueError`, `TypeError`, `AssertionError` in
   `src/`.
6. **Confirm the deterministic check exists or is added.** For every
   invariant the change relies on, find the import-linter contract,
   arch-test, or drift test that enforces it. If none exists and the
   invariant is mechanically checkable, **add the check in the same
   PR**. This is the design-time output of this skill.

## 5. Checklists

### 5.1 The three-layer pipeline

- [ ] No import edge from `osi.parsing` to `osi.planning` or `osi.codegen`.
- [ ] No import edge from `osi.planning` to `osi.codegen`.
- [ ] No import edge from `osi.codegen` to `osi.parsing`.
- [ ] Diagnostics import only from `parsing`, `planning`, `common`, and
      `errors` — never from `codegen`.
- [ ] `common/` imports only from stdlib, `sqlglot`, `networkx`, and
      other `common/` modules.

### 5.2 One-way information flow

- [ ] Planning submodules receive `ctx: PlannerContext` rather than
      `model: SemanticModel` directly.
- [ ] Direct `osi.parsing.models` imports inside `planning/` are used
      for type annotations only; no submodule instantiates a parsed
      type or calls a top-level parsing function on its own
      (`ARCHITECTURE.md §6.7`).
- [ ] Codegen never reads `SemanticModel`, `Namespace`,
      `RelationshipGraph`, or any field that is not on the plan
      (`ARCHITECTURE.md §6.6`).

### 5.3 Algebra closure

- [ ] No `CalculationState` is constructed outside
      `osi.planning.algebra` (invariant 1).
- [ ] No mutation of an existing state — operators return new values
      (invariant 2).
- [ ] No `random`, `time`, `os.environ`, or filesystem access in
      `planning/` (invariant 3).
- [ ] Same `(model, query, dialect)` ⇒ same plan and same SQL,
      byte-identical (invariant 4 — guarded by `tests/properties/test_algebra_determinism.py`).
- [ ] Grain set on every state is non-empty for `source(...)` and only
      coarsens on `aggregate(...)` (invariant 5).

### 5.4 Error discipline

- [ ] Every new failure raises an `OSIError` subclass.
- [ ] The error code is in `osi.errors.ErrorCode` and either in
      `_APPENDIX_C_CODES` or in `_IMPLEMENTATION_EXTENSIONS` (with a
      one-line rationale) inside `tests/unit/test_appendix_c_drift.py`.
- [ ] The error message names the dataset / field / grain and suggests
      a fix (`ARCHITECTURE.md §7`).
- [ ] No bare `raise Exception(...)`, `raise ValueError(...)`, or
      `assert` inside `src/`.

### 5.5 Deterministic enforcement

- [ ] Each new architectural invariant the change introduces has an
      import-linter contract, an arch-test, or a drift test.
- [ ] If no mechanical check is possible, the invariant is documented
      in `ARCHITECTURE.md §6` *and* in `INFRA.md §4` as a decision log
      entry (so it cannot be relitigated without a new entry).

## 6. Triage rule: prefer deterministic enforcement

Findings from this skill walk a strict hierarchy. Apply this rule
whether you're using the skill to review existing code or to design new
code:

1. **Convert to a deterministic check** — import-linter contract,
   arch-test, drift test, mypy rule, lint rule. Preferred. Never
   regresses; applies to every future change automatically.
2. **Sharpen this skill's checklist** — if the finding revealed a
   missing angle, update this `SKILL.md` so future runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update `ARCHITECTURE.md` / a README /
   `INFRA.md` and add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

The same rule, in its design-time framing: before writing code that
establishes a boundary or invariant, ask "can I add a deterministic
check that locks this in before the code lands?" If yes, write the
check in the same PR.

## 7. Existing deterministic checks this skill should leverage

Cite these instead of re-inventing them; extend them when this skill
surfaces a missed angle.

| Check | What it enforces | Source |
|:--|:--|:--|
| `import-linter` contracts | One-way layer flow | `pyproject.toml [tool.importlinter]` |
| `mypy --strict` | Frozen / typed state, no `Any` leakage | `pyproject.toml [tool.mypy]` |
| `tests/unit/test_appendix_c_drift.py` | Every raised error code is either in the spec or documented as an extension | source file |
| `tests/properties/test_error_taxonomy.py` | Algebra raises only `OSIError` (mutation-protected) | source file |
| `tests/properties/test_algebra_purity.py` | Operators have no side effects | source file |
| `tests/properties/test_algebra_determinism.py` | Same inputs ⇒ same plan | source file |
| `tests/properties/test_algebra_totality.py` | Every operator returns a state or raises typed | source file |
| `tests/properties/test_grain_closure.py` | Operators preserve / coarsen grain monotonically | source file |
| `make audit-file-size` | 600/700 LOC cap | `Makefile`, `INFRA.md §1.2` |

If you find an architectural rule that *should* be in this table but
isn't, write the check (Phase D / triage step 1) and add the row.

## 8. Example output format

Produce `.review/<N>_architectural_review.md` with one section per
finding:

```markdown
## A-001 Codegen reaches into Namespace

- **Severity**: P0 (breaks ARCHITECTURE.md §6.6 one-way flow)
- **Location**: `impl/python/src/osi/codegen/transpiler.py:142`
- **Finding**: `transpiler._render_join` imports `Namespace` to resolve
  the right-hand identifier. Should resolve at planning time and pass
  the resolved identifier on the plan step.
- **Triage**:
  1. (Deterministic) — `import-linter` already forbids
     `osi.codegen → osi.parsing` but `Namespace` lives in
     `osi.parsing.namespace`; add a forbidden import for this specific
     module so the lint catches it next time. Lands in this PR.
  2. (Skill) — Add `Namespace` to the §5.1 checklist explicitly.
  3. (Code) — Move resolution to `planner._resolve_join_path`;
     extend `PlanStep` with the resolved identifier. Queue as a
     dedicated sprint item; out of scope for this PR.
- **Invariants touched**: §6.6, §6.7
```

## 9. Anti-patterns

- "We can break the layer just this once for performance." Forfeits
  determinism, explainability, and dialect portability simultaneously
  (`ARCHITECTURE.md §5.3`). Always wrong.
- "I'll add the check in the next PR." It never lands. The check is
  the work; without it the boundary is a wish.
- A new module that imports across two layers "because it's only
  helpers." Helpers belong in `common/` if cross-layer, in a layer
  module if not.
- A new `CalculationState` constructed by hand in a planner submodule
  to "save a step." Always indicates a missing operator or a missing
  step factory. Add the factory; don't break closure.
- A second `Planner`. There is one planner (invariant 14). If the new
  path needs different rules, the rules are a phase inside the existing
  planner, not a sibling class.

## See also

- [`../../impl/python/ARCHITECTURE.md`](../../impl/python/ARCHITECTURE.md) — the contract.
- [`../../impl/python/INFRA.md`](../../impl/python/INFRA.md) — quality gates and the file-size cap.
- [`../../impl/python/docs/JOIN_ALGEBRA.md`](../../impl/python/docs/JOIN_ALGEBRA.md) — the closed algebra deep-dive.
- [`../../impl/python/docs/ALGEBRA_LAWS.md`](../../impl/python/docs/ALGEBRA_LAWS.md) — machine-checked laws.
- [`compiler-best-practices-review/SKILL.md`](../compiler-best-practices-review/SKILL.md) — sister skill focused on phase ordering and IR purity.
