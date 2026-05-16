# Code Review — `impl/python` (Phase 8c — Test coverage, quality, and shallow-validation hunt)

Target trees: `impl/python/tests/` (unit, property, golden, e2e) and `compliance/foundation-v0.1/tests/`.

User's standing rule: **tests that don't validate enough are worse than no tests** — they give false confidence. This review explicitly hunts for those and lists each one to deepen in Phase 9.

Findings tagged **`[8c]`** with severity (**BLOCKING** / **IMPORTANT** / **NIT**).

---

## Summary

| Angle | Verdict |
|:--|:--|
| Coverage breadth | **Strong.** Unit, property (Hypothesis), golden (SQL snapshots), e2e (parse→plan→codegen→execute), adapter smoke. |
| Compliance coverage | **Strong.** Foundation surface covered; deferred features have negative `t-042*` tests. |
| Shallow-validation hot spots | **Several.** Multiple `pytest.raises(OSIError)` calls without `.code` assertion; SQL substring checks where row-multiset semantics are intended; some property tests have weak invariants. |
| Test-naming | **Mixed.** Most clear; a handful of `test_smoke_*` files do real work and should be renamed. |
| Critical design flaw | **`GrainSimulationError` subclasses `ValueError` (`src/osi/planning/algebra/grain.py`) rather than `OSIError`** — breaks the typed-error doctrine and lets ad-hoc tests catch the wrong type. |

---

## Blocking findings

### B1 `[8c]` `GrainSimulationError` is not an `OSIError`

**Where**

- `src/osi/planning/algebra/grain.py` `class GrainSimulationError(ValueError)`.

**Why it matters**

The user-visible doctrine (CLAUDE.md, error_catalog) is that all planner-visible failures wear an `ErrorCode` and surface through `OSIError`. Tests under `tests/unit/test_grain_*.py` that catch `ValueError` will not catch genuine OSI bugs that get re-raised through the normal pipeline; conversely tests catching `OSIError` may miss real grain bugs.

**Concrete fix**

1. Make `GrainSimulationError(OSIPlanningError)` with `code = ErrorCode.E_INTERNAL_INVARIANT` (or a new `E_INTERNAL_GRAIN_INVARIANT`).
2. Update grain unit tests to assert on `error.code`.
3. Add an arch-test that walks `osi.*` modules and asserts every `Exception` subclass in OSI either inherits from `OSIError` or is explicitly allow-listed (Pydantic etc.).

---

## Important findings (shallow-validation hunt)

For each item: **location → current assertion → required deepening.** Phase 9 should fix every entry.

### I1 `[8c]` `pytest.raises(OSIError)` without `.code` assertion

| Test location | Current | Required |
|:--|:--|:--|
| `tests/unit/test_planner_metric_grain.py` `test_metric_in_field_rejected` | `with pytest.raises(OSIError):` | `excinfo.value.code is ErrorCode.E_AGGREGATE_IN_FIELD` |
| `tests/unit/test_planner_bridge.py` cases for unsupported aggregates | `pytest.raises(OSIPlanningError)` | Pin `code` per case (`E_UNSAFE_REAGGREGATION` vs `E3011` vs `E3012`) |
| `tests/unit/test_parser_deferred.py` (multiple) | `pytest.raises(OSIError)` | `.code is ErrorCode.E_DEFERRED_KEY_REJECTED` |
| `tests/unit/test_function_whitelist.py` rejections | `pytest.raises(OSIError)` | `.code is ErrorCode.E_FUNCTION_NOT_ALLOWED` |
| `tests/unit/test_planner_scalar.py` scalar misuse | `pytest.raises(OSIError)` | `.code is ErrorCode.E_AGGREGATE_IN_SCALAR_QUERY` |

### I2 `[8c]` Golden tests assert SQL substrings rather than rendered shape

| Test location | Current | Required |
|:--|:--|:--|
| `tests/golden/test_planner_smoke.py::test_simple_sum` | `assert "SUM(" in sql` | Compare against the committed golden file (whole-string equality + a normalised-whitespace fallback) |
| `tests/golden/test_window_running_total.py` | `assert "ROWS BETWEEN" in sql` | Assert window fragment exactly: `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW` and the explicit `NULLS LAST` once Phase 9 lands it |
| `tests/golden/test_bridge_dedup.py` | `assert "GROUP BY" in sql` (twice) | Golden whole-SQL + structural check that there is exactly one bridge dedup CTE |

### I3 `[8c]` Property tests with weak invariants

| Test location | Current invariant | Stronger invariant |
|:--|:--|:--|
| `tests/property/test_grain_simulation.py::test_aggregation_reduces_or_equal_grain` | "output grain set ⊆ input grain set" | Add: aggregating twice in a row is idempotent in cardinality reduction; aggregating to current grain is a no-op (`simulate_aggregate(state, state.grain) == state`) |
| `tests/property/test_join_classifier.py` | "no error raised on randomly generated paths" | Add: classifier output is deterministic given a fixed graph (call twice, compare); classification of an N:N path always returns `E3012` |
| `tests/property/test_filter_routing.py` | "filter mode is one of {WHERE, HAVING, JOIN}" | Add: where the filter references only home-grain columns, mode must be `WHERE`; where it references an aggregated metric, mode must be `HAVING` |

### I4 `[8c]` Compliance metadata: tests with `expected_error: true` and no `expected_error_code`

Search hits in `compliance/foundation-v0.1/tests/` (kept by Phase 6 cleanup but worth a final sweep):

- `validation_errors/easy/t-050-field-dependency-cycle/metadata.yaml` — confirm pinned to `E_FIELD_DEPENDENCY_CYCLE` (Phase 7 fix). 
- Any negative test missing `expected_error_code` must either pin it or be moved to `status: planned` with a TODO.

### I5 `[8c]` E2E tests assert only row count, not row contents

| Test location | Current | Required |
|:--|:--|:--|
| `tests/e2e/test_basic_metric.py::test_total_revenue_by_region` | `assert len(rows) == 4` | Compare to expected `[(region, revenue), ...]` set (multiset equality, sorted) |
| `tests/e2e/test_bridge_paths.py` | `assert len(rows) > 0` | Multiset equality vs `gold_rows.json` |
| `tests/e2e/test_window.py::test_running_total` | `assert any(r.running_total > 0 for r in rows)` | Compare every `(group, ordering_key, running_total)` triple |

### I6 `[8c]` `test_smoke_*` files that do real validation

Rename and tighten:

- `tests/unit/test_smoke_parser.py` → `test_parser_minimal.py`; ensure each smoke also pins the `osi_version` and a representative `ErrorCode` on the negative branch.
- `tests/golden/test_smoke_codegen.py` → `test_codegen_minimal_dialect.py`; reuse the golden infrastructure.

---

## Nits

### N1 `[8c]` Fixtures duplicated across `tests/unit/` and `tests/e2e/`

Move shared model/query fixtures into `tests/_fixtures/models.py` and import; one source of truth keeps invariants consistent.

### N2 `[8c]` `conftest.py` enables `caplog.set_level(logging.WARNING)` globally

This hides genuine warnings emitted during planner pass ordering. Default to `INFO` and let tests opt down where needed.

### N3 `[8c]` Compliance harness reporter prints "PASSED" for `expected_error_missing`

Confirm `compliance/harness/src/harness/reporter.py` is the post-Phase-7 version that surfaces `expected_error_missing` as a failure category in `failures.csv`.

---

## Phase 9 prioritization

| Priority | Item | Rationale |
|:--|:--|:--|
| P0 | **B1** — Fix `GrainSimulationError` taxonomy | Doctrine-level; every test downstream depends on it. |
| P0 | **I1** — Pin `.code` on every `pytest.raises(OSIError)` | Closes the false-confidence hole the user explicitly called out. |
| P1 | **I2** / **I5** — Replace substring/row-count checks with golden + multiset comparisons | Catches dialect drift + row-content regressions. |
| P1 | **I3** — Strengthen property invariants | Highest leverage per LOC for catching regressions. |
| P2 | **I4** / **I6** / **N1–N3** — Compliance metadata sweep + naming + fixtures + logging | Clean baseline for future phases. |

---

**Honest gap check:** Test coverage breadth is good; the failure mode the user warned about (shallow tests giving false confidence) is concentrated in the `pytest.raises(OSIError)` pattern without `.code`, in SQL substring assertions, and in row-count-only E2E checks. Fixing **B1 + I1 + I2 + I5** in Phase 9 will move the bar from "we ran the code" to "we proved the right behaviour."
