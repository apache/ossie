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

import json

import pytest
import yaml

from ossie import OssieSemanticModel


def _serialize(document: OssieSemanticModel, format: str, **kwargs) -> dict:
    if format == "json":
        return json.loads(document.to_ossie_json(**kwargs))
    return yaml.safe_load(document.to_ossie_yaml(**kwargs))


@pytest.mark.parametrize("format", ["json", "yaml"])
@pytest.mark.parametrize(
    "path",
    [
        (),
        ("datasets", 0),
        ("datasets", 0, "fields", 0),
        ("metrics", 0),
        ("relationships", 0),
    ],
    ids=["document", "dataset", "field", "metric", "relationship"],
)
def test_null_ai_context_extras_round_trip(document_data: dict, format: str, path: tuple) -> None:
    document_data["relationships"] = [
        {
            "name": "event_parent",
            "from": "events",
            "to": "events",
            "from_columns": ["occurred_at"],
            "to_columns": ["occurred_at"],
        }
    ]
    target = document_data
    for key in path:
        target = target[key]
    target["ai_context"] = {
        "instructions": "Use this metric",
        "vendor_hint": None,
        "nested": {"hint": None},
        "items": [None],
        "enabled": False,
        "count": 0,
        "label": "",
    }

    if format == "json":
        document = OssieSemanticModel.from_ossie_json(json.dumps(document_data))
    else:
        document = OssieSemanticModel.from_ossie_yaml(yaml.safe_dump(document_data))

    # Unknown properties permit null; omitted declared properties still stay absent.
    assert _serialize(document, format) == document_data


@pytest.mark.parametrize("format", ["json", "yaml"])
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, {"hint": None, "other": None, "instructions": "Use this metric"}),
        ({"include": {"ai_context": {"hint"}}}, {"hint": None}),
        ({"include": {"ai_context": {"hint": True}}}, {"hint": None}),
        (
            {"include": {"ai_context": {"__all__": True}}},
            {"hint": None, "other": None, "instructions": "Use this metric"},
        ),
        ({"include": {"ai_context": {"instructions"}}}, {"instructions": "Use this metric"}),
        (
            {"exclude": {"ai_context": {"hint"}}},
            {"other": None, "instructions": "Use this metric"},
        ),
        (
            {"exclude": {"ai_context": {"hint": True}}},
            {"other": None, "instructions": "Use this metric"},
        ),
        (
            {"exclude": {"ai_context": {"hint": ...}}},
            {"other": None, "instructions": "Use this metric"},
        ),
        (
            {"exclude": {"ai_context": {"hint": {"nested"}}}},
            {"hint": None, "other": None, "instructions": "Use this metric"},
        ),
        ({"exclude": {"ai_context": {"__all__": True}}}, {}),
        (
            {"exclude_unset": True},
            {"hint": None, "other": None, "instructions": "Use this metric"},
        ),
        (
            {"exclude_defaults": True},
            {"hint": None, "other": None, "instructions": "Use this metric"},
        ),
    ],
)
def test_null_ai_context_extras_respect_selection(
    document_data: dict, format: str, kwargs: dict, expected: dict
) -> None:
    document_data["ai_context"] = {
        "instructions": "Use this metric",
        "hint": None,
        "other": None,
    }
    document = OssieSemanticModel.model_validate(document_data)

    assert _serialize(document, format, **kwargs)["ai_context"] == expected


@pytest.mark.parametrize("format", ["json", "yaml"])
def test_null_ai_context_extra_does_not_restore_excluded_context(
    document_data: dict, format: str
) -> None:
    document_data["ai_context"] = {"hint": None}
    document = OssieSemanticModel.model_validate(document_data)

    assert "ai_context" not in _serialize(document, format, exclude={"ai_context"})


@pytest.mark.parametrize("format", ["json", "yaml"])
def test_explicit_none_override_still_includes_declared_properties(
    document_data: dict, format: str
) -> None:
    document_data["ai_context"] = {"hint": None}
    document = OssieSemanticModel.model_validate(document_data)

    assert _serialize(document, format, exclude_none=False)["ai_context"] == {
        "instructions": None,
        "synonyms": None,
        "examples": None,
        "hint": None,
    }
