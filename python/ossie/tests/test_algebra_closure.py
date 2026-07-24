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

"""Mechanical guard for the closed-algebra contract.

``CalculationState`` is the algebra's carrier of correctness. Its
``__post_init__`` validates the state invariants, but the *closure* rule is
architectural and cannot be expressed in the type system: a ``CalculationState``
may be **constructed only by the algebra's operator implementations**. Every
other module (present and future — parsing, planning, codegen, and any caller)
must obtain states by *composing operators*, never by hand-building one.

This test walks the whole ``ossie`` source tree with the ``ast`` module and
fails if ``CalculationState(...)`` is instantiated anywhere outside the
allow-listed operator modules. See ``src/ossie/algebra/ARCHITECTURE.md``.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "ossie"

# The only modules permitted to construct a CalculationState: the algebra
# operator implementations. Adding a module here must be a deliberate,
# reviewed decision — that is the whole point of this gate.
_ALLOWED = {
    Path("algebra/operations.py"),
    Path("algebra/joins.py"),
    Path("algebra/composition.py"),
}

_GUARDED_TYPE = "CalculationState"


def _constructs_guarded_type(tree: ast.AST) -> bool:
    """True if ``tree`` calls ``CalculationState(...)`` directly."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        if name == _GUARDED_TYPE:
            return True
    return False


def test_calculation_state_constructed_only_by_operators() -> None:
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        rel = path.relative_to(_SRC)
        if rel in _ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _constructs_guarded_type(tree):
            offenders.append(str(rel))

    assert not offenders, (
        "CalculationState must be constructed only by the algebra operator "
        f"modules {sorted(str(p) for p in _ALLOWED)}; found direct construction "
        f"in: {offenders}. Compose operators instead of hand-building state — "
        "see src/ossie/algebra/ARCHITECTURE.md."
    )


def test_allowed_operator_modules_exist() -> None:
    """The allow-list must not rot: every listed module must exist."""
    missing = [str(p) for p in _ALLOWED if not (_SRC / p).is_file()]
    assert not missing, f"allow-listed operator modules are missing: {missing}"
