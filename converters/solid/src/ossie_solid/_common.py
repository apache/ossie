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

"""Shared helpers for the Apache Ossie <-> Solid semantic model converters.

Both directions are pure offline YAML transforms. The cross-cutting concerns live
here: version constants, the `custom_extensions` stash protocol, YAML I/O, the
Solid asset-link markup, and small identifier helpers.
"""

import json
import re
import warnings

import yaml

# Apache Ossie semantic model spec version this converter writes (see core-spec).
#
# NOTE: this spoke has no `apache-ossie` runtime dependency, so nothing updates the
# constant automatically -- it MUST be bumped in lockstep with the `version` in
# `core-spec/` whenever the spec version moves.
OSSIE_VERSION = "0.2.0.dev0"

# Spec versions an Apache Ossie document may declare and still be read (see
# convert_ossie_to_solid). Output is always written as OSSIE_VERSION.
#
# 0.1.1 is the only *released* spec version, so it is what models in the wild and
# several of the repository's own converter fixtures declare. As a document, a 0.1.1
# model is a 0.2.0.dev0 model minus three additions: `datatype` on Field and Metric,
# `BIGQUERY` in the dialect enum, and a free-form (rather than enumerated)
# `vendor_name`. Every one of those is additive, so nothing in a 0.1.1 document is
# invalid under 0.2.0.dev0 and no separate read path is needed -- only the absence of
# `datatype` is visible to this converter, and that is already handled the same way a
# 0.2.0.dev0 model that simply omits it is handled.
READABLE_OSSIE_VERSIONS = ("0.2.0.dev0", "0.1.1")

# Read-only spec versions whose omissions are worth naming when one is encountered.
OSSIE_VERSION_NOTES = {
    "0.1.1": "it predates the `datatype` field, so a column with no stashed raw type "
             "gets an empty Solid 'type'",
}

# Vendor id used for the `custom_extensions` stash.
VENDOR = "SOLID"

# Note import writes to a one-to-one relationship's `ai_context.instructions`, where
# Apache Ossie's directional from/to carries no meaning (see solid_to_ossie._orient).
# Export recognizes it as this converter's own marker rather than a user annotation, so
# it is dropped silently on the way back while real annotations are reported.
ONE_TO_ONE_NOTE = ("One-to-one relationship: both sides are unique on the join "
                   "columns, so the from/to direction is arbitrary.")

# Bump when the shape of a stashed `data` blob changes.
STASH_VERSION = 1

# Apache Ossie dialects this converter can resolve a Solid model to. Solid itself
# records the warehouse on the asset row, not in the exported YAML, so the dialect is
# either supplied explicitly or inferred from the column type vocabulary
# (see datatypes.infer_dialect).
DIALECT_ANSI = "ANSI_SQL"
DIALECT_SNOWFLAKE = "SNOWFLAKE"
DIALECT_DATABRICKS = "DATABRICKS"
DIALECT_BIGQUERY = "BIGQUERY"

SUPPORTED_DIALECTS = (
    DIALECT_ANSI,
    DIALECT_SNOWFLAKE,
    DIALECT_DATABRICKS,
    DIALECT_BIGQUERY,
)

# Apache Ossie dialect -> sqlglot dialect name. ANSI_SQL maps to sqlglot's default
# ("" / None) parser rather than a vendor grammar.
SQLGLOT_DIALECTS = {
    DIALECT_ANSI: None,
    DIALECT_SNOWFLAKE: "snowflake",
    DIALECT_DATABRICKS: "databricks",
    DIALECT_BIGQUERY: "bigquery",
}

# Solid renders a composite primary key as a single comma-joined scalar
# (`primary_key: 'ORDER_ID, LINE_NO'`); see semantic_layer_utils.py in solid-server.
# Apache Ossie models it as a column array, so the two are split/joined on this.
PK_SEPARATOR = ", "

# Solid embeds references to its own catalog objects inside `custom_instructions` as
# self-closing markup carrying an internal UUID:
#
#     @<assetlink id='00000000-0000-4000-8000-000000000000' type='column'
#                 name='orders.lead_source'>
#
# There is no closing tag and attribute order is not significant. Solid resolves these
# to their `name` before the text reaches an LLM; this converter does the same for the
# Apache Ossie `ai_context.instructions` a downstream tool will read, and keeps the raw
# tagged text in the stash so an export reproduces the original byte-for-byte.
_ASSET_LINK_RE = re.compile(r"@<assetlink\s+([^>]+)>", re.IGNORECASE)
_ASSET_LINK_ATTR_RE = re.compile(r"(\w+)\s*=\s*[\"']([^\"']*)[\"']")


class ConversionError(Exception):
    """Raised when an input cannot be converted."""


def warn(scope, message):
    """Emit a conversion warning. The CLI surfaces these on stderr."""
    warnings.warn(f"[{scope}] {message}", stacklevel=2)


def require(obj, key, what):
    """Return `obj[key]`, or raise a clean ConversionError if it is missing/empty.

    Presence is tested by key rather than truthiness, so a legitimately falsy value such
    as `0` or `False` is returned; a missing key, a null, or an empty/whitespace-only
    string is rejected.
    """
    if not isinstance(obj, dict) or key not in obj or obj[key] is None:
        raise ConversionError(f"{what} is missing required '{key}'")
    value = obj[key]
    if isinstance(value, str) and not value.strip():
        raise ConversionError(f"{what} has an empty '{key}'")
    return value


def require_str(obj, key, what):
    """Like require(), but also enforce that the value is a string -- so a non-string
    scalar (e.g. a YAML number used as a name) raises a clean ConversionError instead of
    failing later inside a string operation."""
    value = require(obj, key, what)
    if not isinstance(value, str):
        raise ConversionError(
            f"{what}: '{key}' must be a string, got {type(value).__name__}")
    return value


def load_yaml(text):
    """Parse YAML, surfacing a syntax error as a ConversionError so callers (and the
    CLI) get a clean message rather than a raw traceback."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConversionError(f"Invalid YAML: {e}") from e


def dump_yaml(obj):
    """Serialize to YAML, preserving key insertion order and allowing Unicode through
    unescaped (Solid descriptions routinely contain non-ASCII punctuation)."""
    return yaml.safe_dump(
        obj, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100
    )


def read_stash(obj):
    """Return the SOLID stash dict on an Apache Ossie object, or {} if absent.

    The `_v` version marker is stripped from the returned dict.
    """
    for ext in (obj or {}).get("custom_extensions") or []:
        if ext.get("vendor_name") == VENDOR:
            try:
                data = json.loads(ext.get("data") or "{}")
            except json.JSONDecodeError as e:
                raise ConversionError(
                    f"SOLID custom_extensions data is not valid JSON: {e}") from e
            if not isinstance(data, dict):
                raise ConversionError(
                    "SOLID custom_extensions data must be a JSON object")
            data.pop("_v", None)
            return data
    return {}


def write_stash(obj, data):
    """Attach a SOLID `custom_extensions` entry holding `data` (a dict).

    No-op when `data` is empty, so an object with nothing Solid-specific to carry stays
    clean. Merges into an existing SOLID entry if one is already present.
    """
    if not data:
        return
    payload = {"_v": STASH_VERSION}
    payload.update(data)
    blob = json.dumps(payload, ensure_ascii=False)
    exts = obj.setdefault("custom_extensions", [])
    for ext in exts:
        if ext.get("vendor_name") == VENDOR:
            ext["data"] = blob
            return
    exts.append({"vendor_name": VENDOR, "data": blob})


def foreign_vendor_extensions(obj):
    """Return the non-SOLID custom_extensions on an object.

    Solid's YAML has no slot for vendor metadata, so these are dropped on export; the
    caller warns rather than discarding them silently.
    """
    return [
        ext
        for ext in (obj or {}).get("custom_extensions") or []
        if ext.get("vendor_name") != VENDOR
    ]


def pick_expression(ossie_expression, dialect):
    """Choose the SQL string for an Apache Ossie expression: `dialect`, else ANSI_SQL.

    Returns (expression, matched) where `matched` is False when the value came from the
    ANSI_SQL fallback rather than the requested dialect, so the caller can warn. Returns
    (None, False) when neither dialect is present.
    """
    dialects = {
        d.get("dialect"): d.get("expression")
        for d in (ossie_expression or {}).get("dialects") or []
    }
    for candidate, matched in ((dialect, True), (DIALECT_ANSI, False)):
        expr = dialects.get(candidate)
        if expr is None:
            continue
        if not isinstance(expr, str):
            raise ConversionError(
                f"expression must be a string, got {type(expr).__name__}")
        return expr, matched
    return None, False


def readable_dialects(dialect):
    """Name the dialects `pick_expression` will accept, for an error message.

    Reads as "SNOWFLAKE or ANSI_SQL", and as plain "ANSI_SQL" when the selected dialect
    *is* ANSI_SQL rather than the tautological "ANSI_SQL or ANSI_SQL".
    """
    if dialect == DIALECT_ANSI:
        return DIALECT_ANSI
    return f"{dialect} or {DIALECT_ANSI}"


def resolve_asset_links(text):
    """Replace Solid `@<assetlink ...>` markup with the tag's `name` attribute.

    Mirrors solid-server's `replace_asset_links_with_display_names`, which is applied
    before instructions are handed to an LLM. A tag with no usable `name` is left
    verbatim rather than deleted, so no text is silently lost.
    """
    if not isinstance(text, str) or "@<assetlink" not in text.lower():
        return text

    def _replace(match):
        attrs = dict(
            (k.lower(), v) for k, v in _ASSET_LINK_ATTR_RE.findall(match.group(1))
        )
        return attrs.get("name") or match.group(0)

    return _ASSET_LINK_RE.sub(_replace, text)


def has_asset_links(text):
    """True if `text` carries Solid asset-link markup."""
    return isinstance(text, str) and bool(_ASSET_LINK_RE.search(text))


def split_source(source):
    """Split a `catalog.schema.table` source into its parts, honouring backtick quoting.

    Returns the list of parts with any surrounding backticks or double quotes removed.
    BigQuery project ids contain hyphens (`my-project.web.sessions`), which is why
    parts are not validated as bare SQL identifiers here.
    """
    parts, current, quote = [], [], None
    for ch in str(source or ""):
        if quote:
            if ch == quote:
                quote = None
            else:
                current.append(ch)
        elif ch in ('"', "`"):
            quote = ch
        elif ch == ".":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    parts.append("".join(current))
    return [p.strip() for p in parts]


def dataset_name_for(source, taken):
    """Derive a unique Apache Ossie dataset name from a Solid table FQN.

    Solid identifies a table by its fully-qualified `catalog.schema.table` name, which
    doubles as the Apache Ossie `source`. Apache Ossie dataset names are also expression
    qualifiers (`store_sales.ss_quantity`), so a dotted name is not usable -- the last
    identifier is preferred, widening to `schema_table` and then to the whole FQN with
    dots replaced only when a shorter form is already taken.

    `taken` is a set of names already assigned; the chosen name is added to it.
    """
    parts = [p for p in split_source(source) if p]
    if not parts:
        raise ConversionError(f"Table source '{source}' has no usable name")
    candidates = []
    for depth in range(1, len(parts) + 1):
        candidates.append("_".join(parts[-depth:]).replace("-", "_"))
    for candidate in candidates:
        if candidate and candidate not in taken:
            taken.add(candidate)
            return candidate
    # Every qualified form collided (the same FQN appears twice). Suffix rather than
    # raise, so one duplicated table does not fail an otherwise convertible model.
    base = candidates[-1]
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    taken.add(f"{base}_{n}")
    return f"{base}_{n}"


def unique_name(base, taken):
    """Return `base`, or `base_2`/`base_3`/... if it is already in `taken`."""
    name = base
    n = 2
    while name in taken:
        name = f"{base}_{n}"
        n += 1
    taken.add(name)
    return name


def clean_text(value):
    """Normalize a Solid free-text field: strip trailing whitespace, drop empties.

    Solid emits block scalars for descriptions, which leaves a trailing newline, and
    writes `manual_description: ''` for a column whose human description was cleared.
    Both should become "absent" rather than an empty Apache Ossie field.
    """
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def string_list(value):
    """Coerce a Solid list field to a list of non-empty strings, or [] if absent."""
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if v is not None and str(v).strip()]


def build_ai_context(instructions=None, synonyms=None, examples=None):
    """Assemble an Apache Ossie `ai_context` object, or None when nothing is present.

    Key order is fixed (instructions, synonyms, examples) so output is deterministic.
    """
    ctx = {}
    if instructions:
        ctx["instructions"] = instructions
    if synonyms:
        ctx["synonyms"] = list(synonyms)
    if examples:
        ctx["examples"] = list(examples)
    return ctx or None


def ai_context_parts(ai_context):
    """Split an Apache Ossie `ai_context` into (instructions, synonyms, examples).

    The spec allows `ai_context` to be a bare string; that form carries instructions
    only. Anything else (a list, a number) yields all-empty parts.
    """
    if isinstance(ai_context, str):
        return clean_text(ai_context), [], []
    if isinstance(ai_context, dict):
        return (
            clean_text(ai_context.get("instructions")),
            string_list(ai_context.get("synonyms")),
            string_list(ai_context.get("examples")),
        )
    return None, [], []
