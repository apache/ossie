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

"""Shared test helpers: fixture loading, warning capture, and schema validation."""

import json
import sys
import warnings
from pathlib import Path

import pytest
import yaml

FIXTURES = Path(__file__).parent / "fixtures"

# The converter is checked against the specification's own JSON Schema, which lives in
# the repository rather than in this package.
SCHEMA_PATH = Path(__file__).parents[3] / "core-spec" / "ossie-schema.json"
EXAMPLES = Path(__file__).parents[3] / "examples"

# The other converters' own test fixtures, used read-only by the cross-vendor
# interop sweep (see test_cross_vendor.py).
CONVERTERS = Path(__file__).parents[2]

if str(Path(__file__).parents[1] / "src") not in sys.path:
    sys.path.insert(0, str(Path(__file__).parents[1] / "src"))


def fixture(name):
    """Read a fixture file as text."""
    return (FIXTURES / name).read_text()


def example(name):
    """Read a model from the repository's `examples/` directory."""
    return (EXAMPLES / name).read_text()


def convert_quietly(func, *args, **kwargs):
    """Run a converter, returning (output, [warning messages])."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        output = func(*args, **kwargs)
    return output, [str(w.message) for w in caught]


def model_of(ossie_yaml):
    """The first semantic model in an Apache Ossie document."""
    return yaml.safe_load(ossie_yaml)["semantic_model"][0]


def solid_model_of(solid_yaml):
    """The semantic model in a Solid document."""
    return yaml.safe_load(solid_yaml)["semantic_model"]


def by_name(items):
    """Index a list of named mappings by `name`."""
    return {item["name"]: item for item in items or []}


def normalized_yaml(text):
    """Parse YAML and normalize block-scalar whitespace.

    Solid writes descriptions as block scalars, so the same text can differ between two
    renderings only by trailing newlines and line-end whitespace. That is not a
    conversion difference, so it is normalized away before comparing.
    """

    def walk(node):
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        if isinstance(node, str):
            return "\n".join(line.rstrip() for line in node.strip().splitlines())
        return node

    return walk(yaml.safe_load(text))


def dataset_of(model, name):
    return by_name(model["datasets"])[name]


def field_of(model, dataset, field):
    return by_name(dataset_of(model, dataset).get("fields"))[field]


def stash_of(obj):
    """The SOLID custom_extensions payload on an object, or {} when absent."""
    for ext in obj.get("custom_extensions") or []:
        if ext["vendor_name"] == "SOLID":
            data = json.loads(ext["data"])
            data.pop("_v", None)
            return data
    return {}


def expression_of(obj):
    """The single dialect expression on a field or metric."""
    dialects = obj["expression"]["dialects"]
    assert len(dialects) == 1, "converter emits exactly one dialect per expression"
    return dialects[0]["expression"]


@pytest.fixture(scope="session")
def ossie_validator():
    """A Draft 2020-12 validator for the Apache Ossie core schema.

    Skips the whole test if `jsonschema` is not installed, so the suite still runs
    without the dev extra.
    """
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA_PATH.read_text())
    return jsonschema.Draft202012Validator(schema)


@pytest.fixture
def assert_valid_ossie(ossie_validator):
    """Assert that a converted document satisfies the Apache Ossie JSON Schema."""

    def _assert(ossie_yaml):
        document = yaml.safe_load(ossie_yaml)
        errors = sorted(ossie_validator.iter_errors(document), key=lambda e: list(e.path))
        assert not errors, "\n".join(
            f"{'/'.join(str(p) for p in e.path)}: {e.message}" for e in errors
        )

    return _assert
