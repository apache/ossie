"""Frozen :class:`PlannerContext` — the planner's read-only inputs.

Bundles the parsed model, namespace, relationship graph, and the
``FoundationFlags`` that were used to admit the model. The planner
holds it by reference and never rebuilds it; query planning over the
same model is pure over this bundle.

The ``flags`` field carries the *exact* :class:`FoundationFlags`
instance that ``parse_semantic_model`` was called with, so query-time
gates (e.g. semi-join admission in :mod:`osi.planning.classify`) can
honour the same opt-ins the model itself was admitted under. The
default is :meth:`FoundationFlags.strict`, matching the published
Foundation defaults — every flag off.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from osi.config import FoundationFlags
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
    flags: FoundationFlags = field(default_factory=FoundationFlags)


__all__ = ["PlannerContext"]
