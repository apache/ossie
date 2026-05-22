# `osi.codegen` — Layer 3

Walks a `QueryPlan` and produces a SQL string for the requested dialect.

**Contract.**

1. Codegen never reads the `SemanticModel` or `Namespace`. Every fact it
   needs comes from the plan.
2. All SQL composition goes through SQLGlot AST nodes
   (`sqlglot.exp.*`). Raw-string SQL is banned; CI checks for it.
3. Same `(plan, dialect)` ⇒ byte-identical SQL.

## IR surface

`codegen` *does* import several types from `osi.planning.algebra.*`
(`Column`, `CalculationState`, `AggregateFunction`, `JoinType`,
`FilterMode`, `PlanStep`, …). These are the algebra-side IR types
that the plan exposes — they are part of the plan's published surface,
not an internal-only detail of the planner. Refactors to those types
are coordinated with codegen on purpose; the `import-linter` rule for
this layer (see `pyproject.toml`) intentionally allows planning
imports for that reason.

If a new type emerges that codegen needs but the algebra has not yet
published, prefer extending `PlanStep` or its payloads rather than
reaching into a less-visible algebra helper.

## Module map

- `transpiler.py` — `PlanStep` → SQLGlot AST.
- `dialect.py` — dialect-specific transforms (ANSI / DuckDB / Snowflake).
- `cte_optimizer.py` — post-build AST transforms (inlining, folding).
- `types.py` — codegen-local NewTypes.

If you're tempted to look up a metric definition in the semantic model,
stop — the plan is missing information. Extend `PlanStep`.
