"""Shared helpers for the OSI <-> Omni converters.

Both directions are pure offline YAML transforms. The cross-cutting concerns live
here: version constants, the dialect preference order, the `custom_extensions`
stash protocol, Omni file-name conventions, identifier sanitization, and the
`${...}` reference translation between Omni SQL and the plain column references
OSI expressions use.
"""

import datetime
import json
import re

import yaml

# OSI semantic model spec version this converter targets (see core-spec).
OSI_VERSION = "0.2.0.dev0"

# Vendor id used for the `custom_extensions` stash.
VENDOR = "OMNI"

# Omni SQL is the SQL of the model's database connection, so there is no OMNI
# entry in the OSI dialect enum. Import emits ANSI_SQL; export prefers ANSI_SQL
# and lets the caller prepend a warehouse dialect (e.g. SNOWFLAKE) that the
# actual connection would accept.
DIALECT_ANSI = "ANSI_SQL"

# Bump when the shape of a stashed `data` blob changes.
STASH_VERSION = 1

# Omni model file names (the local-editor/git layout, with `.yaml` appended).
MODEL_FILE = "model.yaml"
RELATIONSHIPS_FILE = "relationships.yaml"
VIEW_DIR = "views"
TOPIC_DIR = "topics"

# Omni relationship defaults (left implicit on export when they hold).
DEFAULT_JOIN_TYPE = "always_left"
REL_MANY_TO_ONE = "many_to_one"
REL_ONE_TO_MANY = "one_to_many"

# The timeframes Omni applies to a time dimension by default; used to represent
# OSI `dimension.is_time` when no exact list is stashed.
DEFAULT_TIMEFRAMES = ["raw", "date", "week", "month", "quarter", "year"]

# A valid Omni identifier (view, dimension, measure, topic name). Broader than
# the documented lowercase convention because real Omni-generated models use
# more: leading underscores (Fivetran's `_fivetran_id`), trailing underscores
# (truncated column names), and camelCase (JSON-flattened `..._dimensionIndex`).
_OMNI_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# A bare SQL identifier (single column reference), e.g. `c_name`.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# An Omni `${...}` reference: ${field}, ${view.field}, ${view.field[timeframe]},
# ${TABLE}.column. Group 1 is the reference body (without the braces).
_OMNI_REF_RE = re.compile(r"\$\{\s*([^}]*?)\s*\}")


class ConversionError(Exception):
    """Raised when an input cannot be converted."""


def require(obj, key, what):
    """Return `obj[key]`, or raise a clean ConversionError if it's missing/empty --
    so malformed input surfaces as an error message rather than a raw KeyError.

    Presence is tested by key (not truthiness), so a legitimately falsy value such
    as `0` or `False` is returned; a missing key, a null, or an empty/whitespace
    string is rejected.
    """
    if not isinstance(obj, dict) or key not in obj or obj[key] is None:
        raise ConversionError(f"{what} is missing required '{key}'")
    value = obj[key]
    if isinstance(value, str) and not value.strip():
        raise ConversionError(f"{what} has an empty '{key}'")
    return value


def require_str(obj, key, what):
    """Like require(), but also enforce the value is a string -- so a non-string
    scalar (e.g. a YAML number for a name or expression) raises a clean
    ConversionError instead of crashing later in a string operation."""
    value = require(obj, key, what)
    if not isinstance(value, str):
        raise ConversionError(
            f"{what}: '{key}' must be a string, got {type(value).__name__}")
    return value


# PyYAML's default YAML 1.1 semantics turn bare on/off/yes/no into booleans, which
# would corrupt Omni string values (a label "On", a week_start_day, a synonym).
# The Loader below uses YAML 1.2 booleans (only true/false); the Dumper
# force-quotes bool-like string tokens so the output round-trips through a 1.1
# reader too. Same approach as the osi-databricks converter.
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


_YAML11_BOOL_STRS = frozenset(
    variant
    for word in ("y", "n", "yes", "no", "on", "off", "true", "false")
    for variant in (word, word.capitalize(), word.upper())
)


def _represent_str(dumper, data):
    style = "'" if data in _YAML11_BOOL_STRS else None
    if "\n" in data:
        style = "|"
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Yaml12Dumper.add_representer(str, _represent_str)


def load_yaml(text, what="input"):
    """Parse YAML with 1.2 boolean semantics. A syntax error is surfaced as a
    ConversionError so callers (and the CLI) get a clean message."""
    try:
        return yaml.load(text, Loader=_Yaml12Loader)
    except yaml.YAMLError as e:
        raise ConversionError(f"Invalid YAML in {what}: {e}") from e


def dump_yaml(obj):
    """Serialize to YAML with 1.2 boolean semantics; bool-like string tokens are
    force-quoted so a YAML 1.1 reader of this output sees strings, not booleans."""
    return yaml.dump(obj, Dumper=_Yaml12Dumper, sort_keys=False,
                     default_flow_style=False, allow_unicode=True)


def is_simple_identifier(expr):
    """True if `expr` is a single bare column reference (no operators/functions)."""
    return isinstance(expr, str) and bool(_IDENTIFIER_RE.match(expr.strip()))


def is_omni_name(name):
    """True if `name` is already a valid Omni identifier."""
    return isinstance(name, str) and bool(_OMNI_NAME_RE.match(name))


def sanitize_name(name, what, taken):
    """Coerce an OSI name into a valid Omni identifier.

    A name that is already a valid Omni identifier passes through untouched
    (leading/trailing underscores and camelCase are legal and occur in real
    Omni-generated models); anything else is lowercased with every invalid
    character run replaced by `_`. A result colliding case-insensitively with
    one already in `taken` (a set of casefolded names) is an error rather than
    a silent merge; the caller adds `result.lower()` to `taken`.
    """
    raw = str(name)
    if _OMNI_NAME_RE.match(raw):
        out = raw
    else:
        out = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")
        if not out or not out[0].isalpha():
            out = f"v_{out}" if out else "v"
    if out.lower() in taken:
        raise ConversionError(
            f"{what} '{name}' sanitizes to '{out}', which collides with another "
            f"name; rename it in the OSI model."
        )
    return out


def view_file(view_name):
    return f"{VIEW_DIR}/{view_name}.view.yaml"


def topic_file(topic_name):
    return f"{TOPIC_DIR}/{topic_name}.topic.yaml"


# YAML parses a bare `2024-01-01` (e.g. in a topic's default_filters) into a
# datetime.date, which JSON cannot hold; the stash tags such values so they come
# back as dates and re-dump unquoted, keeping the round trip lossless.
_JSON_TEMPORAL = {"date": datetime.date, "datetime": datetime.datetime}


def _json_default(o):
    for tag, cls in _JSON_TEMPORAL.items():
        if type(o) is cls:
            return {"__osi_omni__": tag, "v": o.isoformat()}
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _json_object_hook(d):
    cls = _JSON_TEMPORAL.get(d.get("__osi_omni__", ""))
    if cls is not None and set(d) == {"__osi_omni__", "v"}:
        return cls.fromisoformat(d["v"])
    return d


def read_stash(obj):
    """Return the OMNI stash dict on an OSI object, or {} if absent.

    The `_v` version marker is stripped from the returned dict.
    """
    for ext in (obj or {}).get("custom_extensions") or []:
        if ext.get("vendor_name") == VENDOR:
            data = json.loads(ext.get("data") or "{}",
                              object_hook=_json_object_hook)
            data.pop("_v", None)
            return data
    return {}


def write_stash(obj, data):
    """Attach an OMNI `custom_extensions` entry holding `data` (a dict).

    No-op when `data` is empty, so hand-authored OSI stays clean. Merges into an
    existing OMNI entry if one is already present.
    """
    if not data:
        return
    payload = {"_v": STASH_VERSION}
    payload.update(data)
    blob = json.dumps(payload, default=_json_default)
    exts = obj.setdefault("custom_extensions", [])
    for ext in exts:
        if ext.get("vendor_name") == VENDOR:
            ext["data"] = blob
            return
    exts.append({"vendor_name": VENDOR, "data": blob})


def foreign_vendor_extensions(obj):
    """Return non-OMNI custom_extensions (dropped on export, with a warning)."""
    return [
        ext
        for ext in (obj or {}).get("custom_extensions") or []
        if ext.get("vendor_name") != VENDOR
    ]


def pick_expression(osi_expression, preferred=None):
    """Choose the SQL string for an OSI expression.

    Preference order: the caller-chosen warehouse dialect (Omni passes SQL through
    to the connection's database, so e.g. SNOWFLAKE SQL is valid on a Snowflake-
    backed Omni model), then ANSI_SQL. Returns None if neither is present (the
    caller warns and skips).
    """
    dialects = {
        d.get("dialect"): d.get("expression")
        for d in (osi_expression or {}).get("dialects") or []
    }
    expr = None
    if preferred:
        expr = dialects.get(preferred)
    if expr is None:
        expr = dialects.get(DIALECT_ANSI)
    if expr is not None and not isinstance(expr, str):
        raise ConversionError(
            f"expression must be a string, got {type(expr).__name__}")
    return expr


def synonyms_of(ai_context):
    """Extract the synonyms list from an OSI ai_context (object form only)."""
    if isinstance(ai_context, dict):
        return list(ai_context.get("synonyms") or [])
    return []


def instructions_of(ai_context):
    """The free-text part of an OSI ai_context: the string itself, or the
    object form's `instructions`."""
    if isinstance(ai_context, str) and ai_context.strip():
        return ai_context
    if isinstance(ai_context, dict):
        text = ai_context.get("instructions")
        if isinstance(text, str) and text.strip():
            return text
    return None


# One part of a dotted source reference: double-quoted (may hold spaces/dots --
# Omni's uploaded-CSV schema is literally `Omni Views`) or a bare name.
_SOURCE_PARTS_RE = re.compile(r'^(?:"[^"]+"|[^".]+)(?:\.(?:"[^"]+"|[^".]+))*$')
_SOURCE_PART_RE = re.compile(r'"([^"]+)"|([^".]+)')


def quote_source_part(part):
    """Quote one part of an OSI dotted source when it needs it."""
    p = str(part)
    return p if re.fullmatch(r"[A-Za-z0-9_$]+", p) else f'"{p}"'


def parse_source(source, dataset_name):
    """Split an OSI dataset `source` into Omni view placement.

    Returns ("sql", sql_text) for a SELECT/WITH subquery source, or
    ("table", catalog_or_None, schema, table) for a dotted table reference
    (parts may be double-quoted: `"Omni Views".channel_info`).
    Omni views require a `schema`, so a bare 1-part table name is rejected.
    """
    if not source or not str(source).strip():
        raise ConversionError(f"Dataset '{dataset_name}': missing/empty 'source'")
    s = str(source).strip()
    if re.match(r"(?i)(select|with)\b", s):
        return ("sql", s)
    if not _SOURCE_PARTS_RE.match(s):
        raise ConversionError(
            f"Dataset '{dataset_name}': source '{source}' is not a valid dotted "
            f"table reference or SELECT/WITH subquery"
        )
    parts = []
    for m in _SOURCE_PART_RE.finditer(s):
        quoted, bare = m.group(1), m.group(2)
        if bare is not None and any(ch.isspace() for ch in bare):
            raise ConversionError(
                f"Dataset '{dataset_name}': source '{source}' is not a valid "
                f"dotted table reference or SELECT/WITH subquery"
            )
        parts.append(quoted if quoted is not None else bare)
    if len(parts) == 3:
        return ("table", parts[0], parts[1], parts[2])
    if len(parts) == 2:
        return ("table", None, parts[0], parts[1])
    raise ConversionError(
        f"Dataset '{dataset_name}': source '{source}' has no schema part; Omni "
        f"views require a `schema`, so use `schema.table` or `catalog.schema.table`"
    )


def join_source(view):
    """Rebuild an OSI dataset `source` string from an Omni view dict."""
    if view.get("sql") is not None:
        return str(view["sql"]).strip()
    schema = view.get("schema")
    table = view.get("table_name")
    if not schema or not table:
        return None
    parts = [view["catalog"], schema, table] if view.get("catalog") else [schema, table]
    return ".".join(quote_source_part(p) for p in parts)


def omni_sql_to_osi(sql, own_view):
    """Translate Omni `${...}` references in a SQL string to the plain references
    OSI expressions use. Returns (translated, changed).

    - `${TABLE}.col`        -> `col`       (a raw column of the owning view)
    - `${field}`            -> `field`     (same-view field)
    - `${own_view.field}`   -> `field`     (qualified same-view field)
    - `${other.field}`      -> `other.field`
    - `${view.field[tf]}`   -> `view.field` (timeframe access has no OSI form;
                                the caller warns and stashes the original)
    OSI has no field-vs-column distinction, so both flavors flatten to names.
    """
    changed = False

    def repl(m):
        nonlocal changed
        changed = True
        body = m.group(1)
        if body == "TABLE":
            return "__OSI_TABLE__"  # handled below with its trailing dot
        body = re.sub(r"\[[^\]]*\]$", "", body).strip()  # drop [timeframe]
        if "." in body:
            head, rest = body.split(".", 1)
            if head == own_view:
                return rest
        return body

    out = _OMNI_REF_RE.sub(repl, sql)
    out = out.replace("__OSI_TABLE__.", "").replace("__OSI_TABLE__", "")
    return out, changed


def has_timeframe_ref(sql):
    """True if the Omni SQL contains a `${view.field[timeframe]}` reference."""
    return bool(re.search(r"\$\{[^}]*\[[^\]]*\][^}]*\}", sql or ""))


def osi_expr_refs_to_omni(expr, view_names):
    """Rewrite `view.column` references in an OSI expression to Omni `${view.column}`
    form, for the known `view_names` only -- so a genuine schema-qualified table or
    an unrelated dotted token is left alone. Bare columns stay bare (raw columns
    are legal in Omni SQL)."""
    if not view_names:
        return expr

    pattern = re.compile(
        r"(?<![\w.$])(" + "|".join(re.escape(v) for v in sorted(view_names, key=len,
                                                                reverse=True))
        + r")\.([A-Za-z_][A-Za-z0-9_]*)(?![\w.])"
    )
    return pattern.sub(lambda m: "${" + m.group(1) + "." + m.group(2) + "}", expr)
