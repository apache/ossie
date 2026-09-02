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

**Known limitation — non-Latin scripts, not diacritics.** An
earlier revision of this module documented ASCII-only folding as a stated
boundary rather than fixing it, on the grounds that a transliteration policy
is a product decision. That reasoning holds for *transliteration* (e.g.
Japanese -> romaji) but not for Unicode canonical *decomposition*, which is
stdlib and needs no policy choice. `normalise` now applies NFKD decomposition
first (`unicodedata.normalize("NFKD", s)`), which separates a base letter from
its combining diacritical marks, then drops non-ASCII before the existing
lowercase-and-substitute folding. A character with no ASCII decomposition
under NFKD (Cyrillic, CJK, and similarly non-Latin scripts) is still dropped,
not transliterated, exactly as before — and a name with no ASCII
alphanumerics surviving still raises `ValueError`. The residual limitation is
narrower than before: `"Café"` -> `"cafe"`, `"Ürün"` -> `"urun"`, and
`"Zürich"` -> `"zurich"` now fold correctly, while a CJK-only name (e.g.
`"北京市"`) still raises. There is also a real open question NFKD does not
settle: some accented Latin folds to a *conventional* ASCII expansion rather
than the bare decomposed letter — German `"Müller"` decomposes to `"Muller"`
here, not the conventional `"Mueller"` — and choosing between them is still a
product decision left to a later change.
"""
import re
import unicodedata

_NON_ALNUM = re.compile(r"[^0-9a-z]+")
_COLUMN_REF = re.compile(r"^\[(?P<table>[^\]]+?)::(?P<column>[^\]]+)\]$")


def normalise(display_name: str) -> str:
    """Fold a ThoughtSpot display name to an Ossie identifier (rule ID1).

    Diacritics are folded via NFKD decomposition before the ASCII
    lowercase-and-substitute step — see the module docstring's "Known
    limitation" note. A character with no ASCII decomposition (non-Latin
    scripts) is dropped, not transliterated; a name with no ASCII
    alphanumerics surviving still raises.
    """
    ascii_form = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode("ascii")
    folded = _NON_ALNUM.sub("_", ascii_form.strip().lower()).strip("_")
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
    """`[TABLE::Column]` -> `("TABLE", "Column")` (rule ID3).

    Raises if `ref` doesn't match the `[TABLE::Column]` shape at all, and also
    if it is *ambiguous* — rather than silently taking the first delimiter and
    mis-splitting a table or column name that itself contains `::` (e.g. one
    produced by `format_column_ref("A::B", "C")`). Two distinct ambiguity
    shapes are checked: more than one non-overlapping `::` delimiter in the
    whole reference (`str.count` is non-overlapping, which correctly catches
    two separated delimiters), and a captured column that itself starts with
    `:` — the signature of a *run* of three or more consecutive colons,
    which `str.count("::") > 1` cannot see because the run has only one
    non-overlapping match. `format_column_ref("ORDERS:", "Col")` produces
    `"[ORDERS:::Col]"`, which is exactly as ambiguous as
    `format_column_ref("ORDERS", ":Col")` (same string, same encoding
    collision) and must not silently mis-split to `("ORDERS", ":Col")`.
    Whether the right fix is an escaping scheme or a different delimiter is a
    real design question against live ThoughtSpot display names, left to a
    later change; loud failure is the correct interim behaviour.

    A table or column name containing a single `:` round-trips correctly —
    `split_column_ref(format_column_ref("A:B", "x")) == ("A:B", "x")`. The
    table group matches lazily up to the *first* `::`, not a character class
    that excludes colons outright; only a `::` occurring inside either part
    is the genuinely ambiguous case the checks above catch.
    """
    stripped = ref.strip()
    match = _COLUMN_REF.match(stripped)
    if match is None:
        raise ValueError(f"{ref!r} is not a ThoughtSpot column reference")
    column = match.group("column")
    if stripped.count("::") > 1 or column.startswith(":"):
        raise ValueError(
            f"{ref!r} is an ambiguous ThoughtSpot column reference: "
            "contains more than one '::' delimiter"
        )
    return match.group("table"), column


def format_column_ref(table: str, column: str) -> str:
    """`("TABLE", "Column")` -> `[TABLE::Column]` (rule ID3)."""
    return f"[{table}::{column}]"
