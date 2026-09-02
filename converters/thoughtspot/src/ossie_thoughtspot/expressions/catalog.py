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

"""The catalog: every specification construct mapped to a ThoughtSpot rendering.

`CATALOG` is populated across Tasks 2-7 of the expression-translation plan; here
it is deliberately empty. What this module provides *now* is
`spec_construct_names()` — an oracle read from the **upstream**
`core-spec/expression_language.md`, not from any document of our own, so that a
construct added upstream fails this package's build instead of silently going
unsupported (see `test_catalog_covers_the_spec.py`).

Extraction approach
--------------------
The spec document mixes three kinds of content that must be told apart:

1. Genuine constructs — a named function, operator or literal form the
   specification defines, almost always as one row of a markdown table whose
   row carries a `Syntax` column (e.g. `SUM(expr)`), or, for a handful of
   operators/keywords with no table of their own, a row of the top-level
   "Supported SQL Constructs" table.
2. Argument vocabularies (rule E1) — the `EXTRACT`/`DATE_PART` parts, the
   `DATE_TRUNC` precisions, the `TO_DATE`/`TO_CHAR` format tokens and the
   `CAST` target types. These describe values an argument may take, not
   constructs in their own right, and must be excluded.
3. Informative tables — the per-engine "Common Dialect Variations" table and
   the "Cross-Reference: Tool Mappings" section describe *other products'*
   spellings (Tableau, Looker Studio, DAX, and per-engine SQL). Names that
   appear only there are not Ossie constructs.

The exclusions are keyed off the document's own structure — a table's own
header naming ("Token" columns, a "Form" cell reading "Cast") and the section
heading text ("Not Supported in Expressions", "Common Dialect Variations",
"Cross-Reference") — rather than a hardcoded list of names to drop. A hardcoded
list would go stale the moment upstream renamed or added a construct, which is
exactly the failure mode this gate exists to catch. The `EXTRACT`/`DATE_PART`
date-part list, the `DATE_TRUNC` precision list and the `CAST` target-type list
need no such marker at all: `_extract_tables()` only ever looks at lines
starting with "|", so a plain bullet list is simply invisible to it, argument
vocabulary or not.

Spelling: `CATALOG` keys must match `spec_construct_names()` exactly (READ THIS
BEFORE TASKS 3-8, AND WHEN IN DOUBT DO NOT TRUST THIS LIST FROM MEMORY)
------------------------------------------------------------------------------
`spec_construct_names()` is the oracle, not the mapping document's prose, and not
this list. Several rows write their Ossie-side syntax differently than this
parser extracts it, and a `CATALOG` entry keyed on the mapping document's own
wording — not this function's output — will read as an "invented" construct
even though it is a real, intended row. **The authoritative check is always:
run `spec_construct_names()`, print it, and match a member of it exactly** —
this list is a convenience audited against that output, not a substitute for
it, and a previous version of this list both omitted a case and misattributed
another's source (both listed below, corrected). If this list and a live run
of `spec_construct_names()` ever disagree, the live run wins.

Grouped by which extractor produces the divergent spelling, so the source is
never ambiguous:

- **`_extract_tables()`** (an ordinary table with a `Syntax` column — the key is
  that column's value, not the mapping document's `Ossie`-column header):
    - Alias pairs the spec merges into ONE table row keep this parser's single
      extracted spelling: `CEIL(x)` (not `CEIL(x) / CEILING(x)`), `TRUNC(x, d)`
      (not `.../ TRUNCATE(x, d)`).
    - Two-alternative-syntax rows keep the spec's own joining word, "or" — not
      the mapping document's "/": `"CURRENT_DATE or CURRENT_DATE()"`,
      `"CURRENT_TIMESTAMP or CURRENT_TIMESTAMP()"`, `"CURRENT_TIME or
      CURRENT_TIME()"`.
    - The merged boolean-literal row (`BOOLEAN`'s `Syntax` cell) is one entry,
      comma-joined: `"TRUE, FALSE"` (mapping document header: `` `TRUE` /
      `FALSE` (boolean literals) ``).
    - The Boolean Functions table's `AND`/`OR` rows keep the spec's own
      `expr1`/`expr2` placeholder names, not the mapping document's `a`/`b`:
      `"expr1 AND expr2"` (mapping document header: `` `a AND b` ``),
      `"expr1 OR expr2"` (mapping document header: `` `a OR b` ``).
- **`_extract_summary_rows()`** (the top-level "Supported SQL Constructs"
  table, bare backtick token — not the mapping document's `a`/`b`/`x`-style
  worked example): `BETWEEN`, `IN`, `NOT IN`, `IS NULL`, `IS NOT NULL`, `CASE
  WHEN`, and the raw symbols `+ - * / % = <> != < > <= >=`.
- **`_extract_null_safe_comparison_operators()`** (the "Null-Safe Comparison"
  code fence — NOT the summary table, despite reading like one more row of it):
  `IS DISTINCT FROM`, `IS NOT DISTINCT FROM`.
- **`_extract_extraction_syntax_functions()`** (the "Alternative Extraction
  Syntax" code fence, bare token, no argument list): `EXTRACT`, `DATE_PART`.
- **`_extract_single_construct_headings()`** (a standalone heading with no
  table, bare token): `CAST`, `TRY_CAST` (not `CAST(expression AS
  target_type)`).

When adding a row, cross-check its key against `spec_construct_names()`'s output
rather than transcribing the mapping document's column text verbatim. See
`CONVENTION_DIVERGENCES` below for the (much shorter) list of constructs that
have no entry in `spec_construct_names()` at all and are exempted instead.
"""
import re
from pathlib import Path

from ._types import Construct

# --------------------------------------------------------------------------
# CATALOG: empty until Tasks 3-7 populate it, one family per task.
# --------------------------------------------------------------------------
CATALOG: dict[str, Construct] = {}

#: Constructs the mapping document (docs/ossie/ts-ossie-function-mapping.md in the
#: thoughtspot-agent-skills repo) counts separately under rule E1 ("one row per
#: construct") that core-spec/expression_language.md does not give a discrete
#: table row of their own. Each entry records WHY it diverges. This is NOT an
#: escape hatch for missing coverage: the 137 names in spec_construct_names() are
#: still parsed from the live upstream file, so an upstream addition still fails
#: the build. It exists because the mapping document's 146-row census counts by a
#: different unit (one row per construct, including constructs the spec only
#: describes in prose) than spec_construct_names() counts by (one entry per
#: parseable table row / heading). Verified directly against the mapping
#: document's actual row list — see catalog.py's docstring and task-1-report.md
#: for the reconciliation (137 + 9 == 146).
#:
#: Two mechanisms an earlier pass mistakenly guessed would appear here do NOT:
#: `CEIL(x)`/`CEILING(x)`, `TRUNC(x, d)`/`TRUNCATE(x, d)` and `TRUE`/`FALSE` are
#: each ONE row in the mapping document too (not split), matching spec_name's
#: single merged entry — a spelling-convention question for Tasks 3-8 (see the
#: module docstring's "Spelling" note), not a divergence.
CONVENTION_DIVERGENCES: dict[str, str] = {
    "-x / +x (unary)": (
        "unary +/- is named only in the 'Operator Precedence' list "
        "(core-spec/expression_language.md:142), never a table row"
    ),
    "CASE expr WHEN v1 THEN r1 ... END (simple)": (
        "simple CASE is described only in the CASE Expression code fence "
        "(core-spec/expression_language.md:508-513) alongside searched CASE; "
        "the top-level summary table's single bare 'CASE WHEN' token covers "
        "the searched form and does not extend to this one"
    ),
    "Parentheses — expression grouping": (
        "its Supported SQL Constructs row (core-spec/expression_language.md:120) "
        "carries no backtick token in either cell, the only marker the "
        "top-table extraction keys on"
    ),
    "DISTINCT aggregate modifier": (
        "the DISTINCT modifier is described only in the Conditional "
        "Aggregations prose/code block (core-spec/expression_language.md:219-230), "
        "never a table row"
    ),
    "Column / metric reference — field, dataset.field": (
        "its Supported SQL Constructs row (core-spec/expression_language.md:108) "
        "carries no backtick token in either cell, same reason as Parentheses"
    ),
    "EXISTS_IN()": (
        "named only in the Reason column of the excluded 'Not Supported in "
        "Expressions' table (core-spec/expression_language.md:131), never in a "
        "table of its own"
    ),
    "OVER (PARTITION BY ... ORDER BY ...) clause": (
        "the generic OVER syntax template (core-spec/expression_language.md:548-560) "
        "is a fenced code block, not a table"
    ),
    "Frame clause — ROWS BETWEEN ... / RANGE BETWEEN ...": (
        "frame options are a bullet list under the OVER syntax section "
        "(core-spec/expression_language.md:556-560), not a table"
    ),
    "Window aggregation — AGG(expr) OVER (...)": (
        "the Window Aggregations section (core-spec/expression_language.md:583-599) "
        "is prose and code examples, not a table"
    ),
}


# --------------------------------------------------------------------------
# spec_construct_names(): the upstream-spec oracle.
# --------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")

# A section whose entire content is about something *other than* an Ossie
# construct in its own right. Matched case-insensitively as a substring of
# the heading text at any level, so a subsection ("### Common Dialect
# Variations" under "## Dialect Extensions") is caught the same way as a
# top-level one ("## Cross-Reference: Tool Mappings").
_EXCLUDED_SECTION_MARKERS = (
    "not supported",  # "Not Supported in Expressions": SELECT/FROM/GROUP BY/... are
    # explicitly things Ossie expressions do NOT support - the opposite of a construct.
    "dialect variation",  # "Common Dialect Variations": other engines' spellings of
    # constructs already counted from their Ossie-standard table.
    "cross-reference",  # "Cross-Reference: Tool Mappings": Tableau / Looker Studio / DAX
    # spellings, not Ossie constructs.
)

# A standalone H3 section that defines exactly one construct via prose/a code
# fence rather than a table - e.g. "### CAST (REQUIRED)". Matches only when
# the heading's own name is a single token (letters, digits, underscore, or a
# markdown-escaped underscore "\_"), which is what distinguishes "CAST" or
# "TRY\_CAST" from a descriptive multi-word heading like "Alternative
# Extraction Syntax" or "Null-Safe Comparison" (those need bespoke handling
# below, because more than one construct - or a construct whose name isn't
# the heading text - lives in their body).
_SINGLE_CONSTRUCT_HEADING_RE = re.compile(
    r"^### ([A-Za-z0-9_]+(?:\\_[A-Za-z0-9_]+)*) \((?:REQUIRED|RECOMMENDED|EXPERIMENTAL)\)\s*$",
    re.MULTILINE,
)


def _find_spec_path() -> Path:
    """Walk up from this file to the repository root and locate the upstream spec."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "core-spec" / "expression_language.md"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"core-spec/expression_language.md not found by walking up from {here}"
    )


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row on '|', but never inside a backtick code span.

    The document contains at least one cell whose code span itself contains a
    pipe character - the `||` concatenation operator, written as
    `` `str1 || str2` `` - which a naive `str.split("|")` would shred into
    extra empty cells. Backticks otherwise never appear as literal (non-code)
    content in this document's tables, so "toggle on backtick" is a safe,
    general rule rather than a special case for that one row.
    """
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code_span = False
    for ch in line:
        if ch == "`":
            in_code_span = not in_code_span
            current.append(ch)
        elif ch == "|" and not in_code_span:
            cells.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    cells.append("".join(current).strip())
    return cells


def _clean(cell: str) -> str:
    """Strip markdown code-span backticks, leaving the underlying syntax text."""
    return cell.replace("`", "").strip()


def _is_section_excluded(heading_stack: dict[int, str]) -> bool:
    return any(
        marker in heading.lower()
        for heading in heading_stack.values()
        for marker in _EXCLUDED_SECTION_MARKERS
    )


def _extract_tables(text: str) -> tuple[set[str], set[str], list[tuple[str, str]]]:
    """One pass over the document collecting constructs from ordinary tables.

    Returns:
        names: constructs whose spec_name is a `Syntax` column value.
        identifier_tokens: the identifying (first) column's backtick tokens for every
            such table row, used to de-duplicate against the top-level summary table.
        summary_rows: (construct_cell, notes_cell) pairs from the top-level
            "Supported SQL Constructs" table, processed by the caller once every
            detailed table has been seen.
    """
    names: set[str] = set()
    identifier_tokens: set[str] = set()
    summary_rows: list[tuple[str, str]] = []

    heading_stack: dict[int, str] = {}
    lines = text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            for lvl in [lvl for lvl in heading_stack if lvl >= level]:
                del heading_stack[lvl]
            heading_stack[level] = heading_match.group(2).strip()
            i += 1
            continue

        stripped = line.strip()

        # A fenced code block is never itself a table; skip its body outright.
        # (The few constructs defined only inside a fence are picked up by the
        # bespoke passes in spec_construct_names(), keyed by heading shape.)
        if stripped.startswith("```"):
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                i += 1
            i += 1
            continue

        if not stripped.startswith("|"):
            i += 1
            continue

        if _is_section_excluded(heading_stack):
            # Skip the whole table without even parsing it.
            while i < n and lines[i].strip().startswith("|"):
                i += 1
            continue

        table_lines = []
        while i < n and lines[i].strip().startswith("|"):
            table_lines.append(lines[i])
            i += 1
        if len(table_lines) < 2:
            continue  # a header with no separator row is not a real table

        header = [_split_table_row(table_lines[0])]
        header_lower = [c.lower() for c in header[0]]
        data_rows = [_split_table_row(r) for r in table_lines[2:]]

        # Rule E1: a table whose identifying column is literally "Token" is a
        # format-token argument vocabulary (TO_CHAR/TO_DATE's `format` argument).
        if header_lower and header_lower[0] == "token":
            continue

        if "syntax" in header_lower:
            syntax_idx = header_lower.index("syntax")
            form_idx = header_lower.index("form") if "form" in header_lower else None
            for row in data_rows:
                if len(row) <= syntax_idx:
                    continue
                if (
                    form_idx is not None
                    and len(row) > form_idx
                    and row[form_idx].strip().lower() == "cast"
                ):
                    # A "Cast" row in the Date/Time Construction table is a worked
                    # example of the already-catalogued generic CAST(...) construct,
                    # not a new one.
                    continue
                for token in _CODE_SPAN_RE.findall(row[0]) if row else []:
                    identifier_tokens.add(token.strip().upper())
                syntax_value = _clean(row[syntax_idx])
                if syntax_value:
                    names.add(syntax_value)
        elif header_lower[:2] == ["construct", "notes"]:
            # The top-level "Supported SQL Constructs" table. Its rows range from
            # genuine one-off operators/keywords (BETWEEN, CASE WHEN, IN / NOT IN)
            # to category headers elaborated in detail elsewhere (Aggregate
            # functions, Window functions) - processed once every detailed table
            # has been seen, so it can tell the two apart (see _extract_summary_rows).
            for row in data_rows:
                if row:
                    summary_rows.append((row[0], row[1] if len(row) > 1 else ""))
        # Any other table shape ("Database Support", "Decomposability Reference",
        # the working-group roster, a comparison-of-quoting-styles example) names
        # no new construct and is left unread.

    return names, identifier_tokens, summary_rows


def _extract_summary_rows(
    summary_rows: list[tuple[str, str]], identifier_tokens: set[str]
) -> set[str]:
    """Pull genuine constructs out of the top "Supported SQL Constructs" table.

    A row counts only if its Construct cell or its Notes cell carries a
    backtick-quoted token - the document's own marker for "this cell names a
    real piece of syntax" - which is how e.g. `BETWEEN`, `` `IN` / `NOT IN` ``
    and `` `CASE WHEN` `` are told apart from plain category labels like
    "Column and Metric references" or "Aggregate functions" (elaborated in
    detailed tables elsewhere, and carrying no backtick markup of their own).

    A token already seen as a detailed table's identifying column (e.g. `LIKE`
    from the Pattern Matching table) is skipped here to avoid counting the same
    construct twice under two different spellings.
    """
    names: set[str] = set()
    for construct_cell, notes_cell in summary_rows:
        tokens = _CODE_SPAN_RE.findall(construct_cell)
        if not tokens:
            for part in notes_cell.split(","):
                tokens.extend(_CODE_SPAN_RE.findall(part))
        for token in tokens:
            token = token.strip()
            if token.upper() in identifier_tokens:
                continue
            names.add(token)
    return names


def _extract_extraction_syntax_functions(text: str) -> set[str]:
    """EXTRACT and DATE_PART: named in a code fence, not a table.

    The "Alternative Extraction Syntax" section is the only place either
    function is named; the bullet list immediately below it enumerates the
    date parts they accept (rule E1: an argument vocabulary, not a construct).
    That list needs no special exclusion - it is a bullet list, not a table,
    so `_extract_tables()` never looks at it in the first place.
    """
    section = re.search(
        r"### Alternative Extraction Syntax.*?\n(.*?)\n###", text, re.S
    )
    if not section:
        return set()
    return set(re.findall(r"\b([A-Z_]+)\(", section.group(1)))


def _extract_null_safe_comparison_operators(text: str) -> set[str]:
    """IS DISTINCT FROM / IS NOT DISTINCT FROM: named only inside a code fence."""
    section = re.search(r"### Null-Safe Comparison.*?\n(.*?)\n---", text, re.S)
    if not section:
        return set()
    return {m.group(0) for m in re.finditer(r"\bIS (?:NOT )?DISTINCT FROM\b", section.group(1))}


def _extract_single_construct_headings(text: str) -> set[str]:
    """A standalone H3 whose own name (not a table) is the one construct it defines.

    Structural, not name-based: matches any "### {token} ({compliance level})"
    heading whose section body contains no pipe-table. CAST and TRY_CAST are
    the only two headings in the current document shaped this way.
    """
    names: set[str] = set()
    for match in _SINGLE_CONSTRUCT_HEADING_RE.finditer(text):
        token = match.group(1).replace("\\_", "_")
        start = match.end()
        next_heading = re.search(r"^#{1,6} ", text[start:], re.M)
        body = text[start : start + next_heading.start()] if next_heading else text[start:]
        if "|" not in body:
            names.add(token)
    return names


def spec_construct_names() -> set[str]:
    """The construct inventory of the upstream expression-language specification.

    Reads `core-spec/expression_language.md` fresh on every call - the file is
    small and this is a test-time oracle, not a runtime hot path.
    """
    text = _find_spec_path().read_text(encoding="utf-8")

    table_names, identifier_tokens, summary_rows = _extract_tables(text)
    names = set(table_names)
    names |= _extract_summary_rows(summary_rows, identifier_tokens)
    names |= _extract_extraction_syntax_functions(text)
    names |= _extract_null_safe_comparison_operators(text)
    names |= _extract_single_construct_headings(text)
    return names
