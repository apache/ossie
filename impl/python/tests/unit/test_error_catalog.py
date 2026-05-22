"""Invariants linking :mod:`osi.errors` to ``docs/ERROR_CODES.md``.

Two catalog invariants:

1. The set of code names in the docs table equals the set in the enum
   (no orphan docs, no orphan enum values).
2. Every code annotated ``RESERVED`` in the docs has a matching
   ``# RESERVED`` comment on the enum member, and vice versa. This
   keeps the two sources of truth from drifting.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCS_PATH = Path(__file__).resolve().parents[2] / "docs" / "ERROR_CODES.md"
_SRC_PATH = Path(__file__).resolve().parents[2] / "src" / "osi" / "errors.py"


def _codes_in_docs() -> dict[str, bool]:
    """Return ``{code: is_reserved}`` for every row in the catalog tables."""
    text = _DOCS_PATH.read_text(encoding="utf-8")
    out: dict[str, bool] = {}
    # Each catalog row begins with ``| `E...` |`` and may declare a
    # status column. We accept both legacy rows (no status column) and
    # the current shape (status column immediately after the code).
    # Codes are either the legacy numeric form (``E1234``/``W6001``)
    # or the Foundation v0.1 named form (``E_NAMED_LIKE_THIS``).
    row_pattern = re.compile(
        r"^\|\s*`(?P<code>[EW]\d{4}|E_[A-Z0-9_]+)`\s*\|\s*(?P<rest>.+)\|",
        re.MULTILINE,
    )
    for match in row_pattern.finditer(text):
        code = match.group("code")
        rest = match.group("rest")
        first_cell = rest.split("|", 1)[0].strip()
        out[code] = first_cell.upper() == "RESERVED"
    return out


def _codes_in_enum() -> dict[str, bool]:
    """Return ``{code: annotated_reserved}`` for every enum member.

    ``RESERVED`` is recognised only when it appears in a Python comment
    (inline after the value or on a comment line immediately above it)
    — this avoids confusing a member's *name* (e.g.
    ``E_DEFERRED_KEY_REJECTED``) with the status annotation.
    """
    text = _SRC_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: dict[str, bool] = {}
    decl_re = re.compile(
        r"^\s*(?P<name>(?:[EW]\d{4}_[A-Z0-9_]+|E_[A-Z0-9_]+))"
        r"\s*=\s*\"(?P<value>(?:[EW]\d{4}|E_[A-Z0-9_]+))\""
        r"(?P<inline>.*)$"
    )
    for i, line in enumerate(lines):
        match = decl_re.match(line)
        if match is None:
            continue
        value = match.group("value")
        inline = match.group("inline")
        inline_has_reserved = "#" in inline and "RESERVED" in inline.split("#", 1)[1]
        # Walk the comment block directly above the declaration.
        preceding_has_reserved = False
        j = i - 1
        while j >= 0 and lines[j].lstrip().startswith("#"):
            if "RESERVED" in lines[j]:
                preceding_has_reserved = True
                break
            j -= 1
        out[value] = inline_has_reserved or preceding_has_reserved
    return out


def test_docs_and_enum_codes_are_identical_sets() -> None:
    docs = _codes_in_docs()
    enum_codes = _codes_in_enum()
    assert set(docs) == set(enum_codes), (
        f"docs-only: {sorted(set(docs) - set(enum_codes))}; "
        f"enum-only: {sorted(set(enum_codes) - set(docs))}"
    )


@pytest.mark.parametrize("code", sorted(_codes_in_enum()))
def test_reserved_annotation_matches_between_docs_and_enum(code: str) -> None:
    docs_reserved = _codes_in_docs()[code]
    enum_reserved = _codes_in_enum()[code]
    assert docs_reserved == enum_reserved, (
        f"{code}: docs RESERVED={docs_reserved}, enum RESERVED={enum_reserved} "
        "— keep the two sources of truth in sync."
    )
