# Code Review — `impl/python` (Phase 8b — BI / analytical-engine best practices)

Target tree: `impl/python/` — grain resolution, fan-out safety, chasm-trap protection, bridge dedup, M:N stitching, null ordering, default join shapes, and semi-additive scoping.

Findings tagged **`[8b]`** with severity (**BLOCKING** / **IMPORTANT** / **NIT**). Paths are relative to `impl/python/`.

---

## Summary

| Angle | Verdict |
|:--|:--|
| Grain resolution | **Strong.** `algebra/grain.py` simulator is row-state-aware; `D-001` home-grain resolution and `D-004` fixed-grain pinning land in dedicated phases (`steps.materialize_cross_grain_aggregates`, `algebra.operations.aggregate_to_grain`). |
| Fan-out / chasm-trap | **Strong.** Enrichment paths must satisfy `_enrichment_path_safe` in `joins.find_enrichment_path` (child uniqueness on the foreign-key role); fan-trap rejected with `E3011`. Cross-fact joins without a stitching dim → `E3013`. |
| Bridge dedup | **Partial.** `_BRIDGE_RESOLVABLE = {SUM, COUNT, MIN, MAX, COUNT_DISTINCT}` only. `AVG`, `MEDIAN`, holistic forms over a bridge currently route away from `planner_bridge` or surface `E_UNSAFE_REAGGREGATION` — divergent from D-027 normative text. |
| M:N stitching | **Strong.** `joins.classify_relationship_path` distinguishes `:1` chains (enrichment), N:N (bridge), unrelated facts (`E3013`). |
| Null ordering | **Risk.** Window functions render `ROWS BETWEEN ... PRECEDING AND CURRENT ROW`; null placement in `ORDER BY` is left to the dialect default. |
| Default join shapes | **Mixed.** Enrichment join uses `INNER` (`algebra/operations.py::enrich`). For LEFT semantics in dimension-conformance the user must declare; nothing wrong, but worth a docs callout. |
| Semi-additive | **Correctly deferred.** Not in `proposals/foundation-v0.1`; tests `t-042r/s/t` register the deferred negative. |

---

## Blocking findings

### B1 `[8b]` Bridge dedup does not yet cover all aggregate categories per D-027

**Where**

- `src/osi/planning/planner_bridge.py` L199–L205 `_BRIDGE_RESOLVABLE`.
- `proposals/foundation-v0.1/Proposed_OSI_Semantics.md` D-027 — single-pass dedup envelope intended to admit AVG (decomposable) and holistic aggregates with surfaced caveats.

**Why it matters**

Test authors and BI users cite D-027 to claim AVG/MEDIAN over an N:N bridge are accepted. The implementation either raises `E_UNSAFE_REAGGREGATION` (after the `E3011` remap, see 8a B2) or routes back to the M:N rejection path. This is the largest user-visible BI gap.

**Concrete fix (Phase 9-aligned)**

1. Add `AVG` to `_BRIDGE_RESOLVABLE` only when the canonical algebraic split (`SUM`/`COUNT`) survives the bridge dedup join; otherwise emit a precise `E_UNSAFE_REAGGREGATION` with the failing decomposition step. Cover with `compliance/foundation-v0.1/tests/bridge/hard/t-016/t-051/t-052`.
2. Add a holistic-aggregate doc paragraph in `planner_bridge.py` describing why MEDIAN/PERCENTILE are gated until single-row-per-grain emission is provable.

---

## Important findings

### I1 `[8b]` Window functions do not pin NULLS ordering in render

**Where**

- `src/osi/codegen/transpiler.py` window rendering (`_render_window_function`-style code path, ~L300–L380).
- Affects dialects whose default differs (Snowflake: NULLS LAST for ASC; some dialects NULLS FIRST).

**Why it matters**

Window-based metrics (e.g. running totals) are sensitive to NULLS LAST/FIRST when grain keys have nullable columns; results differ silently across engines. BI users will see "the same metric returns different rows on Snowflake vs. Postgres".

**Concrete fix**

- Decide policy: emit explicit `NULLS LAST` in ASC window frames, `NULLS FIRST` in DESC (or follow proposal). Document in `SNOWFLAKE_DIVERGENCES.md`.
- Add at least one golden test under `tests/golden/windows/` that locks the rendered fragment to include `NULLS LAST`.

---

### I2 `[8b]` Enrichment join defaults to `INNER` — drops rows when child has no parent

**Where**

- `src/osi/planning/algebra/operations.py::enrich` L256–L304.

**Why it matters**

Most BI tools default dimension joins to LEFT (children survive when the dimension row is missing). OSI's INNER default is a defensible call (foreign-key role implies referential integrity) but should be explicit in docs and tests.

**Concrete fix**

- Add a short subsection to `proposals/foundation-v0.1/JOIN_ALGEBRA.md` (or the matching impl doc) explaining the INNER default + how to opt into LEFT (likely `D-009` deferred extension).
- Add an active test exercising a row with NULL FK to demonstrate the INNER drop, citing this decision.

---

### I3 `[8b]` `E3011` remap obscures fan-trap vs. multi-stage failures

**Where**

- `src/osi/planning/planner.py` L182–L193 (see 8a B2).

**Why it matters (BI angle)**

Authors debugging a "this query fans out" issue see `E_UNSAFE_REAGGREGATION` and chase a re-aggregation bug; the actual failure is enrichment uniqueness. The wrong code wastes BI-author hours.

**Concrete fix**

Keep `E3011` for the fan-trap signal; reserve `E_UNSAFE_REAGGREGATION` for D-022 multi-stage / holistic-survival failures, as 8a B2 prescribes.

---

### I4 `[8b]` `E3013_NO_STITCHING_DIMENSION` text is narrow

**Where**

- `src/osi/diagnostics/error_catalog.py` for `E3013`, message: "no stitching dimension between the requested facts".

**Why it matters**

The same error fires for (a) two unrelated facts with no shared conformed dim, and (b) facts where a dim exists but the path includes a forbidden hop. Authors can't tell which.

**Concrete fix**

Catalog text should branch on cause; planner should pass a `cause: Literal["no_shared_dim", "forbidden_hop"]` into the `OSIPlanningError` payload, and `osi_python_adapter` surfaces it in `error.details`.

---

### I5 `[8b]` `joins.py` documentation contains stale references to deferred features

**Where**

- `src/osi/planning/joins.py` L11–L80 module docstring references concepts (per-metric join overrides, named filters) that are deferred and not part of Foundation classification.

**Why it matters**

Reviewers think Foundation does more than it does; new contributors copy patterns that don't fit.

**Concrete fix**

Trim docstring to D-008/D-009 actual responsibilities (path resolution + cardinality classification + error code selection). Cross-link `proposals/foundation-v0.1/JOIN_ALGEBRA.md`.

---

## Nits

### N1 `[8b]` `algebra/grain.py::simulate_*` functions risk being read as planner public API

**Where:** `src/osi/planning/algebra/grain.py` exports `simulate_*` helpers.

**Fix:** Add `_internal` prefix or `__all__` to clarify these are for tests / debugging, not callable by adapters.

### N2 `[8b]` Bridge planner uses SQLGlot directly

Covered by 8a I3; the BI angle is the same — be explicit that bridge dedup is the sanctioned site for raw SQL surgery.

---

## Phase 9 prioritization

| Priority | Item | Rationale |
|:--|:--|:--|
| P0 | **B1** — D-027 bridge coverage (or matching catalog text) | Largest BI-author surprise vs. spec. |
| P1 | **I1** — Explicit `NULLS LAST`/`FIRST` in windows | Cross-dialect determinism. |
| P1 | **I3** / **I4** — Disambiguate `E3011` / `E3013` error stories | Author-debugging quality. |
| P2 | **I2** / **I5** / **N1** / **N2** — Docs + INNER default rationale + bridge SQLGlot callout | Maintenance + correctness defaults. |

---

**Honest gap check:** OSI's classification of unsafe enrich / N:N / unrelated-fact failures is genuinely tight; the gap is around D-027 bridge dedup (B1) and around clearer per-failure error stories (I3/I4). Window NULL ordering and INNER default policy are next.
