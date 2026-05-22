# `osi.common` — cross-layer primitives

The `common` package holds the value types every layer below it imports.
It has **no upstream dependency** inside `osi/`: no pydantic, no model,
no planner, no codegen. Everything here is pure-Python plus
SQLGlot/`networkx` adapters that the rest of the implementation treats
as a thin protocol.

Keeping these primitives in one place is what lets the import-linter
contract in `pyproject.toml` enforce the one-way flow
`parsing → planning → codegen` (with the `common` package imported by
all) — every layer can reach back here for shared types, none of them
sees the others.

## Modules

| Module | Purpose | Public surface |
| --- | --- | --- |
| `identifiers.py` | Case-folded `Identifier` NewType (`normalize_identifier`, `is_valid_identifier`, `identifiers_equal`). Single source of truth for "is this a valid OSI name?" — the parser, planner, and codegen all defer to it. | `Identifier`, `normalize_identifier`, `is_valid_identifier`, `identifiers_equal` |
| `sql_expr.py` | `FrozenSQL` — an immutable, comparable wrapper around a SQLGlot AST. Provides `FrozenSQL.of(...)`, `parse_sql_expr(...)`, and `sql_expr_equal(...)` so two expressions are equal iff their canonical form is. Required for golden-test determinism. | `FrozenSQL`, `parse_sql_expr`, `sql_expr_equal` |
| `types.py` | Cross-layer NewTypes (`DimensionSet = frozenset[Identifier]`, `CTEName`, `ExpressionId`, `SourceLocation`) and the `Dialect` enum. | `DimensionSet`, `CTEName`, `ExpressionId`, `SourceLocation`, `Dialect` |
| `windows.py` | Pure SQL-AST predicates over window functions (`contains_window`, `is_windowed_expression`). Lives in this package because both parsing (deferred-feature gate) and planning (window placement / fan-out rewrite) need them — see the architecture review of S-9 / S-12. | `contains_window`, `is_windowed_expression`, … |

## Invariants

1. **No internal dependency.** The package may only import from the
   Python standard library, `sqlglot`, `networkx`, and other modules
   in this package.
2. **Frozen everywhere.** Every public type is immutable. Mutating a
   `FrozenSQL` or rebinding an `Identifier` is a bug.
3. **One canonical form.** `normalize_identifier` and
   `FrozenSQL.of(...)` are the only places that materialise a
   canonical form; anything else must call through them so equality
   stays definition-driven, not source-text-driven.

If you find yourself reaching for something more "domain-specific"
than a value type or a SQL-AST predicate, it belongs in
`osi.parsing` (model shapes), `osi.planning` (plan / algebra), or
`osi.diagnostics` (introspection) — not here.
