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

"""Guard: nothing shipped may cite a document a repository-only reader cannot see.

This package was developed against internal design material that is not part of
this repository and will not be. Two shapes of unresolvable reference leak in as a
result, and this module checks for both every time the suite runs, so a new one
introduced by future work is caught immediately rather than found by the next
person who happens to grep for it by hand.

**Identifier-shaped citations** — a short run of uppercase letters immediately
followed by digits, with an optional hyphen between the two, the shape a rule id
or a backlog-style item number is written in — are handled fail-closed: every
token of that shape actually present in a shipped file is collected, a curated
ALLOWED_TOKENS set of genuinely unrelated technical tokens (data types, encodings,
lint codes, ...) is subtracted, and a second, explicitly provisional set of known
mapping-document rule identifiers is subtracted, and *anything left over fails
the suite*. A blocklist can only catch an id someone already thought to list;
this can't be evaded that way, because the burden is on a new token to justify
itself, not on this file to have predicted it.

**Ordinary-English process language** — internal task-tracking, multi-option
planning, and change-review vocabulary that reads as a normal sentence and so
has no identifier shape a scanner can key off — stays a hand-curated,
case-insensitive phrase blocklist for that reason. It trades recall for
precision in the other direction: it can miss a new phrasing, but it will not
fail to explain a hit.

Both halves run over the *actual shipped surface* — every ``.py`` file under
``src/`` and ``tests/`` (this file included — a guard that exempts itself is not
a guard), ``README.md``, ``pyproject.toml``, and this package's own CI workflow
file if one is ever added directly under the package directory.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]


def _shipped_files() -> list[Path]:
    """Every file this test module treats as "shipped" — see the module docstring."""
    patterns = (
        "src/**/*.py",
        "tests/**/*.py",
        "README.md",
        "pyproject.toml",
        # Only matches if this package ever grows its own workflow file directly
        # under the package directory, per the module docstring; today it does not,
        # and the repository's top-level CI is out of this package's scope.
        "*.yml",
        "*.yaml",
    )
    seen: set[Path] = set()
    files: list[Path] = []
    for pattern in patterns:
        for path in sorted(PACKAGE_ROOT.glob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                files.append(path)
    return files


# ---------------------------------------------------------------------------
# Half 1 — identifier-shaped tokens. Fail-closed: ALLOWED_TOKENS below is the
# complete list of tokens of this shape that are *not* a citation to unshipped
# material. Anything of this shape found in a shipped file and not in one of
# the two sets below (this one, or the provisional one further down) fails.
# ---------------------------------------------------------------------------

#: An uppercase letter run (1-6 chars) followed by 1-4 digits, with an optional
#: hyphen between them — a short in-house rule code and a longer, hyphenated
#: backlog-style item number are both this same shape. Letters capped at 6 and
#: digits at 4 deliberately excludes the ASF licence header's own repeated
#: "LICENSE-2.0" URL fragment (a 7-letter run cannot start a match under this
#: cap) without needing a special-case exclusion for it.
TOKEN_SHAPE_RE = re.compile(r"\b[A-Z]{1,6}-?[0-9]{1,4}\b")

#: Tokens of the id shape above that are genuinely unrelated technical terms —
#: not a citation to anything, mapping-document or otherwise. Each entry is
#: justified individually; an entry that is actually a rule id belongs in the
#: provisional set below instead, not here.
ALLOWED_TOKENS: frozenset[str] = frozenset(
    {
        # ANSI/SQL function and format names emitted into translated expressions —
        # real function and format-token spellings, not references to anything.
        "ATAN2",  # two-argument arctangent
        "LOG10",  # base-10 logarithm
        "NVL2",  # three-argument null-coalescing form
        "HH24",  # 24-hour hour component in a TO_TIMESTAMP format string
        "INT64",  # a datatype name written into TML's db_column_properties
        "UTF-8",  # the character encoding standard
        "COM1",  # a Windows-reserved device name, from filename-safety tests
        "SCD-2",  # "Slowly Changing Dimension type 2" — a data-warehousing term
        "H3",  # a Markdown heading level (### = h3), describing source structure
        "P75",  # the 75th percentile — a statistical term, not an identifier
        "BLE001",  # a ruff lint rule code, appearing only in a `# noqa:` comment
        "TS001",  # an arbitrary example issue code used as test fixture data
        # Loss-category codes this repository defines and explains itself, in
        # README's own coverage matrix — resolvable from inside this repository
        # alone, unlike every entry in the provisional set below.
        "L1",
        "L2",
        "L3",
        "L4",
        "L5",
        "L6",
    }
)

# ---------------------------------------------------------------------------
# PROVISIONAL — pending a decision that is not this test's to make.
#
# These are rule identifiers from ThoughtSpot's construct/expression mapping
# tables and conversion-invariant catalogue, maintained in an internal
# repository that is not part of this project and is not currently public (see
# README.md's "Rules" section for the full account). Whether that source
# material is ever contributed into this repository — which would make each of
# these resolvable — is a larger decision above this test's authority, and is
# not made here.
#
# Until that decision lands, citing them is allowed. This block is the single
# place to edit when it does: delete the whole block once the source material
# ships alongside this converter, or move individual entries up into
# ALLOWED_TOKENS with their own justification if only some turn out to stay.
# ---------------------------------------------------------------------------
_MAPPING_DOC_RULE_ID_FAMILIES: dict[str, tuple[int, ...]] = {
    "A": (3, 9, 10, 11, 12),
    "E": tuple(range(1, 14)),
    "G": (2, 3),
    "I": (1, 4, 5, 7),
    "ID": (1, 2, 3, 4),
    "KD": (1, 2, 3),
    "NM": (1, 2, 6),
    "R": (1, 11),
    "X": tuple(range(1, 10)),
}
MAPPING_DOC_RULE_IDS: frozenset[str] = frozenset(
    f"{prefix}{number}"
    for prefix, numbers in _MAPPING_DOC_RULE_ID_FAMILIES.items()
    for number in numbers
)


def test_allowed_token_sets_do_not_overlap() -> None:
    # A token provisionally-allowed as an external rule id must not also be
    # claimed as an unrelated legitimate token — that would hide which bucket
    # it is really in, and defeat the point of separating the two.
    overlap = ALLOWED_TOKENS & MAPPING_DOC_RULE_IDS
    assert overlap == set(), f"tokens claimed in both allowlists: {sorted(overlap)}"


def test_no_unresolvable_identifier_shaped_tokens() -> None:
    shipped = _shipped_files()
    assert shipped, "expected at least one shipped file to scan"

    allowed = ALLOWED_TOKENS | MAPPING_DOC_RULE_IDS
    offenders: list[str] = []
    for path in shipped:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in TOKEN_SHAPE_RE.finditer(line):
                token = match.group(0)
                if token not in allowed:
                    rel = path.relative_to(PACKAGE_ROOT)
                    offenders.append(f"{rel}:{lineno}: {token!r} — {line.strip()!r}")

    assert offenders == [], (
        "Identifier-shaped token(s) found that are not in ALLOWED_TOKENS or "
        "MAPPING_DOC_RULE_IDS. A reader of this repository alone cannot resolve "
        "what they name. Either it is a genuinely unrelated technical token — add "
        "it to ALLOWED_TOKENS with a one-line justification — or it is a new "
        "citation to unshipped material, which needs a human decision (reword to "
        "state the substance, or add to the provisional mapping-doc set with "
        "reason):\n" + "\n".join(offenders)
    )


# ---------------------------------------------------------------------------
# Half 2 — ordinary English used in a process sense. No identifier shape
# describes this half, so it stays a hand-curated, case-insensitive blocklist
# of phrases that only make sense with access to material this repository does
# not ship: an internal task tracker, a set of named alternative plans, an
# original instructions document, a named human role in that process and the
# cycle of revising drafts against its feedback, and the internal agent-skill
# framework with its planning/tracking directories.
#
# Every pattern below leads with `\b` (a word-boundary assertion). That choice
# is deliberate, not incidental: it is also what keeps this module passing its
# own check below. In this file's own source text, each pattern's *raw
# characters* read literally as backslash-b-then-the-phrase (e.g. the actual
# bytes of the fourth pattern are `\btranscribed\b`), so the phrase is always
# immediately preceded by the letter "b" from that escape — a word character
# butted against another word character, which is never a word boundary. A
# pattern can therefore never match its own definition here. This was verified
# by running this suite against this file, not just reasoned about.
# ---------------------------------------------------------------------------
PROCESS_LANGUAGE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\btask\s+\d+\b",
        r"\bplan\s+[a-d]\b",
        r"\bthe\s+brief\b",
        r"\btranscribed\b",
        r"\breviewers?\b",
        r"\bfix\s+rounds?\b",
        r"\breview\s+rounds?\b",
        r"\bsuperpowers\b",
        r"\bsdd/",
        r"\bopen[- ]items?\b",
        r"\bledger\b",
        r"\bthe\s+findings?\b",
    )
)


def test_no_internal_process_language() -> None:
    shipped = _shipped_files()
    assert shipped, "expected at least one shipped file to scan"

    offenders: list[str] = []
    for path in shipped:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pattern in PROCESS_LANGUAGE_PATTERNS:
                if pattern.search(line):
                    rel = path.relative_to(PACKAGE_ROOT)
                    offenders.append(
                        f"{rel}:{lineno}: matched {pattern.pattern!r} — {line.strip()!r}"
                    )

    assert offenders == [], (
        "Ordinary-English process language found — a reference that only makes "
        "sense with access to an internal document this repository does not "
        "ship. Reword to state the substance directly:\n" + "\n".join(offenders)
    )
