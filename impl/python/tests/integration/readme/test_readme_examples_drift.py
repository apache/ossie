"""README ``python`` example drift test (long-term-viability audit Phase C).

The Quick Start in [`impl/python/README.md`](../../../README.md) is the
single most important on-ramp for new contributors and external
adopters. If it stops running, every other adoption signal we send is
undermined.

This test extracts every fenced ``python`` block from ``README.md`` and
either compiles or executes it, depending on a per-block directive in
the fence's info string:

* By default (`````python```), the block is **executed** in a fresh
  namespace. A failure to execute fails the test.
* `````python illustrative````` blocks are **compiled** only (syntax +
  bytecode), not run. Use this for snippets that depend on an external
  YAML file, a network, or a database — the fact that they parse as
  valid Python is enough to catch most rot.

The intent is to refuse "the README quick start uses a renamed import"
PRs mechanically. The Quick Start is part of the public surface; a
breaking change in the impl is a breaking change in the README.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
_README = _REPO_ROOT / "impl" / "python" / "README.md"

# ``` ```python ``` or ``` ```python illustrative ```
# We capture the directive list after the language so a block can opt
# out of execution with ``illustrative``. Other directives can be
# added without changing this test.
_FENCE_OPEN_RE = re.compile(r"^```python(?P<directives>(?:\s+\w+)*)\s*$")
_FENCE_CLOSE = "```"


def _extract_blocks(text: str) -> list[tuple[str, list[str], int]]:
    """Return ``(code, directives, start_line)`` per fenced python block."""
    blocks: list[tuple[str, list[str], int]] = []
    lines = text.splitlines()
    idx = 0
    while idx < len(lines):
        match = _FENCE_OPEN_RE.match(lines[idx])
        if not match:
            idx += 1
            continue
        directives = match.group("directives").split()
        start = idx + 1
        end = start
        while end < len(lines) and not lines[end].startswith(_FENCE_CLOSE):
            end += 1
        blocks.append(("\n".join(lines[start:end]), directives, start + 1))
        idx = end + 1
    return blocks


def test_readme_is_present() -> None:
    """A guard against repo-layout drift."""
    assert _README.is_file(), (
        f"README missing: {_README}. The Quick Start is part of the "
        "public surface; the drift test cannot run without it."
    )


def test_readme_has_at_least_one_python_block() -> None:
    """Without examples the test would be vacuously passing."""
    text = _README.read_text(encoding="utf-8")
    blocks = _extract_blocks(text)
    assert blocks, (
        f"No fenced ``python`` blocks found in {_README}. Either the "
        "Quick Start was removed (re-add it) or the fence regex is "
        "out of date."
    )


def _compile_block(code: str, start_line: int) -> Any:
    """Compile ``code`` with a synthetic filename for clean tracebacks."""
    try:
        return compile(code, f"<README.md line {start_line}>", "exec")
    except SyntaxError as exc:  # pragma: no cover — drift signal
        pytest.fail(
            f"README ``python`` block at line {start_line} has a "
            f"SyntaxError: {exc}. Either the code rotted (rename, "
            "removed symbol) or the example was never valid; fix the "
            "block in README.md.",
            pytrace=False,
        )


def test_every_python_block_compiles() -> None:
    """Every ``python`` block parses as valid Python.

    Catches the cheap kind of rot: renamed imports that the README
    still cites. Does not catch logic errors — that's what
    ``test_executable_blocks_run`` is for.
    """
    text = _README.read_text(encoding="utf-8")
    for code, _directives, start_line in _extract_blocks(text):
        _compile_block(code, start_line)


def test_executable_blocks_run() -> None:
    r"""Non-``illustrative`` blocks run end-to-end in a fresh namespace.

    The Quick Start MUST be runnable as written. Blocks that depend on
    an external file (YAML model, database) should declare the
    ``illustrative`` directive on their opening fence:

        \`\`\`python illustrative
        result = parse_semantic_model("model.yaml")
        \`\`\`

    Add a comment in the README pointing at this drift test if you are
    tempted to introduce a new directive.
    """
    text = _README.read_text(encoding="utf-8")
    for code, directives, start_line in _extract_blocks(text):
        if "illustrative" in directives:
            continue
        compiled = _compile_block(code, start_line)
        namespace: dict[str, Any] = {"__name__": "__readme_example__"}
        try:
            exec(compiled, namespace)
        except Exception as exc:  # pragma: no cover — drift signal
            pytest.fail(
                f"README ``python`` block at line {start_line} failed "
                f"to execute: {type(exc).__name__}: {exc}. Either the "
                "example needs the ``illustrative`` directive (if it "
                "now depends on external state) or the code rotted; "
                "fix README.md or the underlying API.",
                pytrace=False,
            )
