"""Grain-matching join operators: ``merge`` and ``filtering_join``.

Extracted from :mod:`osi.planning.algebra.operations` to keep the
per-file line budget (``INFRA.md §1.2``, 600 lines). Everything here is
still part of the closed algebra and shares the same contract:

* pure, total, deterministic;
* never mutates its inputs;
* raises :class:`AlgebraError` with a stable :class:`ErrorCode` on any
  precondition failure.

Mutation-score target: **≥ 90%** (same as ``operations.py``). A
surviving mutation in these functions is a P0 bug.
"""

from __future__ import annotations

from osi.common.types import DimensionSet
from osi.errors import AlgebraError, ErrorCode
from osi.planning.algebra.operations import FilterMode
from osi.planning.algebra.state import CalculationState


def merge(
    left: CalculationState,
    right: CalculationState,
    *,
    on: DimensionSet | None = None,
) -> CalculationState:
    """FULL OUTER join at matching grain (chasm-trap resolution).

    Preconditions
    -------------
    * ``left.grain == right.grain``
    * if ``on`` is given, ``on == left.grain`` (the spec §3.7 defines
      merging on the shared grain — other joins must go through
      ``enrich`` or ``filtering_join``)
    * non-grain columns of the two sides are disjoint

    Grain effect: **preserved** (both sides share it).
    """
    if left.grain != right.grain:
        raise AlgebraError(
            ErrorCode.E3008_GRAIN_MISMATCH_MERGE,
            "merge requires equal grains",
            context={
                "left_grain": sorted(left.grain),
                "right_grain": sorted(right.grain),
            },
        )
    if on is not None and on != left.grain:
        raise AlgebraError(
            ErrorCode.E3008_GRAIN_MISMATCH_MERGE,
            "merge `on` argument must equal the shared grain",
            context={
                "on": sorted(on),
                "grain": sorted(left.grain),
            },
        )
    left_nongrain = {c.name for c in left.columns if c.name not in left.grain}
    right_nongrain = {c.name for c in right.columns if c.name not in right.grain}
    overlap = left_nongrain & right_nongrain
    if overlap:
        raise AlgebraError(
            ErrorCode.E4003_MERGE_COLUMN_OVERLAP,
            f"merge cannot overlap non-grain columns: {sorted(overlap)}",
            context={"columns": sorted(overlap)},
        )
    grain_columns = tuple(c for c in left.columns if c.name in left.grain)
    left_extras = tuple(c for c in left.columns if c.name not in left.grain)
    right_extras = tuple(c for c in right.columns if c.name not in right.grain)
    return CalculationState(
        grain=left.grain,
        columns=grain_columns + left_extras + right_extras,
        provenance=left.provenance | right.provenance,
        # FULL OUTER on the shared grain may introduce rows that exist
        # on only one side. A UK that held on the left may not hold on
        # the right's-only rows (and vice versa), so only the
        # intersection of the two UK sets is provably safe post-merge.
        unique_keys=left.unique_keys & right.unique_keys,
    )


def filtering_join(
    state: CalculationState,
    rhs: CalculationState,
    *,
    lhs_keys: DimensionSet,
    rhs_keys: DimensionSet,
    mode: FilterMode,
) -> CalculationState:
    """Semi-join (``SEMI``) or anti-semi-join (``ANTI``).

    Used for ``EXISTS_IN`` / ``NOT EXISTS_IN``. No columns are added —
    that is the defining difference from
    :func:`osi.planning.algebra.enrich`.

    Preconditions
    -------------
    * ``lhs_keys ⊆ state.column_names``
    * ``rhs_keys ⊆ rhs.column_names``
    * ``len(lhs_keys) == len(rhs_keys)`` (composite-key join needs a
      matched arity; the plan step records the pairing)
    """
    if len(lhs_keys) != len(rhs_keys):
        raise AlgebraError(
            ErrorCode.E4005_FILTERING_JOIN_ADDS_COLUMNS,
            "filtering_join requires matching key arity",
            context={
                "lhs_arity": len(lhs_keys),
                "rhs_arity": len(rhs_keys),
            },
        )
    missing_lhs = lhs_keys - state.column_names
    if missing_lhs:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            f"filtering_join lhs_keys missing: {sorted(missing_lhs)}",
            context={"missing": sorted(missing_lhs), "side": "lhs"},
        )
    missing_rhs = rhs_keys - rhs.column_names
    if missing_rhs:
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            f"filtering_join rhs_keys missing: {sorted(missing_rhs)}",
            context={"missing": sorted(missing_rhs), "side": "rhs"},
        )
    if mode not in FilterMode:
        raise AlgebraError(
            ErrorCode.E4005_FILTERING_JOIN_ADDS_COLUMNS,
            f"unknown filtering_join mode: {mode!r}",
            context={"mode": str(mode)},
        )
    _ = rhs  # rhs is only used for key presence checks; no columns flow
    return state


__all__ = ["filtering_join", "merge"]
