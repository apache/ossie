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

"""Guard: the committed `docs/*.md` reference documents must equal what
`tools/generate_reference_docs.py` produces right now.

`docs/expression-mapping.md`, `docs/reverse-inventory.md`, `docs/datatype-map.md`
and `docs/vendor-payload.md` are generated, not hand-authored — the code
(`expressions/catalog.py`, `expressions/reverse.py`, `datatypes.py`,
`constants.py`) is the single source of truth for the mapping they describe.
Committing the generated output makes the reference readable on GitHub without
running anything; this test is what keeps a committed file from silently going
stale after the code it was generated from changes underneath it.

**Comparison is byte-exact (full string equality), deliberately.** A softer
comparison — ignoring whitespace, or checking only that each row's data is
*present* somewhere in the file — would tolerate the generator's own Markdown
formatting drifting out of sync with what is actually committed, which defeats
the point: the committed file must be reproducible from a single, deterministic
command, not merely "close enough". The generator has no non-deterministic
inputs (no timestamps, no unsorted set/dict iteration — every set-derived
listing in `tools/generate_reference_docs.py` is explicitly `sorted()`), so
byte-exact does not mean flaky. The trade-off this accepts: a purely cosmetic
change to the generator's Markdown layout (e.g. column order, a reworded
banner) requires regenerating and committing `docs/*.md` in the same change,
even though no *fact* in the tables moved — this is treated as a feature, not
a cost: it is the same discipline `test_shipped_references.py` already applies
to every other shipped file, and it is exactly what proves this test can fail
at all (see the module for how that was verified).
"""
from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = PACKAGE_ROOT / "tools" / "generate_reference_docs.py"
DOCS_DIR = PACKAGE_ROOT / "docs"


def _load_generator() -> types.ModuleType:
    """Load `tools/generate_reference_docs.py` by file path rather than
    `import`. `tools/` is not listed in `pyproject.toml`'s wheel `packages`
    and carries no `[project.scripts]` entry point, so nothing under `src/`
    reaches it this way either — this loader exists only so *this test* can
    reach it, the same way it would reach any other standalone script.
    """
    spec = importlib.util.spec_from_file_location("generate_reference_docs", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator() -> types.ModuleType:
    return _load_generator()


def test_generator_is_not_part_of_the_installed_package() -> None:
    """The generator must not be a runtime dependency (see its own docstring):
    not part of the wheel this package ships, not a new entry in
    `dependencies`, and not a console-script entry point. A plain substring
    check on the raw file, not a full TOML parse — `tomllib` needs Python
    3.11+, and this package's floor is 3.10 (`requires-python = ">=3.10"`).
    """
    text = (PACKAGE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'packages = ["src/ossie_thoughtspot"]' in text, (
        "wheel packages line changed shape -- update this check, and confirm "
        "'tools' was not added to it"
    )
    assert 'dependencies = [\n    "PyYAML>=6.0",\n]' in text, (
        "runtime dependencies changed shape -- confirm PyYAML is still the only one"
    )
    assert "[project.scripts]" in text
    scripts_block = text.split("[project.scripts]", 1)[1].split("\n\n", 1)[0]
    assert "generate_reference_docs" not in scripts_block
    assert "generate-reference-docs" not in scripts_block


def test_generator_produces_exactly_the_files_docs_contains(generator) -> None:
    expected_names = set(generator.DOCS)
    on_disk = {p.name for p in DOCS_DIR.glob("*.md")}
    assert on_disk == expected_names, (
        f"docs/ and tools/generate_reference_docs.py's DOCS registry disagree on the "
        f"file set -- on disk but not generated: {sorted(on_disk - expected_names)}; "
        f"generated but not on disk: {sorted(expected_names - on_disk)}"
    )


def test_committed_docs_match_generator_output_byte_for_byte(generator) -> None:
    mismatches: list[str] = []
    for name, content in generator.generate_all().items():
        path = DOCS_DIR / name
        if not path.exists():
            mismatches.append(f"docs/{name}: missing from docs/")
            continue
        on_disk = path.read_text(encoding="utf-8")
        if on_disk != content:
            mismatches.append(
                f"docs/{name}: committed content does not match the generator's "
                f"current output ({len(on_disk)} bytes on disk vs {len(content)} "
                "bytes generated)"
            )
    assert mismatches == [], (
        "One or more generated reference documents are stale relative to the code "
        "they are generated from. Regenerate and commit the result:\n"
        "  uv run --python 3.13 python tools/generate_reference_docs.py\n\n"
        + "\n".join(mismatches)
    )
