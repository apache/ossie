"""Invariant tests for synthetic naming.

The Foundation routes every synthetic name (CTE aliases, mangled join
keys, anonymous aggregates) through :mod:`osi.planning.prefixes`. This
test surfaces regressions where a literal sneaks back into emitter
modules — the kind of mistake that breaks the
``ARCHITECTURE.md §6`` byte-identical SQL invariant the moment the
prefix changes.

The check is a string scan on purpose: import-linter cannot enforce
"do not embed literal prefix" because it is a value-level concern.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Modules that legitimately define the prefix vocabulary.
_OWNERS: frozenset[str] = frozenset(
    {
        "src/osi/planning/prefixes.py",
        # Tests and docs may mention the prefix in commentary.
    }
)

# Patterns that indicate the step CTE alias is being constructed or
# matched directly instead of via :mod:`osi.planning.prefixes`. We
# look for two shapes that flagged real bugs:
#   - ``f"step_{...}"`` / ``"step_%d" %`` formatting of the alias
#   - ``.startswith("step_")`` / ``"step_" in`` reachability checks
# Plain occurrences of ``step_id``, ``step_count``, etc. are excluded
# by requiring the literal to end immediately or contain a format
# placeholder.
_FORBIDDEN_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r'(?<![A-Za-z_])f["\']step_\{'),
        "step alias formatting — use prefixes.step_alias",
    ),
    (
        re.compile(r'["\']step_["\']'),
        "step alias prefix literal — use prefixes.is_step_alias",
    ),
    (
        re.compile(r'["\']step_%[ds]'),
        "step alias %-format — use prefixes.step_alias",
    ),
)


def _osi_python_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and parent.name == "osi_python":
            return parent
    raise RuntimeError("could not locate osi_python project root")


def _python_files() -> list[Path]:
    root = _osi_python_root()
    src = root / "src"
    return sorted(src.rglob("*.py"))


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.name))
def test_no_step_prefix_literal_outside_owner_modules(path: Path) -> None:
    rel = path.resolve().relative_to(_osi_python_root().resolve()).as_posix()
    if rel in _OWNERS:
        return
    text = path.read_text(encoding="utf-8")
    for pattern, message in _FORBIDDEN_PATTERNS:
        match = pattern.search(text)
        assert match is None, (
            f"{rel}: forbidden literal {match.group(0)!r} — {message}. "
            "Route synthetic names through osi.planning.prefixes."
        )
