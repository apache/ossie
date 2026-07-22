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

"""Shared constants and mapping tables for the OSI ↔ OBML converter.

These module-level constants are used by more than one of the converter
direction classes (``OSItoOBML``, ``OBMLtoOSI``, ``OBMLtoOSIOntology``) and the
validation helpers. They live here so both the facade ``converter`` module and
the per-direction class modules can import them without forming an import cycle.
"""

from __future__ import annotations

import re

# ─── Spec version pin ───────────────────────────────────────────────────────
# Single source of truth for the OSI spec we emit. Bump when upstream cuts
# a stable v0.2.0 (drop the ``.dev0`` suffix). All read paths accept both
# 0.1.x (via the legacy shim) and 0.2.x.
_OSI_VERSION = "0.2.0.dev0"

# SQL dialects (of the OSI enum) whose aggregation expressions our regex-based
# metric parser can read, in preference order. ANSI_SQL first; SNOWFLAKE and
# DATABRICKS are SQL engines OrionBelt also targets, and their simple/expression
# aggregations (``SUM(t.c)``, ``SUM(t.a * t.b)``) are syntactically identical to
# ANSI. MDX / TABLEAU / MAQL are non-SQL languages and are never parsed as SQL.
_SQL_PARSEABLE_DIALECTS = ("ANSI_SQL", "SNOWFLAKE", "DATABRICKS")

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
#   ORIONBELT - OrionBelt/OBML-proprietary payloads we author on OBML -> OSI.
#   OSI       - OSI-native fields OBML can't hold (unique_keys, field label,
#               ai_context leftovers), stashed into OBML on OSI -> OBML.
# Read paths also accept the legacy tags we emitted before this scheme so older
# documents still round-trip; foreign vendors (SNOWFLAKE, DBT, ...) are
# preserved verbatim, never relabelled.
_VENDOR_OBML = "ORIONBELT"
_VENDOR_OSI = "OSI"
_OBML_VENDOR_READ = ("ORIONBELT", "COMMON")
_OSI_VENDOR_READ = ("OSI", "OBSL")
# Vendors the converter handles internally (its own payloads + native-field
# stashes). Any custom_extension from a vendor outside this set is third-party
# and is carried through verbatim in both directions, never relabelled.
_INTERNAL_VENDORS = frozenset({"ORIONBELT", "COMMON", "OSI", "OBSL"})

# ─── Type mapping ───────────────────────────────────────────────────────────

OBML_TO_OSI_TYPE = {
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

OSI_TO_OBML_TYPE = {
    "string": "string",
    "integer": "int",
    "number": "float",
    "date": "date",
    "time": "time",
    "timestamp": "timestamp",
    "boolean": "boolean",
}
