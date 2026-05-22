"""The closed algebra — the correctness boundary of the compiler.

All compiler transformations must compose operators from this module.
See ``../../../../docs/JOIN_ALGEBRA.md`` (the
authoritative spec) for operator signatures, preconditions, grain
contracts, and laws.

The nine operators and their current planner wiring:

- :func:`source` — initialize from a dataset. *Emitted for every leaf.*
- :func:`filter_` — apply a row-level predicate (underscored; ``filter``
  is a Python builtin). *Emitted for row-level ``WHERE`` and for
  ``HAVING``.*
- :func:`enrich` — N:1 join that preserves parent grain. *Emitted for
  every safe single-hop or multi-hop enrichment step.*
- :func:`aggregate` — coarsen to a target grain. *Emitted once per
  measure group.*
- :func:`project` — keep only the named columns. *Emitted once at the
  root.*
- :func:`add_columns` — introduce derived scalar columns. *Emitted only
  for composite metrics — arithmetic combinations of already-defined
  metrics. (Spec: §4.5 — Metrics, rule 2.)*
- :func:`merge` — full-outer chasm-safe join at matching grain.
  *Emitted when two measure groups with different fact datasets must
  be combined via the stitch plan. (Spec: §6.8.2 — stitch.)*
- :func:`filtering_join` — semi-/anti-semi-join. **Experimental.**
  Emitted for ``EXISTS_IN`` / ``NOT EXISTS_IN`` predicates in
  ``WHERE`` when the caller opts in via
  ``FoundationFlags(experimental_exists_in=True)``. Foundation v0.1
  §10 / D-017 lists semi-join filtering as deferred, so the default
  Foundation parser rejects ``EXISTS_IN`` with
  ``E_DEFERRED_KEY_REJECTED``. The operator and its laws are kept
  in the closed algebra so the experimental codepath remains
  testable; turning the flag off does not remove this operator from
  the package, only from the planner's emission path.
- :func:`broadcast` — attach a scalar column. **Reserved.** The
  operator and its ``BROADCAST`` plan step exist so the algebra stays
  closed under nine operators, but today's planner never emits it —
  scalar-per-row calculations go through composite metrics. The
  operator's ``E4004_BROADCAST_NOT_SCALAR`` precondition is still
  covered by unit tests so a future sprint can turn on the planner
  path without destabilising the algebra.

Mutation-score target: ≥ 90% (``INFRA.md §1.1``). A surviving mutation
in this module is a P0.
"""

from osi.planning.algebra.composition import add_columns, broadcast
from osi.planning.algebra.grain import (
    AggregateStep,
    BroadcastStep,
    EnrichStep,
    GrainSimulationError,
    MergeStep,
    OperatorTag,
    SimpleStep,
    SimState,
    SourceStep,
    Step,
    combine_grains,
    is_coarser,
    simulate,
    simulate_grain,
)
from osi.planning.algebra.joins import filtering_join, merge
from osi.planning.algebra.operations import (
    FilterMode,
    JoinType,
    aggregate,
    enrich,
    filter_,
    project,
    source,
)
from osi.planning.algebra.state import (
    AggregateFunction,
    AggregateInfo,
    CalculationState,
    Column,
    ColumnKind,
    Decomposability,
)

__all__ = [
    "AggregateFunction",
    "AggregateInfo",
    "AggregateStep",
    "BroadcastStep",
    "CalculationState",
    "Column",
    "ColumnKind",
    "Decomposability",
    "EnrichStep",
    "FilterMode",
    "GrainSimulationError",
    "JoinType",
    "MergeStep",
    "OperatorTag",
    "SimState",
    "SimpleStep",
    "SourceStep",
    "Step",
    "add_columns",
    "aggregate",
    "broadcast",
    "combine_grains",
    "enrich",
    "filter_",
    "filtering_join",
    "is_coarser",
    "merge",
    "project",
    "simulate",
    "simulate_grain",
    "source",
]
