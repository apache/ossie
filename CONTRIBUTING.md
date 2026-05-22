# Contributing to OSI

Thanks for your interest in the Open Semantic Interchange (OSI). This
repository ships three artefacts that move together:

- **Proposal docs** under [`proposals/foundation-v0.1/`](proposals/foundation-v0.1/) —
  the normative spec for Foundation Tier v0.1.
- **Python reference implementation** under [`impl/python/`](impl/python/) —
  the parser, planner, and codegen that compile a semantic model to
  SQL.
- **Compliance suite** under [`compliance/foundation-v0.1/`](compliance/foundation-v0.1/) —
  engine-agnostic tests that verify any implementation matches the
  spec.

A change to one almost always implies a change to the others.

For implementation-specific guidance (setup, commands, PR checklist,
proposal lifecycle), see
[`impl/python/CONTRIBUTING.md`](impl/python/CONTRIBUTING.md). This
top-level file covers the contribution rules that apply across all
three artefacts.

---

## 1. The triage rule (load-bearing)

Every finding — whether it comes from a reviewer skill, a failing
test, a `make check` warning, a user bug report, or a design review —
walks this hierarchy, top-down:

1. **Convert to a deterministic check** — drift test, arch-test,
   `import-linter` contract, mypy rule, lint rule. Preferred. Never
   regresses; applies to every future change automatically.
2. **Sharpen the relevant skill's checklist** — if the finding revealed
   an angle a reviewer skill missed, update the `SKILL.md` so future
   runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update `ARCHITECTURE.md` / a README /
   `INFRA.md` and add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

The same rule, in its **design-time framing**: before writing code
that establishes a new boundary or invariant, ask "can I add a
deterministic check that locks this in before the code lands?" If
yes, write the check in the same PR.

This rule is not aspirational. It is the source of truth — every
reviewer skill copies it verbatim, every PR template asks the
contributor to apply it, and every audit closes the loop by
converting findings into deterministic checks rather than into
review reports.

---

## 2. The cadence rule

Reviews and designs use [reviewer skills](.agent-skills/REVIEWER_SKILLS.md)
to make sure we are looking for the right things at every change.

### Mandatory for any architectural change

Any change that touches behaviour, the planner, codegen, dialect
emission, or the algebra **must** run all three of these skills, both
at design time (to lock in boundaries up front) and at review time (to
verify them):

- [`bi-best-practices-review`](.agent-skills/bi-best-practices-review/SKILL.md) —
  grain awareness, fan-out / chasm trap, bridge dedup, conformed
  dimensions, semi-additive measures, holistic-over-fan-out rejection.
- [`compiler-best-practices-review`](.agent-skills/compiler-best-practices-review/SKILL.md) —
  phase boundaries, IR purity, totality, deterministic codegen, error
  taxonomy, pass ordering.
- [`database-best-practices-review`](.agent-skills/database-best-practices-review/SKILL.md) —
  SQL emission via AST not strings, identifier quoting, NULL ordering,
  multiset vs set semantics, dialect adapter isolation, FrozenSQL
  discipline.

### Run as relevant to the change

| If the change touches… | Run these skills |
|:---|:---|
| Layer boundaries, new modules, new IR types | [`architectural-review`](.agent-skills/architectural-review/SKILL.md) |
| Public APIs, layer facades, the CLI | [`interfaces-and-api-review`](.agent-skills/interfaces-and-api-review/SKILL.md) + [`code-encourages-correct-use-review`](.agent-skills/code-encourages-correct-use-review/SKILL.md) |
| Types, new dataclasses, mypy configuration | [`typing-enforcement-review`](.agent-skills/typing-enforcement-review/SKILL.md) |
| Spec sections, error codes, decisions, compliance tests | [`spec-coherence-review`](.agent-skills/spec-coherence-review/SKILL.md) |
| Docs that codify a rule (ARCHITECTURE / INFRA / READMEs) | [`doc-as-enforcement-review`](.agent-skills/doc-as-enforcement-review/SKILL.md) |

See [`.agent-skills/REVIEWER_SKILLS.md`](.agent-skills/REVIEWER_SKILLS.md)
for the full index, the deterministic checks each skill leverages, and
the recommended sweep order when running them as a set.

---

## 3. The design-time rule

Before writing code that establishes a new boundary or invariant:

1. **Consult the relevant skill's "Design use" section** for the
   patterns the skill cares about. Every skill has a
   `## 3. When to use it (Design)` block — it's the design-time
   counterpart to the review checklist.
2. **Identify the deterministic check that would catch a violation.**
   Drift test? Arch-test? Import-linter contract? mypy rule?
3. **If the check exists, cite it in the PR description.** If it
   doesn't and the invariant is mechanically checkable, **write the
   check in the same PR as the code that establishes the boundary**.
4. Don't defer the check to "a future review pass." It never lands;
   the boundary becomes a wish.

The "Existing deterministic checks this skill should leverage" table
inside each `SKILL.md` is the starting catalogue — pick the entries
that apply, and add new rows when you land new checks.

---

## 4. Pull-request checklist (top-level)

Use this in addition to the implementation-specific checklist in
[`impl/python/CONTRIBUTING.md §4`](impl/python/CONTRIBUTING.md). PRs
that touch only the compliance suite or the proposals docs can skip
the impl-specific items.

```
## Summary
<1-2 sentences — what changes and why>

## Artefacts touched
- [ ] proposals/foundation-v0.1/
- [ ] impl/python/
- [ ] compliance/foundation-v0.1/

## Skills consulted (cadence rule §2)
For any architectural change, all three are required:
- [ ] bi-best-practices-review
- [ ] compiler-best-practices-review
- [ ] database-best-practices-review
Plus any others relevant to the change.

## Triage applied (rule §1)
For each finding the skills surfaced, state which level applied:
- [ ] Deterministic check added (cite test / contract / mypy rule)
- [ ] Skill checklist sharpened (cite SKILL.md section)
- [ ] Documentation tightened (cite file / section)
- [ ] Queued as follow-up (cite issue / sprint item)

## Coherence (artefacts move together)
- [ ] If a spec section changed, the impl and the compliance test
      changed in this PR or in linked PRs that land together.
- [ ] If an ErrorCode changed, Appendix C, error_catalog.py, and at
      least one test changed in this PR.
- [ ] If a deferred feature was promoted, all five stages of
      `impl/python/CONTRIBUTING.md §8` were followed.

## Quality gates
- [ ] `cd impl/python && make check` passes locally (if impl touched)
- [ ] Compliance suite runs (if compliance touched)
- [ ] No new "review-only" architectural invariant added without a
      deterministic-check candidate noted in ARCHITECTURE.md §6.5
```

---

## 5. Where conversation happens

- **Spec amendments** → PR against `proposals/foundation-v0.1/` with
  the proposal lifecycle from
  [`impl/python/CONTRIBUTING.md §8`](impl/python/CONTRIBUTING.md).
- **Implementation discussion** → PR comments under `impl/python/`.
- **Compliance test additions / changes** → PR against
  `compliance/foundation-v0.1/tests/`; the metadata schema is in
  [`compliance/foundation-v0.1/SPEC.md`](compliance/foundation-v0.1/SPEC.md).
- **Infrastructure changes** (tooling, CI, quality gates) → PR + an
  update to [`impl/python/INFRA.md`](impl/python/INFRA.md) §3 roadmap
  and §4 decisions log.

---

## 6. Anti-patterns we will push back on

- "I'll add the check in the next PR." It never lands. The check is
  the work.
- A new `bool` parameter on a public function instead of an enum or a
  separate function.
- An `Optional[T]` return that means "lookup failed." Raise an
  `OSIError` instead.
- A new `ErrorCode` without an Appendix C row *and* without an entry
  in `_IMPLEMENTATION_EXTENSIONS`.
- An "exception" added to the layer-flow contract because of a
  specific need. Either the abstraction is wrong (refactor) or the
  contract is wrong (rewrite it).
- A new module that imports across two layers "for one helper." The
  helper belongs in `osi.common` or hasn't been factored correctly.
- A new infrastructure choice (test framework, linter, mutation tool)
  not recorded in `INFRA.md §4`.
- A new architectural invariant added to `ARCHITECTURE.md §6` without
  a row in `§6.5 Invariants enforced in code`.

---

## 7. Where to start

- New to the project? Read [`README.md`](README.md), then
  [`proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
  §1–§3.
- New to the reference implementation? Read
  [`impl/python/ARCHITECTURE.md`](impl/python/ARCHITECTURE.md) §1, then
  [`impl/python/docs/JOIN_ALGEBRA.md`](impl/python/docs/JOIN_ALGEBRA.md).
- About to contribute code? Read
  [`impl/python/CONTRIBUTING.md`](impl/python/CONTRIBUTING.md), then
  this file's §1–§4.
- About to review someone else's contribution? Open the appropriate
  skill from [`.agent-skills/REVIEWER_SKILLS.md`](.agent-skills/REVIEWER_SKILLS.md)
  and walk its checklist.
