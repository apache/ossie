"""Immutable value types that flow through the closed algebra.

See ``../../../../docs/JOIN_ALGEBRA.md §1`` for
the normative contract. Nothing in this file imports from
``osi.parsing`` or ``osi.codegen``; those layers see algebra values but
never construct them directly. Construction happens only through
:func:`osi.planning.algebra.operations.source` (and its downstream
operator chain).

Invariants (see ``ARCHITECTURE.md §6``):

* **I-1** ``grain ⊆ {c.name for c in columns if c.kind is DIMENSION}``
* **I-2** column names in ``columns`` are unique
* **I-5** ``grain == frozenset()`` implies scalar (one row)
* **I-6** ``column.dependencies ⊆ {other.name for other in columns}``
* **I-8** ``provenance`` grows only through operators that serve a
  requested expression
* **I-9** every set in ``unique_keys`` is non-empty and a subset of
  the dimension column names; ``unique_keys`` are *alternative*
  minimum keys at the current grain (the grain itself is always a
  key — see :meth:`CalculationState.is_unique_on`)

Violations are always raised as :class:`AlgebraError` (``E4xxx``) from
the operator that produced the state, never silently tolerated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum, auto
from typing import TYPE_CHECKING

from osi.common.identifiers import Identifier
from osi.common.sql_expr import FrozenSQL
from osi.common.types import DimensionSet, ExpressionId
from osi.errors import AlgebraError, ErrorCode

if TYPE_CHECKING:
    # Only used for type hints to avoid runtime dependency cycles.
    pass


class ColumnKind(StrEnum):
    """Classification of a column inside a :class:`CalculationState`.

    Drives the algebra's safety checks (``aggregate`` may only introduce
    ``AGGREGATE`` columns, ``filter`` may not read one, etc.).

    .. note::

       The Foundation deliberately does **not** distinguish a
       ``TIME_DIMENSION`` from a plain :attr:`DIMENSION`. The
       parser-level :class:`~osi.parsing.models.FieldKind` does (see
       ``parsing/models.py``), but the *algebra* only needs to know
       whether a column groups (``DIMENSION``), aggregates
       (``AGGREGATE``), or carries a per-row value (``FACT``). Time
       semantics — period comparisons, rolling windows, snapshot
       grains — are deferred features (``specs/deferred/``) and have
       no algebra-level consequences in this slice. When that
       changes, add ``TIME_DIMENSION`` here *and* update every
       branch on :class:`ColumnKind` to handle it explicitly; do not
       silently let it pattern-match as ``DIMENSION``.
    """

    DIMENSION = "dimension"
    FACT = "fact"
    AGGREGATE = "aggregate"


class Decomposability(StrEnum):
    """Decomposability class for aggregation functions (Han 2001).

    ``DISTRIBUTIVE`` aggregates (``SUM``/``COUNT``/``MIN``/``MAX``) can be
    re-aggregated losslessly. ``ALGEBRAIC`` aggregates
    (``AVG``, ``STDDEV``) can be re-aggregated via auxiliary state.
    ``HOLISTIC`` aggregates (``COUNT DISTINCT``, ``MEDIAN``) must run at
    the final grain. This attribute guards the Foundation's fan-out
    safety proofs (§5 of the algebra spec).
    """

    DISTRIBUTIVE = "distributive"
    ALGEBRAIC = "algebraic"
    HOLISTIC = "holistic"


class AggregateFunction(StrEnum):
    """Foundation aggregation functions.

    Intentionally small. Adding a function means answering "what is its
    decomposability class?" and "how does it behave under re-aggregation?"
    """

    SUM = auto()
    COUNT = auto()
    COUNT_DISTINCT = auto()
    MIN = auto()
    MAX = auto()
    AVG = auto()

    @property
    def decomposability(self) -> Decomposability:
        """Static classification used by fan-out safety (see §5.1)."""
        if self is AggregateFunction.COUNT_DISTINCT:
            return Decomposability.HOLISTIC
        if self is AggregateFunction.AVG:
            return Decomposability.ALGEBRAIC
        return Decomposability.DISTRIBUTIVE


@dataclass(frozen=True, slots=True)
class AggregateInfo:
    """Static shape of an aggregation.

    Carried by :class:`Column` when ``kind == AGGREGATE``. The frozen
    SQL expression captures the *argument* to the aggregation
    (``SUM(<expr>)`` etc.); ``function`` identifies which reduction.
    """

    function: AggregateFunction
    argument: FrozenSQL


@dataclass(frozen=True, slots=True)
class Column:
    """An addressable, fully-qualified output column of a state.

    Every field is immutable. ``dependencies`` records the other column
    names this column's expression reads — checked by operators that
    look at column-level dataflow (I-6).
    """

    name: Identifier
    expression: FrozenSQL
    dependencies: frozenset[Identifier]
    kind: ColumnKind
    aggregate: AggregateInfo | None = None
    is_single_valued: bool = False
    from_join_rhs: bool = False

    def __post_init__(self) -> None:
        """Enforce the (kind, aggregate) contract at construction time."""
        if self.kind is ColumnKind.AGGREGATE and self.aggregate is None:
            raise AlgebraError(
                ErrorCode.E4001_EXPLOSION_UNSAFE,
                f"AGGREGATE column {self.name!r} requires aggregate info",
                context={"column": self.name},
            )
        if self.kind is not ColumnKind.AGGREGATE and self.aggregate is not None:
            raise AlgebraError(
                ErrorCode.E4001_EXPLOSION_UNSAFE,
                f"non-aggregate column {self.name!r} has aggregate info",
                context={"column": self.name, "kind": self.kind},
            )


@dataclass(frozen=True, slots=True)
class CalculationState:
    """The single value flowing through the algebra.

    Constructed only by :mod:`osi.planning.algebra.operations` — never
    directly. The algebra package exports the operators, not this
    constructor; callers who import ``CalculationState`` are expected to
    use it for type annotations and structural equality.

    See the module docstring for the full invariant list.
    """

    grain: DimensionSet
    columns: tuple[Column, ...]
    provenance: frozenset[ExpressionId] = field(default_factory=frozenset)
    unique_keys: frozenset[DimensionSet] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate invariants I-1, I-2, I-6, and I-9 eagerly."""
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            seen: set[Identifier] = set()
            dup: Identifier | None = None
            for n in names:
                if n in seen:
                    dup = n
                    break
                seen.add(n)
            raise AlgebraError(
                ErrorCode.E3005_COLUMN_NAME_COLLISION,
                f"duplicate column name {dup!r}",
                context={"column": dup, "columns": names},
            )
        dimension_names = {
            c.name for c in self.columns if c.kind is ColumnKind.DIMENSION
        }
        missing_grain = self.grain - dimension_names
        if missing_grain:
            raise AlgebraError(
                ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
                f"grain references non-dimension columns: {sorted(missing_grain)}",
                context={"grain": sorted(self.grain), "missing": sorted(missing_grain)},
            )
        all_names = set(names)
        for col in self.columns:
            unknown = col.dependencies - all_names
            if unknown:
                raise AlgebraError(
                    ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
                    f"column {col.name!r} depends on unknown columns: "
                    f"{sorted(unknown)}",
                    context={"column": col.name, "missing": sorted(unknown)},
                )
        for uk in self.unique_keys:
            if not uk:
                raise AlgebraError(
                    ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
                    "unique_keys may not contain an empty key set",
                    context={"unique_keys": sorted(map(sorted, self.unique_keys))},
                )
            missing_uk = uk - dimension_names
            if missing_uk:
                raise AlgebraError(
                    ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
                    f"unique_key references non-dimension columns: "
                    f"{sorted(missing_uk)}",
                    context={
                        "unique_key": sorted(uk),
                        "missing": sorted(missing_uk),
                    },
                )

    @property
    def is_scalar(self) -> bool:
        """Return whether this state has scalar grain (exactly one row)."""
        return len(self.grain) == 0

    @property
    def column_names(self) -> frozenset[Identifier]:
        """Set of column names in this state, cached per access."""
        return frozenset(c.name for c in self.columns)

    def column(self, name: Identifier) -> Column:
        """Return the column with ``name`` or raise :class:`AlgebraError`."""
        for c in self.columns:
            if c.name == name:
                return c
        raise AlgebraError(
            ErrorCode.E3006_MISSING_COLUMN_DEPENDENCY,
            f"no column named {name!r}",
            context={"column": name, "available": sorted(self.column_names)},
        )

    def is_unique_on(self, keys: DimensionSet) -> bool:
        """Return whether ``keys`` functionally determines a single row.

        The state is unique on ``keys`` when ``keys`` is a superset of
        any declared key:

        1. the **grain** itself (always a key by I-1 / I-5), or
        2. any member of :attr:`unique_keys` (alternative minimum keys
           at this grain — see I-9).

        Used by :func:`osi.planning.algebra.enrich` to discharge the
        fan-trap rule and by future operators that need to prove a
        join-key set covers a key. The check is *subset*: a strict
        superset of a key is still a key, so wider join-key sets stay
        safe.
        """
        if self.grain.issubset(keys):
            return True
        return any(uk.issubset(keys) for uk in self.unique_keys)


__all__ = [
    "AggregateFunction",
    "AggregateInfo",
    "CalculationState",
    "Column",
    "ColumnKind",
    "Decomposability",
]
