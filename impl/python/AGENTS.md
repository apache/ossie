# AGENTS.md — Guidance for AI coding agents working on `impl/python`

This file is read by AI coding agents before they make changes. It covers
orientation, non-negotiable rules, reviewer-skill cadence, and common pitfalls.

## Quick orientation

- **Project type:** Python package — reference compiler for the Foundation of
  the Open Semantic Interchange (OSI) standard.
- **Authoritative spec:** [`../../proposals/foundation-v0.1/`](../../proposals/foundation-v0.1/)
  — in particular
  [`Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
  (`osi_version: "0.1"`),
  [`SQL_EXPRESSION_SUBSET.md`](../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md)
  (`OSI_SQL_2026` default dialect),
  [`JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md),
  [`DATA_TESTS.md`](../../compliance/foundation-v0.1/DATA_TESTS.md) (T-NNN vectors).
- **Implementation docs:** [`SPEC.md`](SPEC.md), [`ARCHITECTURE.md`](ARCHITECTURE.md),
  [`INFRA.md`](INFRA.md).
- **Companion compliance suite:** [`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/)
  — exercises every D-NNN in Appendix B of the Foundation spec.

## First principles (do not violate)

1. **The Foundation is thin on purpose.** If a feature is not in
   [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
   (`osi_version: "0.1"`), it is out of scope. The deferred list in §10
   of that spec is normative. Adding speculative plumbing for deferred
   features is the #1 way this project gets derailed.
2. **Cleanliness over backwards compatibility.** This implementation has
   not shipped. When a name, error code, public API, or YAML key
   changes, delete the old one in the same sprint. No deprecation
   shims, no legacy aliases, no compat flags. See `SPEC.md` header for
   the full project-wide rule.
3. **The algebra is the correctness boundary.** Every compiler
   transformation is a composition of the operators in
   [`docs/JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md).
   Do not invent a new one. Do not bypass preconditions.
4. **No silent wrong SQL.** Any semantics the compiler cannot handle
   correctly raise a typed `OSIError` with an error code from
   **Appendix C of
   [`Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)**.
   [`docs/ERROR_CODES.md`](docs/ERROR_CODES.md) is the implementation
   mirror; Appendix C is the source of truth. Returning plausibly-wrong
   SQL is the worst possible outcome.
5. **Every feature needs tests at five layers.** Unit + property +
   golden + E2E + compliance
   ([`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/)).
   See [`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md) and
   `SPEC.md §9`. Every D-NNN row in Appendix B has at least one `T-NNN`
   vector in
   [`DATA_TESTS.md`](../../compliance/foundation-v0.1/DATA_TESTS.md) and a
   runnable case in the compliance suite.
6. **Mutation testing is a hard gate.** A surviving mutation in
   `src/osi/planning/algebra/` is a P0. See [`INFRA.md §1.1`](INFRA.md).

## Before modifying code

- Read [`ARCHITECTURE.md`](ARCHITECTURE.md) §6 "Architectural invariants"
  including §6.5 "Invariants enforced in code" (the catalog mapping
  each invariant to its deterministic check).
- Check [`INFRA.md §3`](INFRA.md) for an in-progress infrastructure item
  that might overlap with your work.
- If you are adding or changing an algebra operator, read
  [`JOIN_ALGEBRA.md`](docs/JOIN_ALGEBRA.md)
  in full and check the corresponding property tests in
  `tests/properties/`.
- For any architectural change (planner, codegen, dialect, algebra),
  consult all three of the BI / Compiler / Database best-practices
  skills — at design time *and* at review time (see Reviewer skills below).

## Reviewer skills (load-bearing — use at design time AND review time)

The repo carries dual-purpose reviewer skills under
[`../../.agent-skills/`](../../.agent-skills/) — each one is usable
both to *review* an existing change and to *design* a new one. The
index is [`../../.agent-skills/REVIEWER_SKILLS.md`](../../.agent-skills/REVIEWER_SKILLS.md).

**Cadence rule (from [`../../CONTRIBUTING.md §2`](../../CONTRIBUTING.md)).**
Any architectural change must consult all three of these skills, both
at design time and at review time:

- `bi-best-practices-review` — grain / fan-out / bridge / conformed
  dims / semi-additive measures.
- `compiler-best-practices-review` — phase boundaries / IR purity /
  totality / determinism / error taxonomy.
- `database-best-practices-review` — SQL emission via AST / identifier
  quoting / NULL ordering / multiset semantics / dialect isolation.

Other skills (architectural, interfaces, typing, doc-as-enforcement,
code-encourages-correct-use, spec-coherence) run as relevant to the
change.

Cite the skill(s) you consulted in the PR description per
[`../../CONTRIBUTING.md §4`](../../CONTRIBUTING.md).

## Triage rule (applies to every finding you produce or receive)

Findings — from a reviewer skill, a failing test, a lint warning, a
user bug — walk this hierarchy top-down:

1. **Convert to a deterministic check** — drift test, arch-test,
   import-linter contract, mypy rule, lint rule. Preferred.
2. **Sharpen the relevant skill's checklist** — update the `SKILL.md`
   so future runs catch the missed angle.
3. **Tighten documentation** — `ARCHITECTURE.md` / README / `INFRA.md`.
4. **Queue as a code-change sprint item** — last resort.

Design-time framing: before writing code that establishes a new
boundary or invariant, ask "can I add a deterministic check that
locks this in before the code lands?" If yes, write the check in the
same PR. Don't defer it.

## Hard rules

| Rule | Enforcement |
|:---|:---|
| No raw-string SQL (no f-strings, no `+`, no `.format()` for SQL) | CI grep + flake8 rule |
| No file in `src/osi/` exceeds 600 LOC | CI audit |
| One-way imports: `parsing ← planning ← codegen`; `common` by all | `import-linter` |
| Strict mypy; `# type: ignore` requires `[<code>]  # reason: <text>` | flake8 + CI |
| Tests assert on `error.code`, never on message text | Code review |
| No `@pytest.skip` without a platform-specific reason | Code review |
| Every new `ErrorCode` is a value listed in Appendix C of the Foundation spec and has a `T-NNN` test in `DATA_TESTS.md` | Code review |
| No legacy alias / deprecation shim / compat flag for any name that changes during a sprint | Code review (cleanliness gate, `SPEC.md` header) |

## Common pitfalls

1. **Importing `SemanticModel` directly inside `planning/` helpers.**
   Don't — receive `ctx: PlannerContext` instead. The model lives on
   the context and nowhere else.
2. **Building `CalculationState` by hand.** Don't — only the algebra
   creates states. Outside the algebra, use `source(...)` as the
   starting point.
3. **Adding a plan field to work around codegen needing model info.**
   First confirm the plan actually lacks the information; if so, add
   the field to `PlanStep` rather than having codegen re-read the
   model.
4. **Making a property test "flaky" by adding `@example` cases instead
   of narrowing the strategy.** Strategy narrowing is the right fix;
   `@example` is for specific regression seeds after a shrunk
   counterexample.
5. **Skipping golden refresh because "the diff is big".** If the diff
   is big, the plan changed substantially — explain why in the PR.

## Tooling

This project has its own isolated virtualenv and pre-commit hooks.
Always activate the project-local venv; do not use the repo-root one.

```bash
cd impl/python
source .venv/bin/activate
```

```bash
make install-dev         # create .venv, install deps + pre-commit hooks
make format              # black + isort
make lint                # flake8 + mypy + import-linter
make test                # pytest (unit + property + golden + E2E)
make check               # lint + test
make mutation-fast       # mutation on the algebra module
make mutation            # full mutation run
```

Before committing: run `make check`. A surviving mutation in
`src/osi/planning/algebra/` from `make mutation-fast` is a P0 — kill
it first. Cite the invariant number from `ARCHITECTURE.md §6` in the
PR description when your change touches one.

See [`README.md`](README.md) and [`RUNNING_TESTS.md`](RUNNING_TESTS.md)
for full entry points.
