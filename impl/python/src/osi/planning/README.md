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

- `algebra/` — the nine operators, the state, grain-safety guards. The
  load-bearing module; see [`../../../specs/JOIN_ALGEBRA.md`](../../../specs/JOIN_ALGEBRA.md).
- `plan.py` — `QueryPlan`, `PlanStep`, `PlanOperation` enum.
- `planner_context.py` — frozen bundle of model + namespace + graph.
- `planner.py` — the single `Planner` class.
- `classify.py` — filter classification (row-level vs semi-join vs having).
- `joins.py` — join-path resolution and cardinality inference.
- `resolve.py` — name resolution against `Namespace`.
- `prefixes.py` — deterministic synthetic-column / CTE names.

## Reading order for contributors

1. `algebra/state.py` — learn the data model.
2. `algebra/operations.py` — internalize the grain-safety rules.
3. `planner_context.py` — how the environment is bundled.
4. `planner.py` — follow one query through the composer.

Property tests under `tests/properties/` enforce the algebra laws. See
[`../../../docs/ALGEBRA_LAWS.md`](../../../docs/ALGEBRA_LAWS.md).
