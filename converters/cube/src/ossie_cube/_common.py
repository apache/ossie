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

import dataclasses
import datetime
import json
import re

import yaml

# Ossie semantic model spec version this converter targets (see core-spec).
OSSIE_VERSION = "0.2.0.dev0"

# Vendor id used for the `custom_extensions` stash.
VENDOR = "CUBE"

# The Ossie model name import synthesizes when the Cube model offers none -- no view is
# mapped and `--name` was not given. Shared so export can recognize a name it did *not*
# synthesize and therefore has to preserve; two copies of the literal would drift.
DEFAULT_MODEL_NAME = "cube_model"

# Cube SQL is the SQL of the model's data source, so there is no CUBE entry in
# the Ossie dialect enum. Import emits ANSI_SQL; export prefers ANSI_SQL and lets
# the caller prepend a warehouse dialect the actual data source would accept.
DIALECT_ANSI = "ANSI_SQL"

# Dialects whose expressions are SQL a warehouse executes, so Cube can pass them
# straight to the data source. The spec's enum also contains MDX, TABLEAU and MAQL,
# which are query or calculation languages rather than warehouse SQL -- an expression
# in one of those is not usable as a Cube `sql` at all.
WAREHOUSE_DIALECTS = frozenset({DIALECT_ANSI, "SNOWFLAKE", "DATABRICKS", "BIGQUERY"})

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

# One identifier: a regular one, or an ANSI double-quoted one (with `""` escaping an
# embedded quote).
_IDENT_PART = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_]*)'

# `cube.member`, where either part may be quoted: `orders.amount`, `"Orders"."Amount"`,
# `orders."Amount"`. The guards stop `a.b.c` and `1.5` from matching.
DOTTED_REF_RE = re.compile(
    rf'(?<![\w.$"]){_IDENT_PART}\s*\.\s*{_IDENT_PART}(?![\w.])'
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


def json_safe(value):
    """Coerce YAML scalars JSON cannot hold into strings, recursively.

    The stash is a JSON blob, and PyYAML resolves an unquoted `2022-01-01` to a
    `datetime.date` -- which `json.dumps` refuses, so a Cube model with a date in an
    access policy used to abort the conversion with a raw TypeError. Dates become ISO
    strings, which is what Cube compares against anyway (every value in a policy
    filter reaches SQL as text).
    """
    if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    return value


def write_stash(obj, data):
    """Attach a CUBE `custom_extensions` entry holding `data` (a dict).

    No-op when `data` is empty, so hand-authored Ossie stays clean. Merges into an
    existing CUBE entry if one is already present.
    """
    if not data:
        return
    payload = {"_v": STASH_VERSION}
    payload.update(json_safe(data))
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
    """Choose the SQL string for an Ossie expression. Returns (sql, dialect).

    Preference order: the caller-chosen warehouse dialect (Cube passes SQL through to
    the data source, so e.g. SNOWFLAKE SQL is valid on a Snowflake-backed Cube model),
    then ANSI_SQL, then the first dialect on offer that is warehouse SQL.

    The last step matters for real interop. Converters commonly emit their own dialect
    and no ANSI: everything from the Databricks converter is `DATABRICKS`. Requiring
    ANSI meant a Databricks-authored model exported to an *empty* Cube model, every
    field and metric dropped.

    Only `WAREHOUSE_DIALECTS` qualify. An expression in MDX, TABLEAU or MAQL is not SQL
    a warehouse can run, so there is nothing to fall back *to* -- those still drop, with
    the issue saying so. `(None, None)` means nothing usable was found.

    Taking the *first* rather than insisting on a sole candidate matters: an expression
    offering SNOWFLAKE and BIGQUERY but no ANSI has no single obvious choice, and requiring
    one dropped the field altogether. Cube passes SQL to one data source, so picking in
    document order and reporting it keeps the model; the alternatives are parked, so
    nothing is lost on the way back.
    """
    dialects = [(d.get("dialect"), d.get("expression"))
                for d in (ossie_expression or {}).get("dialects") or []
                if d.get("expression") is not None]
    by_dialect = dict(dialects)
    for candidate in (preferred, DIALECT_ANSI):
        if candidate and candidate in by_dialect:
            return _checked_expression(by_dialect[candidate]), candidate
    for dialect, expr in dialects:
        if dialect in WAREHOUSE_DIALECTS:
            return _checked_expression(expr), dialect
    return None, None


def _checked_expression(expr):
    if not isinstance(expr, str):
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


def cube_sql_to_ossie(sql, own_cube, resolve_ref=None, self_prefix=None,
                      cube_names=()):
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
    known_cubes = set(cube_names)
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
            if body in known_cubes:
                # Another cube's alias (`{users}.ltv` -- a raw column of the joined
                # cube), so the trailing `.column` hangs off *that* cube. Prefixing it
                # with the own cube produced `orders.users.ltv`, a three-part name no
                # reference matches -- which also hid it from the fan-out analysis.
                return body
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


def safe_relative_path(path, what):
    """Validate a stashed file path before it is used as an output filename.

    The stash is part of the input document, so a path in it is untrusted: an entry
    like `../../etc/thing.yml` in `cube_files` would make export write outside the
    directory the caller named. Refuses anything that is not a plain relative path
    inside the output root.
    """
    raw = str(path)
    if not raw.strip():
        raise ConversionError(f"{what}: stashed file path is empty")
    if raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", raw):
        raise ConversionError(
            f"{what}: stashed file path '{raw}' is absolute; expected a path "
            f"relative to the output directory")
    parts = [p for p in raw.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise ConversionError(
            f"{what}: stashed file path '{raw}' escapes the output directory")
    if not parts:
        raise ConversionError(f"{what}: stashed file path '{raw}' names no file")
    return "/".join(parts)


def escape_braces_for_cube(value):
    """Escape `{`/`}` in every string of `value` (recursing into lists and dicts).

    Cube compiles *every* string in a YAML model as a Python f-string -- only the
    handful of boolean-ish keys in the compiler's `nonStringFields` are exempt -- so
    an unescaped brace in a description, an AI context, or a parked JSON blob is read
    as an interpolation and the model fails to compile. `\\{` / `\\}` is Cube's escape
    for a literal brace.

    Applied only to strings this converter puts there from Ossie. Content restored
    from a Cube stash is left byte-identical: it was written for Cube in the first
    place, so its braces are already whatever Cube needs them to be.
    """
    if isinstance(value, str):
        return value.replace("{", "\\{").replace("}", "\\}")
    if isinstance(value, list):
        return [escape_braces_for_cube(v) for v in value]
    if isinstance(value, dict):
        return {k: escape_braces_for_cube(v) for k, v in value.items()}
    return value


def unescape_braces_from_cube(value):
    """Undo `escape_braces_for_cube` when reading a Cube model back."""
    if isinstance(value, str):
        return value.replace("\\{", "{").replace("\\}", "}")
    if isinstance(value, list):
        return [unescape_braces_from_cube(v) for v in value]
    if isinstance(value, dict):
        return {k: unescape_braces_from_cube(v) for k, v in value.items()}
    return value


def quoted_runs(sql):
    """Split SQL into (text, is_quoted) runs, delimiters included in the quoted run.

    Used by the **export** direction only, to keep a rewrite out of string literals
    and delimited identifiers. Import deliberately does not do this: a Cube YAML
    `sql` is compiled as a Python f-string (`f"<sql>"` in YamlCompiler), so `{CUBE}`
    interpolates anywhere in the value -- SQL's own quotes are ordinary characters to
    it. Skipping quoted text on the way in would therefore *lose* a reference Cube
    really does resolve.

    Only a *string literal* counts: `'` and backtick open a run. An ANSI double-quoted
    run is a quoted *identifier* -- a name, not text -- so it stays parseable, and
    `DOTTED_REF_RE` matches it as one identifier part. Treating it as opaque left
    a valid `SUM("Orders"."Amount")` as raw SQL, bypassing the member it names.

    A run is closed by its own delimiter, and an unterminated one runs to the end
    (reported quoted, so nothing in it is rewritten). SQL's `''` doubling needs no
    special case: it reads as a close immediately followed by an open, leaving an empty
    unquoted run between.
    """
    runs, buf, quote = [], [], None
    for ch in str(sql):
        if quote:
            buf.append(ch)
            if ch == quote:
                runs.append(("".join(buf), True))
                buf, quote = [], None
        elif ch in "'`":
            if buf:
                runs.append(("".join(buf), False))
            buf, quote = [ch], ch
        else:
            buf.append(ch)
    if buf:
        runs.append(("".join(buf), quote is not None))
    return runs


def sub_outside_quotes(sql, transform):
    """Apply `transform` to the parts of `sql` outside quoted runs."""
    return "".join(text if quoted else transform(text)
                   for text, quoted in quoted_runs(sql))


# --- identifiers -----------------------------------------------------------------
#
# Resolving a reference means matching what someone *wrote* against what the model
# *declares*, and Ossie's rules make those two different strings: a regular identifier is
# case-insensitive while a quoted one is exact. Both sides are reduced to the same small
# set of match keys, so there is one notion of "could these be the same identifier"
# rather than one per call site.

def _unquoted(text):
    """(content, was_quoted) for an ANSI-quoted identifier, with `""` unescaped."""
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1].replace('""', '"'), True
    return text, False


def normalize_identifier(name):
    """An Ossie identifier in the spec's *normalized* form.

    From core-spec/expression_language.md: "Regular identifiers (unquoted) should be
    case insensitive [...] Regular identifiers are upper cased; quoted identifiers have
    their quotes stripped". So `orders.AMOUNT` addresses the field `amount`, and matching
    them exactly -- as this converter used to -- emitted `{CUBE}.AMOUNT`, a raw column
    that bypasses the member's own expression entirely.

    Cube identifiers, by contrast, *are* case-sensitive, so the canonical Cube spelling
    is what gets emitted; this form is only used to find it.
    """
    content, quoted = _unquoted(str(name).strip())
    return content if quoted else content.upper()


def match_keys(identifier):
    """Every key this identifier can be matched by, most specific first.

    A quoted identifier matches on its content alone -- that is what "exact" means, and
    it is also how a name that *must* be quoted stays referenceable: `"Order Items"` is
    the only way to write that dataset's name. An unquoted one matches its own spelling
    (so a declared name spelled the same way is found) and its normalized form (so any
    casing is).
    """
    content, quoted = _unquoted(str(identifier).strip())
    if quoted:
        return (content,)
    return (content, content.upper())


def normalized_expression(expr):
    """`expr` with every `dataset.member` reference in its normalized form.

    For comparing two expressions that mean the same thing. Ossie identifiers are
    case-insensitive, so `COUNT(DISTINCT DIM_0.ID)` and `COUNT(DISTINCT dim_0.id)` are one
    expression -- and comparing them exactly made the primary-key count go unrecognized
    whenever the metric spelled the key in another case.
    """
    return DOTTED_REF_RE.sub(
        lambda m: ".".join(normalize_identifier(part)
                           for part in split_dotted_ref(m.group(0))),
        str(expr))


def datasets_in_expression(expr, prepared):
    """Dataset names referenced in `expr`, resolved against a prepared lookup map.

    Split from `referenced_datasets` so a caller holding prepared tables does not rebuild
    the map for every expression.
    """
    found = set()
    for text, quoted in quoted_runs(expr):
        if quoted:
            continue
        for match in DOTTED_REF_RE.finditer(text):
            head, _ = split_dotted_ref(match.group(0))
            name = resolve_identifier(prepared, head)
            if name is not None:
                found.add(name)
    return found


def referenced_datasets(expr, known):
    """The dataset names an Ossie expression references, ignoring quoted text.

    Decides which cube a measure lands on and whether it crosses cubes, so a name
    that only appears inside a string literal must not count -- otherwise
    `SUM(orders.amount) || ' per users.id unit'` reads as a two-dataset metric and
    gets attributed to the base cube rather than to `orders`.
    """
    # What callers need back is the *canonical* name -- one they can place a measure
    # under. Returning the spelling as written filed it under a cube that does not exist,
    # and the measure vanished with no issue reported.
    return datasets_in_expression(expr, lookup_map(known))


def split_dotted_ref(text):
    """Split a matched `cube.member` reference into its two identifier parts.

    Done on the match rather than with capture groups because either part may be a
    quoted identifier containing a dot (`"My.Cube".amount`).
    """
    depth_quote, split_at = False, None
    for i, ch in enumerate(text):
        if ch == '"':
            depth_quote = not depth_quote
        elif ch == "." and not depth_quote:
            split_at = i
            break
    if split_at is None:
        return text.strip(), ""
    return text[:split_at].strip(), text[split_at + 1:].strip()


def quoted_char_mask(sql):
    """One flag per character: True where it sits inside *any* quoted region.

    Deliberately wider than `quoted_runs`, and the two are not interchangeable. Rewriting
    a reference must look inside a double-quoted identifier, because that is a name.
    *Finding an aggregate call* must not: `orders."SUM(X)"` is a column whose name happens
    to contain `SUM(`, and treating it as a call produced a bogus hidden measure and
    malformed SQL. So single quotes, double quotes and backticks are all opaque here.
    """
    mask, quote = [], None
    for ch in str(sql):
        if quote:
            mask.append(True)
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            mask.append(True)
            quote = ch
        else:
            mask.append(False)
    return mask


def lookup_map(names):
    """{match key: the name to emit}, from a set of names or a mapping of spellings.

    A mapping lets several accepted spellings resolve to one canonical name -- which is
    how an Ossie dataset name and the Cube name it sanitizes to both reach the Cube one.
    """
    pairs = names.items() if isinstance(names, dict) else ((n, n) for n in names)
    out = {}
    for spelling, canonical in pairs:
        for key in match_keys(spelling):
            out.setdefault(key, canonical)
    return out


def resolve_identifier(mapping, written):
    """What `written` names in a `lookup_map`, or None."""
    for key in match_keys(written):
        if key in mapping:
            return mapping[key]
    return None


def source_part_count(source):
    """How many identifier parts a dotted dataset `source` has, or None for a query.

    Dots inside double quotes or backticks belong to a quoted identifier, not to the
    path -- `"My.Catalog".public.t` is three parts, not four.
    """
    s = str(source).strip()
    if re.match(r"(?i)(select|with)\b", s):
        return None
    parts, quote = 1, None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
        elif ch in '"`':
            quote = ch
        elif ch == ".":
            parts += 1
    return parts


def sql_is_reversible(sql, plain_members=(), own_cube=None):
    """True if translating this Cube SQL to Ossie and back reproduces it.

    `{CUBE}.column` / `{TABLE}.column` -- a raw physical column of the owning cube --
    always survives, because Ossie expressions address columns and the exporter
    re-emits them in that form.

    A *member* reference (`{CUBE.member}`, `{member}`) survives only when the member
    is **plain**: its own `sql` is just the same-named column, so the reference and
    the raw column are the same thing. Otherwise Cube inlines the member's own SQL,
    which a bare column name would not reproduce, and the original spelling has to be
    kept.

    A **cross-cube** reference never survives: `{other.member}` is what makes Cube
    add the implicit join, and the raw `{other}.column` form does not, so the two are
    not interchangeable.
    """
    if not isinstance(sql, str):
        sql = str(sql)
    plain = set(plain_members)
    protected = sql.replace("\\{", "").replace("\\}", "")
    for m in _CUBE_REF_RE.finditer(protected):
        body = m.group(1).strip()
        head, _, rest = body.partition(".")
        if not rest:
            if body in _SELF_REFS or (own_cube and body == own_cube):
                # A bare alias only makes sense followed by `.column`.
                if not protected[m.end():].startswith("."):
                    return False
                continue
            # `{member}` -- an unqualified own-cube member reference.
            if body not in plain:
                return False
            continue
        if head in _SELF_REFS or (own_cube and head == own_cube):
            if rest not in plain:
                return False
            continue
        return False  # cross-cube reference; carries join semantics
    return True


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


@dataclasses.dataclass(frozen=True)
class ReferenceTables:
    """The prepared lookups `ossie_expr_to_cube_sql` resolves a reference against.

    Built once per model rather than per expression. Passing the raw name collections
    instead meant every measure rewrite rebuilt a lookup map for every cube's members --
    six maps per call, over the same names each time -- which is the cost of threading
    collections through a signature rather than preparing them once.
    """

    datasets: dict           # match key -> Cube cube name
    references: dict         # cube -> match key -> member needing `{CUBE.member}`
    columns: dict            # cube -> match key -> canonical column/dimension name
    inline_sql: dict         # cube -> match key -> Cube SQL to substitute

    @classmethod
    def of(cls, cube_names=(), references_by_cube=None, columns_by_cube=None,
           inline_sql_by_cube=None):
        def per_cube(source, prepare):
            return {normalize_identifier(cube): prepare(value)
                    for cube, value in (source or {}).items()}

        return cls(
            datasets=lookup_map(cube_names),
            references=per_cube(references_by_cube, lookup_map),
            columns=per_cube(columns_by_cube, lookup_map),
            # Keyed with the same match logic as every other table. Using only the
            # normalized form meant an exact-quoted reference to a split geo half --
            # `users."home_latitude"` -- missed its substitution and came out as a raw
            # column of that name, which exists in Ossie and not in the database.
            inline_sql=per_cube(
                inline_sql_by_cube,
                lambda fields: {key: sql for f, sql in fields.items()
                                for key in match_keys(f)}),
        )

    def for_cube(self, cube, attribute):
        return getattr(self, attribute).get(normalize_identifier(cube)) or {}

    def datasets_in(self, expr):
        """The datasets `expr` references, by canonical Cube name."""
        return datasets_in_expression(expr, self.datasets)


def ossie_expr_to_cube_sql(expr, own_cube, tables):
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

    A dotted token inside a string literal is left alone. This matters more than it
    looks: Cube compiles a YAML `sql` as a Python f-string, so a `{...}` it emitted
    into a literal would be interpolated at compile time and replace the literal's
    own text with a column reference.
    """
    escaped = str(expr).replace("{", "\\{").replace("}", "\\}")
    known = tables.datasets
    members = tables.for_cube(own_cube, "references")
    own_columns = tables.for_cube(own_cube, "columns")
    own_norm = normalize_identifier(own_cube) if own_cube else None

    def repl(m):
        head, name = split_dotted_ref(m.group(0))
        # Ossie regular identifiers are case-insensitive, so the reference is matched
        # in normalized form; what is *emitted* is the canonical Cube spelling, since
        # Cube's own member lookup is case-sensitive.
        head_keys, name_keys = match_keys(head), match_keys(name)
        # Resolve the dataset first, then decide which branch applies. Comparing the
        # written spelling against `own_cube` directly was wrong once the two could
        # differ: dataset `Order Items` becomes cube `order_items`, so a reference to
        # `"ORDER ITEMS"` took the cross-cube branch on its own cube.
        target = resolve_identifier(known, head)
        is_own = target == own_cube or (target is None and own_norm in head_keys)
        # Keyed on the *resolved* cube, not the token as written: a sanitized dataset name
        # differs from the Ossie one, and looking inline SQL up by the written token meant
        # a split geo half referenced through the Ossie name was never substituted.
        inline_for = tables.for_cube(target, "inline_sql") if target else {}
        substitute = resolve_identifier(inline_for, name)
        if substitute is not None:
            # Already-Cube SQL, so it bypasses the escaping above; `{CUBE}` inside
            # it means `head`, which only stays true while head is the own cube.
            return (str(substitute) if is_own
                    else requalify_self_refs(substitute, target or head))
        if is_own:
            member = resolve_identifier(members, name)
            if member is not None:
                return "{CUBE." + member + "}"
            # A plain member is the same thing either way, but the *column* still has a
            # canonical spelling -- emitting `"AMOUNT"` as written would force an exact
            # uppercase match in the database against a column named `amount`.
            column = resolve_identifier(own_columns, name)
            return "{CUBE}." + (column if column is not None else name)
        if target is not None:
            # A cross-cube member needs the target cube's own spelling for the same
            # reason: `{users.ID}` does not resolve when the member is declared `id`.
            member = resolve_identifier(tables.for_cube(target, "columns"), name)
            return "{" + target + "." + (member if member is not None else name) + "}"
        # Not a dataset in this model -- a genuine schema-qualified table
        # reference or an unrelated dotted token. Leave it alone.
        return m.group(0)

    return sub_outside_quotes(
        escaped, lambda run: DOTTED_REF_RE.sub(repl, run))


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
    # Cube collapses Integer/Decimal/Float into one type, so no mapping back is
    # exact. `Decimal` is chosen over omitting a datatype because a downstream
    # converter can use it: exact base-10 is the safe reading for the money and
    # quantity columns `number` overwhelmingly holds, and asserting it beats
    # emitting nothing plus a Cube-only extension no other spoke reads. When the
    # model came from Ossie in the first place, the precise datatype is recovered
    # from `meta.ossie.datatype` instead of guessed.
    "number": "Decimal",
}

# The datatype each Cube type maps back to by default. Export parks the original in
# `meta.ossie.datatype` only when it is *not* the default -- Cube cannot hold the
# distinction, and `meta.ossie` is Cube-side, so this keeps Ossie -> Cube -> Ossie
# exact without putting anything in `custom_extensions`.
DEFAULT_DATATYPE_FOR_CUBE_TYPE = dict(DIM_TYPE_TO_DATATYPE)

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
