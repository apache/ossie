# Error Codes

> **Authoritative catalog: Appendix C of
> [`specs/Proposed_OSI_Semantics.md`](../specs/Proposed_OSI_Semantics.md).**
> This document is the implementation-side mirror — it documents the
> Python `ErrorCode` enum members in `src/osi/errors.py` and how they
> map to Appendix-C codes. When the two disagree, Appendix C wins and
> this document and `errors.py` are updated to match in the same PR.
>
> **Migration in flight (sprints S-1, S-10).** The legacy
> `E10xx` / `E11xx` / `E2xxx` / `E3xxx` numeric codes below are being
> renamed to the `E_*` family from Appendix C. For example, `E1105
> RESERVED_FOR_DEFERRED` becomes `E_DEFERRED_KEY_REJECTED`; `E2001
> AMBIGUOUS_NAME` becomes `E_NAME_COLLISION` (model namespace) /
> `E_AMBIGUOUS_PATH` (relationship traversal); `E2002 NAME_NOT_FOUND`
> becomes `E_NAME_NOT_FOUND`; `E3001 AMBIGUOUS_JOIN_PATH` becomes
> `E_AMBIGUOUS_PATH`. The `E3011 / E3012 / E3013` M:N family is kept
> in numeric form (Appendix C names them `E3012_MN_NO_SAFE_REWRITE`
> and `E3013_NO_STITCHING_DIMENSION`). Until S-10 lands, both names
> coexist in this catalog so existing tests keep working — but
> per the cleanliness clause in `SPEC.md`, S-10 deletes the old
> spellings outright (no aliases).

Every error raised by `osi_python` is an `OSIError` subclass carrying a
stable `ErrorCode`. This file is the catalog; `src/osi/errors.py` is the
source of truth for Python identifiers.

**Tests must assert on `error.code`, never on message text.** The text
evolves; the codes do not.

## Ranges

| Range | Layer | Kind |
|:---|:---|:---|
| `E10xx`–`E11xx` | Parsing | YAML syntax, missing fields, type mismatches, use of deferred features. |
| `E12xx` | Parsing (SQL surface) | `SEMANTIC_VIEW(...)` clause and bare-view SQL grammar / resolution errors. See `specs/SQL_INTERFACE.md §8`. |
| `E2xxx` | Validation | Cross-reference and semantic-rule violations in the model. |
| `E3xxx` | Planning | Grain conflicts, unreachable fields, path ambiguity, chasm traps. |
| `E4xxx` | Algebra | Safety violations (explosion-unsafe aggregations, M:N enrich). |
| `E5xxx` | Codegen | Rendering failures, unsupported-by-dialect. |
| `W6xxx` | Warnings | Non-fatal; analytically suspect patterns. Elevated to errors in strict mode. |

Tests under `tests/properties/test_error_taxonomy.py` assert that every
exception raised anywhere in the compiler is an `OSIError` with a code
from this catalog.

**Status legend:** each code below is either **active** (raised somewhere
in `src/osi/`) or **RESERVED**. A RESERVED code is stable — tooling may
pin to it — but the emit path belongs to a deferred feature
(`SEMANTIC_VIEW` SQL surface, M:N stitch paths, strict-mode warnings,
etc.). `tests/unit/test_error_catalog.py` enforces that every RESERVED
annotation here matches the enum in `src/osi/errors.py`.

---

## `E1xxx` — Parse errors

| Code | Status | Name | Meaning |
|:---|:---:|:---|:---|
| `E1001` | active | `YAML_SYNTAX` | YAML syntax error. |
| `E1002` | active | `MISSING_REQUIRED_FIELD` | Required field absent in YAML. |
| `E1003` | active | `INVALID_ENUM_VALUE` | Enum value not recognized. |
| `E1004` | active | `TYPE_MISMATCH` | Field type does not match schema. |
| `E1005` | active | `IDENTIFIER_INVALID` | Identifier does not conform to the grammar in `specs/OSI_core_file_format.md`. |
| `E1006` | active | `SQL_EXPRESSION_SYNTAX` | Inline SQL expression inside a YAML field fails to parse as a SQLGlot AST. |
| `E_DEFERRED_KEY_REJECTED` | active | `DEFERRED_KEY_REJECTED` | Feature exists in the full OSI spec but is deferred from Foundation v0.1. Fired for `EXISTS_IN`, `referential_integrity`, named filters, per-metric `joins.{type, using_relationships}`, FIXED/INCLUDE/EXCLUDE, filter context, windows, pivot, grouping sets, non-equijoins, `ATTR`/`UNSAFE`/`AGG`/`GRAIN_AGG`. See `specs/deferred/README.md` and `Proposed_OSI_Semantics.md §10`. Replaces the legacy `E1105` (S-1). |
| `E_MIXED_QUERY_SHAPE` | active | `MIXED_QUERY_SHAPE` | Query mixes the aggregation shape (`Dimensions` / `Measures`) with the scalar shape (`Fields`). Foundation v0.1 routes per query into exactly one shape — see `Proposed_OSI_Semantics.md` D-010. (S-2) |
| `E_AGGREGATE_IN_SCALAR_QUERY` | active | `AGGREGATE_IN_SCALAR_QUERY` | A `Fields` entry resolves to an aggregate at the home grain, which is rejected because scalar queries do not collapse rows. See D-011. (S-2) |
| `E_EMPTY_AGGREGATION_QUERY` | active | `EMPTY_AGGREGATION_QUERY` | Aggregation query has neither `Dimensions` nor `Measures`. See D-010. (S-2) |
| `E_EMPTY_SCALAR_QUERY` | active | `EMPTY_SCALAR_QUERY` | Scalar query has no `Fields`. See D-011. (S-2) |
| `E_FAN_OUT_IN_SCALAR_QUERY` | active | `FAN_OUT_IN_SCALAR_QUERY` | A `Fields` entry traverses a one-to-many edge (fan-out), which a scalar query cannot resolve without aggregation. See D-023. (S-2) |
| `E_AGGREGATE_IN_WHERE` | active | `AGGREGATE_IN_WHERE` | `Where` predicate contains an aggregate (raw aggregate function or measure reference). Aggregates evaluate post-`GROUP BY`; move the predicate to `Having`. See D-012a. Replaces the legacy `E3009` for this shape. (S-3) |
| `E_NON_AGGREGATE_IN_HAVING` | active | `NON_AGGREGATE_IN_HAVING` | `Having` predicate is purely row-level (no aggregate). Push it down to `Where`. See D-012b. Replaces the legacy `E3009` for this shape. (S-3) |
| `E_MIXED_PREDICATE_LEVEL` | active | `MIXED_PREDICATE_LEVEL` | Boolean predicate mixes aggregate and non-aggregate halves in one expression tree. Split into separate `Where` / `Having` clauses so the engine can route each half. See D-012c. (S-3) |
| `E_UNAGGREGATED_FINER_GRAIN_REFERENCE` | active | `UNAGGREGATED_FINER_GRAIN_REFERENCE` | A field body references a column from a finer-grain dataset without aggregating it (e.g. `customers.first_order_amount: orders.amount`). Wrap the reference in an aggregate (`SUM(orders.amount)`, …) so the implicit home-grain aggregation can resolve. See D-024. (S-5) |
| `E_UNSAFE_REAGGREGATION` | active | `UNSAFE_REAGGREGATION` | The chosen plan forces a multi-stage decomposition the aggregate cannot survive — typically a holistic aggregate (`MEDIAN`, `PERCENTILE_CONT`) over a §6.7 chasm pre-aggregation or a §6.8.2 stitch. Note: the §6.8.1 bridge plan is **not** in this family — it is a single-pass aggregate over the de-duplicated `(measure-home-row, group-key)` row set, and is accepted bare for every aggregate category per D-027 (`AVG`, `MEDIAN`, and `COUNT(DISTINCT)` over an N:N bridge are all accepted). The fix is either (a) switch to a distributive aggregate, (b) restate at a coarser grain that does not require chasm pre-aggregation, or (c) for M:N references, rely on the bridge plan. See D-022. (S-9) |
| `E_AMBIGUOUS_NESTED_AGGREGATION_GRAIN` | RESERVED | `AMBIGUOUS_NESTED_AGGREGATION_GRAIN` | RESERVED — superseded by `E_NESTED_AGGREGATION_DEFERRED`. The Foundation defers all nested aggregation in metric expressions, so the inner-grain ambiguity this code described is moot today. Retained so external tooling that pinned to it does not break. See D-027. |
| `E_NESTED_AGGREGATION_DEFERRED` | active | `NESTED_AGGREGATION_DEFERRED` | A metric expression contains a nested aggregate (`AVG(COUNT(orders.oid))`, `AVG(AVG(orders.amount))`, …). The Foundation defers all nested aggregation to §10's grain-aware-functions proposal because the construct requires an implicit grain pin on the inner aggregate that §10 will settle explicitly. For distributive aggregates (`SUM`, `COUNT`, `MIN`, `MAX`) the single-step form gives identical numbers; write `SUM(orders.amount)` instead of `SUM(SUM(orders.amount))`. Engines MAY opt back into the legacy two-step planner via the `allow_nested_aggregation` feature flag. See D-027. |
| `E_AGGREGATE_IN_FIELD` | active | `AGGREGATE_IN_FIELD` | A field expression contains an aggregate function (`SUM`, `COUNT`, `AVG`, …), whether over the home dataset's own columns or cross-grain via a `1:N` reach. All aggregates live in model-scoped metrics (top-level `metrics:` section); field expressions are non-aggregate by construction (window functions remain allowed because they are not aggregates in the spec sense). Move the aggregate to a top-level metric and reference it from `Measures`. Engines MAY opt back into the legacy implicit-home-grain field rewrite via the `allow_aggregate_in_field` feature flag. See D-003. |
| `E_FIELD_DEPENDENCY_CYCLE` | active | `FIELD_DEPENDENCY_CYCLE` | Two or more fields on the same dataset reference one another in a cycle (e.g. `a` depends on `b` which depends back on `a`). The planner lowers derived fields into a topologically ordered chain of `ADD_COLUMNS` CTE stages so the emitted SQL is portable across dialects (Snowflake, PostgreSQL, and SQLite reject lateral aliasing within a single `SELECT`). A cyclic dependency cannot be lowered to a finite stage count and is therefore rejected at parse time. Break the cycle by promoting the shared sub-expression to a single field that the others depend on, or by inlining one of the bodies. (Spec: §4.3.) |
| `E_NAME_NOT_FOUND` | active | `NAME_NOT_FOUND` | Identifier (dataset / field / metric / parameter) does not exist in the namespace. Replaces `E2002` for user-facing diagnostics. See D-006. (S-10) |
| `E_NAME_COLLISION` | active | `NAME_COLLISION` | Bare identifier resolves to more than one declared entity (two datasets define the same field name; two metrics share a name). Replaces `E2001`. See D-006. (S-10) |
| `E_AMBIGUOUS_PATH` | active | `AMBIGUOUS_PATH` | Two or more relationship paths reach the same dataset; the planner can't pick one. Disambiguate by tightening the model. Replaces `E3001` for the user surface. See D-018. (S-10) |
| `E_NO_PATH` | active | `NO_PATH` | No relationship-graph path connects the two datasets the query needs to join. Replaces `E2004` and `E3013` for the user surface. See D-018. (S-10) |
| `E_RESERVED_IDENTIFIER` | active | `RESERVED_IDENTIFIER` | User declared an identifier that collides with a reserved Foundation keyword (`GRAIN`, `FILTER`, `QUERY_FILTER`, …). See D-019. (S-10) |
| `E_RESERVED_NAME` | active | `RESERVED_NAME` | User declared a field, dataset, or metric whose name matches a reserved SQL keyword (`SELECT`, `FROM`, `WHERE`, …). The Foundation forbids these because the generated SQL would be ambiguous in some dialects. See D-019. (S-10) |
| `E_WINDOW_IN_WHERE` | active | `WINDOW_IN_WHERE` | A `WHERE` predicate contains a window function. Windows are only allowed in `Measures` / `Fields` / `Order By` / `Having`. See D-028(b). |
| `E_NESTED_WINDOW` | active | `NESTED_WINDOW` | A window function's argument or frame contains another window function (`SUM(SUM(x) OVER ...) OVER ...`). The Foundation rejects nested windows because the outer grain is structurally ambiguous. See D-031. (S-12) |
| `E_WINDOWED_METRIC_COMPOSITION` | active | `WINDOWED_METRIC_COMPOSITION` | A composite metric references a windowed metric. Composing arithmetic on top of a window changes the grain non-uniformly. Wrap the window in an aggregating CTE first if you need to compose. See D-031. (S-12) |
| `E_DEFERRED_FRAME_MODE` | active | `DEFERRED_FRAME_MODE` | A window uses a frame mode (`GROUPS`) or bound (parameterised expressions like `:n PRECEDING`) that is not in Foundation v0.1. Only literal `ROWS` and `RANGE` frames with constant bounds are accepted. See D-032. (S-12) |
| `E_WINDOW_OVER_FANOUT_REWRITE` | active | `WINDOW_OVER_FANOUT_REWRITE` | A window function would be evaluated over a fan-out join (the partition key includes a duplicated row from a 1:N enrichment). The planner cannot rewrite to a pre-fan-out CTE in this case. See D-030. (S-12) |
| `E_UNKNOWN_FUNCTION` | active | `UNKNOWN_FUNCTION` | A function call references a name not in the OSI_SQL_2026 catalog. The whitelist and validator live in `osi.parsing.function_whitelist`; vendor-specific functions must go through the per-dialect `dialects:` block. See D-021. |

## `E12xx` — SQL-surface errors

Raised by the SQL-interface parser defined in
[`specs/SQL_INTERFACE.md`](../specs/SQL_INTERFACE.md). Every code here
fires *before* planning — a malformed SQL query never reaches the
algebra.

| Code | Status | Name | Meaning |
|:---|:---:|:---|:---|
| `E1201` | RESERVED | `SEMANTIC_VIEW_EMPTY` | `SEMANTIC_VIEW(sv)` call has no `DIMENSIONS`, `FACTS`, or `METRICS` clause. |
| `E1202` | RESERVED | `CLAUSE_ORDER` | Clauses inside `SEMANTIC_VIEW(...)` appear in the wrong order (expected `DIMENSIONS → FACTS → METRICS → WHERE`). |
| `E1203` | RESERVED | `REFERENCE_TOO_DEEP` | Three-part reference (`schema.sv.col`) used in Foundation; only bare and `dataset.field` forms are supported. |
| `E1204` | RESERVED | `AMBIGUOUS_BARE_REFERENCE` | Bare name resolves to multiple datasets; qualify with `dataset.name`. |
| `E1205` | RESERVED | `DUPLICATE_OUTPUT_COLUMN` | Output row set would contain two columns with the same name; use an explicit `AS alias` on at least one. |
| `E1206` | active | `METRIC_IN_RAW_AGGREGATE` | Pre-declared metric appears inside a raw aggregate (e.g. `SUM(revenue)`); use `AGG(revenue)` instead. |
| `E1207` | active | `FACTS_METRICS_EXCLUSIVE` | `FACTS` and `METRICS` cannot appear in the same `SEMANTIC_VIEW(...)` call. |
| `E1208` | active | `UNSUPPORTED_SQL_CONSTRUCT` | Bare-view query uses a disallowed construct (`SELECT *`, `LATERAL`, `QUALIFY`, `JOIN`, sub-queries in `WHERE`/`HAVING`, raw window syntax, …). |
| `E1209` | active | `CROSS_DATASET_AD_HOC_AGGREGATE` | Bare view computes aggregates from two datasets at implied cross-grain; use the `SEMANTIC_VIEW(...)` clause with explicit dimensions. |
| `E1210` | RESERVED | `WINDOW_METRIC_DEFERRED` | Query references a window-function metric; window metrics are out of the Foundation. |
| `E1211` | RESERVED | `CLAUSE_ONLY_OUTER` | `LIMIT`, `ORDER BY`, `HAVING`, or `OFFSET` placed inside the `SEMANTIC_VIEW(...)` clause instead of on the outer `SELECT`. |
| `E1212` | active | `COUNT_STAR_AMBIGUOUS` | `COUNT(*)` in a multi-dataset context; qualify with `COUNT(dataset.*)`. |
| `E1213` | RESERVED | `PARAMETER_USED_AS_REFERENCE` | Bare name resolves to a parameter but is used where a dimension or measure is required. |

## `E2xxx` — Validation errors

| Code | Status | Name | Meaning |
|:---|:---:|:---|:---|
| `E2001` | active | `AMBIGUOUS_NAME` | Unqualified reference matches multiple scopes; use `dataset.field`. |
| `E2002` | active | `NAME_NOT_FOUND` | Identifier does not resolve to any declared entity. |
| `E2003` | active | `DUPLICATE_NAME` | Same name declared twice in the same scope. |
| `E2004` | active | `UNREACHABLE_DATASET` | No join path to the referenced dataset. |
| `E2005` | active | `CIRCULAR_METRIC` | Metric composition forms a cycle. |
| `E2006` | active | `INVALID_RELATIONSHIP` | Relationship references missing dataset or field. |
| `E2007` | active | `MISSING_PRIMARY_KEY` | Dataset used on the one-side of an inferred N:1 has no primary key. |
| `E2008` | active | `RESERVED_IDENTIFIER` | Reserved word used as identifier without quoting. |

## `E3xxx` — Planning errors

| Code | Status | Name | Meaning |
|:---|:---:|:---|:---|
| `E3001` | active | `AMBIGUOUS_JOIN_PATH` | Multiple paths between the required datasets; must disambiguate with `using_relationships`. |
| `E3002` | active | `UNSATISFIABLE_GRAIN` | Requested grain cannot be produced from the model's relationships. |
| `E3003` | RESERVED | `AMBIGUOUS_CARDINALITY` | Relationship lacks key declarations to infer cardinality; add PK/UK or declare `cardinality`. Today's planner always infers from declared keys. |
| `E3004` | active | `GRAIN_NOT_SUBSET` | `aggregate()` target grain is not a subset of the source grain. |
| `E3005` | active | `COLUMN_NAME_COLLISION` | Operation produces two columns with the same name. |
| `E3006` | active | `MISSING_COLUMN_DEPENDENCY` | Expression references a column that is not in the current state. |
| `E3007` | active | `AGGREGATE_IN_SCALAR_CONTEXT` | Aggregate function appears in a scalar-only expression (e.g. a dimension). |
| `E3008` | active | `GRAIN_MISMATCH_MERGE` | `merge()` requires equal grains on both branches. |
| `E3009` | RESERVED | `POST_AGGREGATE_REF_PRE_AGGREGATE` | RESERVED — historically raised when a post-aggregation expression referenced a pre-aggregation column. S-3 split this into the three predicate-routing codes (`E_AGGREGATE_IN_WHERE`, `E_NON_AGGREGATE_IN_HAVING`, `E_MIXED_PREDICATE_LEVEL`); the legacy code is retained so external pinning does not break, but no path raises it today. |
| `E3010` | RESERVED | `CHASM_TRAP` | Two facts joined through shared dimension without the planner's per-fact decomposition. Today's merge strategy (`§4.11`) prevents this structurally. |
| `E3011` | active | `MN_AGGREGATION_REJECTED` | **Engine-capability opt-out** for M:N traversal — declared by an engine that does not support M:N at all. M:N-supporting engines (including `osi_python`) emit `E3012` / `E3013` for per-query failures and never raise `E3011` at the user-facing surface. The algebra layer raises this internally as a precondition signal on `N : N` edges; the planner translates to the per-query `E3012` / `E3013`. (Spec: `Proposed_OSI_Semantics.md` §6.8 *Semantic guarantee*.) |
| `E3012` | active | `MN_NO_STITCH_PATH` | An `N : N` traversal in a measure has no semantically-equivalent safe rewrite at the query's grain — no bridge dataset, no shared-dimension stitch path. The user-facing per-query M:N failure code for M:N-supporting engines. Suggest adding a bridge dataset or a shared dimension. (Spec name: `E3012_MN_NO_SAFE_REWRITE`; Python identifier still uses the pre-S-10 spelling. Spec: §6.8.) |
| `E3013` | active | `NO_STITCHING_DIMENSION` | Two unrelated facts (different roots, no path) are referenced together with no dimension shared by both — the result would otherwise be a Cartesian product. Per-query failure code for the multi-fact stitch case. (`E_NO_PATH` is the named-family alias for the same shape; see row above.) (Spec: §6.8.) |

## `E4xxx` — Algebra safety errors

| Code | Status | Name | Meaning |
|:---|:---:|:---|:---|
| `E4001` | active | `EXPLOSION_UNSAFE` | Aggregation reads a `from_join_rhs=True` column without pre-aggregation or cardinality proof. |
| `E4002` | RESERVED | `ENRICH_KEYS_NOT_IN_GRAIN` | `enrich()` join keys on the left side are not in the parent's grain or PK. Today's precondition uses a child-grain fan-trap check. |
| `E4003` | active | `MERGE_COLUMN_OVERLAP` | `merge()` finds overlapping non-grain columns. |
| `E4004` | active | `BROADCAST_NOT_SCALAR` | `broadcast()` received a state whose grain is not empty. |
| `E4005` | active | `FILTERING_JOIN_ADDS_COLUMNS` | A filtering-join was asked to add columns (impossible by definition). |

## `E5xxx` — Codegen errors

| Code | Status | Name | Meaning |
|:---|:---:|:---|:---|
| `E5001` | active | `DIALECT_UNSUPPORTED` | Requested dialect is not implemented. |
| `E5002` | active | `SQLGLOT_RENDER_FAILED` | SQLGlot raised while rendering the final AST. |
| `E5003` | RESERVED | `DIALECT_MISSING_FEATURE` | Plan uses a construct the target dialect cannot express. |

## `W6xxx` — Non-fatal warnings

| Code | Status | Name | Meaning |
|:---|:---:|:---|:---|
| `W6001` | RESERVED | `AVG_OF_AVG` | `AVG` applied to an already-averaged column; arithmetic mean is not decomposable. |
| `W6002` | RESERVED | `REAGG_PRECISION_LOSS` | Re-aggregation pattern that loses precision. |
| `W6003` | RESERVED | `SUSPICIOUS_PATTERN` | Other analytically suspect pattern. |

The warning channel is specified but not yet attached to `QueryPlan`;
enabling it is tracked in `INFRA.md §3`.

Warnings surface in `QueryPlan.warnings` and are written to
`diagnostics.explain(...)` output. Wrap the offending expression in
`UNSAFE(...)` in the metric definition to silence. Strict mode (set via
`PlannerContext(strict=True)`) elevates all `W6xxx` to errors.

---

## Extending this catalog

When adding a new error code:

1. Add the enum value to `ErrorCode` in `src/osi/errors.py` with a
   docstring one-liner.
2. Pick the **lowest unused number in the right range** — never reuse
   a retired code.
3. Add a row here, in the same order as the enum.
4. Add at least one unit test that asserts on the new code.
5. If the code is raised from the algebra (`E4xxx`), add or extend a
   property test under `tests/properties/` to confirm it fires for the
   generated counterexamples the code is meant to catch.
