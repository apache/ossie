# `osi.codegen` — Layer 3

Walks a `QueryPlan` and produces a SQL string for the requested dialect.

**Contract.**

1. Codegen never reads the `SemanticModel` or `Namespace`. Every fact it
   needs comes from the plan.
2. All SQL composition goes through SQLGlot AST nodes
   (`sqlglot.exp.*`). Raw-string SQL is banned; CI checks for it.
3. Same `(plan, dialect)` ⇒ byte-identical SQL.

## Module map

- `transpiler.py` — `PlanStep` → SQLGlot AST.
- `dialect.py` — dialect-specific transforms (ANSI / DuckDB / Snowflake).
- `cte_optimizer.py` — post-build AST transforms (inlining, folding).
- `types.py` — codegen-local NewTypes.

If you're tempted to look up a metric definition in the semantic model,
stop — the plan is missing information. Extend `PlanStep`.
