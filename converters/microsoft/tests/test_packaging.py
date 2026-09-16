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

"""Guards on how this converter is packaged.

The converter is distributed, so its layout is part of its contract rather than
an implementation detail. Every sibling Python converter in this repository ships
a single ``src/<package>/`` directory, and a flat ``src/*.py`` layout silently
breaks three things at once: the wheel publishes top-level modules that squat on
generic names like ``cli`` in every environment that installs it, the console
script fails to resolve, and ``import ossie_microsoft`` stops working even though
the package builds without complaint.

None of that is visible from the unit tests, which import modules through
``pythonpath = ["src"]`` and therefore pass either way. These tests close that
gap so the layout cannot regress unnoticed.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - exercised only on Python 3.10
    import tomli as tomllib

PACKAGE = "ossie_microsoft"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
PACKAGE_DIR = SRC / PACKAGE

#: Modules that live inside the package and must never be importable top-level.
MODULES = frozenset(
    {
        "_common",
        "_sql_to_dax",
        "cli",
        "engine",
        "ossie_to_semantic_model",
        "semantic_model_to_ossie",
        "tom",
    }
)


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with (PROJECT_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_package_lives_in_its_own_directory() -> None:
    assert PACKAGE_DIR.is_dir(), (
        f"expected the package at {PACKAGE_DIR}; every Python converter in this "
        "repository uses a src/<package>/ layout"
    )
    assert (PACKAGE_DIR / "__init__.py").is_file()


def test_no_loose_modules_directly_under_src() -> None:
    stray = sorted(path.name for path in SRC.glob("*.py"))
    assert stray == [], (
        f"{stray} sit directly under src/ and would be published as top-level "
        f"modules; move them into src/{PACKAGE}/"
    )


def test_every_module_is_present() -> None:
    found = {path.stem for path in PACKAGE_DIR.glob("*.py")} - {"__init__", "__main__"}
    assert found == set(MODULES)


@pytest.mark.parametrize("module", sorted(MODULES) + ["__init__"])
def test_sibling_imports_are_relative(module: str) -> None:
    """A flattened package is usually re-flattened by rewriting these imports."""
    tree = ast.parse((PACKAGE_DIR / f"{module}.py").read_text(encoding="utf-8"))
    absolute = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 0
        and node.module in MODULES
    ]
    assert absolute == [], (
        f"{module}.py imports {absolute} absolutely; sibling modules must be "
        "imported relatively (e.g. `from ._common import ...`) so the package is "
        "not dependent on src/ being on sys.path"
    )


def test_public_api_is_importable_through_the_package() -> None:
    module = pytest.importorskip(PACKAGE)
    for name in (
        "convert_semantic_model_to_ossie",
        "convert_ossie_to_semantic_model",
    ):
        assert hasattr(module, name), f"{PACKAGE}.{name} is not exported"


def test_console_script_points_at_the_package(pyproject: dict) -> None:
    scripts = pyproject["project"]["scripts"]
    assert scripts == {"ossie-microsoft": f"{PACKAGE}.cli:main"}


def test_wheel_target_declares_the_package(pyproject: dict) -> None:
    packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]
    assert packages == [f"src/{PACKAGE}"], (
        "the wheel target must name the package directory; listing individual "
        ".py files publishes them as top-level modules"
    )


def test_coverage_measures_the_package(pyproject: dict) -> None:
    assert pyproject["tool"]["coverage"]["run"]["source"] == [PACKAGE]


def test_authors_and_maintainers_are_declared(pyproject: dict) -> None:
    project = pyproject["project"]
    assert project["authors"] == [
        {"name": "Apache Software Foundation", "email": "dev@ossie.apache.org"}
    ], "authors stays the ASF, matching every sibling converter"
    assert project["maintainers"], "the individual contributors should be credited"
    for person in project["maintainers"]:
        assert person.get("name") and person.get("email")


def test_built_wheel_ships_only_the_package(tmp_path: Path) -> None:
    """The end-to-end check: what a user actually installs.

    Declarations can be right while the built artifact is wrong, so this builds
    the wheel and inspects it rather than trusting ``pyproject.toml``.
    """
    pytest.importorskip("build", reason="the 'build' package is required")
    pytest.importorskip("hatchling", reason="the build backend is required")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "--outdir",
            str(tmp_path),
            str(PROJECT_ROOT),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout

    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"

    with zipfile.ZipFile(wheels[0]) as archive:
        names = archive.namelist()

    shipped = {
        name.split("/", 1)[0] for name in names if not name.startswith(f"{PACKAGE}/")
    }
    stowaways = {name for name in shipped if not name.endswith(".dist-info")}
    assert stowaways == set(), (
        f"the wheel ships {sorted(stowaways)} outside the package; installing it "
        "would pollute site-packages with generic top-level names"
    )
    assert f"{PACKAGE}/__init__.py" in names, (
        "the wheel does not contain the package __init__, so `import "
        f"{PACKAGE}` fails after install"
    )
