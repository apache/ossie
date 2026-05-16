---
name: run-osi-python-tests
description: Run the full test pyramid for the OSI Python reference implementation (impl/python) including unit, property, golden, e2e, adapter-smoke, lint, typecheck, architecture, coverage, and mutation testing; then surface the consolidated readable report. Use when the user asks to run impl/python tests, validate the OSI Python implementation, generate a test report, run mutation tests, or check pre-PR readiness for impl/python.
---

# Run OSI Python tests

Run every test category for `impl/python/` and surface a single readable
Markdown report. Mutation testing is included by default in fast mode (~5 min).

## Instructions

### 1. Confirm scope (one quick question, only if not specified)

Before running, decide between:

- **Pre-PR fast check** (default): `--with-mutation-fast` (~5–10 min total)
- **Full pre-merge check**: `--with-mutation` (full mutation ~30 min)
- **Iteration check**: `--skip-static` (only test categories)

If the user didn't say, default to **pre-PR fast check** and tell them you're
doing so.

### 2. Run

```bash
cd impl/python
scripts/run_all_tests.sh --with-mutation-fast
```

Other variants:

```bash
scripts/run_all_tests.sh                       # static + tests, no mutation
scripts/run_all_tests.sh --with-mutation       # full mutation (~30 min)
scripts/run_all_tests.sh --skip-static         # only test categories
```

The script:

- Never aborts on the first failure — every stage runs.
- Captures structured raw output under `test-results/raw/`.
- Writes the consolidated report at `test-results/REPORT.md`.
- Exits non-zero if any stage failed.

### 3. Read the report and report findings

Read `impl/python/test-results/REPORT.md`. Surface to the user:

1. **Overall** PASS / FAIL banner.
2. **Stage summary** (one line per stage) — note any FAIL stages and link the
   raw log path `test-results/raw/<stage>.log`.
3. **Test counts** — total tests run, failures, errors, skipped.
4. **Coverage** — line %, branch %.
5. **Mutation** — score %, surviving mutants. **A surviving mutation in
   `src/osi/planning/algebra/` is a P0** (INFRA.md §1.1).
6. **Failing tests** — list each with its category. For each failing test,
   read its raw log under `test-results/raw/` and quote the relevant
   pytest failure block.
7. **Slowest 10 tests** if any are unexpectedly slow (>5 s).

If overall PASS, end with a short "ready to commit / PR" sentence.

If anything failed, end with a numbered remediation list pointing to the
log lines that drove each finding.

### 4. Do NOT

- Do not re-run a single failing test before reading the report — surface
  everything from the first pass.
- Do not modify code to make a test pass without explicit user instruction.
- Do not refresh golden snapshots automatically. `make golden-refresh` is
  an explicit user action; the report writer will flag golden failures.
- Do not skip mutation testing silently. If the user said "run the tests",
  mutation-fast is part of the default contract this skill exposes.

## See also

- [`impl/python/RUNNING_TESTS.md`](../../../impl/python/RUNNING_TESTS.md) —
  the longer human-facing guide; the script + this skill are the agent path.
- [`impl/python/Makefile`](../../../impl/python/Makefile) — every category
  exists as a `make` target if you need to run one in isolation.
- [`impl/python/INFRA.md`](../../../impl/python/INFRA.md) — quality
  thresholds the report compares against.
