"""Identifier primitives shared by every compiler layer.

An ``Identifier`` is a normalized name used for datasets, fields, columns,
CTEs, and synthetic names. Normalization (lower-casing) and validation
(shape) are centralized here to enforce **invariant 11** from
``ARCHITECTURE.md``: raw ``==`` on identifier strings is a bug.
"""

from __future__ import annotations

import re
from typing import NewType

from osi.errors import ErrorCode, OSIError

Identifier = NewType("Identifier", str)

# Foundation identifier shape: ASCII letter or underscore, followed by any
# run of letters / digits / underscores. Matches SPEC.md §3.1.
_IDENTIFIER_RE = re.compile(r"\A[A-Za-z_][A-Za-z0-9_]*\Z")

# Reserved identifiers we refuse to accept even though SQL might.
# (Stay conservative: these conflict with pieces of the algebra contract.)
_RESERVED: frozenset[str] = frozenset(
    {
        "__grain__",
        "__provenance__",
        "__all__",
    }
)


def is_valid_identifier(raw: str) -> bool:
    """Return whether ``raw`` is a syntactically valid Foundation identifier."""
    return bool(_IDENTIFIER_RE.match(raw))


def normalize_identifier(raw: str) -> Identifier:
    """Normalize and validate an identifier.

    Normalization is case-folding. Two identifiers that differ only in
    case are the same identifier — this matches standard SQL semantics for
    unquoted identifiers.

    Raises
    ------
    OSIError
        ``E1005_IDENTIFIER_INVALID`` if ``raw`` is empty, has the wrong
        shape, or is reserved.
    """
    if not isinstance(raw, str):
        raise OSIError(
            ErrorCode.E1005_IDENTIFIER_INVALID,
            f"identifier must be a string, got {type(raw).__name__}",
            context={"value": repr(raw)},
        )
    if not raw:
        raise OSIError(
            ErrorCode.E1005_IDENTIFIER_INVALID,
            "identifier is empty",
        )
    if not _IDENTIFIER_RE.match(raw):
        raise OSIError(
            ErrorCode.E1005_IDENTIFIER_INVALID,
            f"identifier {raw!r} has invalid shape; "
            "must match [A-Za-z_][A-Za-z0-9_]*",
            context={"value": raw},
        )
    normalized = raw.lower()
    if normalized in _RESERVED:
        raise OSIError(
            ErrorCode.E2008_RESERVED_IDENTIFIER,
            f"identifier {raw!r} is reserved",
            context={"value": raw},
        )
    return Identifier(normalized)


def identifiers_equal(a: str, b: str) -> bool:
    """Case-insensitive identifier equality without raising on invalid shape."""
    return a.lower() == b.lower()
