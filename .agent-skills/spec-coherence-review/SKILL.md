---
name: spec-coherence-review
description: Review or design changes so the OSI Foundation spec, the Python reference implementation, and the compliance suite stay coherent. Covers the spec ↔ Appendix C ↔ ErrorCode axis, the proposals.yaml registry, the deferred-features gate, decisions.yaml ↔ tests, and the spec-section-refs citations. Use when adding a feature, an error code, a test, a decision, or any change that needs the three artifacts (spec, code, tests) to move together.
---

# Spec coherence review

A reference implementation is only useful if the spec, the impl, and
the compliance tests are *coherent*. This skill is the playbook for
keeping the three artifacts (spec, code, tests) in lockstep — and for
designing new features so the coherence is enforced by drift tests
rather than by review vigilance.

## 1. Purpose

Ensure every change that touches semantic behaviour touches all three
artifacts (spec, code, tests) in the same PR, with citations that
resolve and drift tests that fail if any side moves alone.

## 2. When to use it (Review)

Apply when the change:

- Adds, modifies, or removes a section in
  `proposals/foundation-v0.1/Proposed_OSI_Semantics.md`.
- Adds or modifies a `D-NNN` row in Appendix B.
- Adds or modifies an `E_*` / `E_NNNN` row in Appendix C.
- Adds, modifies, or removes a member of `osi.errors.ErrorCode`.
- Adds or modifies a row in
  `compliance/foundation-v0.1/decisions.yaml` or
  `compliance/foundation-v0.1/proposals.yaml`.
- Adds, modifies, or `xfail`s a test under
  `compliance/foundation-v0.1/tests/`.
- Promotes a deferred feature from
  `proposals/foundation-v0.1/Proposed_OSI_Semantics.md` §10 into the
  Foundation proper.
- Adds a citation like `(Spec: §X.Y)` or `(D-NNN)` anywhere in the
  repo.

## 3. When to use it (Design)

Apply *before* writing code when you are:

- Drafting a new Foundation feature (it starts as a spec section).
- Drafting a new compliance test for a behaviour the planner already
  implements (the test pins behaviour the spec should already mandate).
- Promoting a deferred feature — design which Stage-1..Stage-5 PRs
  will land which artifacts together
  (`impl/python/CONTRIBUTING.md §8`).
- Adding a new `D-NNN` decision (needs a code path that raises the
  decided error, plus a test that pins it).
- Adding a new error code that the planner can emit.

At design time, the goal is: *which artifact moves first, and which
drift test catches the others being out of sync?*

## 4. Methodology

1. **Find the home for the change.** Identify which of the three
   artifacts owns the canonical statement: spec (semantics), code
   (mechanism), or test (behavioural pin). All three must agree
   eventually; the order of the PRs depends on the type of change.
2. **Audit Appendix C.** Every `ErrorCode` enum value with prefix `E_`
   is either in `_APPENDIX_C_CODES` (in
   `tests/unit/test_appendix_c_drift.py`) or in
   `_IMPLEMENTATION_EXTENSIONS` with a one-line rationale.
3. **Audit `decisions.yaml`.** Every `D-NNN` row points at a test that
   exists, has the right `status`, and has the right `must_pass` flag.
4. **Audit `proposals.yaml`.** Every deferred feature that has a
   compliance test referencing it is registered with the right
   `status` (`proposed` / `foundation`).
5. **Audit citations.** `(Spec: §X.Y)` in code resolves to a heading
   in `Proposed_OSI_Semantics.md`. `(D-NNN)` resolves to a row in
   Appendix B. `(E_*)` resolves to an `ErrorCode` member.
6. **Audit the deferred-features gate.** Any deferred YAML key is
   rejected at parse time with `E_DEFERRED_KEY_REJECTED` or `E1105`.
   No deferred feature has partial plumbing in
   `planning/` or `codegen/`.

## 5. Checklists

### 5.1 Spec ↔ code

- [ ] Every new `ErrorCode` is in Appendix C or in
      `_IMPLEMENTATION_EXTENSIONS`.
- [ ] Every Appendix C row resolves to an enum value.
- [ ] Every `(Spec: §X.Y)` citation in source code resolves to a
      heading in the spec (Phase C `c1-specrefs`).
- [ ] Every deferred feature has an `E_DEFERRED_KEY_REJECTED` or
      `E1105` raise path; no partial plumbing.

### 5.2 Spec ↔ tests

- [ ] Every `D-NNN` decision in `decisions.yaml` has at least one test
      with `decision: D-NNN` in metadata.
- [ ] Every Appendix C row has at least one test that pins the error
      code (positive: the spec says when it should fire; negative:
      adapters that don't implement the rule produce it correctly).
- [ ] Every `proposals.yaml` entry has at least one test in
      `tests/<area>/<difficulty>/` with `required_features:
      [<proposal_id>]`.

### 5.3 Code ↔ tests

- [ ] Every new code path that can raise an error has a unit test or
      compliance test pinning the code.
- [ ] No `xfail` without an `xfail_reason` referencing a sprint or a
      `D-NNN` decision.
- [ ] No `xfail` for a `must_pass` decision (use
      `decisions.yaml` `must_pass: false` or a different status).

### 5.4 Deferred-feature gate

- [ ] Every deferred YAML key (`filter.expression`, `reset`, `joins.using_relationships`, …)
      raises `E_DEFERRED_KEY_REJECTED` or `E1105` at parse time.
- [ ] No planner / codegen module imports a "future-only" model field.
- [ ] Any feature flag for an experimental capability
      (e.g. `experimental_exists_in`) flips a parse-time rejection,
      not a runtime branch deep in the planner.

### 5.5 Citation conventions

- [ ] `(Spec: §X.Y)` ↔ `Proposed_OSI_Semantics.md` heading.
- [ ] `(D-NNN)` ↔ Appendix B row.
- [ ] `(E_*)` / `(E1NNN)` ↔ `ErrorCode` enum member.
- [ ] `(T-NNN)` ↔ test under `compliance/foundation-v0.1/tests/`.

## 6. Triage rule: prefer deterministic enforcement

Findings from this skill walk a strict hierarchy. Apply this rule
whether you're using the skill to review existing code or to design new
code:

1. **Convert to a deterministic check** — drift test (Appendix C vs
   enum, decisions.yaml vs tests, spec-section-refs, proposals.yaml
   vs `required_features`). Preferred. Never regresses; applies to
   every future change automatically.
2. **Sharpen this skill's checklist** — if the finding revealed a
   missing angle, update this `SKILL.md` so future runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update `ARCHITECTURE.md` / `CONTRIBUTING.md
   §8` / `INFRA.md` and add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

The same rule, in its design-time framing: before writing a new spec
section, ask "what drift test will keep this in sync with the code and
tests?" If the answer is "none," design one before writing the spec.

## 7. Existing deterministic checks this skill should leverage

| Check | What it enforces | Source |
|:--|:--|:--|
| `tests/unit/test_appendix_c_drift.py` | Appendix C ↔ `ErrorCode` | source |
| `compliance/harness/proposals_check.py` (referenced in CONTRIBUTING §8 Stage 2) | `proposals.yaml` ↔ test metadata | source |
| `compliance/foundation-v0.1/tests/.../metadata.yaml` `decision: D-NNN` | Decision ↔ test | source |
| Phase C `c1-specrefs` (added) | `(Spec: §X.Y)` citations resolve | `tests/unit/test_spec_section_refs_drift.py` |
| Phase C `c2-invariants` (added) | ARCHITECTURE invariants ↔ enforcement | `tests/unit/test_arch_invariants_drift.py` |
| `test_registry_yaml.py` (already present) | `decisions.yaml` paths match filesystem | source |

## 8. Example output format

```markdown
## SC-001 New `E_MIXED_AGGREGATION_LEVEL` is in the enum but not in Appendix C

- **Severity**: P0 (drift test would fail; spec ↔ code out of sync)
- **Location**:
  - `impl/python/src/osi/errors.py:E_MIXED_AGGREGATION_LEVEL`
  - `proposals/foundation-v0.1/Proposed_OSI_Semantics.md` Appendix C
    (missing row)
- **Finding**: A new code was added to handle a planner-level case
  but the corresponding Appendix C row and the
  `_APPENDIX_C_CODES` entry are missing.
- **Triage**:
  1. (Deterministic) — `test_appendix_c_drift.py` would fail this
     change; confirm it does. If the new code is an implementation
     extension (not Foundation-level), document it in
     `_IMPLEMENTATION_EXTENSIONS`. If it's spec-level, add the
     Appendix C row.
  2. (Skill) — Add an explicit "new ErrorCode" bullet to §5.1 (done).
  3. (Spec) — Decide whether this is a Foundation rule or an
     extension. If Foundation, the spec PR lands first; the code PR
     references it.
```

## 9. Anti-patterns

- A new `ErrorCode` without a corresponding Appendix C row *and*
  without a `_IMPLEMENTATION_EXTENSIONS` entry. Drift test catches it,
  but the design choice should be made up-front.
- An `xfail` for a `must_pass` decision. Adjusts the test to make CI
  green; hides spec ↔ impl drift.
- A `(D-NNN)` citation that points at a missing or renamed decision
  row. Use the citation-resolves drift test.
- A deferred-feature spec section with partial plumbing in the
  planner. Plumbing for deferred features is forbidden; either the
  feature is rejected at parse time or the spec has moved.
- A new `proposals.yaml` entry without a corresponding test. The
  proposal-check CI would fail; but more importantly, an unreferenced
  proposal has no behavioural pin.

## See also

- [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md) Appendix B (decisions) + Appendix C (error codes).
- [`../../impl/python/CONTRIBUTING.md`](../../impl/python/CONTRIBUTING.md) §8 — proposal ratification lifecycle.
- [`../../compliance/foundation-v0.1/decisions.yaml`](../../compliance/foundation-v0.1/decisions.yaml) — decision registry.
- [`../../compliance/foundation-v0.1/proposals.yaml`](../../compliance/foundation-v0.1/proposals.yaml) — deferred-feature registry.
- [`doc-as-enforcement-review/SKILL.md`](../doc-as-enforcement-review/SKILL.md) — sister skill on the broader doc ↔ code axis.
