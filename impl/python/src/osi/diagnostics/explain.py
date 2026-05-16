"""Per-step trace of a :class:`~osi.planning.plan.QueryPlan`.

The text format is one line per :class:`PlanStep`, grouped into blocks
by operation. Each line carries:

- the step alias (``step_000``, ``step_001``, ...) — identical to the
  CTE alias emitted by :mod:`osi.codegen.transpiler` so traces line up
  with the generated SQL;
- the operation name;
- the step inputs (so the DAG is reconstructable by eye);
- the *output grain* as the primary invariant — grain is the semantic
  guarantee the algebra makes about each intermediate state;
- a short summary of the payload.

The JSON variant is the same content, structured for tools.
"""

from __future__ import annotations

from typing import Any

from osi.common.identifiers import Identifier
from osi.common.types import DimensionSet
from osi.errors import ErrorCode, OSIError
from osi.planning.algebra.state import Column
from osi.planning.plan import (
    AddColumnsPayload,
    AggregatePayload,
    BroadcastPayload,
    EnrichDerivedPayload,
    EnrichPayload,
    FilteringJoinPayload,
    FilterPayload,
    MergePayload,
    PlanPayload,
    PlanStep,
    ProjectPayload,
    QueryPlan,
    SourcePayload,
)
from osi.planning.prefixes import step_alias as _alias


def explain(plan: QueryPlan) -> str:
    """Render ``plan`` as a human-readable per-step trace."""
    lines: list[str] = []
    lines.append(
        f"root: {_alias(plan.root_step_id)}  "
        f"(steps={len(plan.steps)}, limit={plan.limit})"
    )
    if plan.output_columns:
        cols = ", ".join(str(c) for c in plan.output_columns)
        lines.append(f"output: [{cols}]")
    if plan.order_by:
        order = ", ".join(
            f"{o.column}{' DESC' if o.descending else ''}" for o in plan.order_by
        )
        lines.append(f"order_by: [{order}]")
    lines.append("")
    for step in plan.steps:
        lines.extend(_render_step(step))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def explain_json(plan: QueryPlan) -> dict[str, Any]:
    """Return a JSON-safe dict mirroring :func:`explain`'s content."""
    return {
        "root": _alias(plan.root_step_id),
        "limit": plan.limit,
        "output_columns": [str(c) for c in plan.output_columns],
        "order_by": [
            {"column": str(o.column), "descending": o.descending} for o in plan.order_by
        ],
        "steps": [_step_to_json(s) for s in plan.steps],
    }


def _render_step(step: PlanStep) -> list[str]:
    header_inputs = ", ".join(_alias(i) for i in step.inputs) if step.inputs else "-"
    grain = sorted(str(g) for g in step.state.grain)
    grain_str = "{" + ", ".join(grain) + "}" if grain else "{}"
    lines = [
        f"{_alias(step.step_id)}  {step.operation.name}  "
        f"<- {header_inputs}  grain={grain_str}"
    ]
    summary = _payload_summary(step.payload)
    if summary:
        lines.append(f"    {summary}")
    cols = ", ".join(str(c.name) for c in step.state.columns)
    lines.append(f"    columns: [{cols}]")
    return lines


def _payload_summary(payload: PlanPayload) -> str:
    """Return a one-line trace summary for every payload variant.

    Every subclass of :data:`PlanPayload` must have a case here. The
    exhaustive-match test
    (:mod:`tests/unit/diagnostics/test_explain_exhaustive`) walks
    ``PlanPayload`` so adding a payload variant without a case
    surfaces the gap immediately. ``E_INTERNAL_INVARIANT`` is the
    safety net for the property "every plan can be explained".
    """
    if isinstance(payload, SourcePayload):
        return f"source: {payload.dataset} @ {payload.source}"
    if isinstance(payload, FilterPayload):
        scope = "post-aggregate" if payload.is_post_aggregate else "row"
        return f"filter ({scope}): {payload.predicate.canonical}"
    if isinstance(payload, EnrichPayload):
        pairs = _render_enrich_pairs(
            payload.parent_keys, payload.child_keys, payload.keys
        )
        cols = _render_column_list(payload.child_columns)
        return (
            f"enrich {payload.join_type.name}: "
            f"{payload.child_dataset} @ {payload.child_source}  "
            f"on [{pairs}]  adds [{cols}]"
        )
    if isinstance(payload, EnrichDerivedPayload):
        pairs = _render_enrich_pairs(
            payload.parent_keys, payload.child_keys, payload.keys
        )
        cols = _render_column_list(payload.child_columns)
        return (
            f"enrich_derived {payload.join_type.name}: " f"on [{pairs}]  adds [{cols}]"
        )
    if isinstance(payload, AggregatePayload):
        grain = ", ".join(sorted(str(g) for g in payload.new_grain))
        aggs = ", ".join(str(a.name) for a in payload.aggregations)
        return f"aggregate: grain=({grain}) aggs=[{aggs}]"
    if isinstance(payload, ProjectPayload):
        cols = ", ".join(str(c) for c in payload.columns)
        return f"project: [{cols}]"
    if isinstance(payload, AddColumnsPayload):
        defs = ", ".join(str(d.name) for d in payload.definitions)
        return f"add_columns: [{defs}]"
    if isinstance(payload, MergePayload):
        on = ", ".join(sorted(str(k) for k in payload.on))
        return f"merge: on=({on})"
    if isinstance(payload, FilteringJoinPayload):
        lhs = ", ".join(sorted(str(k) for k in payload.lhs_keys))
        rhs = ", ".join(sorted(str(k) for k in payload.rhs_keys))
        return f"filtering_join {payload.mode.name}: lhs=({lhs}) rhs=({rhs})"
    if isinstance(payload, BroadcastPayload):
        return f"broadcast: column={payload.column.name}"
    raise OSIError(
        ErrorCode.E_INTERNAL_INVARIANT,
        f"_payload_summary has no case for "
        f"{type(payload).__name__} — every PlanPayload variant must "
        "have an entry",
        context={"payload_type": type(payload).__name__},
    )


def _render_enrich_pairs(
    parent_keys: tuple[Identifier, ...],
    child_keys: tuple[Identifier, ...],
    keys: DimensionSet,
) -> str:
    """Render the join-key pairing for an enrich step.

    When the parent / child sides use the same column name the algebra
    only carries one ``keys`` set; otherwise the split-out ``parent_keys``
    / ``child_keys`` sequences let us print the actual mapping.
    """
    if parent_keys and child_keys:
        return ", ".join(
            f"{p}={c}" for p, c in zip(parent_keys, child_keys, strict=True)
        )
    return ", ".join(sorted(str(k) for k in keys))


def _render_column_list(columns: tuple[Column, ...]) -> str:
    return ", ".join(str(c.name) for c in columns)


def _step_to_json(step: PlanStep) -> dict[str, Any]:
    return {
        "alias": _alias(step.step_id),
        "operation": step.operation.value,
        "inputs": [_alias(i) for i in step.inputs],
        "grain": sorted(str(g) for g in step.state.grain),
        "columns": [str(c.name) for c in step.state.columns],
        "summary": _payload_summary(step.payload),
    }


__all__ = ["explain", "explain_json"]
