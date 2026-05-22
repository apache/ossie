---
name: doc-as-enforcement-review
description: Review or design documentation so it stays mechanically consistent with the code via drift tests, layer READMEs, and citation conventions. Use when adding ARCHITECTURE.md content, a new layer README, a spec section reference in code, an example in a README, or any doc that the codebase mechanically depends on. Sister skill to spec-coherence-review (which targets the spec ↔ impl axis specifically).
---

# Doc as enforcement review

Documentation is a load-bearing component of the reference
implementation; it tells contributors and external implementers what
the invariants are. To stay useful, every doc claim that *can* be
mechanically checked *must* be — otherwise the doc rots and reviewers
lose their reference. This skill is the playbook for both writing
docs and converting documented invariants into drift tests.

## 1. Purpose

Ensure every documented architectural claim, layer README "what's
here" table, code citation of a spec section, and runnable README
example is backed by a drift test that fails when the doc and the
code disagree.

## 2. When to use it (Review)

Apply when the change:

- Adds, modifies, or removes a section in `impl/python/ARCHITECTURE.md`,
  `INFRA.md`, `SPEC.md`, or any `docs/*.md` under `impl/python/`.
- Adds, modifies, or removes a layer `README.md` (under
  `impl/python/src/osi/<layer>/`).
- Adds a citation like `(Spec: §X.Y)` or `(D-NNN)` or `(F-NN)` inside
  source code or a docstring.
- Adds a runnable example to a README or a docs file.
- Adds or modifies a `docs/ERROR_CODES.md` row.
- Adds a new top-level doc anywhere in the repo.

## 3. When to use it (Design)

Apply *before* writing docs when you are:

- Designing the structure of a new `*.md` file (which sections, how
  cross-references work).
- Designing how a doc invariant ("helpers never import the model") gets
  enforced in code.
- Deciding which doc is "load-bearing" vs "narrative." Load-bearing
  docs need drift tests; narrative docs do not.
- Designing how examples in a README get exercised in CI.
- Adding a new doc-citation convention (e.g. a new tag like `(I-NN)`).

## 4. Methodology

1. **List the load-bearing claims.** For every doc the change touches,
   list claims that are mechanically checkable: a numbered invariant,
   a section header, a path reference, a code example, a list of
   modules in a layer.
2. **Check each claim has a drift test.** If a claim is mechanically
   checkable and no drift test exists, the doc is unenforced — every
   future edit can silently break it.
3. **Audit citations.** Every `(Spec: §X.Y)`, `(D-NNN)`, `(F-NN)`,
   `(I-NN)` in source code or docs cites a real anchor in the
   referenced document. Phase C's spec-section-refs drift test
   enforces this for `(Spec: §X.Y)`; new tag families need new drift
   tests.
4. **Audit examples.** Every Python / shell example in a README is
   either commented as "illustrative only" or is exercised in CI
   (`pytest --doctest-glob='*.md'` or a dedicated test).
5. **Audit cross-references.** Every link to a sibling doc is relative
   and resolves. Every link to an external URL has a fallback path
   (the rule it enforces should be self-contained in the repo).
6. **Audit deletion.** When removing a doc section, search the rest of
   the repo for citations to that section. A removed `§3.5` is a broken
   link in every catalog row, error message, and SKILL.md that cited it.

## 5. Checklists

### 5.1 Load-bearing doc inventory

- [ ] `ARCHITECTURE.md` invariants are numbered and each cites a code
      location or an existing arch-test.
- [ ] `INFRA.md §3` roadmap items reference the sprint that completed
      them or `planned` / `in-progress` status.
- [ ] `INFRA.md §4` decision-log entries cover every settled
      infrastructure choice.
- [ ] Every layer's `README.md` is present and lists every public
      symbol in that layer's `__init__.py`.
- [ ] `docs/ERROR_CODES.md` mirrors `osi.errors.ErrorCode`.

### 5.2 Drift tests

- [ ] `tests/unit/test_appendix_c_drift.py` — Appendix C vs
      `ErrorCode`.
- [ ] `tests/unit/test_operator_enum_sync.py` — `PlanOperation` enum
      vs operator dispatch.
- [ ] Phase C `c1-specrefs` — `(Spec: §X.Y)` citations resolve.
- [ ] Phase C `c2-invariants` — `ARCHITECTURE.md §6` numbered
      invariants are listed in `pyproject.toml` import-linter or in a
      named arch-test.
- [ ] Phase C `c3-readme` — README examples actually run.
- [ ] Phase C `c4-layer-readme` — Layer README "Modules" table matches
      the `.py` files in the folder.

### 5.3 Citation conventions

- [ ] `(Spec: §X.Y)` cites a heading in
      `proposals/foundation-v0.1/Proposed_OSI_Semantics.md`.
- [ ] `(D-NNN)` cites a row in Appendix B of the spec.
- [ ] `(E_NNN)` or `(E1NNN, E2NNN, …)` cites a member of
      `osi.errors.ErrorCode`.
- [ ] `(I-NN)` cites an `INFRA.md §3` roadmap item.
- [ ] `(F-NN)` cites a finding in a `.review/<n>_*.md` report.
- [ ] `(T-NNN)` cites a test under `compliance/foundation-v0.1/tests/`.

### 5.4 Examples in docs

- [ ] Python examples in `impl/python/README.md` are either commented
      as illustrative or exercised by a test under
      `tests/integration/readme/` (Phase C `c3-readme`).
- [ ] Shell examples in skill files / READMEs use commands that exist
      (`make check`, not `make test:fast` if no such target).

### 5.5 Deletion / rename safety

- [ ] Before removing a doc section, `rg` for the section title and
      anchor across the repo.
- [ ] When renaming, leave a redirect block (`> moved to §X.Y`) for at
      least one release cycle.

## 6. Triage rule: prefer deterministic enforcement

Findings from this skill walk a strict hierarchy. Apply this rule
whether you're using the skill to review existing code or to design new
code:

1. **Convert to a deterministic check** — drift test, doctest, anchor
   check, link checker. Preferred. Never regresses; applies to every
   future change automatically.
2. **Sharpen this skill's checklist** — if the finding revealed a
   missing angle, update this `SKILL.md` so future runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update the relevant doc and add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

The same rule, in its design-time framing: before writing a new doc
section, ask "what drift test will keep this in sync with the code?"
If the answer is "none," consider whether the section is load-bearing
or narrative; if load-bearing, the drift test lands in the same PR.

## 7. Existing deterministic checks this skill should leverage

| Check | What it enforces | Source |
|:--|:--|:--|
| `tests/unit/test_appendix_c_drift.py` | Appendix C ↔ `ErrorCode` enum | source |
| `tests/unit/test_operator_enum_sync.py` | `PlanOperation` ↔ operator registry | source |
| `tests/unit/test_synthetic_naming_invariants.py` | Synthetic names use `prefixes.py` | source |
| Phase C `c1-specrefs` (added) | `(Spec: §X.Y)` citations resolve | `tests/unit/test_spec_section_refs_drift.py` |
| Phase C `c2-invariants` (added) | ARCHITECTURE invariants ↔ import-linter contracts | `tests/unit/test_arch_invariants_drift.py` |
| Phase C `c3-readme` (added) | README examples run | `tests/integration/readme/` |
| Phase C `c4-layer-readme` (added) | Layer README modules table ↔ filesystem | `tests/unit/test_layer_readme_drift.py` |

## 8. Example output format

```markdown
## D-001 §3.4 module map lists `windows.py`; file has been moved to `common/`

- **Severity**: P1 (broken cross-reference)
- **Location**: `impl/python/ARCHITECTURE.md §3.4`
- **Finding**: `§3.4 Module map` lists `windows.py` under
  `planning/`, but the file lives at
  `src/osi/common/windows.py` (moved during F-9 cleanup).
- **Triage**:
  1. (Deterministic) — Phase C `c4-layer-readme` will surface this
     class of drift automatically. Verify the test fails on a
     manufactured mismatch; if not, sharpen the assertion.
  2. (Skill) — Add `§3.4 Module map ↔ filesystem` to §5.1.
  3. (Doc) — Update §3.4 to move `windows.py` to the `common/` row.
     Lands in this PR.
```

## 9. Anti-patterns

- A new "load-bearing" doc claim without a drift test. The claim will
  drift; reviewers will then ignore the doc.
- A doc that explains "the code should do X" without a test that fails
  when X is violated. Either turn X into an arch-test or remove the
  claim.
- Examples in READMEs that are subtly out of date — wrong import
  path, wrong CLI flag. Exercise them in CI or mark them illustrative.
- A renamed module without a redirect in the docs that pointed to it.
- A new tag family (`(R-NN)`, `(S-NN)`) in citations without a drift
  test that verifies the citation resolves.

## See also

- [`spec-coherence-review/SKILL.md`](../spec-coherence-review/SKILL.md) — sister skill on the spec ↔ impl axis.
- [`../../impl/python/ARCHITECTURE.md`](../../impl/python/ARCHITECTURE.md) — the load-bearing architectural doc.
- [`../../impl/python/INFRA.md`](../../impl/python/INFRA.md) — the load-bearing infrastructure roadmap and decisions log.
