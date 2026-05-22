"""Typed error hierarchy for the OSI Python reference implementation.

See ``docs/ERROR_CODES.md`` for the full catalog. Every code listed there
must have an enum value here before it can be raised in production code.

Tests must assert on ``error.code``, never on message text.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    """Stable error codes. See ``docs/ERROR_CODES.md``.

    A code marked ``RESERVED`` is documented in ``docs/ERROR_CODES.md``
    and the relevant spec, but has no emit path in the current
    implementation. Reserved codes belong to deferred features
    (``SEMANTIC_VIEW`` SQL surface, M:N stitch paths, strict-mode
    warnings). They are retained so (a) external tooling that pins to a
    code is not broken when the feature lands, and (b) the spec
    references in the catalog stay stable. Tests covering reserved
    codes live alongside the features that raise them — not here.
    """

    # E1xxx — Parse errors
    E1001_YAML_SYNTAX = "E1001"
    E1002_MISSING_REQUIRED_FIELD = "E1002"
    E1003_INVALID_ENUM_VALUE = "E1003"
    E1004_TYPE_MISMATCH = "E1004"
    E1005_IDENTIFIER_INVALID = "E1005"
    E1006_SQL_EXPRESSION_SYNTAX = "E1006"
    # E_* — Foundation v0.1 named codes (Appendix C of
    # ``Proposed_OSI_Semantics.md``). The Foundation rollout (S-1..S-17)
    # is migrating every ``E1xxx``/``E2xxx``/``E3xxx`` numeric code to
    # an ``E_*`` named code; new code MUST use the named form.
    E_DEFERRED_KEY_REJECTED = "E_DEFERRED_KEY_REJECTED"
    # S-2 / D-010 / D-011 / D-023 — query-shape errors.
    E_MIXED_QUERY_SHAPE = "E_MIXED_QUERY_SHAPE"
    E_AGGREGATE_IN_SCALAR_QUERY = "E_AGGREGATE_IN_SCALAR_QUERY"
    E_EMPTY_AGGREGATION_QUERY = "E_EMPTY_AGGREGATION_QUERY"
    E_EMPTY_SCALAR_QUERY = "E_EMPTY_SCALAR_QUERY"
    E_FAN_OUT_IN_SCALAR_QUERY = "E_FAN_OUT_IN_SCALAR_QUERY"
    # S-3 / D-005 / D-012 — predicate-routing errors that replace the
    # legacy E3009 with named codes matching the spec's three-way
    # taxonomy.
    E_AGGREGATE_IN_WHERE = "E_AGGREGATE_IN_WHERE"
    E_NON_AGGREGATE_IN_HAVING = "E_NON_AGGREGATE_IN_HAVING"
    E_MIXED_PREDICATE_LEVEL = "E_MIXED_PREDICATE_LEVEL"
    # S-5 / D-024 — a field body that references a finer grain
    # without aggregating it.
    E_UNAGGREGATED_FINER_GRAIN_REFERENCE = "E_UNAGGREGATED_FINER_GRAIN_REFERENCE"
    # S-9 / D-022 — the chosen plan forces a multi-stage decomposition the
    # aggregate cannot survive (holistic over §6.7 chasm pre-aggregation
    # or §6.8.2 stitch). The §6.8.1 bridge plan is **conceptually** not
    # in this family — D-027 describes it as a single-pass aggregate over
    # the de-duplicated row set. The reference implementation currently
    # realises that route only for the distributive operators (``SUM``,
    # ``COUNT``, ``MIN``, ``MAX``) plus ``COUNT(DISTINCT)``; ``AVG``,
    # ``MEDIAN``, and ``PERCENTILE_CONT`` over an N:N bridge are still
    # pending and surface this code today (see ``planner_bridge.py``).
    E_UNSAFE_REAGGREGATION = "E_UNSAFE_REAGGREGATION"
    # RESERVED — superseded by E_NESTED_AGGREGATION_DEFERRED. The
    # Foundation defers all nested aggregation in metric expressions to
    # §10's grain-aware-functions proposal (Proposed_OSI_Semantics.md
    # §4.5, D-027). The catalog keeps this code so older tooling that
    # pinned to it does not break, but no path raises it today; the
    # active code is ``E_NESTED_AGGREGATION_DEFERRED``.
    E_AMBIGUOUS_NESTED_AGGREGATION_GRAIN = "E_AMBIGUOUS_NESTED_AGGREGATION_GRAIN"
    # Foundation v0.1 §4.5 / D-027 — nested aggregation in a metric
    # expression (an aggregate function applied to another aggregate's
    # result, e.g. ``AVG(COUNT(orders.oid))``) is deferred to §10's
    # grain-aware-functions proposal. Behind the
    # ``allow_nested_aggregation`` feature flag the planner accepts the
    # construct via ``planner_nested``; with the flag off the parser
    # rejects the metric body up front with this code.
    E_NESTED_AGGREGATION_DEFERRED = "E_NESTED_AGGREGATION_DEFERRED"
    # Foundation v0.1 §4.3 / D-003 — a field expression contains an
    # aggregate function (``SUM``, ``COUNT``, ``AVG``, …), whether
    # over the home dataset's own columns or via a ``1:N`` reach. All
    # aggregates live in model-scoped metrics (§4.5); field expressions
    # are non-aggregate by construction. Behind the
    # ``allow_aggregate_in_field`` feature flag the planner falls back
    # to the legacy implicit-home-grain rewrite in
    # ``osi.planning.home_grain``.
    E_AGGREGATE_IN_FIELD = "E_AGGREGATE_IN_FIELD"
    # Foundation v0.1 §4.3 — fields on the same dataset may reference
    # one another, but the dependency graph must be a DAG. A cycle
    # (e.g. field ``a`` depends on field ``b`` which depends on
    # field ``a``) cannot be lowered to a finite sequence of
    # ``ADD_COLUMNS`` stages and so is rejected at parse time. The
    # planner relies on the topological order of inter-field
    # dependencies to emit portable SQL — see
    # :func:`osi.planning.steps.source_step` and
    # :func:`osi.planning.columns.compute_field_dependencies`.
    E_FIELD_DEPENDENCY_CYCLE = "E_FIELD_DEPENDENCY_CYCLE"
    # S-10 / D-006 / D-018 / D-019 — identifier resolution + path
    # errors. These replace the legacy E2001 / E2002 / E2004 / E2008 /
    # E3001 numeric codes for user-facing diagnostics.
    E_NAME_NOT_FOUND = "E_NAME_NOT_FOUND"
    E_NAME_COLLISION = "E_NAME_COLLISION"
    E_AMBIGUOUS_PATH = "E_AMBIGUOUS_PATH"
    E_NO_PATH = "E_NO_PATH"
    E_RESERVED_IDENTIFIER = "E_RESERVED_IDENTIFIER"
    E_RESERVED_NAME = "E_RESERVED_NAME"
    # S-12 / D-028 / D-030 / D-031 / D-032 — window-function placement
    # and composition rules. Window functions live in ``Measures``,
    # ``Fields``, ``Order By``, and ``Having``; never in ``Where`` or
    # nested under another window. Frame modes other than ``ROWS`` /
    # ``RANGE`` and parameterised frame bounds are deferred.
    E_WINDOW_IN_WHERE = "E_WINDOW_IN_WHERE"
    E_NESTED_WINDOW = "E_NESTED_WINDOW"
    E_WINDOWED_METRIC_COMPOSITION = "E_WINDOWED_METRIC_COMPOSITION"
    E_DEFERRED_FRAME_MODE = "E_DEFERRED_FRAME_MODE"
    # Implementation extension (F-16). Spec §6.10 accepts windowed
    # metrics in ``Measures`` of an aggregation query directly (D-031
    # only defers *composing* a windowed metric from another metric).
    # The aggregation planner does not yet implement that surface —
    # it currently misclassified windowed metrics as composite and
    # raised the misleading ``E1206_METRIC_IN_RAW_AGGREGATE``. The new
    # code is the precise diagnostic the spec called for in F-16.
    # Scalar (Fields-only) queries continue to compile windowed
    # metrics as ``ADD_COLUMNS`` per §6.10 / D-028; see
    # :mod:`osi.planning.planner_scalar`.
    E_WINDOWED_MEASURE_NOT_SUPPORTED = "E_WINDOWED_MEASURE_NOT_SUPPORTED"
    # RESERVED — D-030. The fan-out-vs-window failure mode is foreclosed
    # earlier in the current planner: the scalar branch rejects every
    # 1:N edge with ``E_FAN_OUT_IN_SCALAR_QUERY`` (D-023) before
    # reaching the window step, and the aggregation branch rejects
    # windowed measures at parse with ``E_WINDOWED_METRIC_COMPOSITION``
    # (windowed metric expressions are not yet planned in the
    # aggregation branch — see ``INFRA.md`` I-43). The code stays in
    # the enum because Appendix C requires it and so the future
    # surface — windowed measures in aggregation queries — has a
    # ready landing pad. Compliance test
    # ``t-052-window-over-fanout-foreclosed`` pins the current
    # foreclose-before-window behaviour.
    E_WINDOW_OVER_FANOUT_REWRITE = "E_WINDOW_OVER_FANOUT_REWRITE"
    # D-021 — function call that is not in the OSI_SQL_2026 catalog.
    # The catalog is the contract for every Foundation v0.1
    # implementation; vendor-specific functions go through the
    # per-dialect ``dialects:`` block. The active whitelist and
    # validator live in :mod:`osi.parsing.function_whitelist`.
    E_UNKNOWN_FUNCTION = "E_UNKNOWN_FUNCTION"
    # RESERVED — D-025 catch-all for measures with multiple
    # incompatible starting grains where none of the more-specific
    # codes (``E3012``, ``E3013``, ``E_UNSAFE_REAGGREGATION``)
    # applies. The reference implementation reaches one of those
    # specific codes today; this code is reserved for engines that
    # synthesise different plan choices and need to surface the
    # ambiguity. The diagnostic MUST list the starting grains the
    # engine identified. (Appendix C / D-025.)
    E_AMBIGUOUS_MEASURE_GRAIN = "E_AMBIGUOUS_MEASURE_GRAIN"
    # RESERVED — Appendix C / §4.2. Engines that opt to require
    # ``primary_key`` declarations on every dataset (so the table
    # grain is well-defined) raise this when a model omits one. The
    # reference implementation does not currently impose this
    # requirement; the code is reserved so an opt-in deployment can
    # raise it under a stable name.
    E_PRIMARY_KEY_REQUIRED = "E_PRIMARY_KEY_REQUIRED"
    # RESERVED — Appendix C. Declared by a model that uses the
    # (deferred) ``natural_grain`` proposal. Reserved here so the
    # diagnostic surface is stable when the natural-grain proposal
    # lands; the Foundation parser rejects ``natural_grain`` outright
    # through ``E_DEFERRED_KEY_REJECTED`` today. See
    # ``proposals/foundation-v0.1/Proposed_OSI_Semantics.md`` §10
    # (the natural_grain feature is deferred to a future proposal).
    E_INVALID_NATURAL_GRAIN = "E_INVALID_NATURAL_GRAIN"
    # RESERVED — sibling of ``E_INVALID_NATURAL_GRAIN`` for the
    # pre-aggregation-unsafe case. Same deferred future proposal as
    # above — see ``Proposed_OSI_Semantics.md`` §10.
    E_NATURAL_GRAIN_PRE_AGGREGATION_UNSAFE = "E_NATURAL_GRAIN_PRE_AGGREGATION_UNSAFE"
    # Implementation extension — raised when the IR or a diagnostic
    # detects a *programmer* error, never a user error. Examples:
    # a ``QueryPlan`` whose step DAG is not topologically sorted
    # (``plan.py:__post_init__``); a payload subclass that has no
    # JSON-encoder case (``_payload_to_json``); a resolved reference
    # subclass that has no ``_reference_entry`` case
    # (``diagnostics/resolve.py``). The shape of the error means
    # "the compiler invariants are out of sync; ship a fix" rather
    # than "your model is wrong"; keeping it inside the typed
    # ``OSIError`` hierarchy means our property tests ("every failure
    # carries a code") still hold for these paths.
    E_INTERNAL_INVARIANT = "E_INTERNAL_INVARIANT"

    # E12xx — SQL-surface errors (reserved for the future
    # SQL_INTERFACE proposal §8). Only E1206 / E1207 / E1208 / E1209 /
    # E1212 have active emit paths today; the rest are RESERVED for
    # the SEMANTIC_VIEW clause parser.
    E1201_SEMANTIC_VIEW_EMPTY = "E1201"  # RESERVED — future SQL_INTERFACE §8
    E1202_CLAUSE_ORDER = "E1202"  # RESERVED — future SQL_INTERFACE §8
    E1203_REFERENCE_TOO_DEEP = "E1203"  # RESERVED — future SQL_INTERFACE §8
    E1204_AMBIGUOUS_BARE_REFERENCE = "E1204"  # RESERVED — future SQL_INTERFACE §8
    E1205_DUPLICATE_OUTPUT_COLUMN = "E1205"  # RESERVED — future SQL_INTERFACE §8
    E1206_METRIC_IN_RAW_AGGREGATE = "E1206"
    E1207_FACTS_METRICS_EXCLUSIVE = "E1207"
    E1208_UNSUPPORTED_SQL_CONSTRUCT = "E1208"
    E1209_CROSS_DATASET_AD_HOC_AGGREGATE = "E1209"
    E1210_WINDOW_METRIC_DEFERRED = "E1210"  # RESERVED — window metrics deferred
    E1211_CLAUSE_ONLY_OUTER = "E1211"  # RESERVED — future SQL_INTERFACE §8
    E1212_COUNT_STAR_AMBIGUOUS = "E1212"
    E1213_PARAMETER_USED_AS_REFERENCE = "E1213"  # RESERVED — future SQL_INTERFACE §8

    # E2xxx — Validation errors
    E2001_AMBIGUOUS_NAME = "E2001"
    E2002_NAME_NOT_FOUND = "E2002"
    E2003_DUPLICATE_NAME = "E2003"
    E2004_UNREACHABLE_DATASET = "E2004"
    E2005_CIRCULAR_METRIC = "E2005"
    E2006_INVALID_RELATIONSHIP = "E2006"
    E2007_MISSING_PRIMARY_KEY = "E2007"
    E2008_RESERVED_IDENTIFIER = "E2008"

    # E3xxx — Planning errors
    E3001_AMBIGUOUS_JOIN_PATH = "E3001"
    E3002_UNSATISFIABLE_GRAIN = "E3002"
    # RESERVED — cardinality is inferred from declared keys today, so
    # there is no path that raises this. Kept so a future explicit
    # ``cardinality:`` YAML field or a constraint-free relationship can
    # fail with a stable code.
    E3003_AMBIGUOUS_CARDINALITY = "E3003"
    E3004_GRAIN_NOT_SUBSET = "E3004"
    E3005_COLUMN_NAME_COLLISION = "E3005"
    E3006_MISSING_COLUMN_DEPENDENCY = "E3006"
    E3007_AGGREGATE_IN_SCALAR_CONTEXT = "E3007"
    E3008_GRAIN_MISMATCH_MERGE = "E3008"
    # RESERVED — S-3 split this code into the named predicate-routing
    # codes (E_AGGREGATE_IN_WHERE, E_NON_AGGREGATE_IN_HAVING,
    # E_MIXED_PREDICATE_LEVEL). Retained so external pinning does not
    # break, but no path raises it today.
    E3009_POST_AGGREGATE_REF_PRE_AGGREGATE = "E3009"
    # RESERVED — today's per-fact merge strategy (§4.11) means a chasm
    # trap is prevented structurally rather than raised; see
    # ``Proposed_OSI_Semantics.md §6.4``.
    E3010_CHASM_TRAP = "E3010"
    # E3011 is the engine-capability opt-out code: an engine that does
    # not support M:N traversal at all raises it for every M:N query.
    # This reference implementation is M:N-supporting (per ``Proposed_OSI_Semantics.md``
    # §6.8 *Semantic guarantee*); the algebra layer raises ``E3011``
    # internally as a precondition signal on ``N : N`` edges, and the
    # planner translates it to the user-facing per-query codes
    # ``E3012`` / ``E3013``.
    E3011_MN_AGGREGATION_REJECTED = "E3011"
    # E3012 / E3013 are the user-facing per-query M:N failure codes
    # for M:N-supporting engines: ``E3012`` when no safe rewrite exists
    # for a particular query (no bridge, no shared-dimension stitch);
    # ``E3013`` when two unrelated facts have no shared dimension to
    # stitch on. See ``Proposed_OSI_Semantics.md`` §6.8.
    E3012_MN_NO_SAFE_REWRITE = "E3012"
    E3013_NO_STITCHING_DIMENSION = "E3013"

    # E4xxx — Algebra safety errors
    E4001_EXPLOSION_UNSAFE = "E4001"
    # RESERVED — enrich's precondition is phrased as a fan-trap check
    # over child grain, so this shape never fires independently today.
    E4002_ENRICH_KEYS_NOT_IN_GRAIN = "E4002"
    E4003_MERGE_COLUMN_OVERLAP = "E4003"
    E4004_BROADCAST_NOT_SCALAR = "E4004"
    E4005_FILTERING_JOIN_ADDS_COLUMNS = "E4005"

    # E5xxx — Codegen errors
    E5001_DIALECT_UNSUPPORTED = "E5001"
    E5002_SQLGLOT_RENDER_FAILED = "E5002"
    # RESERVED — the Foundation lifts every dialect via SQLGlot, so a
    # feature that reaches codegen is supported by construction. This
    # code is carved out for when bespoke transpilers ship.
    E5003_DIALECT_MISSING_FEATURE = "E5003"

    # W6xxx — Warnings (non-fatal unless strict mode). All RESERVED:
    # the diagnostic warnings channel is specified but not yet wired
    # into planning (``diagnostics.explain`` does not attach warnings
    # to the QueryPlan today).
    W6001_AVG_OF_AVG = "W6001"  # RESERVED
    W6002_REAGG_PRECISION_LOSS = "W6002"  # RESERVED
    W6003_SUSPICIOUS_PATTERN = "W6003"  # RESERVED


class OSIError(Exception):
    """Root of every error raised anywhere in the compiler.

    Carries a stable ``code`` (see ``ErrorCode``) and an optional
    ``context`` dict with actionable fields (dataset, field, grain,
    suggestion). Tests should assert on ``error.code``, never on
    message text.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        context: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.context: dict[str, object] = dict(context or {})


class OSIParseError(OSIError):
    """Raised from ``osi.parsing``. Codes in ``E1xxx`` / ``E2xxx``."""


class OSIPlanningError(OSIError):
    """Raised from ``osi.planning`` (outside the algebra). Codes in ``E3xxx``."""


class AlgebraError(OSIError):
    """Raised from ``osi.planning.algebra``.

    The algebra raises two adjacent code families:

    * ``E4xxx`` — *safety* failures (explosion, broadcast shape, merge
      column overlap, filtering-join shape). These are conditions only
      the algebra can detect.
    * ``E3xxx`` — *contract* failures inherited from the surrounding
      planning layer (grain mismatch, missing column, M:N rejection).
      They are surfaced by algebra preconditions because the algebra
      is the place where the planner's promises become non-negotiable.

    Tests should assert on ``error.code``, never on which family a
    code happens to fall in.
    """


class OSICodegenError(OSIError):
    """Raised from ``osi.codegen``. Codes in ``E5xxx``."""


class OSIWarning(OSIError):
    """Non-fatal warnings. Codes in ``W6xxx``. In strict mode these are errors."""
