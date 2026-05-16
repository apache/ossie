# Code Review — `impl/python` (Phase 8a — Compiler best practices)

Target tree: `impl/python/` under the OSI reference Python implementation.
Focus: phase boundaries, IR immutability, error taxonomy, planner pass ordering (bridge / M:N), SQLGlot/FrozenSQL usage, dialect shape, and file-size hotspots.

Findings use severity (**BLOCKING** / **IMPORTANT** / **NIT**) and tag **`[8a]`**. Paths are written relative to `impl/python/`.

---

## Summary

| Angle | Verdict |
|:--|:--|
| Phase boundaries (parse → plan → codegen) | **Strong.** `import-linter` forbids `osi.parsing` → planning/codegen, `osi.planning` → codegen, and `osi.codegen` → parsing. Codegen correctly consumes `QueryPlan` + algebra view types and does not call `plan()`. |
| IR purity & immutability | **Strong for published IR.** `QueryPlan`, payloads, `PlanStep`, and algebra `Column` / `CalculationState` are `frozen=True` + `slots=True`; `PlanBuilder` mutates only an internal accumulator then exposes `tuple` steps. |
| Error reporting precision | **Mixed.** `ErrorCode` is a `StrEnum`; failures use `OSIError` subclasses. **Internal contradiction:** normative text in `errors.py` / `error_catalog.py` asserts D-027 bridge behaviour that `planner_bridge.py` explicitly does not implement. **Code remapping:** `E3011` from measure-group build is rewritten to `E_UNSAFE_REAGGREGATION`, which blurs Appendix C semantics. |
| Optimization / pass design | **Mostly sound.** Enrichment path runs first; bridge dispatch is a guarded fallback on `E3011`/`E3012` from path resolution; M:N classification in `joins.py` distinguishes `E3012` (N:N) vs `E3011` (bad enrichment direction / fan-trap signal). |
| SQLGlot usage / FrozenSQL | **Consistent in planner + codegen.** Expressions on the IR use `FrozenSQL`; codegen unwraps with `.expr.copy()` before mutating/qualifying. |
| Dialect adapter shape | **Thin.** Single `codegen/dialect.py` maps `Dialect` → SQLGlot dialect name + `render_sql`; adding a dialect is enum + map + goldens (as documented in-module). |
| File size / complexity | **Under documented exception cap.** `make audit-file-size` allows 700 LOC for `planner_bridge.py`, `planner.py`, `steps.py`; several other files are **>500 LOC** and are natural split candidates when the exception list is retired. |

---

## Blocking findings

### B1 `[8a]` Normative error / spec text contradicts bridge implementation (D-027)

**Where**

- `src/osi/errors.py` L54–L58 — comment claims §6.8.1 bridge plan accepts every aggregate category bare per D-027.
- `src/osi/diagnostics/error_catalog.py` L116–L127 — `E_UNSAFE_REAGGREGATION` explanation claims `AVG`, `MEDIAN`, `COUNT(DISTINCT)` over N:N bridge are all accepted per D-027.
- `src/osi/planning/planner_bridge.py` L20–L29 module docstring and `_BRIDGE_RESOLVABLE` L199–L205 / `can_apply_bridge_resolution` L229–L243 — only `SUM`, `COUNT`, `MIN`, `MAX`, `COUNT_DISTINCT` allowed; AVG / MEDIAN / PERCENTILE explicitly pending.

**Why it matters**

A reference compiler's typed errors + catalog are part of the contract. When `errors.py` and `error_catalog.py` describe D-027 outcomes that `planner_bridge.py` rejects, authors and compliance tooling get false normative guidance ("accepted") while the implementation fails or routes away.

**Concrete fix**

1. Short term: edit `errors.py` and `error_catalog.py` so they match `planner_bridge.py` — bridge v1 accepts only the listed operators; AVG/MEDIAN/holistic over bridge remain unsupported, pointing to conformance tests / roadmap.
2. Long term: implement bridge lowering for `AVG` (algebraic state) and holistic aggregates per D-027 single-pass dedup, then restore the stronger catalog language.

---

### B2 `[8a]` `E3011_MN_AGGREGATION_REJECTED` is remapped to `E_UNSAFE_REAGGREGATION` for all aggregation measure groups

**Where**

- `src/osi/planning/planner.py` L182–L193 — any `OSIPlanningError` with `code is ErrorCode.E3011_MN_AGGREGATION_REJECTED` caught while building a measure group is re-raised as `E_UNSAFE_REAGGREGATION`.

**Why it matters**

`E3011` and `E_UNSAFE_REAGGREGATION` serve different Appendix C stories: `E3011` is the internal/planner signal for unsafe M:N enrichment / fan-out preconditions (see `joins.py` L251–L261, `algebra/operations.py` enrich L288–L302); `E_UNSAFE_REAGGREGATION` is documented for D-022 multi-stage / holistic-survival failures (`errors.py` L54–L58, `error_catalog.py` L116–L127). Collapsing them loses the code the spec and tests may expect.

**Concrete fix**

Only translate `E3011` → `E_UNSAFE_REAGGREGATION` when the failure is provably the D-022 pattern; otherwise surface `E3011` (or a new named code) for pure fan-trap / join-uniqueness violations. Add/adjust unit tests that assert stable `ErrorCode` for: N:N edge without bridge, child-not-unique enrich, vs true unsafe re-aggregation.

---

## Important findings

### I1 `[8a]` OSI_SQL_2026 whitelist allows holistic/statistical functions that metric planning does not model

**Where**

- `src/osi/parsing/function_whitelist.py` `_AGGREGATE_FUNCTIONS` L39–L61 includes MEDIAN, PERCENTILE_*, etc.
- `src/osi/planning/metric_shape.py` `_as_top_level_aggregate` L35–L41, L95–L117 only maps Sum/Count/Min/Max/Avg.

**Why it matters**

Authors pass parse-time whitelist checks then hit `E1206_METRIC_IN_RAW_AGGREGATE` or composite-resolution failures at planning time for constructs the spec sheet suggests are valid. That violates "fail fast at the right layer" for a reference implementation.

**Concrete fix**

Either: (a) extend `_AGG_BY_AST` / `AggregateFunction` / decomposability rules to cover every whitelist aggregate intended for top-level metrics, or (b) tighten the whitelist for metric bodies vs field bodies so unsupported holistic top-level forms are rejected at parse with a dedicated `ErrorCode`.

---

### I2 `[8a]` Codegen depends on planner internals (`algebra` + `prefixes`), not only `plan.py`

**Where**

- `src/osi/codegen/transpiler.py` L24–L40 imports `FilterMode`, `JoinType`, `AggregateFunction`, `CalculationState`, `Column`, `PlanStep`, etc., from `osi.planning.algebra.*` and `plan`, and `step_alias` from `osi.planning.prefixes`.
- `src/osi/codegen/cte_optimizer.py` L27 imports `is_step_alias` from `osi.planning.prefixes`.

**Why it matters**

Not a layer violation (import-linter allows it), but it couples codegen refactors to algebra types. Any change to `Column` or join enums can break SQL emission silently.

**Concrete fix**

Document in `codegen/README.md` that these imports are intentional IR surface, or introduce a narrow `osi.planning.ir` facade that re-exports only what transpiler needs.

---

### I3 `[8a]` Direct SQLGlot use in bridge planner blurs "planner reasons over IR, not SQL text"

**Where**

- `src/osi/planning/planner_bridge.py` L59 (`from sqlglot import expressions as exp`) and subsequent metric/column materialisation.

**Why it matters**

The headline story in `planner.py` is that the planner does not inspect raw SQL strings; bridge code still constructs and walks SQLGlot. Reasonable, but should be explicitly justified to avoid future contributors leaking more SQL surgery into planning.

**Concrete fix**

Module docstring: one paragraph stating that bridge is the sanctioned exception for SQLGlot in planning, and new SQLGlot usage must stay confined to `planner_bridge.py` / listed helpers.

---

### I4 `[8a]` `AggregateFunction` enum omits holistic functions whitelisted elsewhere

**Where**

- `src/osi/planning/algebra/state.py` `AggregateFunction` L86–L98 ends at `AVG`; docstring L73–L77 mentions holistic aggregates conceptually but the enum has no `MEDIAN` / `PERCENTILE_*`.

**Why it matters**

Bridge and fan-out logic key off `AggregateFunction`; absent members force ad hoc handling or block features that the expression subset already names.

**Concrete fix**

Extend `AggregateFunction` + decomposability + `_BRIDGE_RESOLVABLE` / unsafe-reagg paths together when holistic metrics are productised.

---

### I5 `[8a]` Files >500 LOC (split targets when INFRA exceptions are removed)

| File | Lines | Note |
|:--|--:|:--|
| `src/osi/planning/planner_bridge.py` | 674 | On 700-cap exception list (`Makefile` L117–L120). |
| `src/osi/planning/steps.py` | 626 | Exception-listed; highest cyclomatic surface. |
| `src/osi/planning/planner.py` | 604 | Exception-listed; orchestration only. |
| `src/osi/planning/algebra/operations.py` | 550 | Natural split: enrich/merge/filter vs aggregate/project. |
| `src/osi/planning/joins.py` | 504 | Path-finding vs cardinality/error classification. |
| `src/osi/codegen/transpiler.py` | 509 | Per-op render functions modular; could move op blocks to `transpiler_ops.py`. |
| `src/osi/diagnostics/error_catalog.py` | 545 | Mechanical data; acceptable size. |

**Concrete fix**

When closing INFRA roadmap items for `planner_bridge` / `steps` / `planner`, remove exception entries only after physical splits.

---

## Nits

### N1 `[8a]` Stale "E1105" reference in planner module docstring

**Where:** `src/osi/planning/planner.py` L36–L38.

**Fix:** Replace with `E_DEFERRED_KEY_REJECTED` (or modern codes) or delete the sentence.

### N2 `[8a]` Error catalog still points at `specs/deferred/README.md`

**Where:** `src/osi/diagnostics/error_catalog.py` L63–L64 (`E_DEFERRED_KEY_REJECTED`).

**Fix:** Point to the real path under `proposals/foundation-v0.1/`.

### N3 `[8a]` `enrich` docstring overstates `E3011` as covering "wider N:N case"

**Where:** `src/osi/planning/algebra/operations.py` L231–L236.

**Fix:** Clarify that declared N:N is normally rejected earlier in `joins.find_enrichment_path` with `E3012`, while `E3011` here is the child-uniqueness / fan-out guard.

---

## Phase 9 prioritization

| Priority | Item | Rationale |
|:--|:--|:--|
| P0 | **B1** — Align `errors.py` / `error_catalog.py` with `planner_bridge.py` | Stops lying to users and compliance about bridge semantics. |
| P0 | **B2** — Stop blanket `E3011` → `E_UNSAFE_REAGGREGATION` mapping | Restores Appendix C-shaped diagnostics. |
| P1 | **I1** / **I4** — Close whitelist vs `metric_shape` / `AggregateFunction` gap | Eliminates layer-wrong surprises for MEDIAN/percentile metrics. |
| P2 | **I2** / **I3** — Document codegen's algebra imports; justify SQLGlot in bridge | Reduces accidental architecture drift. |
| P3 | **I5** + **N1–N3** — LOC splits per INFRA; doc/link cleanups | Maintenance and reviewer ergonomics. |

---

**Honest gap check:** Import boundaries, frozen plan/algebra IR, FrozenSQL + `.copy()` codegen discipline, dialect thinness, and `make audit-file-size` policy are in good shape for a reference compiler. The highest-leverage Phase 9 work is making normative text, error codes, and bridge behaviour line up (B1–B2) before expanding aggregate or bridge coverage.
