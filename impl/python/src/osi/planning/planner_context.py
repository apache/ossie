"""Frozen :class:`PlannerContext` — the planner's read-only inputs.

Bundles the parsed model, namespace, and relationship graph so the
planner can pass a single handle through its internal functions. Also
exposes cached derived facts (dimension roles, aggregate classifications)
that every stage of the planner needs.
"""

from __future__ import annotations

from dataclasses import dataclass

from osi.parsing.graph import RelationshipGraph
from osi.parsing.models import SemanticModel
from osi.parsing.namespace import Namespace


@dataclass(frozen=True, slots=True)
class PlannerContext:
    """Read-only bundle of the parsed, validated model artefacts.

    The planner holds this by reference and never rebuilds it. Query
    planning over the same model is pure over this bundle.
    """

    model: SemanticModel
    namespace: Namespace
    graph: RelationshipGraph


__all__ = ["PlannerContext"]
