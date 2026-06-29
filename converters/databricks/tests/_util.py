"""Shared test helpers: fixture loading and structural normalization."""

import copy
import json
import pathlib

from osi_databricks._common import load_yaml  # src is on sys.path via conftest.py

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def load_fixture(name):
    with open(FIXTURES / name) as fh:
        return fh.read()


def parse(yaml_str):
    # YAML 1.2 booleans so a join `on:` key parses as the string "on" (matching the
    # converter's own load/dump), not the YAML-1.1 boolean True.
    return load_yaml(yaml_str)


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


def strip_dropped(osi):
    """Normalize away what the OSI -> MV -> OSI trip changes, so a round-trip
    comparison reflects the documented limitations. Besides outright losses (model
    name, descriptions), a declared key transforms across the trip: `primary_key` ->
    `rely.at_most_one_match` (MV) -> `unique_keys` + a relationship rely-stash. We drop
    both key forms and the relationship stash so the key info is compared as 'gone'."""
    osi = copy.deepcopy(osi)
    for model in osi.get("semantic_model", []):
        model.pop("name", None)         # MV carries no model name
        model.pop("description", None)  # model + fact descriptions merge into one comment
        for ds in model.get("datasets", []):
            ds.pop("primary_key", None)
            ds.pop("unique_keys", None)
            ds.pop("description", None)  # no per-source comment in single-source MV
        for rel in model.get("relationships", []):
            rel.pop("custom_extensions", None)  # derived rely-stash from a declared key
    return osi
