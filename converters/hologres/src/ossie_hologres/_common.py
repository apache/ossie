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

"""Shared helpers for the Apache Ossie <-> Hologres Semantic View converters.

Both directions are pure offline transforms. Cross-cutting concerns live here:
version constants, the dialect preference order, the `custom_extensions` stash
protocol, SQL identifier/literal quoting, and the sqlglot expression layer that
qualifies and unqualifies column references.
"""

import json
import re

import sqlglot
import yaml
from sqlglot import exp

# Apache Ossie semantic model spec version this converter targets (see core-spec).
#
# NOTE: this is an exact-match check. Like the Databricks spoke, this converter
# intentionally has no `apache-ossie` package dependency, so nothing updates this
# automatically -- it MUST be bumped in lockstep with the `version` in `core-spec/`
# whenever the spec version moves, or the converter will reject otherwise-valid
# Apache Ossie files.
OSSIE_VERSION = "0.2.0.dev0"

# Vendor id used for the `custom_extensions` stash. This is a vendor name, not a
# dialect: the Ossie Vendor field is free-form, so it needs no spec change.
VENDOR = "HOLOGRES"

# The only expression dialect this converter reads or writes.
#
# Hologres is PostgreSQL-compatible, but that does not justify a vendor dialect token:
# the portable spellings are almost always available (`CAST(x AS TEXT)` rather than
# `x::text`), and sqlglot normalizes to them. For the genuinely PostgreSQL-only syntax
# that remains, tagging it as a vendor dialect would be worse than labelling it
# ANSI_SQL, because a converter looking for an ANSI_SQL expression and finding none
# drops the field entirely. So, as the NVIDIA GSF converter puts it, anything else
# stays ANSI_SQL rather than being labelled inaccurately.
DIALECT_ANSI = "ANSI_SQL"

# Hologres is PostgreSQL wire- and dialect-compatible, so sqlglot parses and
# generates its expressions with the postgres dialect.
SQLGLOT_DIALECT = "postgres"

# Bump when the shape of a stashed `data` blob changes.
STASH_VERSION = 1

# Metric-level stash key naming the owning table alias. Only needed for metrics
# whose expression has no column reference (`count(*)`), where the owner cannot be
# recovered from the expression itself.
STASH_OWNER = "owner"

# Model-level stash key recording the schema the Semantic View itself lives in.
# `CREATE SEMANTIC VIEW public.sales_sv` has no Ossie home -- the document's root
# `name` holds only the bare view name.
STASH_VIEW_SCHEMA = "view_schema"

# The only aggregate functions Hologres METRICS accept, keyed by sqlglot node type.
# Membership is tested by exact type, not `isinstance(node, exp.AggFunc)`: `stddev`
# and `percentile_cont` are also AggFuncs but Hologres rejects them.
METRIC_AGGREGATES = {
    exp.Count: "count",
    exp.Sum: "sum",
    exp.Avg: "avg",
    exp.Min: "min",
    exp.Max: "max",
}

# Expression shapes Hologres forbids in a DIMENSIONS or METRICS definition, which
# must be a row-level expression over a single physical alias. Order matters: a
# windowed aggregate and an aggregate FILTER clause both *contain* an AggFunc, so the
# more specific shapes are checked first to produce the more accurate message.
_NON_ROW_LEVEL = {
    exp.Window: "a window function",
    exp.Filter: "an aggregate FILTER clause",
    exp.Select: "a subquery",
    exp.Subquery: "a subquery",
    exp.AggFunc: "an aggregate function",
}

# A bare SQL identifier that needs no quoting, e.g. `svacc_orders`. Uppercase is
# excluded on purpose: PostgreSQL folds unquoted identifiers to lower case, so an
# identifier that is not already lower case must be quoted to survive.
_BARE_IDENTIFIER_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# PostgreSQL keywords that cannot appear as a bare table or column name. sqlglot's
# generator does not quote these (it emits `select` for the identifier `select`,
# which is a syntax error), so the DDL writer consults this set itself. Covers the
# "reserved" and "reserved (cannot be function or type name)" categories of the
# PostgreSQL keyword table.
_RESERVED_WORDS = frozenset(
    """
    all analyse analyze and any array as asc asymmetric both case cast check
    collate column constraint create current_catalog current_date current_role
    current_time current_timestamp current_user default deferrable desc distinct
    do else end except false fetch for foreign from grant group having in initially
    intersect into lateral leading limit localtime localtimestamp not null offset
    on only or order placing primary references returning select session_user some
    symmetric table then to trailing true union unique user using variadic when
    where window with authorization binary collation concurrently cross
    current_schema freeze full ilike inner is isnull join left like natural notnull
    outer overlaps right similar tablesample verbose
    """.split()
)


class ConversionError(Exception):
    """Raised when an input cannot be converted."""


def require(obj, key, what):
    """Return `obj[key]`, or raise a clean ConversionError if it is missing/empty -- so
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
    (e.g. a YAML number used as a name or expression) raises a clean ConversionError
    instead of crashing later in a string operation."""
    value = require(obj, key, what)
    if not isinstance(value, str):
        raise ConversionError(f"{what}: '{key}' must be a string, got {type(value).__name__}")
    return value


# YAML 1.1 (PyYAML's default) treats bare on/off/yes/no/y/n as booleans. Hologres emits
# its `model_yaml` with YAML 1.2 semantics, so a dimension literally named `no` or a
# description of `on` would silently become a Python bool and be written into Ossie as
# `true`/`false`. The Loader below uses 1.2 semantics; the Dumper additionally
# force-quotes those tokens on output so the YAML it emits round-trips the same way
# through a YAML 1.1 reader (e.g. stock yaml.safe_load).
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
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_Yaml12Dumper.add_representer(str, _represent_str)


def load_yaml(text):
    """Parse YAML with 1.2 boolean semantics. A syntax error is surfaced as a
    ConversionError so callers (and the CLI) get a clean message, not a traceback."""
    try:
        return yaml.load(text, Loader=_Yaml12Loader)
    except yaml.YAMLError as e:
        raise ConversionError(f"Invalid YAML: {e}") from e


def dump_yaml(obj):
    """Serialize to YAML with 1.2 boolean semantics, preserving key insertion order."""
    return yaml.dump(
        obj,
        Dumper=_Yaml12Dumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )


def is_simple_identifier(text):
    """True if `text` is a bare lower-case SQL identifier needing no quoting."""
    return isinstance(text, str) and bool(_BARE_IDENTIFIER_RE.match(text.strip()))


def needs_quoting(name):
    """True if `name` must be double-quoted to be a valid SQL identifier.

    Anything not already a bare lower-case identifier needs quoting because PostgreSQL
    folds unquoted identifiers to lower case, and reserved keywords need it because they
    are a syntax error when left bare.
    """
    return not is_simple_identifier(name) or name.strip() in _RESERVED_WORDS


def quote_identifier(name, what):
    """Render `name` as a SQL identifier, double-quoting it only when necessary."""
    if not isinstance(name, str) or not name.strip():
        raise ConversionError(f"{what}: identifier must be a non-empty string")
    name = name.strip()
    if "\x00" in name:
        raise ConversionError(f"{what}: identifier contains a NUL byte")
    if not needs_quoting(name):
        return name
    escaped = name.replace('"', '""')
    return f'"{escaped}"'


def quote_literal(text, what):
    """Render `text` as a SQL string literal for a COMMENT clause.

    Single quotes are doubled. No backslash escaping is applied: PostgreSQL (and
    Hologres) default to `standard_conforming_strings = on`, where a backslash in a
    plain literal is an ordinary character.
    """
    if not isinstance(text, str):
        raise ConversionError(f"{what}: comment must be a string, got {type(text).__name__}")
    if "\x00" in text:
        raise ConversionError(f"{what}: comment contains a NUL byte")
    escaped = text.replace("'", "''")
    return f"'{escaped}'"


def read_stash(obj):
    """Return the HOLOGRES stash dict on an Apache Ossie object, or {} if absent.

    The `_v` version marker is stripped from the returned dict.
    """
    for ext in (obj or {}).get("custom_extensions") or []:
        if ext.get("vendor_name") == VENDOR:
            try:
                data = json.loads(ext.get("data") or "{}")
            except json.JSONDecodeError as e:
                raise ConversionError(
                    f"HOLOGRES custom_extensions data is not valid JSON: {e}"
                ) from e
            if not isinstance(data, dict):
                raise ConversionError("HOLOGRES custom_extensions data must be a JSON object")
            data.pop("_v", None)
            return data
    return {}


def write_stash(obj, data):
    """Attach a HOLOGRES `custom_extensions` entry holding `data` (a dict).

    No-op when `data` is empty, so hand-authored Apache Ossie stays clean. Merges into
    an existing HOLOGRES entry if one is already present.
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
    """Return non-HOLOGRES custom_extensions (dropped on export, with a warning)."""
    return [
        ext
        for ext in (obj or {}).get("custom_extensions") or []
        if ext.get("vendor_name") != VENDOR
    ]


def pick_expression(ossie_expression):
    """Choose the SQL string for an Apache Ossie expression.

    Returns None if there is no ANSI_SQL dialect, so the caller can raise with the name
    of the offending field or metric.
    """
    dialects = {
        d.get("dialect"): d.get("expression")
        for d in (ossie_expression or {}).get("dialects") or []
    }
    expr = dialects.get(DIALECT_ANSI)
    if expr is not None and not isinstance(expr, str):
        raise ConversionError(f"expression must be a string, got {type(expr).__name__}")
    return expr


def ossie_expression(text, dialect):
    """Build an Apache Ossie `expression` block holding a single dialect."""
    return {"dialects": [{"dialect": dialect, "expression": text}]}


def synonyms_of(ai_context):
    """Extract the synonyms list from an Apache Ossie ai_context (object form only)."""
    if isinstance(ai_context, dict):
        return list(ai_context.get("synonyms") or [])
    return []


def merge_description(description, ai_context):
    """Fold a string-form ai_context into a description.

    The Apache Ossie schema allows ai_context to be either a string or an object. A
    string has no Semantic View home of its own, so it is appended to the description
    (which maps to COMMENT). Object-form ai_context has no COMMENT equivalent at all and
    is reported as dropped by the caller.
    """
    if isinstance(ai_context, str) and ai_context.strip():
        return f"{description}\n{ai_context}" if description else ai_context
    return description


def parse_expression(text, what):
    """Parse a SQL expression with the postgres dialect, or raise ConversionError.

    Never let an unparseable expression through: the export path writes DDL straight to
    a database, so an expression we cannot understand must not be emitted verbatim.
    """
    if not isinstance(text, str) or not text.strip():
        raise ConversionError(f"{what}: expression must be a non-empty string")
    try:
        node = sqlglot.parse_one(text, dialect=SQLGLOT_DIALECT)
    except Exception as e:  # sqlglot raises ParseError/TokenError, both non-public
        raise ConversionError(f"{what}: cannot parse expression {text!r}: {e}") from e
    if node is None:
        raise ConversionError(f"{what}: cannot parse expression {text!r}")
    return node


def render_expression(node):
    """Serialize a sqlglot node back to Hologres-compatible SQL."""
    return node.sql(dialect=SQLGLOT_DIALECT)


# Top-level expression forms the CREATE SEMANTIC VIEW grammar accepts unparenthesised.
# Verified against Hologres 5.0.0: a bare operator at the top level of a definition is a
# syntax error there -- `a || b`, `a + 1` and `a::text` are all rejected while `(a || b)`,
# `(a + 1)` and `cast(a as text)` are accepted -- even though the same operators are fine
# inside a function call's argument list.
_BARE_DEFINITION_FORMS = (
    exp.Column,
    exp.Literal,
    exp.Boolean,
    exp.Null,
    exp.Func,
    exp.Case,
    exp.Paren,
)


def render_definition(node):
    """Render an expression for a DIMENSIONS or METRICS definition clause.

    Adds the parentheses the Hologres DDL grammar requires around a top-level operator,
    and leaves everything else alone so ordinary definitions stay readable.
    """
    sql = render_expression(node)
    if isinstance(node, _BARE_DEFINITION_FORMS):
        return sql
    return f"({sql})"


def normalize_expression(text, what="expression"):
    """Round-trip an expression through sqlglot to get its canonical form.

    Conversion is normalization-stable, not byte-stable: sqlglot upper-cases function
    names and rewrites `x::text` as `CAST(x AS TEXT)`. Comparisons in tests go through
    this so they assert semantic equality rather than incidental formatting.
    """
    return render_expression(parse_expression(text, what))


def strip_parens(node):
    """Unwrap redundant outer parentheses, so `(sum(x))` is treated as `sum(x)`."""
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def column_refs(node):
    """Return the (qualifier, column) pairs referenced by an expression.

    The qualifier is `""` for an unqualified column, matching sqlglot's `Column.table`.
    """
    return [(col.table, col.name) for col in node.find_all(exp.Column)]


def assert_row_level(node, what):
    """Reject expression shapes Hologres forbids in a definition.

    Hologres definitions are row-scope ASTs over a single physical alias. Volatile and
    set-returning functions are also forbidden but are not structurally detectable here;
    Hologres rejects those at CREATE SEMANTIC VIEW time.
    """
    for kind, description in _NON_ROW_LEVEL.items():
        if isinstance(node, kind) or next(node.find_all(kind), None) is not None:
            raise ConversionError(
                f"{what}: Hologres definitions must be row-level expressions over a "
                f"single table, but this contains {description}"
            )


def _apply_quoting(identifier):
    """Force `quoted` on a sqlglot identifier that cannot be emitted bare.

    sqlglot's generator does not quote reserved words, so without this a column named
    `user` on an alias named `order` renders as the invalid `order.user`.
    """
    if identifier is not None and not identifier.args.get("quoted") and needs_quoting(identifier.name):
        identifier.set("quoted", True)


def qualify_columns(node, alias, known_aliases, what):
    """Qualify every column in `node` with `alias`, in place.

    Ossie field expressions are conventionally unqualified bare columns while Hologres
    requires `alias.column`, so unqualified columns get `alias`. A column already
    qualified with a *different* dataset is a cross-table reference, which Hologres
    rejects, so it is an error here rather than invalid DDL later.
    """
    for col in node.find_all(exp.Column):
        qualifier = col.table
        if not qualifier:
            col.set("table", exp.to_identifier(alias, quoted=needs_quoting(alias)))
        elif qualifier != alias:
            if qualifier in known_aliases:
                raise ConversionError(
                    f"{what}: references table '{qualifier}' but belongs to '{alias}'; "
                    f"Hologres definitions cannot span tables"
                )
            raise ConversionError(
                f"{what}: references unknown table '{qualifier}' "
                f"(known tables: {', '.join(sorted(known_aliases))})"
            )
        else:
            _apply_quoting(col.args.get("table"))
        _apply_quoting(col.this)
    return node


def unqualify_columns(node, alias):
    """Drop the `alias.` qualifier from every column in `node` that carries it, in place.

    The inverse of qualify_columns, so an imported Ossie field expression reads as a
    plain column name the way hand-authored Ossie models do.
    """
    for col in node.find_all(exp.Column):
        if col.table == alias:
            col.set("table", None)
    return node


def metric_aggregate(node, what):
    """Validate a metric expression and return (aggregate_name, aggregate_node).

    Hologres METRICS accept exactly one whitelisted aggregate applied to a row-level
    expression over one table. Anything else -- a ratio, a sum of two aggregates, a
    CASE around an aggregate, or a non-whitelisted aggregate -- has no Semantic View
    form and is rejected here.
    """
    root = strip_parens(node)
    agg = METRIC_AGGREGATES.get(type(root))
    if agg is None:
        raise ConversionError(
            f"{what}: Hologres METRICS must be exactly one of "
            f"count/sum/avg/min/max over a single table, but the expression is "
            f"{render_expression(node)!r}. Derived and ratio metrics such as "
            f"'SUM(a) / COUNT(*)' have no Semantic View form -- compute them in the "
            f"query layer instead."
        )
    inner = root.this
    # `count(*)` has a Star argument and `count(DISTINCT x)` a Distinct wrapper; neither
    # is a row-level expression to check, but their operands are.
    if not isinstance(inner, exp.Star):
        assert_row_level(inner, what)
    return agg, root
