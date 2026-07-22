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

"""Shared test helpers: fixture loading and structural normalization."""

import copy
import json
import pathlib

from osi_omni._common import load_yaml  # src is on sys.path via conftest.py

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def load_fixture(name):
    with open(FIXTURES / name) as fh:
        return fh.read()


def load_fixture_dir(name):
    """Read a fixture Omni model directory as {relative posix path: text}."""
    root = FIXTURES / name
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_text()
    return files


def parse(yaml_str):
    return load_yaml(yaml_str)


def parse_files(files):
    """Parse every YAML file of an Omni model dict for structural comparison."""
    return {name: load_yaml(text, name) for name, text in files.items()}


def canon(obj):
    """Deep-copy with every `custom_extensions[].data` JSON string parsed into a
    dict, so comparisons are insensitive to JSON key order / whitespace."""
    obj = copy.deepcopy(obj)

    def walk(node):
        if isinstance(node, dict):
            for ext in node.get("custom_extensions") or []:
                if isinstance(ext.get("data"), str):
                    ext["data"] = json.loads(ext["data"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(obj)
    return obj


def strip_normalized(osi):
    """Normalize away what the OSI -> Omni -> OSI trip changes by design, so a
    round-trip comparison reflects the documented behavior:

    - `custom_extensions` everywhere: the import adds OMNI stashes (topic set,
      timeframe lists, ...) that a hand-authored source model does not carry.
    - `unique_keys`: no Omni home (dropped with a warning on export).
    - relationship `name`: regenerated as `<from>_to_<to>` on import.
    - relationship `ai_context`: no Omni home (dropped with a warning).
    - model `ai_context` synonyms/examples: no Omni home; only the
      instructions text maps (onto the topic).
    - `dimension: {is_time: false}`: equivalent to an absent `dimension`
      (only `is_time: true` has an Omni form -- `timeframes`).
    - a primary-key column no field covers materializes as a hidden
      dimension on export, so it comes back as an extra (stash-only) field.
    """
    osi = copy.deepcopy(osi)
    for model in osi.get("semantic_model", []):
        model.pop("custom_extensions", None)
        ai = model.get("ai_context")
        if isinstance(ai, dict):
            ai.pop("synonyms", None)
            ai.pop("examples", None)
            if not ai:
                model.pop("ai_context")
        for ds in model.get("datasets", []):
            ds.pop("custom_extensions", None)
            ds.pop("unique_keys", None)
            ai = ds.get("ai_context")
            if isinstance(ai, dict):
                ai.pop("synonyms", None)
                if not ai:
                    ds.pop("ai_context")
            fields = ds.get("fields", []) or []
            for field in fields:
                field.pop("custom_extensions", None)
                if field.get("dimension") == {"is_time": False}:
                    field.pop("dimension")
            # Drop backfilled key fields: a bare-column field named after a
            # primary_key column, carrying nothing but its expression.
            pk = set(ds.get("primary_key") or [])
            ds_fields = [
                f for f in fields
                if not (f["name"] in pk and set(f) <= {"name", "expression"})
            ]
            if ds_fields:
                ds["fields"] = ds_fields
            else:
                ds.pop("fields", None)
        for rel in model.get("relationships", []) or []:
            rel["name"] = f"{rel['from']}_to_{rel['to']}"
            rel.pop("ai_context", None)
            rel.pop("custom_extensions", None)
        for metric in model.get("metrics", []) or []:
            metric.pop("custom_extensions", None)
        # Metric order is not semantic; the import regroups metrics by the view
        # their measure lives on.
        if model.get("metrics"):
            model["metrics"].sort(key=lambda m: m["name"])
    return osi
