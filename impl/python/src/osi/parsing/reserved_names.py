"""OSI-grammar reserved names (D-019).

A small, deliberately minimal policy module. The Foundation reserves
the names ``GRAIN``, ``FILTER``, ``QUERY_FILTER`` so they can be used
as OSI grammar keywords without colliding with user identifiers.

This is a separate concern from:

* :mod:`osi.common.identifiers` ``_RESERVED`` — internal sentinels
  (``__grain__`` etc.) that the algebra layer reserves; those raise
  ``E_RESERVED_IDENTIFIER`` at identifier-construction time.
* SQL keywords like ``SELECT`` — those are the responsibility of
  the dialect / OSI_SQL_2026 catalog (D-021), not of D-019.

D-019 is checked at parse time over the built ``SemanticModel``
(every dataset, field, metric, and relationship name) so a model
that uses any of these as a user identifier is rejected with
``E_RESERVED_NAME``.
"""

from __future__ import annotations

from typing import Final

OSI_RESERVED_NAMES: Final[frozenset[str]] = frozenset(
    {
        "grain",
        "filter",
        "query_filter",
    }
)


def is_osi_reserved_name(name: str) -> bool:
    """Return whether ``name`` (case-insensitively) is OSI-reserved."""
    return name.lower() in OSI_RESERVED_NAMES


__all__ = ["OSI_RESERVED_NAMES", "is_osi_reserved_name"]
