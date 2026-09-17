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

"""Shared fixtures for the ontology converter test suite."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ossie_ontology.model import OssieOntology
from ossie_ontology.parser import OssieParser

# Test inputs are vendored under tests/fixtures/ so the suite runs even when the
# repo-level examples/ directory isn't present (e.g. from an sdist/wheel or a
# subset checkout). tests/test_examples_in_sync.py guards against drift from the
# canonical examples/ copies.
#
# Sorted by what a file is for, because the suites read them differently:
#   specs/       full ontologies the snapshot suites render end to end
#   behaviours/  one spec per behaviour, asserted directly rather than snapshotted
#   validation/  formulas that must parse, or must fail with a stated message
#   palantir/    a vendor export, in that vendor's own layout
# Harness configuration is not a test input and lives in tests/config/.
_TESTS_DIR = Path(__file__).resolve().parent
_FIXTURES_DIR = _TESTS_DIR / "fixtures"
_SPECS_DIR = _FIXTURES_DIR / "specs"
_BEHAVIOURS_DIR = _FIXTURES_DIR / "behaviours"

# The snapshot files under tests/snapshots/ carry the ASF license header inline,
# so that a source-release audit (Apache RAT) finds a header on every file in the
# tree without needing an exclude list. pytest-snapshot compares the whole file
# byte-for-byte, so the header has to be part of the asserted value; the snapshot
# fixture below applies it, which also means `pytest --snapshot-update` reproduces
# it and it cannot drift.
#
# Both snapshot formats tolerate it: '#' starts a comment in YAML, and the
# structure snapshot is free-form text.
LICENSE_HEADER = """\
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

"""


@pytest.fixture
def snapshot(snapshot):
    """pytest-snapshot's fixture, with the ASF license header applied for free.

    Overrides the plugin fixture so tests assert only their own payload and the
    header stays in one place. Applies on both paths: comparison prepends it to
    the expected value, and --snapshot-update writes it into the file.

    Note: only assert_match is wrapped, since that is all this suite uses. A test
    reaching for assert_match_dir would need the same treatment.
    """
    assert_match = snapshot.assert_match

    def assert_match_with_license(value: str | bytes, snapshot_name: str | Path) -> None:
        __tracebackhide__ = True
        if isinstance(value, bytes):
            assert_match(LICENSE_HEADER.encode() + value, snapshot_name)
        else:
            assert_match(LICENSE_HEADER + value, snapshot_name)

    snapshot.assert_match = assert_match_with_license
    return snapshot


# `relationalai` resolves its config the first time a Model is constructed, and
# searches the CWD, ~/.rai, ~/.snowflake and ~/.dbt in turn. On a contributor's
# machine that search usually finds a real, credentialed config — so the Ossie ->
# RelationalAI tests would build their models against a live warehouse without
# anyone noticing. Pinning the variable at import time, before any test can
# construct a Model, forces the offline fixture instead.
#
# Set unconditionally: the pin matters most on exactly the machines where a real
# config exists, and the tests never need one.
os.environ["RAI_CONFIG_FILE_PATH"] = str(_TESTS_DIR / "config" / "raiconfig.yaml")


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return _FIXTURES_DIR


@pytest.fixture(scope="session")
def specs_dir() -> Path:
    """Full ontologies: flights, retail, tpch."""
    return _SPECS_DIR


@pytest.fixture(scope="session")
def behaviours_dir() -> Path:
    """One spec per behaviour, each asserted on directly."""
    return _BEHAVIOURS_DIR


@pytest.fixture(scope="session")
def validation_dir() -> Path:
    """Formulas that must parse, or must fail with a stated message."""
    return _FIXTURES_DIR / "validation"


@pytest.fixture(scope="session")
def flights_path(fixtures_dir: Path) -> Path:
    """The reference corpus: read by the spec snapshots, the PyRel snapshots, the
    parser round-trip tests, and test_reasoner for its composite reference scheme
    (Runway is identified by designator *and* airport).

    Its note lives here rather than in the file, because the file is the vendored
    copy of examples/flights.yaml and test_examples_in_sync holds the two
    byte-identical -- retail.yaml and tpch.yaml are test-only and carry theirs
    inline.
    """
    return _SPECS_DIR / "flights.yaml"


@pytest.fixture
def flights_model(flights_path: Path) -> OssieOntology:
    return OssieParser().parse(flights_path)

# The corpus the snapshot suites run over, and the single source of that list --
# tests/relationalai/test_converter.py imports it rather than repeating it. `flights`
# also keeps the named fixtures above, since several tests assert on it directly.
SPEC_NAMES = ["flights", "retail", "tpch"]


@pytest.fixture
def spec_model(request) -> OssieOntology:
    """The parsed ontology for the spec named by an indirect parametrize.

    Formulas stay as raw text: these suites snapshot the shape of the spec, and
    parsing them would pull the optional `relationalai` extra in through the
    formula factories for no gain.
    """
    return OssieParser().parse(_SPECS_DIR / f"{request.param}.yaml")


# `relationalai` resolves its config when a Model is constructed and will not
# start without one, so the pin above is load-bearing, not belt-and-braces --
# removing it does not make the suite offline, it makes it fail. What keeps the
# suite offline is that nothing it does needs a warehouse: models are compiled
# to the metamodel in-process and decompiled back to source.
#
# That is a property worth failing on rather than documenting. Every socket
# connection is refused for the duration of a test, so a test that reaches for a
# warehouse -- by using the default `warehouse_table` provider, say, or its own
# config -- says so immediately instead of quietly succeeding on the machine of
# whoever happens to have credentials.
@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    import socket

    def deny(*args, **kwargs):
        raise RuntimeError(
            "This suite runs offline: a test tried to open a network connection. "
            "Conversion and compilation happen in-process; if you need a table, "
            "declare its columns with `declared_table` rather than reading them "
            "from a warehouse."
        )

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
