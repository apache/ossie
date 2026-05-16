# Phase 8 — BI + Compiler + Test-Quality Review

This is the consolidated Phase 8 review report referenced in the OSI_will migration plan. It is the merge of three parallel deep passes:

- [`08a_compiler_review.md`](./08a_compiler_review.md) — compiler best practices (phase boundaries, IR purity, error taxonomy, planner pass ordering, SQLGlot use, dialect shape, file size).
- [`08b_bi_review.md`](./08b_bi_review.md) — BI / analytical-engine norms (grain resolution, fan-out, chasm-trap, bridge dedup, M:N stitching, null ordering, default join shapes, semi-additive scope).
- [`08c_test_quality_review.md`](./08c_test_quality_review.md) — test coverage and shallow-validation hunt across `tests/` and `compliance/foundation-v0.1/tests/`.

Read the three sub-reports for full findings, severities, file/line citations, and proposed fixes. This file is the executive view used to drive Phase 9.

---

## Top-level verdict

| Dimension | Status | Phase 9 work? |
|:--|:--|:--|
| Pipeline boundaries (parse → plan → codegen) | Sound; enforced by `import-linter` | No |
| IR immutability / `FrozenSQL` discipline | Sound | No |
| Error taxonomy alignment with Appendix C | **Drift** — normative text claims D-027 bridge coverage that the planner does not implement; `E3011` blanket-remapped to `E_UNSAFE_REAGGREGATION` | **Yes — P0** |
| D-027 bridge dedup coverage | Partial; `AVG`, `MEDIAN`, holistic over bridge not yet supported | **Yes — P0** |
| Cross-dialect determinism (window NULL order) | Implicit, dialect-dependent | **Yes — P1** |
| Error-code precision (`E3011` vs `E3013` cause text) | Conflates fan-trap vs unrelated-fact vs forbidden-hop | **Yes — P1** |
| Typed-error doctrine | **`GrainSimulationError` subclasses `ValueError`** — breaks the contract | **Yes — P0** |
| Test depth (shallow-validation hunt) | Multiple `pytest.raises(OSIError)` without `.code`; SQL substring checks; row-count-only E2E | **Yes — P0/P1** |

---

## Phase 9 priority queue (consolidated)

### P0 — Doctrine + spec/impl coherence

1. **Fix `GrainSimulationError` to inherit from `OSIError`** with a stable `ErrorCode`. Add an arch-test guarding "every OSI exception is an `OSIError`". *(8c B1)*
2. **Align `errors.py` + `error_catalog.py` D-027 text with `planner_bridge.py`** — bridge v1 accepts SUM/COUNT/MIN/MAX/COUNT_DISTINCT only; AVG/holistic over bridge are roadmap items. *(8a B1, 8b B1)*
3. **Stop blanket `E3011 → E_UNSAFE_REAGGREGATION` remap** in `planner.py`. Keep `E3011` for fan-trap; reserve `E_UNSAFE_REAGGREGATION` for D-022 multi-stage / holistic-survival. *(8a B2, 8b I3)*
4. **Deepen every `pytest.raises(OSIError)` to pin `.code`** across unit + golden + e2e + adapter tests. *(8c I1)*

### P1 — Determinism + author-visible clarity

5. **Emit explicit `NULLS LAST` / `NULLS FIRST` in window ORDER BY**; add golden fragment locking it. Update `SNOWFLAKE_DIVERGENCES.md`. *(8b I1)*
6. **Disambiguate `E3013_NO_STITCHING_DIMENSION` causes** (no shared dim vs forbidden hop) via payload + catalog text. *(8b I4)*
7. **Close OSI_SQL_2026 whitelist vs `AggregateFunction` enum gap** so MEDIAN / PERCENTILE_* either parse-reject early or plan correctly. *(8a I1, 8a I4)*
8. **Replace golden substring assertions with whole-SQL + structural comparisons.** *(8c I2)*
9. **Replace e2e row-count checks with multiset equality vs `gold_rows.json`/`gold.sql`.** *(8c I5)*
10. **Strengthen property test invariants** (idempotency / determinism / mode-routing). *(8c I3)*

### P2 — Maintenance + docs

11. **Trim `joins.py` docstring** of stale deferred-feature references; pin to D-008/D-009. *(8b I5)*
12. **Document codegen's intentional `osi.planning.algebra` imports** as the IR surface in `codegen/README.md`. *(8a I2)*
13. **Justify SQLGlot inside `planner_bridge.py`** in module docstring; mark it the sanctioned exception. *(8a I3, 8b N2)*
14. **Make `INFRA` exception list match real LOC splits** for `planner_bridge.py`, `steps.py`, `planner.py` (>700) and natural split candidates `algebra/operations.py`, `joins.py`, `transpiler.py` (>500). *(8a I5)*
15. **Compliance metadata sweep**: every `expected_error: true` test pins `expected_error_code`; rename `test_smoke_*` files that do real validation. *(8c I4, 8c I6, 8c N1–N3)*
16. **Clarify `INNER` default for enrichment** in `proposals/foundation-v0.1/JOIN_ALGEBRA.md`. *(8b I2)*
17. **Mark `algebra/grain.py::simulate_*` as internal** (e.g. `__all__` / prefix). *(8b N1)*
18. **Replace stale doc references** (`E1105` in planner docstring, `specs/deferred/README.md` in catalog). *(8a N1, 8a N2, 8a N3)*

---

## Shallow-validation hunt — full list to deepen in Phase 9

(Detailed table is in [`08c_test_quality_review.md`](./08c_test_quality_review.md) §I1–I5; reproduced here as a single checklist for the Phase 9 commit cadence.)

- [ ] Pin `.code` on `pytest.raises(OSIError)` in:
  - `tests/unit/test_planner_metric_grain.py::test_metric_in_field_rejected` (`E_AGGREGATE_IN_FIELD`)
  - `tests/unit/test_planner_bridge.py` rejection cases (per-case code)
  - `tests/unit/test_parser_deferred.py` (`E_DEFERRED_KEY_REJECTED`)
  - `tests/unit/test_function_whitelist.py` (`E_FUNCTION_NOT_ALLOWED`)
  - `tests/unit/test_planner_scalar.py` (`E_AGGREGATE_IN_SCALAR_QUERY`)
- [ ] Replace SQL `"SUM(" in sql` / `"ROWS BETWEEN" in sql` / `"GROUP BY" in sql` substring checks with whole-SQL golden equality + structural counts.
- [ ] Replace `assert len(rows) == N` and `assert len(rows) > 0` e2e assertions with multiset equality vs `gold_rows.json` (or `gold.sql` execution under the harness contract).
- [ ] Strengthen property tests: aggregation idempotence, classifier determinism, filter-mode routing constraints.
- [ ] Rename / tighten `test_smoke_*` files that do real validation.

---

## How Phase 9 will run

Each numbered item above is its own fix-group (`make check` green + compliance suite green before commit). The P0 items (1–4) ship first; they unblock the rest by ensuring assertions on error codes are meaningful.

When Phase 9 is complete, Phase 10 (standards + app-dev review) runs against an implementation whose typed errors and shallow tests have been brought up to reference grade.
