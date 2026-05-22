"""The :class:`SemanticQuery` value type — the planner's input.

A semantic query is a structured, dialect-agnostic request over a
:class:`~osi.parsing.models.SemanticModel`. Its shape mirrors
``Proposed_OSI_Semantics.md §5.1``: dimensions, measures, a pre-
aggregation ``where`` predicate, a post-aggregation ``having`` predicate,
``order_by``, ``limit``, and bind parameters.

References within the query are always fully qualified strings
(``dataset.field``). The planner resolves those into
:class:`~osi.parsing.namespace.Namespace` lookups; anything the parser
module doesn't know how to index is a hard error (``E2002``).

The Foundation does **not** expose: fixed-grain metric overrides,
per-metric filter context, grain modifiers on a query, window functions,
grouping-set / pivot operators, or metric reset. Attempting to construct
a query with those shapes raises ``E_DEFERRED_KEY_REJECTED`` at parse
time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Optional

from osi.common.identifiers import Identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import ErrorCode, OSIParseError

_EMPTY_PARAMETERS: Mapping[Identifier, object] = MappingProxyType({})


class SortDirection(StrEnum):
    """Sort direction for :class:`OrderBy` entries."""

    ASC = "ASC"
    DESC = "DESC"


@dataclass(frozen=True, slots=True)
class Reference:
    """A ``dataset.field`` or bare-metric reference used by a query."""

    dataset: Optional[Identifier]
    name: Identifier

    @property
    def is_qualified(self) -> bool:
        """Return whether this reference names a dataset."""
        return self.dataset is not None

    def __str__(self) -> str:
        """Render as ``dataset.name`` (or bare ``name`` when unqualified)."""
        if self.dataset is None:
            return str(self.name)
        return f"{self.dataset}.{self.name}"


@dataclass(frozen=True, slots=True)
class OrderBy:
    """One entry in the query's ``order_by`` clause."""

    target: Reference
    direction: SortDirection = SortDirection.ASC


@dataclass(frozen=True, slots=True)
class SemanticQuery:
    """Structured semantic query over a model.

    Immutable; the planner never mutates a query.

    Foundation v0.1 (D-010 / D-011) recognises two query shapes:

    1. **Aggregation query** — ``dimensions`` and/or ``measures``;
       result cardinality is ``DISTINCT(dimensions)`` (§5.1.1).
    2. **Scalar query** — ``fields`` only; result cardinality is the
       home-grain row set (§5.1.2).

    Mixing the two ⇒ :data:`ErrorCode.E_MIXED_QUERY_SHAPE`. An empty
    aggregation query ⇒ :data:`ErrorCode.E_EMPTY_AGGREGATION_QUERY`;
    an empty scalar query ⇒ :data:`ErrorCode.E_EMPTY_SCALAR_QUERY`.
    The full scalar-query planner branch lands in S-2 + S-7; this
    class is the parse-time gate.
    """

    dimensions: tuple[Reference, ...] = ()
    measures: tuple[Reference, ...] = ()
    fields: tuple[Reference, ...] = ()
    where: Optional[FrozenSQL] = None
    having: Optional[FrozenSQL] = None
    order_by: tuple[OrderBy, ...] = ()
    limit: Optional[int] = None
    parameters: Mapping[Identifier, object] = field(
        default_factory=lambda: _EMPTY_PARAMETERS
    )

    def __post_init__(self) -> None:
        """Enforce §5.1 query-shape rules + freeze parameters."""
        is_aggregation = bool(self.dimensions or self.measures)
        is_scalar = bool(self.fields)
        if is_aggregation and is_scalar:
            raise OSIParseError(
                ErrorCode.E_MIXED_QUERY_SHAPE,
                (
                    "semantic query mixes aggregation shape "
                    "(dimensions/measures) with scalar shape (fields); "
                    "see Proposed_OSI_Semantics.md D-010"
                ),
            )
        if not is_aggregation and not is_scalar:
            # Distinguish the two empty cases — D-010 / D-011 each
            # specify their own error code so callers can tell which
            # shape the user *intended*. Without any signal, default
            # to E_EMPTY_AGGREGATION_QUERY because the aggregation
            # shape is the historical default.
            raise OSIParseError(
                ErrorCode.E_EMPTY_AGGREGATION_QUERY,
                (
                    "semantic query must declare dimensions, measures, "
                    "or fields; see Proposed_OSI_Semantics.md D-010"
                ),
            )
        if self.limit is not None and self.limit < 0:
            raise OSIParseError(
                ErrorCode.E1004_TYPE_MISMATCH,
                "limit must be non-negative",
                context={"limit": self.limit},
            )
        if not isinstance(self.parameters, MappingProxyType):
            object.__setattr__(
                self,
                "parameters",
                MappingProxyType(dict(self.parameters)),
            )

    @property
    def is_aggregation(self) -> bool:
        """True when this query has the aggregation shape (D-010)."""
        return bool(self.dimensions or self.measures)

    @property
    def is_scalar(self) -> bool:
        """True when this query has the scalar shape (D-011)."""
        return bool(self.fields)


__all__ = [
    "OrderBy",
    "Reference",
    "SemanticQuery",
    "SortDirection",
]
