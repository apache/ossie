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

from pathlib import Path

import pytest

from ossie_ontology.model import OssieOntology
from ossie_ontology.parser import OssieParser

# Test inputs are vendored under tests/fixtures/ so the suite runs even when the
# repo-level examples/ directory isn't present (e.g. from an sdist/wheel or a
# subset checkout). tests/test_examples_in_sync.py guards against drift from the
# canonical examples/ copies.
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

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


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return _FIXTURES_DIR


@pytest.fixture(scope="session")
def flights_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "flights.yaml"


@pytest.fixture
def flights_model(flights_path: Path) -> OssieOntology:
    return OssieParser().parse(flights_path)