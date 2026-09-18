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

"""Shared helpers for the Apache Ossie <-> Databricks Metric View converters.

Both directions are pure offline YAML transforms. The only cross-cutting concerns
live here: version constants, the dialect preference order, the `custom_extensions`
stash protocol, and small SQL-string helpers.
"""

import json
import re

import yaml

# Apache Ossie semantic model spec version this converter targets (see core-spec).
#
# NOTE: this is an exact-match check (see convert_ossie_to_metric_view). Unlike the
# dbt converter, this spoke intentionally has no `apache-ossie` package dependency, so
# nothing updates this automatically -- it MUST be bumped in lockstep with the
# `version` in `core-spec/` whenever the spec version moves, or the converter will
# reject otherwise-valid Apache Ossie files.
OSSIE_VERSION = "0.2.0.dev0"

# Databricks Unity Catalog Metric View YAML version. Only 1.1 supports joins,
# per-column comments, synonyms, and the format/window/parameters surface.
MV_VERSION = "1.1"

# Vendor id used for the `custom_extensions` stash and for dialect selection.
VENDOR = "DATABRICKS"

# Expression dialects this converter understands, in preference order.
DIALECT_DATABRICKS = "DATABRICKS"
DIALECT_ANSI = "ANSI_SQL"

# Metric Views cap the number of synonyms per column.
SYNONYM_LIMIT = 10

# Bump when the shape of a stashed `data` blob changes.
STASH_VERSION = 1

# Metric View join cardinalities (the only two values v1.1 defines). Apache Ossie has no
# cardinality field; the value is implied by relationship direction -- `from` is the
# many side, `to` is the one side -- so the converter derives it from / writes it
# into the from/to orientation rather than relying on a dedicated field.
CARD_MANY_TO_ONE = "many_to_one"
CARD_ONE_TO_MANY = "one_to_many"

# Model-level stash key recording which dataset was the Metric View `source` (its
# grain). Needed only when a one_to_many join puts the source on a relationship's
# `to` side, where the natural FK-sink heuristic would otherwise pick the wrong
# fact on re-export. Absent for plain many-to-one stars, so they stay clean.
STASH_SOURCE_KEY = "source_dataset"

# A bare SQL identifier (single column reference), e.g. `c_name`. Used to decide
# whether an expression can be safely alias-prefixed on export / de-prefixed on
# import.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ConversionError(Exception):
    """Raised when an input cannot be converted."""


def require(obj, key, what):
    """Return `obj[key]`, or raise a clean ConversionError if it's missing/empty -- so
    malformed input surfaces as an error message rather than a raw KeyError traceback.

    Presence is tested by key (not truthiness), so a legitimately falsy value such as
    `0` or `False` is returned; a missing key, a null, or an empty/whitespace string is
    rejected.
    """
    if not isinstance(obj, dict) or key not in obj or obj[key] is None:
        raise ConversionError(f"{what} is missing required '{key}'")
    value = obj[key]
    if isinstance(value, str) and not value.strip():
        raise ConversionError(f"{what} has an empty '{key}'")
    return value


def require_str(obj, key, what):
    """Like require(), but also enforce the value is a string -- so a non-string scalar
    (e.g. a YAML number for a name or expression) raises a clean ConversionError instead
    of crashing later in a string operation."""
    value = require(obj, key, what)
    if not isinstance(value, str):
        raise ConversionError(
            f"{what}: '{key}' must be a string, got {type(value).__name__}")
    return value


# YAML 1.1 (PyYAML's default) treats bare on/off/yes/no/y/n as booleans, so a metric
# view join's `on:` key would parse as the boolean True and silently lose the join
# condition. Databricks (Jackson) uses YAML 1.2 booleans (only true/false). The Loader
# below uses 1.2 semantics, so it reads DBR's bare `on:` (and any "on"/"off" value) as a
# string. The Dumper additionally force-quotes those tokens on output (see below), so the
# YAML it emits round-trips the same way through a YAML 1.1 reader too (e.g. stock
# yaml.safe_load) rather than turning an "on"/"off" synonym/label into a boolean.
class _Yaml12Loader(yaml.SafeLoader):
    """SafeLoader with YAML 1.2 boolean semantics."""


class _Yaml12Dumper(yaml.SafeDumper):
    """SafeDumper with YAML 1.2 boolean semantics."""


_YAML12_BOOL = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")
for _cls in (_Yaml12Loader, _Yaml12Dumper):
    # Drop the YAML 1.1 bool resolver (yes/no/on/off/y/n) and re-add a 1.2 one.
    _cls.yaml_implicit_resolvers = {
        ch: [(tag, rx) for (tag, rx) in resolvers if tag != "tag:yaml.org,2002:bool"]
        for ch, resolvers in _cls.yaml_implicit_resolvers.items()
    }
    _cls.add_implicit_resolver("tag:yaml.org,2002:bool", _YAML12_BOOL, list("tTfF"))


# Force-quote string scalars that a YAML 1.1 reader would otherwise interpret as booleans
# (yes/no/on/off/y/n/true/false, any case). Number- and null-like strings are already
# quoted by PyYAML's surviving resolvers; only these bool tokens need it. Without this, a
# synonym/label/comment like "on" emits bare and a 1.1 consumer reads it back as `True`.
_YAML11_BOOL_STRS = frozenset(
    variant
    for word in ("y", "n", "yes", "no", "on", "off", "true", "false")
    for variant in (word, word.capitalize(), word.upper())
)


def _represent_str(dumper, data):
    style = "'" if data in _YAML11_BOOL_STRS else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Yaml12Dumper.add_representer(str, _represent_str)


def load_yaml(text):
    """Parse YAML with 1.2 boolean semantics, so a join `on:` key stays the string
    `on` rather than becoming the boolean True. A syntax error is surfaced as a
    ConversionError so callers (and the CLI) get a clean message, not a raw traceback."""
    try:
        return yaml.load(text, Loader=_Yaml12Loader)
    except yaml.YAMLError as e:
        raise ConversionError(f"Invalid YAML: {e}") from e


def dump_yaml(obj):
    """Serialize to YAML with 1.2 boolean semantics. The bool-like token `on` -- whether
    a join condition key or an "on"/"off"/"yes"/... string value -- is force-quoted as
    `'on'` by the str representer (see `_represent_str`), so a YAML 1.1 reader of this
    output reads it as the string, not the boolean True. Databricks' Jackson (1.2) parser
    reads the quoted key/value correctly too."""
    return yaml.dump(obj, Dumper=_Yaml12Dumper, sort_keys=False, default_flow_style=False)


def is_simple_identifier(expr):
    """True if `expr` is a single bare column reference (no operators/functions).

    A non-string input is simply not an identifier (returns False) rather than raising."""
    return isinstance(expr, str) and bool(_IDENTIFIER_RE.match(expr.strip()))


def read_stash(obj):
    """Return the DATABRICKS stash dict on an Apache Ossie object, or {} if absent.

    The `_v` version marker is stripped from the returned dict.
    """
    for ext in (obj or {}).get("custom_extensions") or []:
        if ext.get("vendor_name") == VENDOR:
            try:
                data = json.loads(ext.get("data") or "{}")
            except json.JSONDecodeError as e:
                raise ConversionError(
                    f"DATABRICKS custom_extensions data is not valid JSON: {e}") from e
            data.pop("_v", None)
            return data
    return {}


def write_stash(obj, data):
    """Attach a DATABRICKS `custom_extensions` entry holding `data` (a dict).

    No-op when `data` is empty, so hand-authored Apache Ossie stays clean. Merges into an
    existing DATABRICKS entry if one is already present.
    """
    if not data:
        return
    payload = {"_v": STASH_VERSION}
    payload.update(data)
    blob = json.dumps(payload)
    exts = obj.setdefault("custom_extensions", [])
    for ext in exts:
        if ext.get("vendor_name") == VENDOR:
            ext["data"] = blob
            return
    exts.append({"vendor_name": VENDOR, "data": blob})


def foreign_vendor_extensions(obj):
    """Return non-DATABRICKS custom_extensions (dropped on export, with a warning)."""
    return [
        ext
        for ext in (obj or {}).get("custom_extensions") or []
        if ext.get("vendor_name") != VENDOR
    ]


def pick_expression(ossie_expression):
    """Choose the SQL string for an Apache Ossie expression: DATABRICKS, else ANSI_SQL.

    Returns None if neither dialect is present (the caller warns and skips). Does
    not warn about other dialects here -- only the absence of a usable one matters.
    """
    dialects = {
        d.get("dialect"): d.get("expression")
        for d in (ossie_expression or {}).get("dialects") or []
    }
    expr = dialects.get(DIALECT_DATABRICKS) or dialects.get(DIALECT_ANSI)
    if expr is not None and not isinstance(expr, str):
        raise ConversionError(
            f"expression must be a string, got {type(expr).__name__}")
    return expr


def synonyms_of(ai_context):
    """Extract the synonyms list from an Apache Ossie ai_context (object form only)."""
    if isinstance(ai_context, dict):
        return list(ai_context.get("synonyms") or [])
    return []


def merge_description(description, ai_context):
    """Fold a string-form ai_context into a description.

    The Apache Ossie schema allows ai_context to be either a string or an object. A string
    has no Metric View home of its own, so it is appended to the description
    (which maps to `comment`). Object-form ai_context is handled separately
    (synonyms map natively; instructions/examples are dropped).
    """
    if isinstance(ai_context, str) and ai_context.strip():
        return f"{description}\n{ai_context}" if description else ai_context
    return description


def validate_source(source, dataset_name):
    """Validate and normalize a dataset source for a Metric View.

    Accepts a 3-part `catalog.schema.table` identifier or a `SELECT`/`WITH`
    subquery. Raises ConversionError otherwise.
    """
    if not source or not str(source).strip():
        raise ConversionError(f"Dataset '{dataset_name}': missing/empty 'source'")
    s = str(source).strip()
    # A SELECT/WITH subquery source. `\b` after the keyword matches `WITH(...)` (no
    # space) too, but not an identifier like `WITHHELD`.
    if re.match(r"(?i)(select|with)\b", s):
        return s
    # Exactly 3 parts, each a non-empty token with no whitespace -- so `.sch.tbl`,
    # `cat..tbl`, `cat.sch.`, and `cat . sch . tbl` are all rejected (an empty or
    # space-laden part is not a valid catalog/schema/table identifier).
    parts = s.split(".")
    if len(parts) == 3 and all(p and not any(ch.isspace() for ch in p) for p in parts):
        return s
    raise ConversionError(
        f"Dataset '{dataset_name}': source '{source}' must be a 3-part "
        f"catalog.schema.table identifier or a SELECT/WITH subquery"
    )


def last_identifier(source):
    """Last dotted part of a table reference, e.g. `samples.tpch.lineitem` -> `lineitem`.

    Coerces to str so a malformed (non-string) source doesn't crash here -- it gets a
    clean error from validate_source instead."""
    return str(source).strip().split(".")[-1].strip("`") if source else source
