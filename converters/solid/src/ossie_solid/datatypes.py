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

"""Warehouse type vocabulary handling for the Solid converter.

Solid stores a column's `type` as the **raw** warehouse type string, verbatim from the
catalog -- `NUMBER(38,0)`, `TEXT`, `INT64`, `LONG` -- with no normalization. Two things
follow from that:

1. The vocabulary identifies the warehouse, which is how `infer_dialect` recovers a
   dialect that the Solid YAML itself never records (see README, "Dialect resolution").
2. Mapping it onto Apache Ossie's portable `datatype` enum is lossy (`NUMBER(38,2)`,
   `NUMERIC` and `DECIMAL` all collapse to `Decimal`), so the raw string is always kept
   in the SOLID stash and is what an export re-emits.
"""

import re

from ._common import (
    DIALECT_ANSI,
    DIALECT_BIGQUERY,
    DIALECT_DATABRICKS,
    DIALECT_SNOWFLAKE,
    SUPPORTED_DIALECTS,
    ConversionError,
    warn,
)

# Apache Ossie portable logical data types (core-spec/ossie-schema.json $defs.DataType).
STRING = "String"
INTEGER = "Integer"
DECIMAL = "Decimal"
FLOAT = "Float"
BOOLEAN = "Boolean"
DATE = "Date"
TIME = "Time"
DATE_TIME = "DateTime"
DATE_TIME_TZ = "DateTimeTz"
OPAQUE = "Opaque"

TEMPORAL_DATATYPES = frozenset({DATE, TIME, DATE_TIME, DATE_TIME_TZ})

# Type names that appear in exactly one of the three warehouses Solid exports from, and
# so identify it. Names shared across warehouses (STRING is both Databricks and
# BigQuery; STRUCT likewise, since `parse_type` strips the angle-bracket params that
# would tell them apart; TIMESTAMP_NTZ is both Snowflake and Databricks; BOOLEAN, DATE,
# FLOAT and DECIMAL are near-universal) are deliberately excluded -- no signal.
_DIALECT_MARKERS = {
    DIALECT_SNOWFLAKE: frozenset(
        {"NUMBER", "TEXT", "VARIANT", "OBJECT", "TIMESTAMP_LTZ", "TIMESTAMP_TZ",
         "GEOGRAPHY", "GEOMETRY"}
    ),
    DIALECT_DATABRICKS: frozenset(
        {"LONG", "MAP", "BIGINT", "TINYINT", "SMALLINT", "BYTE", "SHORT", "VOID"}
    ),
    DIALECT_BIGQUERY: frozenset(
        {"INT64", "FLOAT64", "BOOL", "BIGNUMERIC", "BYTES", "RECORD"}
    ),
}

# Warehouse type -> Apache Ossie datatype, for names that mean the same thing
# everywhere. Dialect-sensitive names (the TIMESTAMP family, NUMBER/NUMERIC scale) are
# resolved in `to_ossie_datatype` instead.
_COMMON_TYPES = {
    # character
    "STRING": STRING, "TEXT": STRING, "VARCHAR": STRING, "CHAR": STRING,
    "CHARACTER": STRING, "NVARCHAR": STRING, "NCHAR": STRING, "STRING_TYPE": STRING,
    # exact integral
    "INT": INTEGER, "INTEGER": INTEGER, "BIGINT": INTEGER, "SMALLINT": INTEGER,
    "TINYINT": INTEGER, "BYTEINT": INTEGER, "LONG": INTEGER, "SHORT": INTEGER,
    "BYTE": INTEGER, "INT64": INTEGER, "INT2": INTEGER, "INT4": INTEGER,
    "INT8": INTEGER, "SERIAL": INTEGER,
    # approximate
    "FLOAT": FLOAT, "FLOAT4": FLOAT, "FLOAT8": FLOAT, "FLOAT64": FLOAT,
    "DOUBLE": FLOAT, "DOUBLE PRECISION": FLOAT, "REAL": FLOAT,
    # boolean
    "BOOLEAN": BOOLEAN, "BOOL": BOOLEAN,
    # temporal (dialect-independent)
    "DATE": DATE, "TIME": TIME,
    # not representable in the portable vocabulary
    "VARIANT": OPAQUE, "OBJECT": OPAQUE, "ARRAY": OPAQUE, "MAP": OPAQUE,
    "STRUCT": OPAQUE, "RECORD": OPAQUE, "JSON": OPAQUE, "XML": OPAQUE,
    "BINARY": OPAQUE, "VARBINARY": OPAQUE, "BYTES": OPAQUE, "GEOGRAPHY": OPAQUE,
    "GEOMETRY": OPAQUE, "INTERVAL": OPAQUE, "VOID": OPAQUE, "NULL": OPAQUE,
}

# Exact-scale decimal families. Whether one is Integer or Decimal depends on the scale,
# which is parsed off the type string when present.
_DECIMAL_TYPES = frozenset({"NUMBER", "NUMERIC", "DECIMAL", "BIGNUMERIC", "DEC"})

# TIMESTAMP means different things per warehouse: BigQuery's and Databricks' are
# instants (offset-aware), Snowflake's bare TIMESTAMP is an alias for TIMESTAMP_NTZ.
_TIMESTAMP_BY_DIALECT = {
    DIALECT_SNOWFLAKE: DATE_TIME,
    DIALECT_DATABRICKS: DATE_TIME_TZ,
    DIALECT_BIGQUERY: DATE_TIME_TZ,
    DIALECT_ANSI: DATE_TIME,
}

_TIMESTAMP_VARIANTS = {
    "TIMESTAMP_NTZ": DATE_TIME,
    "TIMESTAMPNTZ": DATE_TIME,
    "DATETIME": DATE_TIME,
    "TIMESTAMP_TZ": DATE_TIME_TZ,
    "TIMESTAMPTZ": DATE_TIME_TZ,
    "TIMESTAMP_LTZ": DATE_TIME_TZ,
    "TIMESTAMPLTZ": DATE_TIME_TZ,
    "TIMESTAMP WITH TIME ZONE": DATE_TIME_TZ,
    "TIMESTAMP WITHOUT TIME ZONE": DATE_TIME,
    "TIMESTAMP_UNSPECIFIED": DATE_TIME,
}

# Apache Ossie datatype -> the type name an export writes back when the original raw
# string is unavailable (a hand-authored Apache Ossie model with no SOLID stash). Solid
# never interprets these beyond its fact/dimension split, so a representative name per
# dialect is enough.
_DATATYPE_TO_RAW = {
    DIALECT_SNOWFLAKE: {
        STRING: "TEXT", INTEGER: "NUMBER", DECIMAL: "NUMBER", FLOAT: "FLOAT",
        BOOLEAN: "BOOLEAN", DATE: "DATE", TIME: "TIME", DATE_TIME: "TIMESTAMP_NTZ",
        DATE_TIME_TZ: "TIMESTAMP_TZ", OPAQUE: "VARIANT",
    },
    DIALECT_DATABRICKS: {
        STRING: "STRING", INTEGER: "LONG", DECIMAL: "DECIMAL", FLOAT: "DOUBLE",
        BOOLEAN: "BOOLEAN", DATE: "DATE", TIME: "STRING", DATE_TIME: "TIMESTAMP_NTZ",
        DATE_TIME_TZ: "TIMESTAMP", OPAQUE: "STRING",
    },
    DIALECT_BIGQUERY: {
        STRING: "STRING", INTEGER: "INT64", DECIMAL: "NUMERIC", FLOAT: "FLOAT64",
        BOOLEAN: "BOOL", DATE: "DATE", TIME: "TIME", DATE_TIME: "DATETIME",
        DATE_TIME_TZ: "TIMESTAMP", OPAQUE: "STRING",
    },
    DIALECT_ANSI: {
        STRING: "VARCHAR", INTEGER: "INTEGER", DECIMAL: "DECIMAL", FLOAT: "DOUBLE",
        BOOLEAN: "BOOLEAN", DATE: "DATE", TIME: "TIME", DATE_TIME: "TIMESTAMP",
        DATE_TIME_TZ: "TIMESTAMP WITH TIME ZONE", OPAQUE: "VARCHAR",
    },
}

# Leading type name, with any parameter list (`NUMBER(38,2)`, `VARCHAR(16777216)`) and
# any element type (`ARRAY<STRING>`, `MAP<STRING,INT>`) captured separately.
_TYPE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_ ]*?)\s*(?:\(([^)]*)\)|<.*>)?\s*$")


def parse_type(raw):
    """Split a raw warehouse type into (BASE_NAME, params) with BASE_NAME upper-cased.

    `NUMBER(38,2)` -> ("NUMBER", ["38", "2"]); `ARRAY<STRING>` -> ("ARRAY", []);
    an unparseable or empty value -> (None, []).
    """
    if not isinstance(raw, str) or not raw.strip():
        return None, []
    match = _TYPE_RE.match(raw)
    if not match:
        return raw.strip().upper(), []
    base = " ".join(match.group(1).split()).upper()
    params = [p.strip() for p in (match.group(2) or "").split(",") if p.strip()]
    return base or None, params


def infer_dialect(raw_types):
    """Infer the Apache Ossie dialect from a model's column type vocabulary.

    Solid's YAML export does not record the source warehouse -- it lives on the asset
    row in Solid's own database and is dropped at render time -- but the raw type names
    it copies out of the catalog do identify it. Each distinctive name votes for its
    warehouse; the warehouse with the most votes wins.

    Returns (dialect, confident). `confident` is False when no marker was seen or two
    warehouses tie, in which case the dialect is ANSI_SQL and the caller should warn.
    """
    votes = dict.fromkeys(_DIALECT_MARKERS, 0)
    for raw in raw_types:
        base, _ = parse_type(raw)
        if not base:
            continue
        for dialect, markers in _DIALECT_MARKERS.items():
            if base in markers:
                votes[dialect] += 1
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
    best, best_votes = ranked[0]
    if best_votes == 0:
        return DIALECT_ANSI, False
    if len(ranked) > 1 and ranked[1][1] == best_votes:
        return DIALECT_ANSI, False
    return best, True


def to_ossie_datatype(raw, dialect):
    """Map a raw warehouse type onto an Apache Ossie `datatype`.

    Returns None when the type is absent or unrecognized -- the spec says to omit
    `datatype` when it is unknown rather than guess. A type that is known but outside
    the portable vocabulary (VARIANT, MAP, STRUCT, ...) maps to `Opaque`; the raw name
    is preserved in the SOLID stash either way.
    """
    base, params = parse_type(raw)
    if not base:
        return None
    if base in _DECIMAL_TYPES:
        # A declared scale of 0 is an integer -- Snowflake stores every integral column
        # as NUMBER(38,0), so honouring the scale is what keeps counts and ids from
        # being typed as Decimal. An undeclared scale defaults to 0 in Snowflake and
        # BigQuery NUMERIC, but Databricks DECIMAL defaults to (10,0) -- also scale 0.
        if len(params) >= 2:
            return INTEGER if params[1] == "0" else DECIMAL
        return INTEGER
    if base == "TIMESTAMP":
        return _TIMESTAMP_BY_DIALECT.get(dialect, DATE_TIME)
    if base in _TIMESTAMP_VARIANTS:
        return _TIMESTAMP_VARIANTS[base]
    return _COMMON_TYPES.get(base)


def to_raw_type(datatype, dialect):
    """Map an Apache Ossie `datatype` back to a representative warehouse type name.

    Only used when exporting a field that carries no SOLID stash (a hand-authored
    Apache Ossie model). A field that came from Solid re-emits its original raw string.
    """
    if not datatype:
        return None
    table = _DATATYPE_TO_RAW.get(dialect) or _DATATYPE_TO_RAW[DIALECT_ANSI]
    return table.get(datatype)


# Solid splits a table's columns into `facts` and `dimensions` by data type, using a
# lowercase prefix match against this set (see semantic_layer_data_types.py in
# solid-server). Mirrored here so an export of a hand-authored Apache Ossie model --
# where no field carries the `dimension` block that records the original split -- lands
# columns in the same bucket Solid would have chosen.
_SOLID_FACT_TYPE_PREFIXES = (
    "decimal", "double", "int", "float", "number", "numeric", "real", "tiny", "long",
)


def is_solid_fact_type(raw):
    """True if Solid would classify a column of this raw type as a fact."""
    if not isinstance(raw, str):
        return False
    lowered = raw.strip().lower()
    return any(lowered.startswith(p) for p in _SOLID_FACT_TYPE_PREFIXES)


def normalize_dialect(dialect):
    """Validate and upper-case an Apache Ossie dialect name."""
    normalized = str(dialect).strip().upper()
    if normalized not in SUPPORTED_DIALECTS:
        raise ConversionError(
            f"Unsupported dialect '{dialect}'. Choose one of: "
            f"{', '.join(SUPPORTED_DIALECTS)}"
        )
    return normalized


def resolve_dialect(explicit, raw_types, scope="model"):
    """Pick the dialect for a conversion: an explicit choice wins, else inference.

    Warns when inference had to fall back to ANSI_SQL, since a fallback means every
    expression is being labelled with a dialect it was not written in.
    """
    if explicit:
        return normalize_dialect(explicit)
    dialect, confident = infer_dialect(raw_types)
    if not confident:
        warn(
            scope,
            "could not infer the source warehouse from the column type vocabulary; "
            f"defaulting to {DIALECT_ANSI}. Pass --dialect to set it explicitly.",
        )
    return dialect
