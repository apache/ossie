# `osi.planning` — Layer 2

Takes a `SemanticModel` and a `SemanticQuery`, produces a frozen `QueryPlan`.

```
Layer 1: parsing/   YAML ──────────────▶ SemanticModel + Namespace + RelationshipGraph
                                         (immutable, dependency-free)

Layer 2: planning/  + SemanticQuery ───▶ QueryPlan                ← YOU ARE HERE
                                         (tuple[PlanStep], each step holds an
                                          immutable CalculationState)

Layer 3: codegen/   + dialect ─────────▶ SQL string
```

**Contract.**

1. The planner never generates SQL and never mutates the semantic model.
2. The planner is a *composer of algebra operators*. Every transformation
   is a call to an operator in `algebra/operations.py`. Nothing else
   may construct a `CalculationState`.
3. Same `(model, query)` ⇒ same `QueryPlan`, byte-identical.

## Module map

### Core IR + entry point

- `algebra/` — the nine operators, the state, grain-safety guards. The
  load-bearing module; see
  [`../../../../docs/JOIN_ALGEBRA.md`](../../../../docs/JOIN_ALGEBRA.md).
- `plan.py` — `QueryPlan`, `PlanStep`, `PlanOperation` enum, `PlanPayload`
  hierarchy.
- `planner_context.py` — frozen bundle of model + namespace + graph
  plus the `FoundationFlags` opt-ins (e.g. `experimental_exists_in`).
- `planner.py` — the single `Planner` class; aggregation-query composer.
- `semantic_query.py` — `SemanticQuery` value type and its
  parameter/named-filter binding helpers (see `preprocess.py`).

### Phase helpers — consumed in order by `planner.py`

- `preprocess.py` — query-level rewrites that run before the planner
  proper (parameter binding, named-filter expansion).
- `resolve.py` — name resolution against `Namespace`.
- `classify.py` — filter classification (row-level vs semi-join vs
  post-aggregate having).
- `joins.py` — join-path resolution and cardinality inference.
- `home_grain.py` — implicit home-grain rewrite for fields that
  aggregate finer-grained columns (D-015).
- `metric_dispatch.py` — assigns each `ResolvedMetric` to its single
  home dataset; surfaces `E1209` for cross-dataset ad-hoc aggregates.
- `metric_shape.py` — classifies a metric body as aggregate /
  composite / windowed / nested (`Proposed_OSI_Semantics.md §4.5`).

### Per-shape composers — invoked by `planner.py` based on classification

- `planner_scalar.py` — scalar (Fields-only) query composer
  (`Proposed_OSI_Semantics.md §5.1.2`).
- `planner_bridge.py` — M:N bridge resolution, distinct-bridge dedup,
  nested-aggregate-over-bridge plans (D-022, D-026, D-027).
- `planner_nested.py` — nested cross-grain aggregate planner
  (D-020, D-024).
- `planner_composites.py` — composite metric (formula) planning over
  declared metrics.
- `planner_mn.py` — multi-fact / many-to-many helpers used by both
  bridge and stitch paths.

### Algebra-step factories + naming

- `columns.py` — pure builders that convert parsed `Field` / `Metric`
  bodies into algebra-level `Column` / `AggregateInfo` values; called
  at `SOURCE` and `AGGREGATE` step construction time.
- `steps.py` — `PlanBuilder` accumulator that runs an algebra operator
  and records the resulting `PlanStep` with its payload (the bridge
  between the planner's topology and the closed algebra).
- `prefixes.py` — deterministic synthetic-column / CTE names; the
  only place a contributor should obtain a synthetic identifier.

## Reading order for contributors

1. `algebra/state.py` — learn the data model.
2. `algebra/operations.py` — internalize the grain-safety rules.
3. `planner_context.py` — how the environment is bundled.
4. `planner.py` — follow one query through the composer.

Property tests under `tests/properties/` enforce the algebra laws. See
[`../../../docs/ALGEBRA_LAWS.md`](../../../docs/ALGEBRA_LAWS.md).
