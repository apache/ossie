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

"""The standalone converter needs only its packaged root schema, offline."""

import json
import socket
import urllib.request
from copy import deepcopy

import jsonschema
import pytest

from ossie_orionbelt.validation import _OSSIE_SCHEMA_PATH, validate_ossie


@pytest.fixture
def isolated_schema(tmp_path, monkeypatch):
    def reject_request(*args, **kwargs):
        raise AssertionError("Schema validation must not fetch a remote resource")

    monkeypatch.setattr(socket.socket, "connect", reject_request)
    monkeypatch.setattr(urllib.request, "urlopen", reject_request)
    monkeypatch.setattr(jsonschema.validators, "urlopen", reject_request, raising=False)
    path = tmp_path / "ossie-schema.json"
    path.write_bytes(_OSSIE_SCHEMA_PATH.read_bytes())
    assert list(tmp_path.iterdir()) == [path]
    return path


@pytest.fixture
def document():
    return {
        "version": "0.2.0.dev0",
        "name": "purchasing",
        "datasets": [{"name": "orders", "source": "warehouse.public.orders"}],
        "behavior": {
            "namespace": "purchasing",
            "behavior_layer_version": "1.0",
            "actions": [{
                "id": "approve_order",
                "title": "Approve order",
                "kind": "command",
                "effects": [{
                    "entity": "dataset",
                    "mode": "write",
                    "impact_type": "state_transition",
                    "vendor_details": {"system": "ERP"},
                }],
                "vendor_details": {"system": "ERP"},
            }],
            "rules": [{
                "id": "approval_required",
                "title": "Approval required",
                "severity": "error",
                "when": {"operation": "approve_order"},
                "constraint": {"approved": True},
                "message": "An approval is required",
                "vendor_details": {"system": "ERP"},
            }],
            "vendor_details": {"system": "ERP"},
        },
    }


@pytest.mark.parametrize("alias", ["actions", "action_types", "both"])
@pytest.mark.parametrize("empty_lists", [False, True])
def test_behavior_aliases_and_extensions_validate_offline(
    isolated_schema, document, alias, empty_lists
):
    behavior = document["behavior"]
    if empty_lists:
        behavior.update(actions=[], rules=[])
    if alias == "action_types":
        behavior[alias] = behavior.pop("actions")
    elif alias == "both":
        behavior["action_types"] = deepcopy(behavior["actions"])

    # Match consumers that load one schema without a custom resolver/registry.
    jsonschema.Draft202012Validator(json.loads(isolated_schema.read_text())).validate(document)
    result = validate_ossie(document, schema_path=isolated_schema)

    assert result.schema_validation_performed
    assert result.valid, result.schema_errors + result.semantic_errors


@pytest.mark.parametrize(
    "path,value",
    [
        (("namespace",), ""),
        (("namespace",), 1),
        (("behavior_layer_version",), ""),
        (("behavior_layer_version",), 1),
        (("actions",), {}),
        (("rules",), "invalid"),
        (("actions", 0, "id"), ""),
        (("actions", 0, "kind"), "invalid"),
        (("actions", 0, "effects", 0, "entity"), "invalid"),
        (("actions", 0, "effects", 0, "mode"), "invalid"),
        (("actions", 0, "effects", 0, "impact_type"), "invalid"),
        (("rules", 0, "severity"), "invalid"),
        (("rules", 0, "when"), "invalid"),
        (("rules", 0, "constraint"), "invalid"),
        (("rules", 0, "message"), ""),
    ],
)
def test_invalid_behavior_reports_schema_errors_offline(isolated_schema, document, path, value):
    target = document["behavior"]
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = value

    result = validate_ossie(document, schema_path=isolated_schema)

    assert result.schema_validation_performed
    assert not result.valid
    assert result.schema_errors


@pytest.mark.parametrize(
    "path",
    [
        ("namespace",), ("behavior_layer_version",), ("actions",), ("rules",),
        ("actions", 0, "id"), ("actions", 0, "title"),
        ("actions", 0, "effects", 0, "entity"),
        ("actions", 0, "effects", 0, "mode"),
        ("rules", 0, "id"), ("rules", 0, "title"), ("rules", 0, "severity"),
        ("rules", 0, "when"), ("rules", 0, "constraint"), ("rules", 0, "message"),
    ],
)
def test_missing_behavior_fields_are_rejected_offline(isolated_schema, document, path):
    target = document["behavior"]
    for part in path[:-1]:
        target = target[part]
    del target[path[-1]]

    result = validate_ossie(document, schema_path=isolated_schema)

    assert result.schema_validation_performed
    assert not result.valid
    assert result.schema_errors


def test_behavior_remains_optional_offline(isolated_schema, document):
    del document["behavior"]

    result = validate_ossie(document, schema_path=isolated_schema)

    assert result.schema_validation_performed
    assert result.valid, result.schema_errors + result.semantic_errors
