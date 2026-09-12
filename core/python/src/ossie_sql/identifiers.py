# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Identifier normalization per the spec's "Namespacing and Identifier
Resolution" section.

Key rule from the spec's comparison table: **regular (unquoted) identifiers
normalize to upper-case**, while quoted identifiers are matched exactly
(quotes stripped, escapes unescaped, case preserved) --

    id    -> ID   (regular identifier, case-insensitive, normalizes upper)
    Id    -> ID   (same)
    "ID"  -> ID   (quoted, but happens to already be upper)
    "id"  -> id   (quoted: exact, case preserved -- does NOT match column ID)

Identifiers must be valid ANSI SQL names up to 128 characters.
"""

from __future__ import annotations

import re
from typing import Final

from sqlglot import exp

_MAX_IDENTIFIER_LENGTH: Final[int] = 128

# ANSI SQL regular (unquoted) identifier shape: a letter, followed by any run
# of letters/digits/underscores. The spec doesn't spell out a formal grammar
# beyond "follow ANSI SQL naming"; this matches the common, portable subset
# every major engine accepts unquoted.
_REGULAR_IDENTIFIER_RE = re.compile(r"\A[A-Za-z][A-Za-z0-9_]*\Z")


class InvalidIdentifierError(ValueError):
    """Raised when an identifier doesn't meet the spec's shape/length rules."""


def is_valid_identifier(raw: str, *, quoted: bool) -> bool:
    """Return whether ``raw`` is a syntactically valid Ossie identifier.

    ``raw`` is the identifier text with quotes already stripped (as
    SQLGlot's ``exp.Identifier.this`` provides). Quoted identifiers accept
    any non-empty text; regular (unquoted) identifiers must match ANSI
    regular-identifier shape. Both are capped at 128 characters.
    """
    if not raw or len(raw) > _MAX_IDENTIFIER_LENGTH:
        return False
    if quoted:
        return True
    return bool(_REGULAR_IDENTIFIER_RE.match(raw))


def normalize_identifier_text(raw: str, *, quoted: bool) -> str:
    """Return the spec-normalized form of identifier text.

    Regular identifiers case-fold to upper-case (matching the spec's
    normalization rule); quoted identifiers are returned unchanged.

    Raises
    ------
    InvalidIdentifierError
        If ``raw`` doesn't meet the shape/length rules.
    """
    if not is_valid_identifier(raw, quoted=quoted):
        raise InvalidIdentifierError(
            f"{raw!r} is not a valid Ossie identifier "
            f"(quoted={quoted}, max length {_MAX_IDENTIFIER_LENGTH})"
        )
    return raw if quoted else raw.upper()


def normalize_identifier(node: exp.Identifier) -> str:
    """Return the spec-normalized form of a SQLGlot ``exp.Identifier`` node."""
    return normalize_identifier_text(node.this, quoted=bool(node.args.get("quoted")))


def identifiers_equal(a: exp.Identifier, b: exp.Identifier) -> bool:
    """Return whether two identifiers refer to the same normalized name."""
    return normalize_identifier(a) == normalize_identifier(b)


__all__ = [
    "InvalidIdentifierError",
    "is_valid_identifier",
    "normalize_identifier_text",
    "normalize_identifier",
    "identifiers_equal",
]
