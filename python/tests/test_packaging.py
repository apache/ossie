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

"""What an Apache release requires of the built distributions.

A wheel and an sdist are convenience binaries, and the ASF release policy asks
each package to provide a LICENSE and a NOTICE that account for its exact
content, in the location the format keeps licensing materials. `license-files`
is how a PEP 639 build declares them; these tests pin the inputs it needs,
since a build that cannot find a declared file does not fail, it just ships
without it.
"""

import tomllib
from pathlib import Path

import pytest

_PACKAGE = Path(__file__).parents[1]
_REPO = Path(__file__).parents[2]

_DISCLAIMER_OPENING = (
    "Apache Ossie is an effort undergoing incubation at the Apache Software\n"
    "Foundation (ASF), sponsored by the Apache Incubator PMC."
)


@pytest.fixture
def pyproject() -> dict:
    return tomllib.loads((_PACKAGE / "pyproject.toml").read_text(encoding="utf-8"))


def test_declares_its_license_files(pyproject: dict) -> None:
    """Both files are declared, and as names rather than paths out of here.

    A glob that leaves the project directory is ignored rather than refused, so
    `../LICENSE` builds a package carrying no licensing material at all.
    """
    declared = pyproject["project"]["license-files"]

    assert declared == ["LICENSE", "NOTICE"]
    assert all(".." not in entry for entry in declared)


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE"])
def test_the_declared_license_file_is_present(name: str) -> None:
    assert (_PACKAGE / name).is_file(), f"python/{name} is declared but missing"


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE"])
def test_the_declared_license_file_matches_the_repository(name: str) -> None:
    """Each copy is the repository's own, since the package bundles only its source.

    Should the package ever carry third-party code, these have to diverge to
    account for it, and the policy forbids describing what is not there either
    way. That is a decision to make here deliberately, not a mismatch to let
    drift.
    """
    assert (_PACKAGE / name).read_text(encoding="utf-8") == (
        _REPO / name
    ).read_text(encoding="utf-8"), (
        f"python/{name} and {name} have diverged. If the package now bundles "
        f"something the repository root does not, say so in python/{name} and "
        f"relax this test; otherwise copy the root file over."
    )


def test_the_project_description_carries_the_incubating_disclaimer(pyproject: dict) -> None:
    """The readme is the PyPI project description, where the incubator wants this.

    The disclaimer must be displayed where the artifacts are made available, and
    PyPI shows this text. Compared against the repository's own DISCLAIMER so the
    two cannot drift.
    """
    readme = _PACKAGE / pyproject["project"]["readme"]
    description = readme.read_text(encoding="utf-8")

    assert _DISCLAIMER_OPENING in (_REPO / "DISCLAIMER").read_text(encoding="utf-8")
    assert (_REPO / "DISCLAIMER").read_text(encoding="utf-8").strip() in description
    assert "(incubating)" in description
