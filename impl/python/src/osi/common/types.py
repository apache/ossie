"""Cross-layer ``NewType`` aliases and small frozen value objects.

Keeping these in ``osi.common`` avoids circular imports between the three
compiler layers and lets ``import-linter`` enforce the one-way flow (see
``ARCHITECTURE.md §1.1``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType

from osi.common.identifiers import Identifier

CTEName = NewType("CTEName", str)
ExpressionId = NewType("ExpressionId", str)

DimensionSet = frozenset[Identifier]
"""Convenience alias used by the algebra for grain sets.

Kept as a structural alias — not a :class:`typing.NewType` — because
the algebra constructs grain sets by ordinary :class:`frozenset`
operations (union, intersection, set comprehensions) that ``NewType``
would force every call site to wrap. The discipline we actually rely
on is ``DimensionSet`` only ever holding :class:`Identifier` strings,
which the static type already guarantees.
"""


class Dialect(StrEnum):
    """SQL dialects the Foundation supports end-to-end.

    The string values are the canonical lower-case form used by the
    CLI (``--dialect duckdb``) and SQLGlot (``Expression.sql(dialect=...)``).
    YAML inputs may use the SPEC's upper-case spelling
    (``ANSI_SQL``, ``DUCKDB``, ``SNOWFLAKE``) — the parsing layer
    normalises those to this enum.

    This enum is the single source of truth for the dialect vocabulary
    across all three compiler layers.
    """

    # OSI_SQL_2026 is the Foundation v0.1 default expression language.
    # Models that don't pin a dialect are parsed and emitted as
    # OSI_SQL_2026; engine dialects below are downstream lowerings that
    # the codegen produces on demand.
    OSI_SQL_2026 = "osi_sql_2026"
    ANSI = "ansi"
    DUCKDB = "duckdb"
    SNOWFLAKE = "snowflake"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """1-indexed (line, column) pointer into a YAML or SQL source file.

    Used by parser errors and diagnostics. Never by the algebra or codegen.
    """

    file: str
    line: int
    column: int


__all__ = [
    "CTEName",
    "Dialect",
    "DimensionSet",
    "ExpressionId",
    "Identifier",
    "SourceLocation",
]
