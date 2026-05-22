"""Layer-README drift test (long-term-viability audit Phase C).

Every layer under ``impl/python/src/osi/`` carries a ``README.md`` that
documents what lives in the folder. Without a drift test, the README
gets stale the moment someone adds a new module — contributors lose
their map, reviewers stop trusting the README, and onboarding rots
silently.

This test pins the relationship between each layer README and the
filesystem:

1. **Every ``.py`` file in the layer (except ``__init__.py``) appears
   in the README's "Module map" / "Modules" section.** A new
   ``new_helper.py`` without a README mention fails this test.

2. **Every backticked file name in the section actually exists in the
   layer.** A renamed module that the README still cites by old name
   fails this test.

The intent is to refuse "added a module, forgot the README" PRs
mechanically — the design-time rule from the long-term-viability audit
applied to docs.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_OSI_SRC = _REPO_ROOT / "impl" / "python" / "src" / "osi"

# Layers that have a README + are expected to keep it in sync. The
# ``algebra`` sub-package under ``planning/`` has its own README in
# the long-tail and is treated as a separate layer here.
_LAYERS: tuple[str, ...] = (
    "common",
    "parsing",
    "planning",
    "codegen",
    "diagnostics",
)

# Files that the README is not required to mention. ``__init__.py`` is
# always the layer facade; mentioning it adds noise. ``py.typed`` is a
# packaging marker. ``__main__.py`` exists only at the top level.
_ALWAYS_OMIT: frozenset[str] = frozenset(
    {"__init__.py", "py.typed", "__main__.py", "_root.py"}
)

# Section names under which we look for module references. Layer
# READMEs are inconsistent ("Module map" vs "Modules"); we match
# either, and stop at the next *top-level* ``## `` heading — we want
# sub-sections (``### Core IR`` etc.) inside the Modules block to stay
# part of the same conceptual section, not split it.
_MODULES_HEADING_RE = re.compile(r"^##\s+(?:Module map|Modules)\b", re.IGNORECASE)
_NEXT_SECTION_RE = re.compile(r"^##\s")

# Module references inside a section. We require the ``.py`` suffix
# or a trailing ``/`` so backticked tokens like
# ```normalize_identifier``` (a public function name) or
# ```E_DEFERRED_KEY_REJECTED``` (an error code) are NOT misread as
# module claims. The two patterns we match are:
#
#   ``- `name.py` — desc``     (bullet, file)
#   ``- `name/` — desc``       (bullet, sub-package)
#   ``| `name.py` | desc |``   (table cell, file)
#   ``| `name/` | desc |``     (table cell, sub-package)
_REF_RE = re.compile(r"`(?P<name>[\w_]+)(?:\.py|/)`")


def _modules_section_text(readme: Path) -> str:
    """Return the text between the modules heading and the next heading."""
    lines = readme.read_text(encoding="utf-8").splitlines()
    start: int | None = None
    end: int | None = None
    for idx, line in enumerate(lines):
        if start is None:
            if _MODULES_HEADING_RE.match(line):
                start = idx + 1
            continue
        if _NEXT_SECTION_RE.match(line):
            end = idx
            break
    if start is None:
        return ""
    return "\n".join(lines[start : end or len(lines)])


def _layer_modules_on_disk(layer: Path) -> set[str]:
    """Filenames the README is expected to mention.

    Returns names with ``.py`` stripped for ``.py`` files; sub-package
    folders are returned as the folder name (so ``algebra/`` becomes
    ``algebra``).
    """
    files: set[str] = set()
    for entry in layer.iterdir():
        if entry.name in _ALWAYS_OMIT:
            continue
        if entry.is_dir():
            if (entry / "__init__.py").is_file():
                files.add(entry.name)
            continue
        if entry.suffix != ".py":
            continue
        if entry.name.startswith("_"):
            # Private helpers (``_root.py``, ``_internal.py``) are
            # optional in the README. Skip.
            continue
        files.add(entry.stem)
    return files


def _readme_mentions(readme_section: str) -> set[str]:
    """Stems / folder names mentioned via backticks in the modules section."""
    return {match.group("name") for match in _REF_RE.finditer(readme_section)}


def test_every_layer_has_a_readme() -> None:
    """The five Foundation layers all carry a README."""
    missing = [
        layer for layer in _LAYERS if not (_OSI_SRC / layer / "README.md").is_file()
    ]
    assert not missing, (
        "Layers without README.md: "
        f"{missing}. Every layer under src/osi/ keeps an up-to-date "
        "README; the layer-README drift test only runs against present "
        "READMEs."
    )


def test_layer_readme_lists_every_module() -> None:
    """Files in the layer ⊆ files cited in the README's modules section."""
    failures: dict[str, list[str]] = {}
    for layer_name in _LAYERS:
        layer = _OSI_SRC / layer_name
        readme = layer / "README.md"
        if not readme.is_file():
            continue
        section = _modules_section_text(readme)
        assert section, (
            f"{readme} has no Module map / Modules section — add one "
            "so this drift test can keep it in sync with the filesystem."
        )
        on_disk = _layer_modules_on_disk(layer)
        mentioned = _readme_mentions(section)
        unmentioned = sorted(on_disk - mentioned)
        if unmentioned:
            failures[layer_name] = unmentioned
    assert not failures, (
        "Layer modules not mentioned in their README's Module map / "
        "Modules section:\n"
        + "\n".join(
            f"  {layer}/README.md is missing: {modules}"
            for layer, modules in sorted(failures.items())
        )
        + "\n\nAdd a one-line entry for each new module under the "
        "Modules heading. Private modules (``_internal.py``) are exempt."
    )


def test_layer_readme_does_not_invent_modules() -> None:
    """Files cited in the README ⊆ files on disk.

    Stale citations of renamed / removed modules are caught here.
    """
    failures: dict[str, list[str]] = {}
    for layer_name in _LAYERS:
        layer = _OSI_SRC / layer_name
        readme = layer / "README.md"
        if not readme.is_file():
            continue
        section = _modules_section_text(readme)
        on_disk = _layer_modules_on_disk(layer)
        mentioned = _readme_mentions(section)
        # ``_ALWAYS_OMIT`` carries ``_root.py`` etc. as bare names;
        # strip the ``.py`` for comparison.
        omit_stems = {name.removesuffix(".py") for name in _ALWAYS_OMIT}
        stale = sorted(mentioned - on_disk - omit_stems)
        if stale:
            failures[layer_name] = stale
    assert not failures, (
        "Layer README mentions module names that do not exist in the "
        "folder:\n"
        + "\n".join(
            f"  {layer}/README.md cites missing: {modules}"
            for layer, modules in sorted(failures.items())
        )
        + "\n\nRemove the stale entry or restore the file. The drift "
        "test treats any lowercase / snake_case backticked token in "
        "the Modules section as a file-stem claim."
    )
