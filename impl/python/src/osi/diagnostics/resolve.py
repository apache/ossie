"""Static resolution view over a :class:`SemanticQuery`.

Given a query and a :class:`~osi.planning.planner_context.PlannerContext`,
report which datasets, fields, metrics, and relationships will be
touched — *without* running the planner. This is the surface users
reach for when diagnosing "why is my query hitting that table?" or
"which relationship path is picked?". It deliberately shadows the real
planner just enough to describe the inputs.

For the Foundation:

- Reference resolution uses the same :mod:`osi.planning.resolve` path
  the planner uses.
- Join-path discovery uses :mod:`osi.planning.joins` (the same resolver
  the planner consumes), so a diagnostics drift from the planner is a
  regression.
"""

from __future__ import annotations

from typing import Any

from osi.common.identifiers import Identifier
from osi.errors import ErrorCode, OSIError
from osi.planning.joins import find_enrichment_path
from osi.planning.planner_context import PlannerContext
from osi.planning.resolve import (
    ResolvedDimension,
    ResolvedFact,
    ResolvedMetric,
    ResolvedReference,
    resolve_reference,
)
from osi.planning.semantic_query import Reference, SemanticQuery


def resolve(query: SemanticQuery, context: PlannerContext) -> str:
    """Render the static-resolution view of ``query`` as text."""
    view = resolve_json(query, context)
    lines: list[str] = []
    lines.append("datasets:")
    for ds in view["datasets"]:
        lines.append(f"  - {ds}")
    if view["dimensions"]:
        lines.append("")
        lines.append("dimensions:")
        for d in view["dimensions"]:
            lines.append(
                f"  - {d['dataset']}.{d['name']}  " f"(expression: {d['expression']})"
            )
    if view["measures"]:
        lines.append("")
        lines.append("measures:")
        for m in view["measures"]:
            dataset = m["dataset"] or "<model-level>"
            lines.append(
                f"  - {dataset}.{m['name']}  " f"(expression: {m['expression']})"
            )
    if view["relationships"]:
        lines.append("")
        lines.append("relationships used:")
        for r in view["relationships"]:
            lines.append(
                f"  - {r['name']}  "
                f"{r['from_dataset']}({', '.join(r['from_columns'])}) → "
                f"{r['to_dataset']}({', '.join(r['to_columns'])})  "
                f"[{r['join_type']}]"
            )
    if view["filters"]:
        lines.append("")
        lines.append("filters:")
        for f in view["filters"]:
            lines.append(f"  - {f}")
    return "\n".join(lines)


def resolve_json(query: SemanticQuery, context: PlannerContext) -> dict[str, Any]:
    """Return a JSON-safe dict mirroring :func:`resolve`."""
    dims_used: list[dict[str, Any]] = []
    measures_used: list[dict[str, Any]] = []
    datasets: set[Identifier] = set()

    for ref in query.dimensions:
        resolved = resolve_reference(ref, context.namespace)
        entry = _reference_entry(ref, resolved)
        dims_used.append(entry)
        _collect_datasets(resolved, datasets)

    fact_datasets: list[Identifier] = []
    for ref in query.measures:
        resolved = resolve_reference(ref, context.namespace)
        entry = _reference_entry(ref, resolved)
        measures_used.append(entry)
        _collect_datasets(resolved, datasets)
        dataset_of_measure = _dataset_of(resolved)
        if dataset_of_measure is not None:
            fact_datasets.append(dataset_of_measure)

    dim_datasets: set[Identifier] = set()
    for r in query.dimensions:
        d = _dataset_of(resolve_reference(r, context.namespace))
        if d is not None:
            dim_datasets.add(d)

    relationships_used: list[dict[str, Any]] = []
    seen_rels: set[str] = set()
    for fact_ds in fact_datasets:
        targets = frozenset(d for d in dim_datasets if d != fact_ds)
        if not targets:
            continue
        try:
            path = find_enrichment_path(
                root=fact_ds, targets=targets, graph=context.graph
            )
        except OSIError:
            # An unresolvable join path is a *normal* outcome for a
            # diagnostics view — the user may be inspecting an
            # under-modelled query. We skip the unreachable target
            # rather than abort the report. Any other exception
            # type is a compiler bug and must propagate so the
            # property test "every failure carries a code" can
            # surface it.
            continue
        for step in path:
            name = str(step.edge.name)
            if name in seen_rels:
                continue
            seen_rels.add(name)
            relationships_used.append(
                {
                    "name": name,
                    "from_dataset": str(step.edge.from_dataset),
                    "to_dataset": str(step.edge.to_dataset),
                    "from_columns": [str(c) for c in step.edge.from_columns],
                    "to_columns": [str(c) for c in step.edge.to_columns],
                    "join_type": step.join_type.name,
                }
            )
            datasets.add(step.edge.from_dataset)
            datasets.add(step.edge.to_dataset)

    filters = []
    if query.where is not None:
        filters.append(query.where.canonical)

    return {
        "datasets": sorted(str(d) for d in datasets),
        "dimensions": dims_used,
        "measures": measures_used,
        "relationships": relationships_used,
        "filters": filters,
    }


def _reference_entry(ref: Reference, resolved: ResolvedReference) -> dict[str, Any]:
    if isinstance(resolved, ResolvedMetric):
        dataset = str(resolved.dataset) if resolved.dataset is not None else None
        return {
            "dataset": dataset,
            "name": str(resolved.metric.name),
            "expression": resolved.metric.expression.canonical,
            "kind": "metric",
        }
    if isinstance(resolved, (ResolvedDimension, ResolvedFact)):
        return {
            "dataset": str(resolved.dataset),
            "name": str(resolved.field.name),
            "expression": resolved.field.expression.canonical,
            "kind": resolved.field.role.value,
        }
    raise OSIError(  # pragma: no cover — exhaustive above
        ErrorCode.E_INTERNAL_INVARIANT,
        f"unknown resolved reference: {type(resolved).__name__} — "
        "every ResolvedReference subclass must have a case in "
        "_reference_entry",
        context={"resolved_type": type(resolved).__name__},
    )


def _dataset_of(resolved: ResolvedReference) -> Identifier | None:
    if isinstance(resolved, ResolvedMetric):
        return resolved.dataset
    return resolved.dataset


def _collect_datasets(resolved: ResolvedReference, into: set[Identifier]) -> None:
    ds = _dataset_of(resolved)
    if ds is not None:
        into.add(ds)


__all__ = ["resolve", "resolve_json"]
