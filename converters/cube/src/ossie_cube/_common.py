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

"""Shared helpers for the Apache Ossie <-> Cube converters.

Both directions are pure offline YAML transforms. The cross-cutting concerns
live here: version constants, the `custom_extensions` stash protocol, Cube
identifier rules, key-spelling normalization, the type/aggregate mapping tables,
and the member-reference translation between Cube's f-string SQL and the plain
column references Ossie expressions use.
"""

import json
import re

import yaml

# Ossie semantic model spec version this converter targets (see core-spec).
OSSIE_VERSION = "0.2.0.dev0"

# Vendor id used for the `custom_extensions` stash.
VENDOR = "CUBE"

# Cube SQL is the SQL of the model's data source, so there is no CUBE entry in
# the Ossie dialect enum. Import emits ANSI_SQL; export prefers ANSI_SQL and lets
# the caller prepend a warehouse dialect the actual data source would accept.
DIALECT_ANSI = "ANSI_SQL"

# Bump when the shape of a stashed `data` blob changes.
STASH_VERSION = 1

# Cube's default data model directory layout (`CUBEJS_SCHEMA_PATH` defaults to
# `model`, and `cube create` scaffolds these two subdirectories).
CUBE_DIR = "model/cubes"
VIEW_DIR = "model/views"

# A valid Cube identifier -- `identifierRegex` in Cube's CubeValidator.
_CUBE_NAME_RE = re.compile(r"^[_a-zA-Z][_a-zA-Z0-9]*$")

# A bare SQL identifier (single column reference), e.g. `c_name`.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# `cube.member` -- a dotted reference an Ossie expression uses to point into a
# dataset. Guarded so `a.b.c` and `1.5` do not match.
DOTTED_REF_RE = re.compile(
    r"(?<![\w.$])([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)(?![\w.])"
)

# A Cube member reference: `{...}` in YAML models, `${...}` in JavaScript ones --
# the YAML compiler rewrites the former into the latter, so they mean the same
# thing. Group 1 is the reference body.
_CUBE_REF_RE = re.compile(r"\$?\{\s*([^{}]*?)\s*\}")

# Cube's own-cube constants (CURRENT_CUBE_CONSTANTS in CubeSymbols): both stand
# for the cube the member is declared on.
_SELF_REFS = ("CUBE", "TABLE")

# Sentinels used while translating, so escaped braces and consumed `{CUBE}.`
# prefixes cannot collide with real content.
_ESC_OPEN = "\x00ossie_lbrace\x00"
_ESC_CLOSE = "\x00ossie_rbrace\x00"
_SELF_MARK = "\x00ossie_self\x00"

# Jinja templating in a data model has no static form at all.
JINJA_RE = re.compile(r"{%|%}|{{|}}")


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
    scalar (e.g. a YAML number for a name) raises a clean ConversionError instead
    of crashing later in a string operation."""
    value = require(obj, key, what)
    if not isinstance(value, str):
        raise ConversionError(
            f"{what}: '{key}' must be a string, got {type(value).__name__}")
    return value


# PyYAML's default YAML 1.1 semantics turn bare on/off/yes/no into booleans, which
# would corrupt Cube string values (a title "On", a status synonym, a segment
# name). The Loader below uses YAML 1.2 booleans (only true/false); the Dumper
# force-quotes bool-like string tokens so the output round-trips through a 1.1
# reader too. Same approach as the osi-omni and osi-databricks converters.
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


# --- identifiers ----------------------------------------------------------------

def is_simple_identifier(expr):
    """True if `expr` is a single bare column reference (no operators/functions)."""
    return isinstance(expr, str) and bool(_IDENTIFIER_RE.match(expr.strip()))


def sanitize_name(name, what, taken):
    """Coerce an Ossie name into a valid Cube identifier.

    A name that already matches Cube's `identifierRegex` passes through
    untouched; anything else is lowercased with every invalid character run
    replaced by `_`. A result colliding case-insensitively with one already in
    `taken` (a set of casefolded names) is an error rather than a silent merge;
    the caller adds `result.lower()` to `taken`.
    """
    raw = str(name)
    if _CUBE_NAME_RE.match(raw):
        out = raw
    else:
        out = re.sub(r"[^a-z0-9_]+", "_", raw.lower()).strip("_")
        if not out or out[0].isdigit():
            out = f"c_{out}" if out else "c"
    if out.lower() in taken:
        raise ConversionError(
            f"{what} '{name}' sanitizes to '{out}', which collides with another "
            f"name; rename it in the Ossie model."
        )
    return out


_CAMEL_RE = re.compile(r"(?<=[a-z0-9])([A-Z])")


def snake(key):
    """Normalize a Cube model key to its snake_case spelling.

    Cube accepts both spellings in YAML (`sqlTable` and `sql_table`, `primaryKey`
    and `primary_key`) because the compiler camelizes on load. Import normalizes
    so the mapping code only has to know one form; export always emits
    snake_case, which is what Cube's own YAML documentation and generators use.
    """
    return _CAMEL_RE.sub(r"_\1", str(key)).lower()


def snake_keys(obj):
    """Shallow-normalize a mapping's keys to snake_case."""
    if not isinstance(obj, dict):
        return obj
    return {snake(k): v for k, v in obj.items()}


def cube_file(cube_name):
    return f"{CUBE_DIR}/{cube_name}.yml"


def view_file(view_name):
    return f"{VIEW_DIR}/{view_name}.yml"


# --- stash protocol -------------------------------------------------------------

def read_stash(obj):
    """Return the CUBE stash dict on an Ossie object, or {} if absent.

    The `_v` version marker is stripped from the returned dict.
    """
    for ext in (obj or {}).get("custom_extensions") or []:
        if ext.get("vendor_name") == VENDOR:
            data = json.loads(ext.get("data") or "{}")
            data.pop("_v", None)
            return data
    return {}


def write_stash(obj, data):
    """Attach a CUBE `custom_extensions` entry holding `data` (a dict).

    No-op when `data` is empty, so hand-authored Ossie stays clean. Merges into an
    existing CUBE entry if one is already present.
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
    """Return non-CUBE custom_extensions.

    Unlike Omni, Cube has a `meta` field at every level, so export parks these
    under `meta.ossie.custom_extensions` instead of dropping them -- which keeps
    `Ossie -> Cube -> Ossie` lossless for models carrying several vendors.
    """
    return [
        ext
        for ext in (obj or {}).get("custom_extensions") or []
        if ext.get("vendor_name") != VENDOR
    ]


# --- expressions ----------------------------------------------------------------

def pick_expression(ossie_expression, preferred=None):
    """Choose the SQL string for an Ossie expression.

    Preference order: the caller-chosen warehouse dialect (Cube passes SQL
    through to the data source, so e.g. SNOWFLAKE SQL is valid on a
    Snowflake-backed Cube model), then ANSI_SQL. Returns None if neither is
    present (the caller records an issue and skips).
    """
    dialects = {
        d.get("dialect"): d.get("expression")
        for d in (ossie_expression or {}).get("dialects") or []
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
    """Extract the synonyms list from an Ossie ai_context (object form only)."""
    if isinstance(ai_context, dict):
        return list(ai_context.get("synonyms") or [])
    return []


def examples_of(ai_context):
    if isinstance(ai_context, dict):
        return list(ai_context.get("examples") or [])
    return []


def instructions_of(ai_context):
    """The free-text part of an Ossie ai_context: the string itself, or the
    object form's `instructions`."""
    if isinstance(ai_context, str) and ai_context.strip():
        return ai_context
    if isinstance(ai_context, dict):
        text = ai_context.get("instructions")
        if isinstance(text, str) and text.strip():
            return text
    return None


def cube_sql_to_ossie(sql, own_cube, resolve_ref=None, self_prefix=None):
    """Translate Cube member references in a SQL string to the plain references
    Ossie expressions use. Returns (translated, changed).

    - `{CUBE}.col` / `{TABLE}.col` -> `col`        (a raw column of the own cube)
    - `{CUBE.member}`              -> `member`     (own-cube member reference)
    - `{member}`                   -> `member`     (same, unqualified)
    - `{other.member}`             -> `other.member`
    - `{own_cube.member}`          -> `member`

    Ossie has no field-vs-column distinction, so both flavors flatten to names.
    `\\{` / `\\}` (Cube's escape for a literal brace) survive as plain braces.

    `self_prefix`, when given, qualifies own-cube references with it instead of
    reducing them to a bare name -- so `{CUBE}.col` becomes `orders.col`. Ossie
    field expressions are dataset-scoped and want the bare form, but model-level
    metric expressions address columns as `dataset.column`, so measure conversion
    passes the owning cube's name here.

    `resolve_ref`, when given, is called with each raw reference body before the
    rules above are applied; returning a string uses it verbatim instead, and
    returning None falls through. Measure conversion uses this to inline a
    `{other_measure}` reference, which Cube resolves to that measure's own
    aggregate SQL and Ossie has no reference form for.
    """
    if not isinstance(sql, str):
        sql = str(sql)
    changed = False
    protected = sql.replace("\\{", _ESC_OPEN).replace("\\}", _ESC_CLOSE)

    def repl(m):
        nonlocal changed
        body = m.group(1).strip()
        if resolve_ref is not None:
            override = resolve_ref(body)
            if override is not None:
                changed = True
                return override
        changed = True
        head, _, rest = body.partition(".")
        if not rest:
            # A lone `{name}`: either `{CUBE}`/`{TABLE}`, the cube's own name
            # spelled out, or an unqualified member reference. The first two are
            # an alias that a trailing `.column` attaches to, so they are marked
            # for removal along with that dot; a member name is an own-cube
            # reference.
            if body in _SELF_REFS or (own_cube and body == own_cube):
                return _SELF_MARK
            return f"{self_prefix}.{body}" if self_prefix else body
        if head in _SELF_REFS or (own_cube and head == own_cube):
            return f"{self_prefix}.{rest}" if self_prefix else rest
        return body

    out = _CUBE_REF_RE.sub(repl, protected)
    # `{CUBE}.column` -- the alias marker plus the dot the column hangs off.
    out = out.replace(f"{_SELF_MARK}.", f"{self_prefix}." if self_prefix else "")
    out = out.replace(_SELF_MARK, "")
    out = out.replace(_ESC_OPEN, "{").replace(_ESC_CLOSE, "}")
    return out, changed


def requalify_self_refs(sql, cube_name):
    """Rewrite `{CUBE}` / `{TABLE}` in a Cube SQL snippet to name `cube_name`.

    Needed when a snippet written for one cube is inlined into another cube's SQL:
    `{CUBE}` means "the cube this is declared on", so it changes meaning on the
    move, while `{orders}.col` is explicit and does not.
    """
    return re.sub(
        r"\$?\{\s*(?:CUBE|TABLE)\s*(\.\s*[A-Za-z_][A-Za-z0-9_]*\s*)?\}",
        lambda m: "{" + cube_name + (m.group(1).strip() if m.group(1) else "") + "}",
        str(sql),
    )


def ossie_expr_to_cube_sql(expr, own_cube, own_members=(), cube_names=(),
                           inline_sql=None):
    """Rewrite an Ossie expression into Cube member-reference form.

    Only *dotted* `cube.name` references are rewritten -- a bare identifier stays
    bare, because in Ossie it is a physical column of the owning dataset and
    rewriting it to `{CUBE.name}` would make a member's own `sql` self-referential.

    A dotted reference resolves to whichever form Cube expects:
    - `own_cube.member` where `member` is declared -> `{CUBE.member}`
      (compile-time checked, and inlines the member's own SQL)
    - `own_cube.column` where it is not           -> `{CUBE}.column`
      (a raw physical column, passed through to the database)
    - `other_cube.member`                         -> `{other_cube.member}`
      (which is also what triggers the implicit join a cross-dataset metric needs)

    The own cube is always referenced as `{CUBE}` rather than by name, so the
    model keeps working when the cube is extended. Literal braces in the incoming
    expression are escaped.

    `inline_sql` maps `{cube: {field: cube_sql}}` for Ossie fields that have no
    addressable Cube counterpart, and whose SQL therefore has to be substituted
    inline. The case that needs it is a split `geo` dimension: `location_latitude`
    exists only in Ossie -- Cube has neither a column nor a member by that name --
    so a reference to it becomes the half's own SQL (`{CUBE}.lat`), requalified when
    it crosses cubes.
    """
    escaped = str(expr).replace("{", "\\{").replace("}", "\\}")
    known = set(cube_names)
    members = set(own_members)
    inline = inline_sql or {}

    def repl(m):
        head, name = m.group(1), m.group(2)
        substitute = (inline.get(head) or {}).get(name)
        if substitute is not None:
            # Already-Cube SQL, so it bypasses the escaping above; `{CUBE}` inside
            # it means `head`, which only stays true while head is the own cube.
            return (str(substitute) if head == own_cube
                    else requalify_self_refs(substitute, head))
        if head == own_cube:
            return "{CUBE." + name + "}" if name in members else "{CUBE}." + name
        if head in known:
            return "{" + head + "." + name + "}"
        # Not a dataset in this model -- a genuine schema-qualified table
        # reference or an unrelated dotted token. Leave it alone.
        return m.group(0)

    return DOTTED_REF_RE.sub(repl, escaped)


# --- source ---------------------------------------------------------------------

def parse_source(source, dataset_name):
    """Classify an Ossie dataset `source` for placement on a Cube cube.

    Returns ("sql", sql_text) for a SELECT/WITH subquery source, or
    ("sql_table", table_ref) for a table reference. Cube's `sql_table` takes the
    reference verbatim (it is interpolated straight into FROM), so no splitting
    into catalog/schema/table is needed -- unlike Omni, Cube has no separate
    `schema` key, which also means a bare one-part table name is fine.
    """
    if not source or not str(source).strip():
        raise ConversionError(f"Dataset '{dataset_name}': missing/empty 'source'")
    s = str(source).strip()
    if re.match(r"(?i)(select|with)\b", s):
        return ("sql", s)
    return ("sql_table", s)


def join_source(cube, cube_name):
    """Rebuild an Ossie dataset `source` string from a Cube cube dict.

    Cube's schema requires exactly one of `sql` / `sql_table` (an `xor` in
    CubeValidator), so anything else is rejected rather than guessed at.
    """
    sql = cube.get("sql")
    table = cube.get("sql_table")
    if sql is not None and table is not None:
        raise ConversionError(
            f"Cube '{cube_name}': has both 'sql' and 'sql_table'; Cube allows "
            f"exactly one")
    if table is not None:
        return str(table).strip()
    if sql is not None:
        return str(sql).strip()
    raise ConversionError(
        f"Cube '{cube_name}': has neither 'sql' nor 'sql_table' (an `extends`-only "
        f"cube?); Ossie datasets require a source")


# --- type mapping ---------------------------------------------------------------

# Cube dimension `type` -> Ossie `datatype`. `number` is deliberately absent:
# Cube collapses Integer/Decimal/Float into one type, and Ossie says to omit
# `datatype` when it is unknown rather than assert a precision the model does not
# have. (Cube's SQL API reports `number` as Double, but that is a wire-protocol
# floor, not a claim about the column.) `geo` is absent because such a dimension
# is split into two numeric fields.
DIM_TYPE_TO_DATATYPE = {
    "string": "String",
    "boolean": "Boolean",
    "time": "DateTime",
    "switch": "String",
}

# Ossie `datatype` -> Cube dimension `type`, which is required on every
# dimension. Lossy in the numeric and temporal directions by construction.
DATATYPE_TO_DIM_TYPE = {
    "String": "string",
    "Integer": "number",
    "Decimal": "number",
    "Float": "number",
    "Boolean": "boolean",
    "Date": "time",
    "Time": "time",
    "DateTime": "time",
    "DateTimeTz": "time",
    "Opaque": "string",
}

# Cube measure `type` -> the Ossie aggregate function that reproduces it.
# `count` is absent: it maps through the cube's primary key, see
# primary_key_count_expression().
AGG_TO_OSSIE_FUNC = {
    "sum": "SUM",
    "avg": "AVG",
    "min": "MIN",
    "max": "MAX",
    "count_distinct": "COUNT_DISTINCT",
    "count_distinct_approx": "APPROX_COUNT_DISTINCT",
}

OSSIE_FUNC_TO_AGG = {
    "SUM": "sum",
    "AVG": "avg",
    "MIN": "min",
    "MAX": "max",
    "COUNT_DISTINCT": "count_distinct",
    "APPROX_COUNT_DISTINCT": "count_distinct_approx",
}

# Cube measure types whose aggregation is written out in the `sql` itself
# (CubeSymbols.isCalculatedMeasureType). Their sql is emitted verbatim.
CALCULATED_MEASURE_TYPES = frozenset({"number", "string", "boolean", "time"})

# Aggregates whose value is unaffected by duplicate input rows, so a static Ossie
# expression stays correct even when the relationship graph fans the dataset out.
# `count` belongs here only in its bare form, which maps to COUNT(DISTINCT <pk>).
FANOUT_SAFE_AGGS = frozenset({
    "count_distinct", "count_distinct_approx", "min", "max",
})

# Aggregates that over-count under row multiplication. Cube corrects for these at
# query time by deduplicating on the primary key; an Ossie expression cannot.
FANOUT_UNSAFE_AGGS = frozenset({"sum", "avg"})

# The Ossie result datatype Cube itself declares for each aggregate. Only the
# count family is listed: those are exactly the aggregates whose result type does
# not depend on the operand.
AGG_TO_RESULT_DATATYPE = {
    "count": "Integer",
    "count_distinct": "Integer",
    "count_distinct_approx": "Integer",
}


def primary_key_operand(cube_name, primary_keys):
    """The single scalar expression standing for a cube's primary key.

    A composite key is concatenated the same way Cube does it (CAST + CONCAT, in
    `primaryKeyCount`); both are REQUIRED functions in the Ossie expression
    language, so the result stays portable.
    """
    if not primary_keys:
        raise ConversionError(
            f"Cube '{cube_name}': a bare `type: count` measure needs the cube's "
            f"primary key to convert safely, but no dimension declares "
            f"`primary_key: true`")
    if len(primary_keys) == 1:
        return f"{cube_name}.{primary_keys[0]}"
    parts = ", ".join(f"CAST({cube_name}.{pk} AS VARCHAR)" for pk in primary_keys)
    return f"CONCAT({parts})"


def primary_key_count_expression(cube_name, primary_keys, filter_exprs=()):
    """The Ossie expression for Cube's bare `type: count` measure.

    Cube renders such a measure as `count(<pk>)` normally and
    `count(distinct <pk>)` when the cube sits on the multiplied side of a join
    (BaseQuery `primaryKeyCount`). `COUNT(DISTINCT <pk>)` equals both -- a primary
    key is unique, so the DISTINCT is free when there is no fan-out and
    load-bearing when there is -- making it the one static form that is correct in
    every join context.
    """
    operand = filtered_operand(primary_key_operand(cube_name, primary_keys),
                               filter_exprs)
    return f"COUNT(DISTINCT {operand})"


def filtered_operand(operand, filter_sqls):
    """Fold Cube measure `filters` into the operand, the way Cube itself does.

    Cube's `applyMeasureFilters` wraps the operand as
    `CASE WHEN <filters ANDed> THEN <operand or 1> END` inside the aggregate,
    which is the filtered-aggregation idiom the Ossie expression language
    endorses. The `ELSE` is omitted, matching Cube.
    """
    if not filter_sqls:
        return operand
    where = " AND ".join(f"({f})" for f in filter_sqls)
    return f"CASE WHEN {where} THEN {operand} END"
