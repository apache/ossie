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

"""Shared constants for the Apache Ossie <-> Snowflake Cortex Analyst converters.

Both directions read their vocabulary from here so that the data type tables and
the dialect names cannot drift apart as one side is edited.
"""

import json
import re
import warnings

from sqlglot import tokenize
from sqlglot.errors import TokenError
from sqlglot.tokens import TokenType


OSSIE_VERSION = "0.2.0.dev0"

VENDOR = "SNOWFLAKE"

# Bumped when the shape of a stashed payload changes, so a reader can tell which
# layout it is looking at.
STASH_VERSION = 1

DIALECT_SNOWFLAKE = "SNOWFLAKE"
DIALECT_ANSI = "ANSI_SQL"
# OSSIE_SQL_2026 is ANSI-SQL-compatible; both directions treat it as an ANSI fallback.
DIALECT_OSSIE_SQL = "OSSIE_SQL_2026"

TIME_DATATYPES = frozenset({"Date", "Time", "DateTime", "DateTimeTz"})

# Ossie logical datatype -> the Snowflake type the export direction writes.
SNOWFLAKE_DATATYPES = {
    "String": "VARCHAR",
    "Integer": "NUMBER(38,0)",
    "Decimal": "NUMBER",
    "Float": "FLOAT",
    "Boolean": "BOOLEAN",
    "Date": "DATE",
    "Time": "TIME",
    "DateTime": "TIMESTAMP_NTZ",
    "DateTimeTz": "TIMESTAMP_TZ",
}

# The import direction cannot simply invert SNOWFLAKE_DATATYPES: a model written by
# hand or by Snowflake uses the full warehouse type vocabulary and carries
# precision/scale, so the base type name is mapped here and the numeric types are
# resolved separately (Integer vs Decimal depends on the scale, not the name).
NUMERIC_TYPES = frozenset({"NUMBER", "NUMERIC", "DECIMAL", "DEC"})

OSSIE_DATATYPES = {
    "VARCHAR": "String", "CHAR": "String", "CHARACTER": "String",
    "STRING": "String", "TEXT": "String",
    "INT": "Integer", "INTEGER": "Integer", "BIGINT": "Integer",
    "SMALLINT": "Integer", "TINYINT": "Integer", "BYTEINT": "Integer",
    "FLOAT": "Float", "FLOAT4": "Float", "FLOAT8": "Float",
    "DOUBLE": "Float", "DOUBLE PRECISION": "Float", "REAL": "Float",
    "BOOLEAN": "Boolean", "BOOL": "Boolean",
    "DATE": "Date",
    "TIME": "Time",
    "DATETIME": "DateTime", "TIMESTAMP": "DateTime",
    "TIMESTAMP_NTZ": "DateTime", "TIMESTAMPNTZ": "DateTime",
    "TIMESTAMP_TZ": "DateTimeTz", "TIMESTAMPTZ": "DateTimeTz",
    "TIMESTAMP_LTZ": "DateTimeTz", "TIMESTAMPLTZ": "DateTimeTz",
}

# Snowflake types with no portable Ossie meaning. The spec's guidance for these is
# `Opaque` plus the concrete type recorded in custom_extensions.
OPAQUE_TYPES = frozenset({
    "VARIANT", "OBJECT", "ARRAY", "MAP", "BINARY", "VARBINARY",
    "GEOGRAPHY", "GEOMETRY", "VECTOR", "FILE",
})

_TYPE_ARGS_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_ ]*?)\s*(?:\(([^)]*)\))?\s*$")


def parse_snowflake_datatype(data_type, context):
    """Map a Snowflake data type onto an Ossie logical datatype.

    Returns ``(datatype, opaque_type)``, where ``opaque_type`` is the concrete
    Snowflake type name when the result is ``Opaque``, so the caller can record it
    as the spec's guidance for ``Opaque`` requires. An unrecognized type is left
    unspecified with a warning rather than guessed at.
    """
    if data_type is None:
        return None, None

    match = _TYPE_ARGS_RE.match(str(data_type))
    if not match:
        warnings.warn(
            f"Omitting datatype from {context}: "
            f"unparseable Snowflake data type '{data_type}'"
        )
        return None, None

    base = " ".join(match.group(1).upper().split())
    args = match.group(2)

    if base in NUMERIC_TYPES:
        # NUMBER(p,0) and NUMBER(p) are integers; a bare NUMBER and any NUMBER(p,s)
        # with a non-zero scale are not. This mirrors the export direction, which
        # writes Integer as NUMBER(38,0) and Decimal as NUMBER.
        if args is None:
            return "Decimal", None
        return ("Integer" if _numeric_scale(args) == 0 else "Decimal"), None

    if base in OPAQUE_TYPES:
        return "Opaque", str(data_type).strip()

    datatype = OSSIE_DATATYPES.get(base)
    if datatype is None:
        warnings.warn(
            f"Omitting datatype from {context}: "
            f"unrecognized Snowflake data type '{data_type}'"
        )
    return datatype, None


def _numeric_scale(args):
    """Return the scale of a NUMBER(precision, scale) argument list.

    Snowflake defaults the scale to 0 when only a precision is given, so
    ``NUMBER(38)`` is as much an integer as ``NUMBER(38,0)``. A scale that is not
    an integer literal is reported as non-zero, which maps to the wider Decimal.
    """
    parts = [part.strip() for part in args.split(",")]
    if len(parts) < 2:
        return 0
    try:
        return int(parts[1])
    except ValueError:
        return -1


def write_stash(obj, data):
    """Attach a SNOWFLAKE `custom_extensions` entry holding `data` (a dict).

    No-op when `data` is empty, so a model with nothing vendor-specific to carry
    stays clean: every stashed entry is one the next spoke in the hub has to warn
    about and drop, so the converters only stash from an explicit allowlist.
    Merges into an existing SNOWFLAKE entry if one is already present.
    """
    if not data:
        return
    payload = {"_v": STASH_VERSION}
    payload.update(data)
    blob = json.dumps(payload, sort_keys=True)
    extensions = obj.setdefault("custom_extensions", [])
    for extension in extensions:
        if extension.get("vendor_name") == VENDOR:
            extension["data"] = blob
            return
    extensions.append({"vendor_name": VENDOR, "data": blob})


def read_stash(obj):
    """Return the SNOWFLAKE stash dict on an Ossie object, or {} if absent.

    The `_v` version marker is stripped from the returned dict.
    """
    for extension in (obj or {}).get("custom_extensions") or []:
        if extension.get("vendor_name") == VENDOR:
            try:
                data = json.loads(extension.get("data") or "{}")
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"SNOWFLAKE custom_extensions data is not valid JSON: {e}"
                ) from e
            if not isinstance(data, dict):
                raise ValueError(
                    "SNOWFLAKE custom_extensions data must be a JSON object, got "
                    f"{type(data).__name__}"
                )
            data.pop("_v", None)
            return data
    return {}


# The vocabulary `is_portable_sql` recognizes: standard functions every spoke in
# the hub can be expected to read, and the token kinds an expression may be built
# from. Both are deliberately allowlists -- mislabelling Snowflake-only SQL as
# portable produces SQL that fails in another warehouse, which is worse than the
# conservative label.
_ANSI_FUNCTIONS = frozenset({
    "ABS", "AVG", "CAST", "CEIL", "CEILING", "CHAR_LENGTH", "COALESCE",
    "CONCAT", "COUNT", "CUME_DIST", "DENSE_RANK", "EXTRACT", "FIRST_VALUE",
    "FLOOR", "GREATEST", "LAG", "LAST_VALUE", "LEAD", "LEAST", "LENGTH",
    "LOWER", "LTRIM", "MAX", "MIN", "MOD", "NTH_VALUE", "NTILE", "NULLIF",
    "PERCENT_RANK", "POSITION", "POWER", "RANK", "ROUND", "ROW_NUMBER",
    "RTRIM", "SQRT", "SUBSTRING", "SUM", "TRIM", "UPPER",
})

# Operators, literals, identifiers and the reserved words that appear outside a
# call (CASE ... END, IS NOT NULL, a window clause). Snowflake-only syntax lands
# outside this set and is rejected by construction: `::` is DCOLON, a variant path
# COLON, a bind variable PARAMETER, a named argument FARROW, a subscript L_BRACKET.
_PORTABLE_TOKENS = frozenset({
    TokenType.VAR, TokenType.IDENTIFIER, TokenType.STRING, TokenType.NUMBER,
    TokenType.L_PAREN, TokenType.R_PAREN, TokenType.COMMA, TokenType.DOT,
    TokenType.STAR, TokenType.PLUS, TokenType.DASH, TokenType.SLASH,
    TokenType.MOD, TokenType.EQ, TokenType.NEQ, TokenType.LT, TokenType.LTE,
    TokenType.GT, TokenType.GTE, TokenType.DPIPE,
    TokenType.ALIAS, TokenType.AND, TokenType.OR, TokenType.NOT, TokenType.IS,
    TokenType.NULL, TokenType.TRUE, TokenType.FALSE, TokenType.IN,
    TokenType.BETWEEN, TokenType.LIKE, TokenType.ESCAPE,
    TokenType.CASE, TokenType.WHEN, TokenType.THEN, TokenType.ELSE,
    TokenType.END, TokenType.DISTINCT, TokenType.OVER, TokenType.PARTITION_BY,
    TokenType.ORDER_BY, TokenType.ASC, TokenType.DESC, TokenType.ROW,
    TokenType.ROWS, TokenType.RANGE, TokenType.FROM, TokenType.FOR,
})

# The kinds a function name can arrive as, so that `IN (` and `OVER (` are not
# mistaken for calls.
_NAME_TOKENS = frozenset({TokenType.VAR, TokenType.IDENTIFIER})

# Clauses that reach the tokenizer as ordinary names, so the per-token check waves
# them through: a window function's null treatment and the timezone shift. Neither
# is read by every spoke. Standard window framing -- `ROWS BETWEEN 1 PRECEDING AND
# CURRENT ROW`, `NULLS LAST` -- is portable, so it is deliberately absent.
_NON_PORTABLE_SEQUENCES = (
    ("IGNORE", "NULLS"),
    ("RESPECT", "NULLS"),
    ("AT", "TIME", "ZONE"),
)

# A window function's null ordering, which follows the call it orders. The `FROM` in
# `EXTRACT(DAY FROM last)` is standard SQL and tokenizes the same way, so the
# closing parenthesis of the call is what tells the two apart.
_NON_PORTABLE_AFTER_CALL = (
    ("FROM", "FIRST"),
    ("FROM", "LAST"),
)

# Type names, which reach the tokenizer as their own kinds inside CAST(x AS t).
_PORTABLE_TYPE_TOKENS = frozenset({
    TokenType.BIGINT, TokenType.BOOLEAN, TokenType.CHAR, TokenType.DATE,
    TokenType.DATETIME, TokenType.DECIMAL, TokenType.DOUBLE, TokenType.FLOAT,
    TokenType.INT, TokenType.SMALLINT, TokenType.TEXT, TokenType.TIME,
    TokenType.TIMESTAMP, TokenType.VARCHAR,
})


def _starts_sequence(words, start, sequences):
    """Whether any of ``sequences`` runs from ``start`` in ``words``."""
    return any(
        tuple(words[start:start + len(sequence)]) == sequence
        for sequence in sequences
    )


def is_portable_sql(expr):
    """Whether `expr` uses only SQL that every Ossie spoke can be expected to read.

    A conservative check over Snowflake's own tokens, not a parse: an expression
    qualifies when every token is one of the kinds above and every call names a
    standard function. Anything unrecognized -- a Snowflake builtin such as IFF or
    ZEROIFNULL, a `::` cast, a variant path -- disqualifies it.
    """
    if not isinstance(expr, str) or not expr.strip():
        return False

    try:
        tokens = tokenize(expr, read=DIALECT_SNOWFLAKE.lower())
    except TokenError:
        return False

    words = [token.text.upper() for token in tokens]
    for start in range(len(tokens)):
        if _starts_sequence(words, start, _NON_PORTABLE_SEQUENCES):
            return False
        after_call = start > 0 and tokens[start - 1].token_type is TokenType.R_PAREN
        if after_call and _starts_sequence(words, start, _NON_PORTABLE_AFTER_CALL):
            return False

    for index, token in enumerate(tokens):
        called = (
            index + 1 < len(tokens)
            and tokens[index + 1].token_type is TokenType.L_PAREN
        )
        if token.token_type in _PORTABLE_TYPE_TOKENS:
            # A type word is portable as a cast target, `CAST(x AS DATE)`, or as
            # a column named `date`. Called, it is a Snowflake builtin instead --
            # `DATE(ts)`, `CHAR(65)` (which is CHR) -- except for the precision in
            # `CAST(x AS DECIMAL(10, 2))`, which the preceding `AS` marks.
            after_as = index > 0 and tokens[index - 1].token_type is TokenType.ALIAS
            if called and not after_as and words[index] not in _ANSI_FUNCTIONS:
                return False
            continue
        if token.token_type not in _PORTABLE_TOKENS:
            return False
        if token.token_type not in _NAME_TOKENS:
            continue
        # A bare name is a column reference; a name followed by `(` is a call, and
        # only the standard functions are portable.
        if called and words[index] not in _ANSI_FUNCTIONS:
            return False
    return True


def require_list(value, context, error):
    """Return ``value`` as a list, raising ``error`` for anything else.

    A scalar written where a collection belongs is otherwise iterated and reaches
    the caller as a ``TypeError`` traceback rather than as a conversion error.
    ``context`` names the offending key the way the caller's other messages do.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise error(f"{context} must be a list, got {type(value).__name__}")
    return value


def require_mappings(value, context, error):
    """Return ``value`` as a list of mappings, raising ``error`` for anything else.

    Most collections on both sides of the conversion are lists of mappings, and a
    mapping written where a list belongs otherwise reaches ``.get`` as an
    ``AttributeError`` traceback rather than as a conversion error.
    """
    entries = require_list(value, context, error)
    for entry in entries:
        if not isinstance(entry, dict):
            raise error(
                f"{context} must contain mappings, got {type(entry).__name__}"
            )
    return entries
