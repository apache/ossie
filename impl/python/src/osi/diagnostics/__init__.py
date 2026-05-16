"""Read-only projection of model + plan into human-readable output.

Entry points:

- :func:`describe` — render a :class:`~osi.parsing.models.SemanticModel`
  as a grouped, table-like summary.
- :func:`explain` — render a :class:`~osi.planning.plan.QueryPlan` as a
  per-step grain / column trace.
- :func:`resolve` — for a given :class:`~osi.planning.SemanticQuery` +
  :class:`PlannerContext`, show which datasets, relationships, and
  fields will be touched.

All three return *text*; the ``*_json`` variants return JSON-safe
``dict`` / ``list`` structures for programmatic consumption (CLI,
tests, tooling). Neither surface mutates its inputs, and neither
reaches outside the already-parsed / planned inputs — in particular,
nothing here touches the physical data.
"""

from __future__ import annotations

from .describe import describe, describe_json
from .explain import explain, explain_json
from .resolve import resolve, resolve_json

__all__ = [
    "describe",
    "describe_json",
    "explain",
    "explain_json",
    "resolve",
    "resolve_json",
]
