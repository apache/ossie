# `osi.parsing` — Layer 1

Takes a YAML path or string and produces a frozen, validated
`SemanticModel`, `Namespace`, and `RelationshipGraph`.

**Contract.**

1. Parsing produces objects the rest of the compiler can trust without
   re-validating.
2. Any use of a deferred feature (see §10 of
   [`Proposed_OSI_Semantics.md`](../../../../../proposals/foundation-v0.1/Proposed_OSI_Semantics.md)
   and the design archive in
   (`Proposed_OSI_Semantics.md §10`) raises
   `E_DEFERRED_KEY_REJECTED`.
3. Parsing imports nothing from `osi.planning` or `osi.codegen`.

## Module map

- `parser.py` — top-level `parse_semantic_model(path_or_yaml)` entry
  point. Returns a `ParseResult` carrying the frozen `SemanticModel`,
  `Namespace`, and `RelationshipGraph`.
- `models.py` — pydantic v2 schemas (`extra="forbid"`) for every YAML
  construct in the Foundation spec.
- `validation.py` — cross-reference and semantic-rule validation that
  runs after pydantic.
- `foundation.py` — Foundation-only rules (e.g. aggregate-in-field
  rejection per D-003) that fire when the model uses Foundation
  semantics.
- `deferred.py` — visitor that rejects YAML keys and expression forms
  listed in §10 of the Foundation spec with `E_DEFERRED_KEY_REJECTED`.
- `namespace.py` — builds the name-resolution index that the planner
  uses for `dataset.metric`-style references.
- `graph.py` — `RelationshipGraph` construction over declared
  relationships.
- `field_deps.py` — computes per-field dependency closure used by
  validation and the planner.
- `function_whitelist.py` — the `OSI_SQL_2026` function whitelist
  (D-021); the union of every aggregate / window / date / string /
  math / conditional / type-conversion function in
  `proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md`. Functions
  not in this whitelist raise `E_UNKNOWN_FUNCTION` at parse time.
- `reserved_names.py` — single source of truth for identifiers reserved
  by the Foundation surface.
- `_root.py` — internal helpers used during YAML pre-processing.

## Expressions

Every expression in fields, metrics, filters, and havings is parsed
with `sqlglot.parse_one(dialect=...)` and stored as a frozen AST
(`FrozenSQL` in `osi.common.sql_expr`). The default dialect is
`OSI_SQL_2026` (the Foundation expression dialect; see
[`SQL_EXPRESSION_SUBSET.md`](../../../../../proposals/foundation-v0.1/SQL_EXPRESSION_SUBSET.md)).
Raw SQL strings never propagate to the planner.

## SQL surface

The Foundation also defines a SQL surface
(reserved for the future SQL_INTERFACE proposal)
that lets callers issue `SELECT … FROM SEMANTIC_VIEW(…)` queries.
That surface is *not* implemented in this layer; semantic queries are
built programmatically via the `osi.planning.SemanticQuery`
constructor. A SQL-surface parser is on the roadmap (see `INFRA.md`).
