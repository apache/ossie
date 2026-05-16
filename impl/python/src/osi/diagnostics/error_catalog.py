"""Per-error-code prose explanations.

Each :class:`~osi.errors.ErrorCode` has exactly one entry here, with a
one-paragraph explanation, the spec section it implements, and (where
applicable) the rewrite the user should consider. The Foundation
contract is that *every* code in the enum has an entry — the
``test_error_catalog_explanations`` test enforces this so a new error
code cannot land without a docstring.

Consumers:

* CLI ``osi explain <code>`` (future).
* Compliance suite reporters that want a human-readable cause column.
* Internal debugging — ``from osi.diagnostics.error_catalog import
  explain_error`` is the canonical lookup.

This module deliberately does NOT format messages — those are produced
at the raise site with the relevant context. This module explains the
*class* of error.
"""

from __future__ import annotations

from osi.errors import ErrorCode

_EXPLANATIONS: dict[ErrorCode, str] = {
    # --- Parse errors (E1xxx) -------------------------------------------------
    ErrorCode.E1001_YAML_SYNTAX: (
        "The semantic model YAML could not be parsed. The file is malformed "
        "(e.g. mis-indented block, unclosed quote, tab character) before any "
        "OSI-specific validation runs. Fix the YAML syntax and re-run. "
        "(Spec: §4 — semantic model file format.)"
    ),
    ErrorCode.E1002_MISSING_REQUIRED_FIELD: (
        "A required field on a semantic-model object is missing. The catalog "
        "of required fields is in §4 of the spec; common omissions are "
        "``primary_key`` on a fact dataset and ``measures`` on an aggregation "
        "query. (Spec: §4.)"
    ),
    ErrorCode.E1003_INVALID_ENUM_VALUE: (
        "An enum-typed field (e.g. ``join_type``, ``conformance_level``) was "
        "given a value not in the allowed set. The error context lists the "
        "valid values. (Spec: §4.)"
    ),
    ErrorCode.E1004_TYPE_MISMATCH: (
        "A field's declared type does not match the value supplied "
        "(e.g. a list where a string was expected). (Spec: §4.)"
    ),
    ErrorCode.E1005_IDENTIFIER_INVALID: (
        "An identifier did not match the OSI identifier grammar "
        "(``[a-zA-Z_][a-zA-Z0-9_]*``). Names must be valid SQL identifiers "
        "*and* survive JSON serialisation. (Spec: §4.1.)"
    ),
    ErrorCode.E1006_SQL_EXPRESSION_SYNTAX: (
        "A SQL expression in a metric, field, or filter did not parse with "
        "the OSI_SQL_2026 dialect. The error context names the offending "
        "expression. (Spec: SQL_EXPRESSION_SUBSET_updated.md.)"
    ),
    # --- Foundation v0.1 named codes (Appendix C) -----------------------------
    ErrorCode.E_DEFERRED_KEY_REJECTED: (
        "The model used a YAML key, SQL function, or relationship attribute "
        "that the Foundation v0.1 explicitly defers (e.g. ``EXISTS_IN``, "
        "``referential_integrity``, named filters). The catalog of deferred "
        "constructs is in ``specs/deferred/README.md``. The fix is to remove "
        "the construct or wait for the proposal that re-introduces it. "
        "(Spec: §10, Appendix B.)"
    ),
    ErrorCode.E_MIXED_QUERY_SHAPE: (
        "A query mixes the two query shapes — it declares both ``fields`` "
        "and ``measures`` (or ``dimensions``) at the same time. Foundation "
        "v0.1 requires a query be either *aggregation-shaped* or *scalar-"
        "shaped*. (Spec: D-010.)"
    ),
    ErrorCode.E_AGGREGATE_IN_SCALAR_QUERY: (
        "A scalar-shaped query (one with ``fields``) referenced a metric. "
        "Aggregates only belong in aggregation-shaped queries. To get a "
        "single aggregate value, write an aggregation query with no "
        "dimensions. (Spec: D-011.)"
    ),
    ErrorCode.E_EMPTY_AGGREGATION_QUERY: (
        "An aggregation-shaped query (one that uses ``measures`` or "
        "``dimensions``) declared neither. (Spec: D-010.)"
    ),
    ErrorCode.E_EMPTY_SCALAR_QUERY: (
        "A scalar-shaped query declared an empty ``fields`` array. " "(Spec: D-010.)"
    ),
    ErrorCode.E_FAN_OUT_IN_SCALAR_QUERY: (
        "A scalar query reached a finer-grain dataset across an N:1 or N:N "
        "edge. Scalar queries cannot fan out — every selected field must be "
        "reachable without row multiplication from the anchor dataset. "
        "(Spec: D-023.)"
    ),
    ErrorCode.E_AGGREGATE_IN_WHERE: (
        "A ``where`` predicate contained an aggregate function or a metric "
        "reference. ``where`` is row-level only — use ``having`` for "
        "post-aggregation predicates. (Spec: D-005, D-012.)"
    ),
    ErrorCode.E_NON_AGGREGATE_IN_HAVING: (
        "A ``having`` predicate is purely row-level (no aggregate, no metric "
        "reference). Move the predicate to ``where``. (Spec: D-005, D-012.)"
    ),
    ErrorCode.E_MIXED_PREDICATE_LEVEL: (
        "A predicate combines row-level and aggregate-level expressions in "
        "one connective (``revenue > 100 AND status = 'open'``). The "
        "Foundation requires each predicate to be uniformly row-level or "
        "uniformly aggregate-level so the planner can route it without "
        "ambiguity. Split the predicate into a ``where`` part and a "
        "``having`` part. (Spec: D-005.)"
    ),
    ErrorCode.E_UNAGGREGATED_FINER_GRAIN_REFERENCE: (
        "A field expression on dataset A references a column from dataset B "
        "that is at a finer grain than A, without aggregating it. The "
        "Foundation requires either an aggregation (``SUM(B.x)``) or a "
        "filter that lifts B to A's grain. (Spec: D-024.)"
    ),
    ErrorCode.E_UNSAFE_REAGGREGATION: (
        "The chosen plan forces a multi-stage decomposition the aggregate "
        "cannot survive — typically a holistic aggregate (``MEDIAN``, "
        "``PERCENTILE_CONT``) over a §6.7 chasm pre-aggregation or a §6.8.2 "
        "stitch. The §6.8.1 bridge plan is **not** in this family: it is a "
        "single-pass aggregate over the de-duplicated ``(measure-home-row, "
        "group-key)`` row set, and is accepted bare for every aggregate "
        "category per D-027 (``AVG``, ``MEDIAN``, and ``COUNT(DISTINCT)`` "
        "over an N:N bridge are all accepted). The fix is either (a) "
        "switch to a distributive aggregate, (b) restate at a coarser "
        "grain that does not require chasm pre-aggregation, or (c) for "
        "M:N references, rely on the bridge plan that the engine already "
        "uses for distributive aggregates. (Spec: D-022.)"
    ),
    ErrorCode.E_AMBIGUOUS_NESTED_AGGREGATION_GRAIN: (
        "RESERVED — superseded by ``E_NESTED_AGGREGATION_DEFERRED``. The "
        "Foundation defers all nested aggregation in metric expressions "
        "to §10's grain-aware-functions proposal, so the inner-grain "
        "ambiguity this code described is moot today. The catalog "
        "retains the code so external tooling that pinned to it does "
        "not break, but no path raises it. (Spec: §4.5, D-027.)"
    ),
    ErrorCode.E_NESTED_AGGREGATION_DEFERRED: (
        "A metric expression contains a nested aggregate (an aggregate "
        "function applied to another aggregate's result, e.g. "
        "``AVG(COUNT(orders.oid))``, ``AVG(AVG(orders.amount))``). "
        "Nested aggregation requires an implicit grain pin on the inner "
        "aggregate; the rules for choosing that pin are deferred to "
        "§10's grain-aware-functions proposal. For distributive "
        "aggregates (``SUM``, ``COUNT``, ``MIN``, ``MAX``) the "
        "single-step form gives identical numbers — write "
        "``SUM(orders.amount)`` instead of ``SUM(SUM(orders.amount))``. "
        "For non-distributive aggregates the unweighted "
        "per-home-row-first interpretation waits for §10. Engines MAY "
        "opt back into the legacy two-step planner via the "
        "``allow_nested_aggregation`` feature flag, at the cost of "
        "portability. (Spec: §4.5, D-027.)"
    ),
    ErrorCode.E_AGGREGATE_IN_FIELD: (
        "A field expression contains an aggregate function (``SUM``, "
        "``COUNT``, ``AVG``, ``COUNT(DISTINCT)``, …) whether over the "
        "home dataset's own columns or cross-grain via a ``1:N`` "
        "reach. The Foundation requires all aggregates to live in "
        "model-scoped metrics in the top-level ``metrics:`` section; "
        "field expressions are non-aggregate by construction (window "
        "functions remain allowed because they are not aggregates in "
        "the spec sense). The fix is to move the aggregate to a "
        "top-level metric and reference it from ``Measures``. Engines "
        "MAY opt back into the legacy implicit-home-grain field "
        "rewrite via the ``allow_aggregate_in_field`` feature flag, at "
        "the cost of portability. (Spec: §4.3, D-003.)"
    ),
    ErrorCode.E_FIELD_DEPENDENCY_CYCLE: (
        "Two or more fields on the same dataset reference one another "
        "in a cycle (for example, ``a`` depends on ``b`` which depends "
        "back on ``a``). A dataset's fields form a dependency graph; "
        "the Foundation requires this graph to be a DAG so the planner "
        "can lower derived fields into a topologically ordered "
        "sequence of CTE stages — one ``ADD_COLUMNS`` step per level "
        "— that compiles to portable SQL on every dialect. A cycle "
        "cannot be lowered to a finite number of stages and would "
        "force the planner to rely on lateral column aliasing, which "
        "is rejected by Snowflake, PostgreSQL, and SQLite. The fix is "
        "to break the cycle by promoting the shared sub-expression to "
        "a single field that the others depend on, or to inline one "
        "of the bodies. (Spec: §4.3.)"
    ),
    ErrorCode.E_NAME_NOT_FOUND: (
        "A bare or qualified identifier in the query did not resolve to a "
        "field, metric, dataset, or relationship visible from the current "
        "scope. The error context lists the candidates that *were* in scope. "
        "(Spec: D-006, Appendix C.)"
    ),
    ErrorCode.E_NAME_COLLISION: (
        "Two semantic-model objects share a name in the same namespace, or "
        "a bare reference matches more than one object. Qualify the "
        "reference with its dataset (``orders.amount``) or rename one of "
        "the colliding objects. (Spec: D-006, D-018.)"
    ),
    ErrorCode.E_AMBIGUOUS_PATH: (
        "More than one join path connects the requested datasets and the "
        "Foundation refuses to pick one. Disambiguate by selecting a "
        "specific relationship in the query, or by removing the redundant "
        "relationship from the model. (Spec: D-006, Appendix C.)"
    ),
    ErrorCode.E_NO_PATH: (
        "No relationship chain connects the requested datasets. "
        "Either add the missing relationship to the model, or scope the "
        "query to datasets that are reachable from each other. "
        "(Spec: D-006, Appendix C.)"
    ),
    ErrorCode.E_RESERVED_IDENTIFIER: (
        "An identifier collides with a Foundation reserved word "
        "(``GRAIN``, ``FILTER``, ``QUERY_FILTER``, …). Rename the offending "
        "field, metric, or dataset. (Spec: D-019.)"
    ),
    ErrorCode.E_RESERVED_NAME: (
        "An identifier collides with a SQL reserved keyword from the "
        "OSI_SQL_2026 dialect (``SELECT``, ``FROM``, ``WHERE``, …). Rename "
        "the offending field, metric, or dataset to avoid generating SQL "
        "that is ambiguous in some target dialects. (Spec: D-019.)"
    ),
    ErrorCode.E_WINDOW_IN_WHERE: (
        "A ``Where`` predicate contains a window function "
        "(``OVER (...)``). Windows are only allowed in ``Measures``, "
        "``Fields``, ``Order By``, and ``Having``. Move the predicate "
        "to ``Having`` after wrapping the window in a metric, or use "
        "the qualify-style outer-Where pattern. (Spec: D-030.)"
    ),
    ErrorCode.E_NESTED_WINDOW: (
        "A window function's argument or frame contains another "
        "window function — ``SUM(SUM(x) OVER (PARTITION BY a)) OVER "
        "(PARTITION BY b)``. The outer window's grain is structurally "
        "ambiguous because the inner window already partitions, so "
        "the Foundation rejects nested windows up front. Materialise "
        "the inner window into a CTE first. (Spec: D-031.)"
    ),
    ErrorCode.E_WINDOWED_METRIC_COMPOSITION: (
        "A composite metric references a windowed metric (``ratio = "
        "running_total / SUM(amount)``). Composing arithmetic on top "
        "of a window changes the grain non-uniformly because the "
        "window already collapsed across the partition. Wrap the "
        "windowed metric in an aggregating CTE first if you need to "
        "compose with it. (Spec: D-031.)"
    ),
    ErrorCode.E_DEFERRED_FRAME_MODE: (
        "A window uses a frame mode (``GROUPS``) or a parameterised "
        "frame bound (``ROWS BETWEEN :n PRECEDING AND CURRENT ROW``) "
        "that is not in Foundation v0.1. Only literal ``ROWS`` and "
        "``RANGE`` frames with constant bounds are accepted. (Spec: "
        "D-032.)"
    ),
    ErrorCode.E_UNKNOWN_FUNCTION: (
        "A function call references a name not in the OSI_SQL_2026 "
        "catalog (D-021). The Foundation contract is that every "
        "conforming implementation supports the catalog and rejects "
        "functions outside it; vendor-specific functions must be "
        "wrapped in a per-dialect ``dialects:`` block on the owning "
        "metric or field. The whitelist and validator live in "
        "``osi.parsing.function_whitelist``. (Spec: D-021 / "
        "SQL_EXPRESSION_SUBSET.md.)"
    ),
    ErrorCode.E_AMBIGUOUS_MEASURE_GRAIN: (
        "RESERVED — Appendix C / D-025 catch-all for a measure with "
        "multiple incompatible starting grains where none of the more-"
        "specific codes (``E3012``, ``E3013``, "
        "``E_UNSAFE_REAGGREGATION``) applies. The reference "
        "implementation reaches one of those specific codes today; "
        "this code is reserved for engines that synthesise different "
        "plan choices and need to surface the ambiguity to the user. "
        "The diagnostic MUST list the starting grains the engine "
        "identified."
    ),
    ErrorCode.E_PRIMARY_KEY_REQUIRED: (
        "RESERVED — Appendix C / §4.2. Engines MAY require "
        "``primary_key`` declarations on every dataset (so the table "
        "grain is well-defined). The reference implementation does "
        "not impose this requirement; the code is reserved so an "
        "opt-in deployment can raise it under a stable name."
    ),
    ErrorCode.E_INVALID_NATURAL_GRAIN: (
        "RESERVED — Appendix C. Raised by a future ``natural_grain`` "
        "implementation (currently deferred). The Foundation parser "
        "rejects the ``natural_grain`` key through "
        "``E_DEFERRED_KEY_REJECTED`` today. See "
        "``proposals/foundation-v0.1/Proposed_OSI_Natural_Grain.md``."
    ),
    ErrorCode.E_NATURAL_GRAIN_PRE_AGGREGATION_UNSAFE: (
        "RESERVED — sibling of ``E_INVALID_NATURAL_GRAIN`` for the "
        "pre-aggregation-unsafe case. See "
        "``proposals/foundation-v0.1/Proposed_OSI_Natural_Grain.md``."
    ),
    ErrorCode.E_WINDOW_OVER_FANOUT_REWRITE: (
        "A window function would be evaluated over a fan-out join — "
        "the partition key includes a column from a 1:N enrichment "
        "that has duplicated parent rows. The planner could not "
        "rewrite the query into a pre-fan-out CTE because the "
        "partition expression itself depends on a fan-out column. "
        "Materialise the fan-out into an explicit aggregating CTE "
        "first. (Spec: D-030.)"
    ),
    # --- SQL-surface errors (E12xx) -------------------------------------------
    ErrorCode.E1201_SEMANTIC_VIEW_EMPTY: (
        "A ``SEMANTIC_VIEW`` clause was empty. RESERVED — the SEMANTIC_VIEW "
        "surface is part of the SQL Interface proposal, not Foundation v0.1. "
        "(Spec: SQL_INTERFACE.md §8.)"
    ),
    ErrorCode.E1202_CLAUSE_ORDER: (
        "Clauses inside ``SEMANTIC_VIEW`` appeared in the wrong order. "
        "RESERVED — see ``SQL_INTERFACE.md §8`` for the canonical order."
    ),
    ErrorCode.E1203_REFERENCE_TOO_DEEP: (
        "A ``SEMANTIC_VIEW`` reference exceeded the maximum depth permitted "
        "by the Foundation. RESERVED — SQL_INTERFACE.md §8."
    ),
    ErrorCode.E1204_AMBIGUOUS_BARE_REFERENCE: (
        "A bare reference inside ``SEMANTIC_VIEW`` matched more than one "
        "field. RESERVED — SQL_INTERFACE.md §8."
    ),
    ErrorCode.E1205_DUPLICATE_OUTPUT_COLUMN: (
        "Two output columns in a ``SEMANTIC_VIEW`` carry the same name. "
        "RESERVED — SQL_INTERFACE.md §8."
    ),
    ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE: (
        "A SEMANTIC_VIEW used a raw aggregate (``SUM(x)``) where the spec "
        "requires a metric reference. (Spec: SQL_INTERFACE.md §8.)"
    ),
    ErrorCode.E1207_FACTS_METRICS_EXCLUSIVE: (
        "A SEMANTIC_VIEW combined ``FACTS`` and ``METRICS`` in a single "
        "clause. The two are mutually exclusive. (Spec: SQL_INTERFACE.md §8.)"
    ),
    ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT: (
        "A SEMANTIC_VIEW used a SQL construct not in the OSI_SQL_2026 "
        "subset (e.g. ``LATERAL``, ``MATCH_RECOGNIZE``). "
        "(Spec: SQL_EXPRESSION_SUBSET_updated.md.)"
    ),
    ErrorCode.E1209_CROSS_DATASET_AD_HOC_AGGREGATE: (
        "A raw aggregate inside a SEMANTIC_VIEW spanned multiple datasets — "
        "this requires a metric definition (which carries grain). "
        "(Spec: SQL_INTERFACE.md §8.)"
    ),
    ErrorCode.E1210_WINDOW_METRIC_DEFERRED: (
        "Windowed metric definitions are deferred. RESERVED — see "
        "the windows proposal."
    ),
    ErrorCode.E1211_CLAUSE_ONLY_OUTER: (
        "A clause appeared inside an inner SEMANTIC_VIEW that is only "
        "permitted on the outer query. RESERVED — SQL_INTERFACE.md §8."
    ),
    ErrorCode.E1212_COUNT_STAR_AMBIGUOUS: (
        "``COUNT(*)`` appeared in a context where the planner could not "
        "infer which dataset it counts. Qualify it (``COUNT(orders.*)``) or "
        "use a metric reference. (Spec: SQL_INTERFACE.md §8.)"
    ),
    ErrorCode.E1213_PARAMETER_USED_AS_REFERENCE: (
        "A parameter was used in a position the spec reserves for a "
        "reference. RESERVED — see the parameters proposal."
    ),
    # --- Validation (E2xxx — legacy; now mapped to E_* at the boundary) ------
    ErrorCode.E2001_AMBIGUOUS_NAME: (
        "Internal alias of ``E_NAME_COLLISION``. The user-facing surface "
        "translates this to ``E_NAME_COLLISION`` at the adapter boundary. "
        "(Spec: D-006, D-018.)"
    ),
    ErrorCode.E2002_NAME_NOT_FOUND: (
        "Internal alias of ``E_NAME_NOT_FOUND`` raised from "
        "``osi.parsing.namespace``. The adapter boundary translates this "
        "to the user-facing ``E_NAME_NOT_FOUND``. (Spec: D-006.)"
    ),
    ErrorCode.E2003_DUPLICATE_NAME: (
        "Internal alias of ``E_NAME_COLLISION`` for duplicate declarations "
        "inside a single semantic model. (Spec: D-018.)"
    ),
    ErrorCode.E2004_UNREACHABLE_DATASET: (
        "Internal alias of ``E_NO_PATH`` raised from the namespace "
        "builder when no relationship chain reaches the requested dataset. "
        "(Spec: D-006.)"
    ),
    ErrorCode.E2005_CIRCULAR_METRIC: (
        "A metric definition references itself (transitively). " "(Spec: §4.4.)"
    ),
    ErrorCode.E2006_INVALID_RELATIONSHIP: (
        "A relationship's columns do not match the column lists declared on "
        "the two endpoints. (Spec: §4.7.)"
    ),
    ErrorCode.E2007_MISSING_PRIMARY_KEY: (
        "A dataset is referenced as a join target without a "
        "``primary_key`` declaration. (Spec: §4.6.)"
    ),
    ErrorCode.E2008_RESERVED_IDENTIFIER: (
        "Internal alias of ``E_RESERVED_IDENTIFIER`` for the parser layer. "
        "(Spec: D-019.)"
    ),
    # --- Planning (E3xxx — legacy) -------------------------------------------
    ErrorCode.E3001_AMBIGUOUS_JOIN_PATH: (
        "Internal alias of ``E_AMBIGUOUS_PATH`` raised from the join "
        "planner when more than one relationship chain connects the "
        "requested datasets. (Spec: D-006.)"
    ),
    ErrorCode.E3002_UNSATISFIABLE_GRAIN: (
        "A reference cannot be reduced to the requested grain because no "
        "aggregation lifts it. (Spec: §6.)"
    ),
    ErrorCode.E3003_AMBIGUOUS_CARDINALITY: (
        "RESERVED — kept for a future explicit ``cardinality:`` declaration "
        "on relationships. Cardinality is currently inferred from declared "
        "keys. (Spec: §4.7.)"
    ),
    ErrorCode.E3004_GRAIN_NOT_SUBSET: (
        "An algebra step received a grain that is not a subset of its input "
        "grain. (Spec: §6 — algebra invariants.)"
    ),
    ErrorCode.E3005_COLUMN_NAME_COLLISION: (
        "A project step received two columns with the same name. Either "
        "the model has duplicates or the planner has emitted the same "
        "column twice — this is treated as a structural error so the bug "
        "surfaces at the algebra layer. (Spec: §6 — algebra invariants.)"
    ),
    ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY: (
        "A step referenced a column that is not in any of its inputs. "
        "(Spec: §6 — algebra invariants.)"
    ),
    ErrorCode.E3007_AGGREGATE_IN_SCALAR_CONTEXT: (
        "A scalar-context expression (``WHERE``, ``ORDER BY``) reached an "
        "aggregate function. The Foundation surfaces this as "
        "``E_AGGREGATE_IN_WHERE`` at the user boundary. (Spec: D-005.)"
    ),
    ErrorCode.E3008_GRAIN_MISMATCH_MERGE: (
        "A merge step's two inputs do not agree on grain. (Spec: §6 — "
        "merge precondition.)"
    ),
    ErrorCode.E3009_POST_AGGREGATE_REF_PRE_AGGREGATE: (
        "RESERVED — S-3 split this code into the named predicate-routing "
        "codes (``E_AGGREGATE_IN_WHERE``, ``E_NON_AGGREGATE_IN_HAVING``, "
        "``E_MIXED_PREDICATE_LEVEL``)."
    ),
    ErrorCode.E3010_CHASM_TRAP: (
        "RESERVED — chasm traps are prevented structurally by the per-fact "
        "merge strategy in §4.11; no path raises this today."
    ),
    ErrorCode.E3011_MN_AGGREGATION_REJECTED: (
        "Engine-capability opt-out — an engine that does not support M:N "
        "traversal raises this for every M:N query. This reference "
        "implementation is "
        "M:N-supporting; the algebra layer raises this as an internal "
        "precondition signal on ``N : N`` edges, which the planner "
        "translates to the user-facing per-query codes ``E3012`` / "
        "``E3013`` (or ``E_NO_PATH`` for the two-fact stitch case). "
        "(Spec: §6.8 *Semantic guarantee*.)"
    ),
    ErrorCode.E3012_MN_NO_SAFE_REWRITE: (
        "An ``N : N`` traversal in a measure has no semantically-"
        "equivalent safe rewrite at the query's grain — no bridge, no "
        "shared-dimension stitch. The user-facing per-query M:N failure "
        "code for M:N-supporting engines. Suggest adding a bridge "
        "dataset or a shared dimension. (Spec: §6.8.)"
    ),
    ErrorCode.E3013_NO_STITCHING_DIMENSION: (
        "Two unrelated facts (different roots, no path) are referenced "
        "together with no dimension shared by both — the result would "
        "otherwise be a Cartesian product. Per-query failure code for "
        "the multi-fact stitch case (also exposed as ``E_NO_PATH`` in "
        "the named-family surface). (Spec: §6.8 / D-006.)"
    ),
    # --- Algebra (E4xxx) -----------------------------------------------------
    ErrorCode.E4001_EXPLOSION_UNSAFE: (
        "An algebra step would multiply rows in a way the planner does not "
        "promise to deduplicate. The fix is to use a filtering join or to "
        "materialise a distinct bridge. (Spec: §6.6.)"
    ),
    ErrorCode.E4002_ENRICH_KEYS_NOT_IN_GRAIN: (
        "RESERVED — the enrich precondition is currently expressed as a "
        "fan-trap check over child grain, so this shape never fires "
        "independently."
    ),
    ErrorCode.E4003_MERGE_COLUMN_OVERLAP: (
        "Two inputs to a merge step carry overlapping non-grain columns. "
        "Project one side first. (Spec: §6.7.)"
    ),
    ErrorCode.E4004_BROADCAST_NOT_SCALAR: (
        "A broadcast step received an input that is not scalar (more than "
        "one row). (Spec: §6.8.)"
    ),
    ErrorCode.E4005_FILTERING_JOIN_ADDS_COLUMNS: (
        "A filtering-join step would add columns from the rhs to its lhs — "
        "filtering joins are pure semi/anti joins. (Spec: §6.9.)"
    ),
    # --- Codegen (E5xxx) -----------------------------------------------------
    ErrorCode.E5001_DIALECT_UNSUPPORTED: (
        "The requested dialect is not registered with the codegen layer. "
        "Pass ``--dialect <name>`` with a supported value. "
        "(Spec: §7 — codegen.)"
    ),
    ErrorCode.E5002_SQLGLOT_RENDER_FAILED: (
        "SQLGlot raised while rendering the QueryPlan to SQL. The plan is "
        "structurally valid but SQLGlot rejected an AST shape — this is "
        "almost always a bug in the planner. The error context preserves "
        "the SQLGlot exception. (Spec: §7.)"
    ),
    ErrorCode.E5003_DIALECT_MISSING_FEATURE: (
        "RESERVED — every dialect we ship today is lifted via SQLGlot, so a "
        "feature that reaches codegen is supported by construction. Carved "
        "out for when bespoke transpilers ship."
    ),
    # --- Warnings (W6xxx — RESERVED) -----------------------------------------
    ErrorCode.W6001_AVG_OF_AVG: (
        "RESERVED — ``AVG`` of an ``AVG`` warning. The diagnostics warnings "
        "channel is specified but not yet wired into the planner."
    ),
    ErrorCode.W6002_REAGG_PRECISION_LOSS: (
        "RESERVED — re-aggregation precision loss warning. Same status as " "``W6001``."
    ),
    ErrorCode.W6003_SUSPICIOUS_PATTERN: (
        "RESERVED — generic suspicious-pattern warning. Same status as " "``W6001``."
    ),
}


def explain_error(code: ErrorCode) -> str:
    """Return the catalog explanation for ``code``.

    Raises ``KeyError`` if no explanation is registered — but the
    ``test_error_catalog_explanations`` test guarantees at module import
    time that this never happens for any member of :class:`ErrorCode`.
    """
    return _EXPLANATIONS[code]


def all_explanations() -> dict[ErrorCode, str]:
    """Return a copy of the full catalog.

    Used by tests and by tooling that wants to dump the catalog
    (``osi explain --all``).
    """
    return dict(_EXPLANATIONS)


__all__ = ["explain_error", "all_explanations"]
