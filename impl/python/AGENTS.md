# AGENTS.md — Guidance for AI coding agents working on `impl/python`

This file is read by AI coding agents before they make changes. It
summarizes the non-negotiable rules and the most common pitfalls.

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
   [`../../proposals/foundation-v0.1/JOIN_ALGEBRA.md`](../../proposals/foundation-v0.1/JOIN_ALGEBRA.md).
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
   [`DATA_TESTS.md`](../../proposals/foundation-v0.1/DATA_TESTS.md) and a
   runnable case in the compliance suite.
6. **Mutation testing is a hard gate.** A surviving mutation in
   `src/osi/planning/algebra/` is a P0. See [`INFRA.md §1.1`](INFRA.md).

## Before modifying code

- Read [`ARCHITECTURE.md`](ARCHITECTURE.md) §6 "Architectural invariants".
- Check [`INFRA.md §3`](INFRA.md) for an in-progress infrastructure item
  that might overlap with your work.
- If you are adding or changing an algebra operator, read
  [`JOIN_ALGEBRA.md`](../../proposals/foundation-v0.1/JOIN_ALGEBRA.md)
  in full and check the corresponding property tests in
  `tests/properties/`.

## Hard rules

| Rule | Enforcement |
|:---|:---|
| No raw-string SQL (no f-strings, no `+`, no `.format()` for SQL) | CI grep + flake8 rule |
| No file in `src/osi/` exceeds 600 LOC | CI audit |
| One-way imports: `parsing ← planning ← codegen`; `common` by all | `import-linter` |
| No import from `specs/deferred/` symbols into `src/osi/` | `import-linter` |
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

## Running the project

Always activate the project-local venv; do not use the repo-root one.

```bash
cd impl/python
source .venv/bin/activate        # or: `. .venv/bin/activate`
make check                       # lint + type + tests
make mutation-fast               # mutation on algebra only
```

See [`README.md`](README.md) and [`RUNNING_TESTS.md`](RUNNING_TESTS.md)
for full entry points.
