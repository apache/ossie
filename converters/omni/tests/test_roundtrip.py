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

"""Fixture-based round-trip tests.

- Omni -> OSI -> Omni must be lossless (the stash carries everything).
- OSI -> Omni -> OSI must be identical up to the documented normalizations
  (see _util.strip_normalized).
- Every OSI document the importer emits must validate against the core-spec
  JSON schema (skipped when jsonschema is not installed).
"""

import warnings

import pytest

from osi_omni import convert_omni_to_osi, convert_osi_to_omni
from _util import (
    REPO_ROOT,
    load_fixture,
    load_fixture_dir,
    parse,
    parse_files,
    strip_normalized,
)


def _quiet(fn, *args, **kwargs):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return fn(*args, **kwargs)


@pytest.mark.parametrize("fixture", ["fixtureA_omni", "fixtureB_omni", "tpcds_omni"])
def test_omni_roundtrip_is_lossless(fixture):
    files = load_fixture_dir(fixture)
    osi = _quiet(convert_omni_to_osi, files)
    files2 = _quiet(convert_osi_to_omni, osi)
    assert parse_files(files2) == parse_files(files)


@pytest.mark.parametrize("path", [
    "fixtureA_osi.yaml",
    str(REPO_ROOT / "examples" / "tpcds_semantic_model.yaml"),
])
def test_osi_roundtrip_up_to_documented_normalizations(path):
    if "/" in path:
        with open(path) as fh:
            osi_yaml = fh.read()
    else:
        osi_yaml = load_fixture(path)
    files = _quiet(convert_osi_to_omni, osi_yaml)
    osi2 = _quiet(convert_omni_to_osi, files)
    original = strip_normalized(parse(osi_yaml))
    # Foreign-vendor extensions are dropped on export (documented); OSI-side
    # normalization strips all custom_extensions from both sides.
    assert strip_normalized(parse(osi2)) == original


@pytest.mark.parametrize("fixture", ["fixtureA_omni", "fixtureB_omni", "tpcds_omni"])
def test_imported_osi_validates_against_core_spec_schema(fixture):
    jsonschema = pytest.importorskip("jsonschema")
    import json

    with open(REPO_ROOT / "core-spec" / "osi-schema.json") as fh:
        schema = json.load(fh)
    osi = _quiet(convert_omni_to_osi, load_fixture_dir(fixture))
    jsonschema.validate(parse(osi), schema)
