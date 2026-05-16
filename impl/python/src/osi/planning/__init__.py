r"""Layer 2 of the compiler pipeline.

Takes a ``SemanticModel`` + ``SemanticQuery`` and produces a frozen
``QueryPlan`` — an ordered tuple of ``PlanStep``\s, each wrapping a
closed-algebra operator over an immutable ``CalculationState``.

See ``../../../ARCHITECTURE.md`` §3 and ``../../../specs/JOIN_ALGEBRA.md``
for the algebra contract.
"""

from osi.planning.algebra import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
    Decomposability,
    FilterMode,
    JoinType,
    add_columns,
    aggregate,
    broadcast,
    enrich,
    filter_,
    filtering_join,
    merge,
    project,
    source,
)
from osi.planning.classify import (
    ClassifiedWhere,
    PostAggregatePredicate,
    RowLevelPredicate,
    SemiJoinKeyPair,
    SemiJoinPredicate,
    classify_having,
    classify_where,
)
from osi.planning.joins import JoinStep, find_enrichment_path
from osi.planning.plan import (
    AddColumnsPayload,
    AggregatePayload,
    BroadcastPayload,
    EnrichPayload,
    FilteringJoinPayload,
    FilterPayload,
    MergePayload,
    OrderByEntry,
    PlanOperation,
    PlanPayload,
    PlanStep,
    ProjectPayload,
    QueryPlan,
    SourcePayload,
)
from osi.planning.planner import plan
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import (
    ResolvedDimension,
    ResolvedFact,
    ResolvedField,
    ResolvedMetric,
    ResolvedReference,
    resolve_dimension,
    resolve_measure,
    resolve_reference,
)
from osi.planning.semantic_query import OrderBy, Reference, SemanticQuery, SortDirection

__all__ = [
    "AddColumnsPayload",
    "AggregateFunction",
    "AggregateInfo",
    "AggregatePayload",
    "BroadcastPayload",
    "CalculationState",
    "ClassifiedWhere",
    "Column",
    "ColumnKind",
    "Decomposability",
    "EnrichPayload",
    "FilterMode",
    "FilterPayload",
    "FilteringJoinPayload",
    "JoinStep",
    "JoinType",
    "MergePayload",
    "OrderBy",
    "OrderByEntry",
    "PlanOperation",
    "PlanPayload",
    "PlanStep",
    "PlannerContext",
    "PostAggregatePredicate",
    "ProjectPayload",
    "QueryPlan",
    "Reference",
    "ResolvedDimension",
    "ResolvedFact",
    "ResolvedField",
    "ResolvedMetric",
    "ResolvedReference",
    "RowLevelPredicate",
    "SemanticQuery",
    "SemiJoinKeyPair",
    "SemiJoinPredicate",
    "SortDirection",
    "SourcePayload",
    "add_columns",
    "aggregate",
    "broadcast",
    "classify_having",
    "classify_where",
    "enrich",
    "filter_",
    "filtering_join",
    "find_enrichment_path",
    "merge",
    "plan",
    "project",
    "resolve_dimension",
    "resolve_measure",
    "resolve_reference",
    "source",
]
