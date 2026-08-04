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

"""Shared test helpers: fixture loading and structural lookup."""

import copy
import json
import pathlib
import re

from ossie_cube._common import load_yaml  # src is on sys.path via conftest.py

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def load_fixture(name):
    with open(FIXTURES / name) as fh:
        return fh.read()


def load_fixture_dir(name):
    """Read a fixture Cube model directory as {relative posix path: text}."""
    root = FIXTURES / name
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_text()
    return files


def parse(yaml_str):
    return load_yaml(yaml_str)


_ALIAS_DOT_RE = re.compile(r"\$?\{\s*(?:CUBE|TABLE)\s*\}\s*\.")


def canon_sql(node):
    """Canonicalize the one documented Cube SQL normalization, in place-ish.

    `{CUBE}.column` and a bare `column` are the same thing -- a raw physical column
    of the owning cube -- so the converter no longer stashes the original spelling
    just to reproduce it. Round-trip assertions therefore compare with the alias
    prefix removed.

    Deliberately narrow: `{CUBE.member}` is a *member* reference and means something
    else, so it is left alone (and is still stashed, so it round-trips exactly).
    """
    if isinstance(node, dict):
        return {k: (_ALIAS_DOT_RE.sub("", v)
                    if k == "sql" and isinstance(v, str) else canon_sql(v))
                for k, v in node.items()}
    if isinstance(node, list):
        return [canon_sql(v) for v in node]
    return node


def parse_files(files):
    """Parse a Cube model dict into the form round-trip fidelity is asserted on.

    Comments and key order are not part of the data model, so comparison happens on
    parsed structures. A non-YAML file (a `.js` model preserved verbatim) is
    compared as text.

    Also applies `canon_sql`: the converter no longer stashes a member's exact SQL
    spelling just to reproduce `{CUBE}.column` over a bare `column`, since the two
    mean the same thing and stashing it put noise into every other converter's view
    of the model. That spelling is therefore a documented normalization, not a
    difference worth failing on.
    """
    out = {}
    for name, text in files.items():
        out[name] = (canon_sql(load_yaml(text, name))
                     if name.lower().endswith((".yml", ".yaml")) else text)
    return out


def model_of(ossie_yaml):
    """The sole semantic model of an Ossie document."""
    doc = parse(ossie_yaml)
    assert len(doc["semantic_model"]) == 1
    return doc["semantic_model"][0]


def by_name(items):
    """Index a list of named Ossie objects by `name`."""
    return {item["name"]: item for item in items or []}


def expr_of(item, dialect="ANSI_SQL"):
    """The expression string of an Ossie field or metric in a given dialect."""
    for entry in item["expression"]["dialects"]:
        if entry["dialect"] == dialect:
            return entry["expression"]
    raise AssertionError(f"{item['name']} has no {dialect} expression")


def stash_of(item, vendor="CUBE"):
    """The parsed vendor stash on an Ossie object, or {} when absent."""
    for ext in item.get("custom_extensions") or []:
        if ext["vendor_name"] == vendor:
            data = json.loads(ext["data"])
            data.pop("_v", None)
            return data
    return {}


def canon(obj):
    """Deep-copy with every `custom_extensions[].data` JSON string parsed into a
    dict, so comparisons are insensitive to JSON key order and whitespace."""
    obj = copy.deepcopy(obj)

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "custom_extensions" and isinstance(value, list):
                    for ext in value:
                        if isinstance(ext, dict) and isinstance(ext.get("data"), str):
                            ext["data"] = json.loads(ext["data"])
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(obj)
    return obj


def to_cube_sql(expr, own_cube, members=(), cube_names=(), inline_sql=None):
    """`ossie_expr_to_cube_sql` for a single-cube model, for the direct unit tests.

    The converter prepares its reference tables once per model; these tests care about
    one expression, so the tables are assembled here rather than in each test.
    """
    from ossie_cube._common import ReferenceTables, ossie_expr_to_cube_sql

    names = set(cube_names) | {own_cube}
    tables = ReferenceTables.of(
        cube_names=names,
        references_by_cube={own_cube: members},
        columns_by_cube={own_cube: members},
        inline_sql_by_cube={own_cube: inline_sql or {}})
    return ossie_expr_to_cube_sql(expr, own_cube, tables)
