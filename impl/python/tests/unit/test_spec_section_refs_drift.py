"""Spec section-ref drift test (long-term-viability audit Phase C).

Every ``(Spec: §X.Y[.Z])`` citation that appears inside the Python
implementation must resolve to a real heading in
``proposals/foundation-v0.1/Proposed_OSI_Semantics.md``. Without this
test, a spec section can be renumbered or removed and the citations
across the codebase silently rot — reviewers then stop trusting them.

This test is intentionally narrow: it parses citations of the literal
form ``(Spec: §X.Y...)`` (optionally followed by trailing tokens like
``Appendix B``, ``D-027``, or a closing comma / paren) and confirms
the leading section number appears as the prefix of a Markdown heading
in the spec file.

Other citation families (``(D-NNN)``, ``(E_*)``, ``(F-NN)``,
``(I-NN)``, ``(T-NNN)``) have their own drift tests or live invariants
elsewhere (Appendix C drift, INFRA roadmap rows, sprint reports). Add
a new test alongside this one when introducing a new citation family
per the long-term-viability audit triage rule.
"""

from __future__ import annotations

import re
from pathlib import Path

# Source files searched for ``(Spec: §X.Y)`` citations. The spec file
# itself is excluded (it is the citation target, not a citer); test
# files are excluded because their references are illustrative only.
#
# Layout: this file lives at ``impl/python/tests/unit/<name>.py`` so
# the repo root is four parents up.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_PYTHON_IMPL_SRC = _REPO_ROOT / "impl" / "python" / "src"
_SPEC_FILE = _REPO_ROOT / "proposals" / "foundation-v0.1" / "Proposed_OSI_Semantics.md"

# Formal citation form: ``(Spec: §X.Y[.Z])`` (parenthesised, with the
# ``Spec:`` tag). Captures the section number only — we resolve against
# headings using prefix match, so ``§4.5`` resolves whether the heading
# is ``### 4.5 Metrics`` or ``#### 4.5.1 ...``.
#
# Natural-language references like ``the spec §X.Y`` or
# ``(Foundation spec §X.Y)`` are intentionally NOT matched here: those
# are often citing the algebra spec (``JOIN_ALGEBRA.md``) rather than
# the Foundation spec, and conflating the two is the F-04 / Phase 11
# drift this test is meant to prevent. References to JOIN_ALGEBRA.md
# should say so explicitly (``JOIN_ALGEBRA.md §3.7``); references to
# the Foundation spec should use the formal ``(Spec: §X.Y)`` form.
_CITATION_RE = re.compile(
    r"\(Spec:\s+§(?P<section>\d+(?:\.\d+){0,3})",
)
# Markdown headings of the form ``## 4. Semantic Model`` or
# ``#### 4.6.1 Identifier Form``. The captured prefix is the
# dotted-number form; we normalise trailing dots away.
_HEADING_RE = re.compile(
    r"^#{1,6}\s+(?P<section>\d+(?:\.\d+){0,3})\.?\s",
)


def _collect_spec_headings() -> set[str]:
    """Return every numbered Markdown heading in the Foundation spec."""
    sections: set[str] = set()
    for line in _SPEC_FILE.read_text(encoding="utf-8").splitlines():
        match = _HEADING_RE.match(line)
        if match:
            sections.add(match.group("section"))
    return sections


def _collect_citations() -> dict[str, list[Path]]:
    """Map every cited section to the source files that cite it."""
    citations: dict[str, list[Path]] = {}
    for path in _PYTHON_IMPL_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in _CITATION_RE.finditer(text):
            section = match.group("section")
            citations.setdefault(section, []).append(path)
    return citations


def _section_resolves(cited: str, headings: set[str]) -> bool:
    """``§4.5`` resolves if any heading equals or extends ``4.5``."""
    if cited in headings:
        return True
    for heading in headings:
        if heading.startswith(cited + "."):
            return True
    return False


def test_every_spec_citation_resolves() -> None:
    """Every ``(Spec: §X.Y)`` in ``impl/python/src/`` points at a real heading.

    If this test fails, either:

    1. The spec was renumbered — update the citing source file or
       restore the heading in the spec (in that PR, not later).
    2. The citation is a typo — fix the section number.
    3. The cited section is brand new and the spec PR has not landed
       yet — land the spec PR in the same change set; do not commit
       code that cites a section that does not exist.
    """
    headings = _collect_spec_headings()
    assert headings, (
        f"No numbered Markdown headings found in {_SPEC_FILE}. The "
        "drift test cannot run; check the spec file structure."
    )
    citations = _collect_citations()
    assert citations, (
        "No (Spec: §X.Y) citations found in impl/python/src/. The "
        "test should find at least the citations in "
        "src/osi/diagnostics/error_catalog.py; check the citation "
        "regex if no citations are detected."
    )
    unresolved: dict[str, list[Path]] = {
        section: sorted(set(paths))
        for section, paths in citations.items()
        if not _section_resolves(section, headings)
    }
    assert not unresolved, (
        "(Spec: §X.Y) citations that do not resolve to a real heading "
        "in proposals/foundation-v0.1/Proposed_OSI_Semantics.md:\n"
        + "\n".join(
            f"  §{section} cited by " + ", ".join(str(p) for p in paths)
            for section, paths in sorted(unresolved.items())
        )
        + "\n\nFix the citation, restore the spec heading, or land the "
        "spec PR alongside this code change."
    )


def test_spec_file_is_present() -> None:
    """A guard against repo-layout drift."""
    assert _SPEC_FILE.is_file(), (
        f"Spec file missing: {_SPEC_FILE}. The drift test resolves "
        "citations against this path; update the constant if the "
        "spec was relocated."
    )
