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

"""Shared constants and mapping tables for the Ossie ↔ OBML converter.

These module-level constants are used by more than one of the converter
direction classes (``OssietoOBML``, ``OBMLtoOssie``, ``OBMLtoOssieOntology``) and the
validation helpers. They live here so both the facade ``converter`` module and
the per-direction class modules can import them without forming an import cycle.
"""

from __future__ import annotations

import re

# ─── Spec version pin ───────────────────────────────────────────────────────
# Single source of truth for the Ossie spec we emit. Bump when upstream cuts
# a stable v0.2.0 (drop the ``.dev0`` suffix). All read paths accept both
# 0.1.x (via the legacy shim) and 0.2.x.
_OSSIE_VERSION = "0.2.0.dev0"

# SQL dialects (of the Ossie enum) whose aggregation expressions our regex-based
# metric parser can read, in preference order. ANSI_SQL first, then
# OSSIE_SQL_2026 (the spec's portable, ANSI-compatible expression language);
# SNOWFLAKE and DATABRICKS are SQL engines OrionBelt also targets, and their
# simple/expression aggregations (``SUM(t.c)``, ``SUM(t.a * t.b)``) are
# syntactically identical to ANSI. Non-SQL languages (MDX, TABLEAU, MAQL, SIGMA,
# THOUGHTSPOT, DAX) are never parsed as SQL.
_SQL_PARSEABLE_DIALECTS = ("ANSI_SQL", "OSSIE_SQL_2026", "SNOWFLAKE", "DATABRICKS")

# Matches a ``dataset.column`` reference inside a SQL expression, where each
# side is a bare identifier or a quoted identifier (double quotes, backticks, or
# brackets). The leading lookbehind prevents matching the tail of a longer path
# (``a.b.c``) or a mid-token boundary; the bare form must start with a letter or
# underscore so numeric literals (``1.5``) are never treated as references.
_COLUMN_REF_RE = re.compile(
    r'(?<![\w."`\]])'
    r'(?P<ds>[A-Za-z_]\w*|"[^"]+"|`[^`]+`|\[[^\]]+\])'
    r"\s*\.\s*"
    r'(?P<col>[A-Za-z_]\w*|"[^"]+"|`[^`]+`|\[[^\]]+\])'
)
# Vendor identities for custom_extensions.
#   ORIONBELT - OrionBelt/OBML-proprietary payloads we author on OBML -> Ossie.
#   Ossie       - Ossie-native fields OBML can't hold (unique_keys, field label,
#               ai_context leftovers), stashed into OBML on Ossie -> OBML.
# Read paths also accept the legacy tags we emitted before this scheme so older
# documents still round-trip; foreign vendors (SNOWFLAKE, DBT, ...) are
# preserved verbatim, never relabelled.
_VENDOR_OBML = "ORIONBELT"
_VENDOR_OSSIE = "Ossie"
_OBML_VENDOR_READ = ("ORIONBELT", "COMMON")
_OSSIE_VENDOR_READ = ("Ossie", "OBSL")
# Vendors the converter handles internally (its own payloads + native-field
# stashes). Any custom_extension from a vendor outside this set is third-party
# and is carried through verbatim in both directions, never relabelled.
_INTERNAL_VENDORS = frozenset({"ORIONBELT", "COMMON", "Ossie", "OBSL"})

# ─── Type mapping ───────────────────────────────────────────────────────────

OBML_TO_OSSIE_TYPE = {
    "string": "string",
    "json": "string",
    "int": "integer",
    "float": "number",
    "date": "date",
    "time": "time",
    "time_tz": "time",
    "timestamp": "timestamp",
    "timestamp_tz": "timestamp",
    "boolean": "boolean",
}

OSSIE_TO_OBML_TYPE = {
    "string": "string",
    "integer": "int",
    "number": "float",
    "date": "date",
    "time": "time",
    "timestamp": "timestamp",
    "boolean": "boolean",
}

# ─── Ossie DataType ⇄ OBML ──────────────────────────────────────────────────
# Ossie `datatype` on Field/Metric is a *logical* type backed by the capitalised
# `DataType` enum in core-spec/ossie-schema.json - the same layer as OBML's column
# `abstractType` - so this is the field/dimension mapping.
#
# `Decimal` has no logical-layer equivalent in OBML: OBML models exact decimal at
# the physical/result layer (`sqlType`/`sqlPrecision`/`sqlScale`, measure/metric
# `dataType` via `decimal(p, s)`), not as a coarse `abstractType`. So `Decimal`
# narrows to `float` for fields, but is recovered exactly for metrics via the
# physical `dataType` map below (`OSSIE_DATATYPE_TO_OBML_PHYSICAL`).
#
# `Opaque` is Ossie's "known type outside the portable vocabulary" marker and is
# intentionally absent so it falls back to the name heuristic on import.
OSSIE_DATATYPE_TO_OBML_ABSTRACT = {
    "String": "string",
    "Integer": "int",
    "Float": "float",
    "Decimal": "float",
    "Boolean": "boolean",
    "Date": "date",
    "Time": "time",
    "DateTime": "timestamp",
    "DateTimeTz": "timestamp_tz",
}

# OBML column `abstractType` -> Ossie `DataType`, for the export direction.
OBML_ABSTRACT_TO_OSSIE_DATATYPE = {
    "string": "String",
    "json": "Opaque",
    "int": "Integer",
    "float": "Float",
    "date": "Date",
    "time": "Time",
    "time_tz": "Time",
    "timestamp": "DateTime",
    "timestamp_tz": "DateTimeTz",
    "boolean": "Boolean",
}

# Metric/measure `datatype`. Unlike fields, OBML measures/metrics carry an exact
# `dataType` (physical vocabulary: `integer`/`double`/`decimal(p, s)`/...), which
# is where `Decimal` genuinely belongs. So Ossie metric `datatype` maps to that
# field, not the coarse `abstractType`.
OBML_DECIMAL_DEFAULT = "decimal(18, 2)"  # mirrors OrionBelt's built-in default

# Ossie `DataType` -> OBML physical `dataType` (import direction). `Opaque` is
# omitted (non-portable). `DateTimeTz` has no tz-aware physical form, so it
# narrows to `timestamp`.
OSSIE_DATATYPE_TO_OBML_PHYSICAL = {
    "String": "string",
    "Integer": "integer",
    "Float": "double",
    "Decimal": OBML_DECIMAL_DEFAULT,
    "Boolean": "boolean",
    "Date": "date",
    "Time": "time",
    "DateTime": "timestamp",
    "DateTimeTz": "timestamp",
}

# OBML physical `dataType` -> Ossie `DataType` (export direction). `decimal(p, s)`
# is handled by ``obml_datatype_to_ossie`` since it is parametrised.
OBML_PHYSICAL_TO_OSSIE_DATATYPE = {
    "string": "String",
    "integer": "Integer",
    "bigint": "Integer",
    "double": "Float",
    "boolean": "Boolean",
    "date": "Date",
    "time": "Time",
    "timestamp": "DateTime",
}


def obml_datatype_to_ossie(data_type: object) -> str | None:
    """Map an explicit OBML measure/metric ``dataType`` to an Ossie ``DataType``.

    Returns ``None`` when there is no mapping, so the caller emits nothing rather
    than an unknown type. ``decimal(p, s)`` maps to ``Decimal``. A hand-authored
    document may carry a non-string ``dataType`` (``123``) that no schema check
    has rejected yet; that has no mapping either, rather than aborting the whole
    conversion.
    """
    if not isinstance(data_type, str):
        return None
    normalized = data_type.strip().lower()
    if not normalized:
        return None
    if normalized.startswith("decimal"):
        return "Decimal"
    return OBML_PHYSICAL_TO_OSSIE_DATATYPE.get(normalized)


def obml_decimal_default(settings: object) -> str:
    """The ``dataType`` an Ossie ``Decimal`` metric becomes in this model.

    OBML lets a model set ``settings.defaultNumericDataType`` (always a
    ``decimal(p, s)``, which OrionBelt enforces), and a model configured for
    ``decimal(20, 6)`` should not have its metrics written as the built-in
    ``decimal(18, 2)``. Anything other than a decimal string there falls back to
    the built-in default.
    """
    if isinstance(settings, dict):
        configured = settings.get("defaultNumericDataType")
        if isinstance(configured, str) and configured.strip().lower().startswith("decimal"):
            return configured
    return OBML_DECIMAL_DEFAULT


def ossie_metric_datatype_to_obml(ossie_datatype: object, decimal_default: str) -> str | None:
    """Map an Ossie metric ``datatype`` to the OBML measure/metric ``dataType``.

    ``Decimal`` takes the model's numeric default; ``Opaque``, an unknown value
    or a non-string has no mapping.
    """
    if not isinstance(ossie_datatype, str):
        return None
    if ossie_datatype == "Decimal":
        return decimal_default
    return OSSIE_DATATYPE_TO_OBML_PHYSICAL.get(ossie_datatype)
