"""Deterministic synthetic naming for the planner.

Every synthetic name in the compiler comes from this module. The
``ARCHITECTURE.md §6`` invariant that equal ``(model, query, dialect)``
triples produce byte-identical SQL depends on every generated identifier
being derivable from inputs only.

No module outside this file may embed literal prefixes. See the
``import-linter`` contract for the enforced rule.
"""

from __future__ import annotations

from typing import Iterable

from osi.common.identifiers import Identifier, normalize_identifier

CTE_MEASURE_GROUP = "mg"
CTE_FILTER_JOIN_RHS = "fj"
CTE_MERGED = "merged"
CTE_FINAL = "final"
CTE_STEP = "step"

SYNTH_COLUMN_AGG_PREFIX = "__agg"
SYNTH_COLUMN_DERIVED_PREFIX = "__derived"


def cte_name(prefix: str, index: int) -> Identifier:
    """Return ``<prefix>_<index>`` as a normalized identifier."""
    return normalize_identifier(f"{prefix}_{index}")


def step_alias(step_id: int) -> str:
    """Return the canonical CTE alias for plan step ``step_id``.

    The Foundation uses zero-padded ``step_000`` form so that
    lexicographic order matches numeric order in error messages, plan
    dumps, and golden files. All emitters (codegen, diagnostics,
    cte_optimizer) must go through this helper.
    """
    return f"{CTE_STEP}_{step_id:03d}"


def is_step_alias(name: str) -> bool:
    """Return ``True`` if ``name`` looks like a step CTE alias.

    Used by the codegen post-processor to identify step CTEs without
    re-implementing the format. The check is intentionally
    string-prefix only (``cte_optimizer`` cares only about reachability,
    not exact step ids).
    """
    return name.startswith(f"{CTE_STEP}_")


def mangle_join_key(dataset: str, column: str) -> Identifier:
    """Return a deterministic synthetic name for a join-side key column."""
    return normalize_identifier(f"__jk_{dataset}__{column}")


def synth_aggregate_name(index: int) -> Identifier:
    """Return a deterministic name for an unnamed aggregate expression."""
    return normalize_identifier(f"{SYNTH_COLUMN_AGG_PREFIX}_{index}")


def synth_derived_name(index: int) -> Identifier:
    """Return a deterministic name for an unnamed derived scalar."""
    return normalize_identifier(f"{SYNTH_COLUMN_DERIVED_PREFIX}_{index}")


def stable_sorted_identifiers(names: Iterable[Identifier]) -> tuple[Identifier, ...]:
    """Return ``names`` sorted deterministically by their string form."""
    return tuple(sorted(names, key=str))


__all__ = [
    "CTE_FILTER_JOIN_RHS",
    "CTE_FINAL",
    "CTE_MEASURE_GROUP",
    "CTE_MERGED",
    "CTE_STEP",
    "SYNTH_COLUMN_AGG_PREFIX",
    "SYNTH_COLUMN_DERIVED_PREFIX",
    "cte_name",
    "is_step_alias",
    "mangle_join_key",
    "stable_sorted_identifiers",
    "step_alias",
    "synth_aggregate_name",
    "synth_derived_name",
]
