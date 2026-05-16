# Running the tests

This page is a one-stop guide to running every test category for the OSI
Python reference implementation and reading the consolidated report.

If you want the short version: run

```bash
scripts/run_all_tests.sh --with-mutation-fast
```

and open `test-results/REPORT.md` when it finishes.

---

## 0. Setup

You only need to do this once.

```bash
cd impl/python
make install-dev          # creates .venv, installs runtime + dev deps,
                          # installs project-local pre-commit hooks
source .venv/bin/activate
```

The installed dev dependencies are pinned in [`pyproject.toml`](pyproject.toml)
under `[project.optional-dependencies] dev`.

---

## 1. Test pyramid

Every category exists for a reason. See
[`docs/TESTING_STRATEGY.md`](docs/TESTING_STRATEGY.md) for the full rationale.

| Layer | Where | What it proves | Speed |
|:--|:--|:--|:--|
| **Unit** | `tests/unit/` | Individual functions in isolation — pure inputs to outputs. | <1 s/test |
| **Property** | `tests/properties/` | Hypothesis-generated states obey the closed-algebra laws ([`../../proposals/foundation-v0.1/JOIN_ALGEBRA.md`](../../proposals/foundation-v0.1/JOIN_ALGEBRA.md) + [`docs/ALGEBRA_LAWS.md`](docs/ALGEBRA_LAWS.md)). | seconds/test |
| **Golden** | `tests/golden/` | Plan and SQL snapshots — the readable diff when *anything* about the compiler output changes. | <1 s/test |
| **E2E** | `tests/e2e/` | DuckDB executes the generated SQL against fixture data; we assert on the row set. | 1–5 s/test |
| **Adapter smoke** | `conformance/tests/` | The CLI adapter ([`conformance/adapter.py`](conformance/adapter.py)) speaks the contract in [`../../compliance/ADAPTER_INTERFACE.md`](../../compliance/ADAPTER_INTERFACE.md). | <1 s/test |
| **Mutation** | `make mutation*` | The tests above actually *check* correctness — they aren't no-ops. INFRA.md §1.1 says a surviving mutation in `src/osi/planning/algebra/` is a P0. | minutes (fast) — half hour (full) |
| **Compliance** | [`../../compliance/foundation-v0.1/`](../../compliance/foundation-v0.1/) | Every Conformance Decision (D-001..D-033) and every error code in Appendix C has a runnable case here. See [`run-osi-compliance`](../../.cursor/skills/run-osi-compliance/SKILL.md). | 30 s suite-wide |

---

## 2. Make targets

The `Makefile` is the single source of truth for individual categories.

```bash
make test               # unit + property + golden + e2e (everything that runs <10 s/test)
make test-unit          # just tests/unit/
make test-property      # just tests/properties/
make test-golden        # just tests/golden/
make test-e2e           # just tests/e2e/
make test-adapter       # just conformance/tests/

make golden-refresh     # refresh tests/golden/ snapshots (EXPLICIT ACTION; commit only on intent)
make bench              # run pytest-benchmark performance tests

make lint               # black --check + isort --check + flake8
make typecheck          # mypy strict
make architecture       # import-linter one-way flow contract
make audit-file-size    # enforce the 600-LOC cap on src/osi/

make mutation-fast      # mutmut on src/osi/planning/algebra/ (~5 min)
make mutation           # full mutmut run on src/osi/ (~30 min)

make check              # lint + typecheck + architecture + audit-file-size + test
                        # mirrors the CI gate in .github/workflows/impl-python-ci.yml
```

For day-to-day work, `make check` is enough.

---

## 3. The single-shot runner

`scripts/run_all_tests.sh` runs every stage above, captures structured output
in `test-results/raw/`, and writes a single readable Markdown report at
`test-results/REPORT.md`.

```bash
scripts/run_all_tests.sh                       # static checks + every test category
scripts/run_all_tests.sh --with-mutation-fast  # + algebra mutation (~5 min)
scripts/run_all_tests.sh --with-mutation       # + full mutation (~30 min)
scripts/run_all_tests.sh --skip-static         # only test categories
```

Behaviour:

- Every stage runs even if an earlier one failed — you get the full picture in
  one pass.
- Exit code is non-zero if any stage failed, so the script is CI-safe.
- The report includes: per-stage status, per-category test counts (from JUnit
  XML), combined coverage, mutation score (when applicable), failing tests
  (with category tag), and the 10 slowest tests.

Mutation testing is *opt-in* because it is slow. The fast variant
(`--with-mutation-fast`) takes ~5 min and is the recommended pre-PR run.

---

## 4. The standalone mutation runner

If you want to iterate on mutation testing without re-running the full suite:

```bash
scripts/run_mutation.sh --fast    # algebra only (~5 min)
scripts/run_mutation.sh           # full (~30 min)
```

This writes the raw mutmut summary into `test-results/raw/`. Re-run
`scripts/run_all_tests.sh` to fold it into `REPORT.md`.

---

## 5. Reading the report

`test-results/REPORT.md` is a glanceable Markdown file with the structure:

```
# Test Report — impl/python

_Generated <UTC timestamp>_

**Overall:** PASS | FAIL

## Stage summary               -- one row per stage, with link to raw log
## Test counts                  -- totals per category + grand total
## Coverage                     -- line, branch, missing-statement counts
## Mutation testing             -- killed / survived / score (when run)
## Failing tests                -- every test that failed, tagged with category
## Slowest 10 tests             -- across all categories
## Where to next                -- paths to raw logs, HTML coverage, JUnit XML
```

`test-results/htmlcov/index.html` is the per-line coverage HTML. Open it in a
browser to see which lines were never executed.

`test-results/raw/junit_*.xml` are the JUnit XML files emitted by each
pytest run — easy to re-parse from CI or other tooling.

---

## 6. Common workflows

| Task | Command |
|:--|:--|
| Pre-PR check (90s)                          | `make check`                                              |
| Pre-PR with mutation (≈5 min)               | `scripts/run_all_tests.sh --with-mutation-fast`           |
| Investigate a single failing test           | `pytest tests/<path>/test_X.py::test_Y -vvs`              |
| Update a golden snapshot intentionally       | `make golden-refresh` (justify in the PR)                 |
| Run only Hypothesis property tests          | `make test-property`                                      |
| Confirm a code change kills a mutant        | `make mutation-fast`                                      |
| Run the compliance suite against this impl  | See [`run-osi-compliance`](../../.cursor/skills/run-osi-compliance/SKILL.md). |
| Skip static checks (faster iteration)        | `scripts/run_all_tests.sh --skip-static`                  |

---

## 7. CI

The `make check` target is what
[`../../.github/workflows/impl-python-ci.yml`](../../.github/workflows/impl-python-ci.yml)
runs on every push and PR. Mutation runs on a separate job
(`mutation-algebra`) using `make mutation-fast` so a surviving mutant fails
CI without doubling pipeline time.

---

## 8. Troubleshooting

- **`make install-dev` fails on `mutmut`:** on macOS arm64 you may need
  `LDFLAGS="-undefined dynamic_lookup"`. The `pyproject.toml` disables
  `setproctitle` via `use_setproctitle = false` for the same reason.
- **`make architecture` fails with "import-linter not installed":**
  `pip install -e ".[dev]"` once.
- **A golden test fails:** look at the diff; if the new plan/SQL is what you
  intended, run `make golden-refresh` and commit the snapshot update with
  the design justification.
- **A property test fails with a Hypothesis shrunk counterexample:**
  paste the counterexample seed into the failing test as
  `@example(...)` for regression coverage, then fix the underlying bug.
  Do **not** narrow the strategy to silence the failure.
- **A mutmut mutation survives in `src/osi/planning/algebra/`:** that is a
  P0. The mutation tells you exactly what test would have caught the bug —
  add it.
