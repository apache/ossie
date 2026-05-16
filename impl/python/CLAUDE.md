# CLAUDE.md — Guidance for Claude agents in this project

## Quick orientation

- **Project type:** Python package — reference compiler for the
  Foundation of the Open Semantic Interchange (OSI) standard.
- **Authoritative spec:** [`../../proposals/foundation-v0.1/`](../../proposals/foundation-v0.1/)
  — in particular
  [`Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
  (Foundation, `osi_version: "0.1"`),
  [`SQL_EXPRESSION_SUBSET.md`](../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md)
  (`OSI_SQL_2026` default dialect),
  [`JOIN_ALGEBRA.md`](../../proposals/foundation-v0.1/JOIN_ALGEBRA.md),
  [`DATA_TESTS.md`](../../proposals/foundation-v0.1/DATA_TESTS.md)
  (T-NNN vectors).
- **Implementation docs:** [`SPEC.md`](SPEC.md), [`ARCHITECTURE.md`](ARCHITECTURE.md),
  [`INFRA.md`](INFRA.md).
- **Companion compliance suite:** [`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/)
  — exercises every D-NNN in Appendix B of the Foundation spec.

## Project-local tooling

This project has its own isolated virtualenv and its own pre-commit hooks.
Always activate the project-local venv:

```bash
cd impl/python
source .venv/bin/activate
```

Commands:

```bash
make install-dev         # creates .venv, installs deps, installs pre-commit hooks
make format              # black + isort
make lint                # flake8 + mypy + import-linter
make test                # pytest (unit + property + golden + E2E)
make check               # lint + test
make mutation-fast       # mutation on the algebra module (~5 min)
make mutation            # full mutation run (~30 min)
```

See [`RUNNING_TESTS.md`](RUNNING_TESTS.md) for a one-page guide to the
full test pyramid and the readable test report.

## Before committing

Run `make check`. If `make mutation-fast` shows a surviving mutation in
`src/osi/planning/algebra/`, that is a P0 — kill the mutation first.

## What NOT to do

- **Do not** add plumbing for features listed in `specs/deferred/` or in
  §10 of [`Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md).
  The Foundation is thin on purpose. Use `E_DEFERRED_KEY_REJECTED` to
  reject deferred YAML keys at parse time.
- **Do not** add a deprecation shim, legacy alias, or compat flag when
  a name / error code / API changes. The Foundation has not shipped;
  cleanliness over backwards-compat per `SPEC.md`.
- **Do not** invent an error code outside Appendix C of
  [`Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md).
  If you need one, add a `D-NNN` row in Appendix B and an `E_*` row in
  Appendix C in the same PR, then a `T-NNN` test in
  [`DATA_TESTS.md`](../../proposals/foundation-v0.1/DATA_TESTS.md).
- **Do not** use f-strings or string concatenation to build SQL. Ever.
- **Do not** silence a property test by tightening the strategy or by
  adding an `assume()` that makes it vacuous.
- **Do not** refresh golden tests without a PR note explaining which
  intentional behavior change justifies the update.

## When in doubt

Read [`AGENTS.md`](AGENTS.md) for the non-negotiable rules. Then read
[`ARCHITECTURE.md`](ARCHITECTURE.md) for the numbered invariants. Cite
the invariant number in the PR description when your change touches one.
