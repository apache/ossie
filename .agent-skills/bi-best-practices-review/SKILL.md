---
name: bi-best-practices-review
description: Review or design code against BI analytical correctness patterns — grain awareness, fan-out (chasm trap), conformed dimensions, bridge dedup, semi-additive measures, count-distinct fan-out, ambiguous aggregation grain, role-playing dimensions, slowly-changing dimensions. Use when adding a metric, a join, an aggregation strategy, or any planner behaviour that interacts with grain.
---

# BI best-practices review

A semantic-layer compiler stands or falls on whether it gets *grain*
right. This skill is the playbook for verifying that every aggregation,
join, and metric stays grain-safe — and for designing new BI features
so wrong-grain answers are rejected, not silently produced.

The reference for the analytical concepts referenced below is
[`bi-concepts-to-osi`](../../../.cursor/skills/bi-concepts-to-osi/SKILL.md)
(or `.claude/skills/bi-concepts-to-osi/SKILL.md`).

## 1. Purpose

Ensure every BI idiom the planner emits is grain-safe, idempotent over
its declared algebra, and either provably correct or explicitly
rejected with a Foundation error code. *Plausibly wrong SQL is the
worst possible outcome.*

## 2. When to use it (Review)

Apply when the change:

- Adds or modifies a metric — especially non-additive aggregates (`AVG`,
  `MEDIAN`, percentile) or count-distinct.
- Adds or modifies a join path or relationship — particularly anything
  with M:N or bridge semantics.
- Adds or modifies the fan-out / chasm-trap detection logic in
  `planner_bridge.py`, `planner_mn.py`, or `classify.py`.
- Adds a new aggregation context (`group_aggregate`, nested aggregate,
  scalar aggregate).
- Adds a new BI-idiom test under `compliance/foundation-v0.1/tests/`.
- Touches filter classification — `WHERE` vs `HAVING` vs semi-join.

## 3. When to use it (Design)

Apply *before* writing code when you are:

- Designing how the planner will handle a new metric shape.
- Designing a new relationship cardinality (e.g. M:N with a bridge).
- Implementing a new BI proposal from
  [`bi-concepts-to-osi`](../../../.cursor/skills/bi-concepts-to-osi/SKILL.md)
  or the deferred-features section of
  [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md) §10.
- Considering whether a metric is *additive*, *semi-additive*, or
  *non-additive* (and therefore whether it can be summed across the
  fan-out dimension).
- Wondering whether a query should be rejected with `E_FAN_OUT_*`,
  `E_UNSAFE_REAGGREGATION`, or another grain-safety code.

At design time, write the *wrong* answer the new feature could produce
if grain were ignored. If your design can produce that wrong answer,
the implementation must either reject the query or restructure.

## 4. Methodology

1. **Identify the grain at every step.** For every operator in the plan
   the change produces, note the grain explicitly. The grain is the
   primary key of the dataset the state currently represents. An
   operator that changes the grain (`aggregate`, `enrich` into a parent)
   announces the new grain on the resulting state.
2. **Identify the fan-out.** Walk the join path and mark every edge
   that *expands* row count (`1:N` away from the base / N:M / through a
   bridge). The fan-out is where double-counting hides.
3. **Classify the aggregate.** Is each aggregate distributive
   (`SUM`, `MIN`, `MAX`, `COUNT`), algebraic (`COUNT(DISTINCT)` over a
   distributive denominator, `AVG`), or holistic
   (`MEDIAN`, `PERCENTILE_CONT`, `STDDEV_POP` with bridge fan-out)?
   - Distributive: safe to fan out and re-aggregate.
   - Algebraic over a bridge: usually requires a single-pass with
     `COUNT(DISTINCT)` over the bridged dimension (D-026).
   - Holistic over fan-out: **must reject** (`E_UNSAFE_REAGGREGATION`)
     unless a pre-aggregation can resolve to the correct grain first
     (D-022).
4. **Confirm the filter context.** `WHERE` is row-level (pre-aggregate).
   `HAVING` is post-aggregate. A predicate that mixes the two is
   `E_MIXED_PREDICATE_LEVEL`. A predicate that references a finer-grain
   field in a coarser aggregate is `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`
   (D-024).
5. **Confirm the dedup.** Bridge planning (`planner_bridge.py`) MUST
   dedup the bridge side before joining unless the aggregate is
   distinct-safe. Verify a test pins the dedup step (`t-015-bridge-dedup`).
6. **Reject if uncertain.** If neither the spec nor `JOIN_ALGEBRA.md`
   gives an unambiguous answer, the planner must raise — never emit
   "plausible" SQL.

## 5. Checklists

### 5.1 Grain awareness

- [ ] Every new `CalculationState` carries a non-empty `grain`.
- [ ] An `aggregate` op coarsens; an `enrich` op preserves; a `merge`
      requires both sides at the same grain.
- [ ] Tests assert the grain on at least one intermediate state, not
      just the final SQL output (`tests/properties/test_grain_closure.py`
      already does this universally; new operators must keep the law).

### 5.2 Fan-out safety

- [ ] Every join path that has a 1:N or M:N edge is flagged in
      `joins.py` cardinality inference; ambiguous cases raise
      `E3003 AMBIGUOUS_CARDINALITY`.
- [ ] A scalar query that traverses a 1:N edge raises
      `E_FAN_OUT_IN_SCALAR_QUERY`.
- [ ] `COUNT(DISTINCT)` over a fan-out path is single-pass with a
      `DISTINCT` projection or rejected (D-026).
- [ ] No metric is silently re-summed across a fan-out dimension.

### 5.3 Bridge (M:N) safety

- [ ] Every bridge join dedupes the bridge side before aggregation
      (`planner_bridge.py`).
- [ ] Distributive aggregates over a deduped bridge use the
      `bridged-single-pass` plan (D-027).
- [ ] `AVG` / `MEDIAN` / percentile over an M:N bridge raises
      `E_UNSAFE_REAGGREGATION` until a safe rewrite exists (D-022, see
      the open xfails under `tests/bridge/hard/`).
- [ ] Bridge-aware nested aggregation (`AVG(SUM(...))` style) is
      deferred — `E_NESTED_AGGREGATION_DEFERRED` (D-027).

### 5.4 Filter routing

- [ ] Row-level predicate → `WHERE` (pre-aggregate).
- [ ] Aggregate predicate → `HAVING` (post-aggregate, named-filter via
      the planner's classification).
- [ ] Mixing the two raises `E_MIXED_PREDICATE_LEVEL` (D-005, D-012).
- [ ] A predicate that references a finer-grain column in a
      coarser-grain query raises `E_UNAGGREGATED_FINER_GRAIN_REFERENCE`
      (D-024).
- [ ] A predicate on a windowed expression cannot live in `WHERE`;
      raise `E_WINDOW_IN_WHERE` (D-029).

### 5.5 Conformed dimensions and multi-fact

- [ ] When two facts share a dimension, the planner stitches them
      through the shared dimension's primary key, not by an arbitrary
      join.
- [ ] If no shared dimension exists, the planner raises
      `E3013 NO_STITCHING_DIMENSION`.

### 5.6 Deferred BI features

- [ ] Any reference to `FIXED` / `INCLUDE` / `EXCLUDE` / `TABLE` grain
      modes raises `E_DEFERRED_KEY_REJECTED` or `E1105` at parse time.
- [ ] Semi-additive measures (snapshot LAST / FIRST) are deferred —
      reject at parse time, do not emit window plumbing.
- [ ] Non-equijoin conditions are deferred — reject at parse time.

## 6. Triage rule: prefer deterministic enforcement

Findings from this skill walk a strict hierarchy. Apply this rule
whether you're using the skill to review existing code or to design new
code:

1. **Convert to a deterministic check** — a compliance test that pins
   the exact error code, a property test that asserts the algebra law,
   or a drift test between the spec and the planner. Preferred. Never
   regresses; applies to every future change automatically.
2. **Sharpen this skill's checklist** — if the finding revealed a
   missing angle, update this `SKILL.md` so future runs catch it.
3. **Tighten documentation** — when the rule is true but not
   mechanically checkable, update `ARCHITECTURE.md` / `JOIN_ALGEBRA.md`
   / `INFRA.md` and add an example.
4. **Queue as a code-change sprint item** — last resort, for findings
   that need real implementation work (refactors, new abstractions).

The same rule, in its design-time framing: before writing code that
establishes a boundary or invariant, ask "can I add a deterministic
check that locks this in before the code lands?" If yes, write the
check in the same PR.

## 7. Existing deterministic checks this skill should leverage

| Check | What it enforces | Source |
|:--|:--|:--|
| `tests/properties/test_grain_closure.py` | Grain only coarsens monotonically | source |
| `tests/properties/test_chasm_safety.py` | Chasm traps reject; no silent fan-out | source |
| `tests/properties/test_explosion_safety.py` | Cartesian-style explosion rejected | source |
| `tests/properties/test_enrich_preserves_rows.py` | `enrich` never duplicates parent rows | source |
| `tests/properties/test_mn_rejection.py` | M:N without a safe plan raises | source |
| `tests/properties/test_planner_mn_rejection.py` | Planner-level M:N rejection has the right code | source |
| `compliance/foundation-v0.1/tests/bridge/` | Concrete BI scenarios pin behaviour | source |
| `compliance/foundation-v0.1/tests/cross_grain/` | Cross-grain metric scenarios | source |
| `compliance/foundation-v0.1/tests/filters/` | `WHERE` vs `HAVING` vs semi-join routing | source |
| `compliance/foundation-v0.1/tests/windows/moderate/t-052-window-over-fanout-foreclosed/` | Fan-out + window error is foreclosed | source |
| `bi-concepts-to-osi` skill | Mapping from BI concept to OSI implementation | `.cursor/skills/bi-concepts-to-osi/SKILL.md` |

## 8. Example output format

```markdown
## BI-001 `AVG` over a bridge silently produces wrong SQL

- **Severity**: P0 (correctness bug; silently wrong result)
- **Location**: `impl/python/src/osi/planning/planner_bridge.py:312`
- **Scenario**: model has `customer 1—* order *—1 product`, metric is
  `AVG(orders.amount)` grouped by `product.category`. Current behaviour
  emits `AVG(amount)` after the bridge join, double-counting orders for
  products with multiple categories.
- **Triage**:
  1. (Deterministic) — Pin the rejection with a compliance test
     under `tests/bridge/hard/t-NNN-avg-over-bridge`. `metadata.yaml`
     `expected_error_code: E_UNSAFE_REAGGREGATION`. Lands in this PR.
  2. (Skill) — Add §5.3's `AVG`-over-bridge bullet (done already; keep
     it).
  3. (Code) — Implement bridge-aware AVG via two-pass plan; queue as
     an INFRA roadmap item (D-022 sprint).
- **Spec refs**: D-022, D-027.
```

## 9. Anti-patterns

- "We can `SELECT DISTINCT` away the fan-out at the end." `DISTINCT`
  is not commutative with `SUM`. Always wrong.
- "The user wrote `AVG` over an M:N bridge, so we emit `AVG` and trust
  them." The compiler's job is to refuse undefined queries.
- A new metric flag (`safe_avg: bool`) instead of a typed rejection.
  Either the engine can compute it or it raises; no flags.
- Treating "no shared dimension" as a Cartesian join. Raise
  `E3013 NO_STITCHING_DIMENSION`.
- Emitting `WHERE` for a predicate that references an aggregate, or
  `HAVING` for a row-level predicate. The planner's filter
  classifier (`classify.py`) is the only correct router.

## See also

- [`../../impl/python/docs/JOIN_ALGEBRA.md`](../../impl/python/docs/JOIN_ALGEBRA.md) — the closed algebra.
- [`../../impl/python/docs/JOIN_SAFETY.md`](../../impl/python/docs/JOIN_SAFETY.md) — fan-out / chasm rules.
- [`../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md`](../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md) §6 (joins) and §10 (deferred features).
- [`bi-concepts-to-osi`](../../../.cursor/skills/bi-concepts-to-osi/SKILL.md) — analytical-pattern dictionary.
- [`compiler-best-practices-review/SKILL.md`](../compiler-best-practices-review/SKILL.md) — phase ordering and IR purity for the planner side.
- [`database-best-practices-review/SKILL.md`](../database-best-practices-review/SKILL.md) — what makes the emitted SQL safe in real engines.
