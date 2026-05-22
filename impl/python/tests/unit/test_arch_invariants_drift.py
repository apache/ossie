"""Architectural-invariants drift test (long-term-viability audit Phase C).

``ARCHITECTURE.md §6`` declares the numbered architectural invariants
(currently 1..16). ``ARCHITECTURE.md §6.5`` then maps each numbered
invariant to the deterministic check (or explicit "reviewed") that
enforces it.

This test pins the relationship between the two:

1. **Every numbered invariant in §6 has a row in §6.5.** If §6 grows
   a new invariant 17 and §6.5 forgets to list it, this test fails —
   "added an invariant, forgot to declare the enforcement" is exactly
   the drift we want to refuse.

2. **Every row in §6.5 cites an existing file.** A row that names
   ``pyproject.toml`` for an import-linter contract, a
   ``tests/properties/test_X.py`` for a property test, or a similar
   on-disk artefact must resolve. A renamed or removed enforcement
   file is caught here, not at audit time months later.

The test reads ``ARCHITECTURE.md`` once and parses it; it does not
load any spec heuristically, so it stays cheap (<50 ms) and side-effect
free.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_PYTHON_IMPL = _REPO_ROOT / "impl" / "python"
_ARCH_FILE = _PYTHON_IMPL / "ARCHITECTURE.md"

# §6 invariants look like ``1. **Closed state.** ...`` after a sub-heading.
# We tolerate up to 3 digits and the bold-italic period after the
# heading text.
_INVARIANT_RE = re.compile(r"^(?P<num>\d{1,3})\.\s+\*\*")

# §6.5 rows look like ``| 3 | Pure functions | ... |`` (table form).
_CATALOG_ROW_RE = re.compile(r"^\|\s*(?P<num>\d{1,3})\s*\|")

# Files cited in §6.5 cells. We pick up paths that look like
# ``tests/.../*.py``, ``src/.../*.py``, ``docs/.../*.md``,
# ``pyproject.toml``, ``Makefile``, ``INFRA.md`` etc. The intent is to
# extract paths that should resolve from ``impl/python/`` (the repo of
# this test) so the linter / arch-test / property-test files can be
# checked for existence.
_FILE_REF_RE = re.compile(
    r"`(?P<path>"
    r"(?:tests|src|docs|conformance|examples|scripts)/[\w./_-]+\.(?:py|md|toml|json|yaml)"
    r"|pyproject\.toml"
    r"|Makefile"
    r"|INFRA\.md"
    r"|ARCHITECTURE\.md"
    r"|SPEC\.md"
    r")`"
)


def _read_arch_md() -> str:
    assert _ARCH_FILE.is_file(), (
        f"ARCHITECTURE.md not found at {_ARCH_FILE}. Update the "
        "drift test if the file was relocated."
    )
    return _ARCH_FILE.read_text(encoding="utf-8")


def _section_slice(text: str, heading: str, next_heading: str) -> str:
    """Return the text between ``heading`` and ``next_heading``.

    Both headings are matched as line prefixes (with the leading ``#``s
    intact). Returns the empty string if ``heading`` is not found.
    """
    lines = text.splitlines()
    start: int | None = None
    end: int | None = None
    for idx, line in enumerate(lines):
        if line.startswith(heading) and start is None:
            start = idx + 1
            continue
        if start is not None and line.startswith(next_heading):
            end = idx
            break
    if start is None:
        return ""
    return "\n".join(lines[start : end or len(lines)])


def _collect_invariant_numbers(arch_text: str) -> set[int]:
    """Return the set of numbered invariants declared inside §6 (1..N)."""
    section_6 = _section_slice(arch_text, "## 6. ", "## 7. ")
    # §6.5 is itself a sub-section of §6, but its rows start with the
    # invariant number in a table — those match _INVARIANT_RE too if
    # we don't stop early. We slice §6 to end at §6.5 to keep only the
    # narrative invariants from the four sub-section groupings.
    pre_catalog = section_6.split("### Invariants enforced in code", 1)[0]
    nums: set[int] = set()
    for line in pre_catalog.splitlines():
        match = _INVARIANT_RE.match(line)
        if match:
            nums.add(int(match.group("num")))
    return nums


def _collect_catalog(arch_text: str) -> tuple[set[int], dict[int, set[str]]]:
    """Read the §6.5 catalog table.

    Returns ``(row_numbers, row_cited_paths)`` where ``row_cited_paths``
    maps each row's invariant number to the set of repo-relative paths
    cited in the enforcement column.
    """
    section_65 = _section_slice(arch_text, "### Invariants enforced in code", "## 7. ")
    nums: set[int] = set()
    cited: dict[int, set[str]] = {}
    for line in section_65.splitlines():
        row_match = _CATALOG_ROW_RE.match(line)
        if not row_match:
            continue
        num = int(row_match.group("num"))
        nums.add(num)
        cited.setdefault(num, set()).update(
            m.group("path") for m in _FILE_REF_RE.finditer(line)
        )
    return nums, cited


def test_every_invariant_has_a_catalog_row() -> None:
    """§6 numbered invariants ⊆ §6.5 catalog rows.

    Add a new invariant to §6? You must add the matching row to §6.5,
    naming the deterministic check (or stating "reviewed" with
    rationale). This is the design-time rule from the long-term-
    viability audit applied to invariants themselves.
    """
    arch_text = _read_arch_md()
    declared = _collect_invariant_numbers(arch_text)
    catalogued, _ = _collect_catalog(arch_text)
    assert declared, (
        "No numbered invariants found in ARCHITECTURE.md §6. The "
        "drift test cannot run; check the invariant heading format "
        "(expected ``N. **Title.**``)."
    )
    missing = sorted(declared - catalogued)
    assert not missing, (
        "ARCHITECTURE.md §6 declares invariants that §6.5 (Invariants "
        f"enforced in code) does not catalogue: {missing}. Add a row "
        "for each — drift test or import-linter contract if "
        "mechanically possible, 'reviewed' with rationale otherwise."
    )


def test_catalog_does_not_invent_invariants() -> None:
    """§6.5 catalog rows ⊆ §6 numbered invariants.

    A row for invariant 99 in §6.5 with no invariant 99 in §6 means
    a stale row from an old structure; remove it or restore the
    matching invariant.
    """
    arch_text = _read_arch_md()
    declared = _collect_invariant_numbers(arch_text)
    catalogued, _ = _collect_catalog(arch_text)
    extras = sorted(catalogued - declared)
    assert not extras, (
        "ARCHITECTURE.md §6.5 lists invariants that §6 does not "
        f"declare: {extras}. Remove the row or restore the invariant."
    )


def test_catalog_cited_paths_resolve() -> None:
    """Every file referenced in a §6.5 enforcement cell exists.

    Catches catalog rows that point at a deleted test or a renamed
    file. The lookup is intentionally string-match-only (no parsing
    of test contents) so the check is cheap.
    """
    arch_text = _read_arch_md()
    _, cited = _collect_catalog(arch_text)
    missing: dict[int, list[str]] = {}
    for invariant, paths in cited.items():
        for path in sorted(paths):
            resolved = _PYTHON_IMPL / path
            if not resolved.exists():
                missing.setdefault(invariant, []).append(str(resolved))
    assert not missing, (
        "ARCHITECTURE.md §6.5 cites files that do not exist on disk:\n"
        + "\n".join(
            f"  invariant {invariant}: {', '.join(paths)}"
            for invariant, paths in sorted(missing.items())
        )
        + "\n\nFix the path or restore the file. Catalog rows must "
        "name an enforcement that actually exists; an unresolved "
        "row is a missing check, not a passing one."
    )
