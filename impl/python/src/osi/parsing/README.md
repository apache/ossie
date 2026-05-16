# `osi.parsing` — Layer 1

Takes a YAML path or string and produces a frozen, validated
`SemanticModel`, `Namespace`, and `RelationshipGraph`.

**Contract.**

1. Parsing produces objects the rest of the compiler can trust without
   re-validating.
2. Any use of a deferred feature (see
   [`../../../specs/deferred/`](../../../specs/deferred/)) raises
   `E1105 RESERVED_FOR_DEFERRED`.
3. Parsing imports nothing from `osi.planning` or `osi.codegen`.

## Module map

- `models.py` — pydantic v2 schemas (`extra="forbid"`).
- `parser.py` — top-level `parse_semantic_model(path)` entry point.
- `validation.py` — cross-reference and semantic-rule validation.
- `deferred.py` — visitor that raises `E1105` for deferred features.
- `namespace.py` — name-resolution index.
- `graph.py` — `RelationshipGraph` construction.
- `sql/` — SQL-surface parser implementing
  [`../../../specs/SQL_INTERFACE.md`](../../../specs/SQL_INTERFACE.md).
  Converts `SEMANTIC_VIEW(...)` / bare-view SQL text into a
  `SemanticQuery` that the planner consumes. Raises `E12xx` on any
  grammatical or resolution error. This is the **only** entry point for
  SQL-shaped input; direct construction of `SemanticQuery` is available
  for programmatic callers but not required.

Expressions in fields, metrics, filters, and havings are parsed with
`sqlglot.parse_one(dialect="ansi")` and stored as frozen ASTs. Raw SQL
strings never propagate to the planner.
