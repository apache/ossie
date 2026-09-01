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

"""Identifier derivation and column-reference rewriting — rules ID1-ID4.

ThoughtSpot has one `name` per column, serving as display name, search token and
cross-document key at once (gap G2). Ossie splits identifier from label, so the
identifier has to be derived — and derivation collides.
"""
import re

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_COLUMN_REF = re.compile(r"^\[(?P<table>[^\]:]+)::(?P<column>[^\]]+)\]$")


def normalise(display_name: str) -> str:
    """Fold a ThoughtSpot display name to an Ossie identifier (rule ID1)."""
    folded = _NON_ALNUM.sub("_", display_name.strip().lower()).strip("_")
    if not folded:
        raise ValueError(f"{display_name!r} normalises to an empty identifier")
    if folded[0].isdigit():
        # A leading digit is not a valid identifier in most consumers' grammars.
        folded = f"n_{folded}"
    return folded


class Allocator:
    """Allocates unique identifiers, resolving collisions with a numeric suffix.

    Collision detection folds case, because Ossie resolves regular identifiers
    case-insensitively (`core-spec/expression_language.md:77`) even though
    `validation/validate.py` only rejects exact-string duplicates. Detecting on
    the exact string would emit a document that validates and is still ambiguous.
    """

    def __init__(self) -> None:
        self._taken: set[str] = set()

    def allocate(self, display_name: str) -> str:
        base = normalise(display_name)
        candidate, suffix = base, 1
        while candidate.casefold() in self._taken:
            suffix += 1
            candidate = f"{base}_{suffix}"
        self._taken.add(candidate.casefold())
        return candidate


def split_column_ref(ref: str) -> tuple[str, str]:
    """`[TABLE::Column]` -> `("TABLE", "Column")` (rule ID3)."""
    match = _COLUMN_REF.match(ref.strip())
    if match is None:
        raise ValueError(f"{ref!r} is not a ThoughtSpot column reference")
    return match.group("table"), match.group("column")


def format_column_ref(table: str, column: str) -> str:
    """`("TABLE", "Column")` -> `[TABLE::Column]` (rule ID3)."""
    return f"[{table}::{column}]"
