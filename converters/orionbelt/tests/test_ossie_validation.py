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

import sys

import pytest

from ossie_orionbelt.validation import _OSSIE_SCHEMA_PATH, validate_ossie


@pytest.fixture(params=["available", "missing_file", "missing_package"])
def schema_path(request, tmp_path, monkeypatch):
    if request.param == "missing_file":
        return tmp_path / "missing-schema.json"
    if request.param == "missing_package":
        monkeypatch.setitem(sys.modules, "jsonschema", None)
    return _OSSIE_SCHEMA_PATH


@pytest.mark.parametrize(
    "wrapper",
    [None, [], {}, [{"name": "legacy", "datasets": []}]],
)
@pytest.mark.parametrize("include_root_model", [False, True])
def test_legacy_wrapper_is_always_invalid(schema_path, wrapper, include_root_model):
    document = {"version": "0.2.0.dev0", "semantic_model": wrapper}
    if include_root_model:
        document.update(name="m", datasets=[{"name": "t", "source": "a.b.c"}])

    result = validate_ossie(document, schema_path=schema_path)

    assert not result.valid
    assert any("[LEGACY_WRAPPER]" in error for error in result.semantic_errors)


@pytest.mark.parametrize("document", [None, [], "not a mapping", 42])
def test_non_mapping_document_is_always_invalid(schema_path, document):
    result = validate_ossie(document, schema_path=schema_path)

    assert not result.valid
    assert any("[INVALID_DOCUMENT]" in error for error in result.semantic_errors)


def test_valid_flat_document_can_be_checked_without_schema(schema_path):
    document = {
        "version": "0.2.0.dev0",
        "name": "m",
        "datasets": [{"name": "t", "source": "a.b.c"}],
    }

    result = validate_ossie(document, schema_path=schema_path)

    assert result.valid
