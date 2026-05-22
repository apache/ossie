# Mutation Testing Baseline

**Status:** Phase 6 hardening — baseline published for the Foundation.

This document is the published mutation-testing ratchet for
`osi_python`. It pairs with [`INFRA.md §1.1`](../INFRA.md#11-test-quality)
which defines the *targets*; this file records the *current reality*
and the policy for moving it forward.

---

## §1 Current baseline

Captured on the first green `make check` of Phase 6. Regenerate with
`make mutation` (or `make mutation-fast` for the algebra-only subset)
and update this table; the CI gate compares against these numbers.

| Module | Current score | Minimum gate | Target | Notes |
|:---|:---:|:---:|:---:|:---|
| `src/osi/planning/algebra/` | (S-25: tooling-blocked on macOS) | 88% | ≥ 90% | Load-bearing. Surviving mutations here are P0. |
| `src/osi/planning/classify.py` | (S-25: tooling-blocked) | 82% | ≥ 85% | Fan-out / chasm detection. |
| `src/osi/planning/joins.py` | (S-25: tooling-blocked) | 82% | ≥ 85% | Join-path selection. |
| `src/osi/codegen/` | (S-25: tooling-blocked) | 72% | ≥ 75% | Dialect idiom coverage. |
| `src/osi/` overall | (S-25: tooling-blocked) | 72% | ≥ 75% | Project-wide floor. |

**Tooling status (S-25, 2026-05-13).** The mutmut 3 configuration in
`pyproject.toml` is fully wired (correct ``source_paths`` /
``pytest_add_cli_args_test_selection`` / ``also_copy`` /
``use_setproctitle``). Local runs on macOS hit a fork-safety
regression in mutmut 3.5 where every mutated child process
segfaults — the same issue documented in mutmut's macOS notes,
even with ``use_setproctitle = false``. Resolution path:

1. Run the baseline sweep on a Linux CI worker (the GitHub Actions
   workflow `../../../.github/workflows/impl-python-ci.yml` is the
   hook).
2. Or pin mutmut to 2.x and revert the config block to the 2.x
   keys (``paths_to_mutate`` / ``runner``).

CI continues to enforce the **Minimum gate** column once the
baseline lands; a PR that drops any score below the minimum fails.

## §2 Ratchet policy

- **Baseline captured at each release.** Write the numbers into the
  table above as part of the release PR. Do not delete history; keep
  past rows in §4.
- **Sprint regressions > 2 percentage points fail CI** against the
  baseline for that module.
- **Every four sprints, reviewers consider raising a baseline by
  1–2 pp per module.** Raising requires a PR that shows the score has
  stayed above the new floor for at least two sprints.
- **Lowering a baseline is never automatic.** A lowered baseline must
  ship with a decision-log entry in [`INFRA.md §4`](../INFRA.md#4-decisions-log)
  explaining the trade-off.

## §3 How to run mutation tests locally

```bash
# Fast: algebra module only (~5 minutes)
make mutation-fast

# Full run (~30 minutes, runs on all src/osi modules)
make mutation
```

Both commands write their cache to `.mutmut-cache/`. Use
`mutmut results` for a summary and `mutmut show <id>` to inspect a
surviving mutation.

## §4 History

Append one row per release. Keep the most recent entry first.

| Date | Release | Algebra | classify/joins | codegen | Overall |
|:---|:---|:---:|:---:|:---:|:---:|
| _TBD_ | Phase 6 exit | — | — | — | — |

## §5 Reading surviving mutations

A surviving mutation is a diff the test suite did not catch. The
resolution ladder:

1. **Is there a missing test?** Most surviving mutations point at an
   untested branch. Add the test.
2. **Is there equivalent code?** Occasionally `mutmut` mutates code
   that has no observable behaviour difference (e.g. a dead branch).
   Mark it `skipped` with a comment explaining why.
3. **Is the code load-bearing for correctness?** If yes, this is a
   real bug in the algebra or planner. Add the test and fix the code.
   A surviving mutation in `src/osi/planning/algebra/` is always
   treated as #3 until proven otherwise.
