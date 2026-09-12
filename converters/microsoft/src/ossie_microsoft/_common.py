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

"""Shared helpers for the Apache Ossie <-> Microsoft Power BI converters.

Cross-cutting concerns live here: version constants, the data type mapping in both
directions, the `custom_extensions` stash protocol, and the warning helper used to
report every lossy step.

Design rule for this converter pair: **never emit a plausible-looking expression that
was not authored for the target engine**. Power BI measures are DAX; Apache Ossie field
expressions are usually SQL. The two are not interchangeable, and a partial SQL-to-DAX
rewrite that silently produces invalid or subtly wrong DAX is worse than a refusal.
Where an expression cannot be carried across faithfully, the converter warns and uses
an explicit placeholder rather than guessing, preserving the source expression as
metadata when the target format supports it.
"""

import json
import logging
import re
import warnings

# Every lossy step is reported twice, on purpose, because the two channels answer
# different questions. A `UserWarning` is the programmatic contract: a caller can raise
# `warnings.simplefilter("error")` and get a hard guarantee the conversion was lossless.
# A log record is the operational channel: it carries the same message to whatever
# handler an embedding application configured, without that application having to
# install a warning filter.
LOGGER = logging.getLogger("ossie_microsoft")
_WARNING_REGISTRY = {}

# Apache Ossie semantic model spec version this converter targets (see core-spec).
#
# NOTE: this is an exact-match check on import/export. This spoke intentionally has no
# `apache-ossie` package dependency, so nothing updates this automatically -- it MUST be
# bumped in lockstep with the `version` in `core-spec/` whenever the spec version moves.
OSSIE_VERSION = "0.2.0.dev0"

# Vendor id used for the `custom_extensions` stash.
VENDOR = "POWER_BI"

# Expression dialects this converter understands.
DIALECT_DAX = "DAX"
DIALECT_ANSI = "ANSI_SQL"

# Bump when the shape of a stashed `data` blob changes.
STASH_VERSION = 1

# Default TMSL compatibility level. 1550 is the baseline that supports calculation
# groups and the modern partition surface.
DEFAULT_COMPATIBILITY_LEVEL = 1550

# Auto-generated date tables Power BI creates behind the scenes for time intelligence;
# they are an implementation detail, not part of the user-authored model.
AUTO_DATE_TABLE_RE = re.compile(r"^(LocalDateTable_|DateTableTemplate_)")

# A bare SQL identifier, e.g. `order_date`. Used to decide whether an Apache Ossie
# ANSI_SQL field expression is a plain column reference (which maps to a TMSL
# `sourceColumn`) or a computed expression (which does not).
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ---------------------------------------------------------------------------
# Constructs neither model can represent
# ---------------------------------------------------------------------------
#
# Round-trip losslessness (Power BI -> Apache Ossie -> Power BI) is guaranteed by the
# stash, so nothing below is lost when the destination is Power BI again. But an Apache
# Ossie document is meant to be read by tools that are not Power BI, and to those tools
# these constructs are simply absent. Reporting them is the difference between "Apache
# Ossie models this" and "this survived in an opaque vendor blob".
#
# These catalogues list constructs with *semantic* meaning -- security, navigation,
# calculation, localization. Purely presentational TMSL properties (`isHidden`,
# `displayFolder`, `formatString`) are preserved just as faithfully but are not reported,
# because a warning on every cosmetic property would bury the ones that matter.
TMSL_UNSUPPORTED_MODEL = {
    "roles": "row-level security roles",
    "perspectives": "perspectives",
    "cultures": "translations and linguistic metadata",
    "expressions": "shared Power Query expressions and parameters",
    "dataSources": "data source definitions",
    "queryGroups": "Power Query group folders",
}
TMSL_UNSUPPORTED_TABLE = {
    "hierarchies": "hierarchies",
    "calculationGroup": "a calculation group",
    "refreshPolicy": "an incremental refresh policy",
    "defaultDetailRowsDefinition": "a detail rows definition",
}
TMSL_UNSUPPORTED_COLUMN = {
    "variations": "date table variations",
    "sortByColumn": "a sort-by-column",
}
TMSL_UNSUPPORTED_MEASURE = {
    "kpi": "a KPI",
    "detailRowsDefinition": "a detail rows definition",
}

# Apache Ossie constructs with no Power BI counterpart. Unlike the tables above, these
# genuinely cannot be carried across: a TMSL document has nowhere to put them, so they
# are reported and dropped rather than preserved.
OSSIE_UNSUPPORTED = {
    "label": "a display label",
}

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
#
# The TMSL `dataType` vocabulary is the TOM DataType enum. Note that it has no
# date-only, time-only or timezone-aware member: Power BI stores every temporal value
# as `dateTime` and distinguishes date-only presentation purely via `formatString`.
# The Apache Ossie temporal types therefore do not survive a round trip on the
# `dataType` alone, which is why DATE_ONLY_FORMAT_RE exists below.
#
# Precision caveats that converters and consumers should be aware of:
#   * `int64`   -- a 64-bit integer in the model, but Power BI report visuals can only
#                  safely express values up to 2**53 - 1 (9007199254740991).
#   * `decimal` -- "Fixed Decimal Number": 19 digits of significance, exactly 4 digits
#                  to the right of the decimal point. Maps to Ossie `Decimal` (exact).
#   * `double`  -- "Decimal Number": 64-bit floating point, ~15 digits of precision and
#                  approximate. Maps to Ossie `Float`, NOT `Decimal`.
#   * `dateTime` -- model range is years 1900-9999 and time is stored at 1/300 second
#                  (3.33 ms) granularity, so sub-millisecond source data loses fidelity.
TMSL_TO_OSSIE_DATATYPE = {
    "string": "String",
    "int64": "Integer",
    "decimal": "Decimal",
    "double": "Float",
    "boolean": "Boolean",
    "dateTime": "DateTime",
    "binary": "Opaque",
    "variant": "Opaque",
}

# TMSL data types that carry no usable type information. `automatic` and `unknown` mean
# "not yet resolved by the engine", so mapping them to a portable type would invent one.
TMSL_UNTYPED = frozenset({"automatic", "unknown"})

OSSIE_TO_TMSL_DATATYPE = {
    "String": "string",
    "Integer": "int64",
    "Decimal": "decimal",
    "Float": "double",
    "Boolean": "boolean",
    "Date": "dateTime",
    "DateTime": "dateTime",
    "Time": "dateTime",
    "DateTimeTz": "dateTime",
}

# Apache Ossie temporal types, which imply `dimension.is_time` when it is not set.
TEMPORAL_DATATYPES = frozenset({"Date", "Time", "DateTime", "DateTimeTz"})

# Model-level format string applied when exporting an Ossie `Date` field, so the
# date-only intent survives into a model that has no date-only data type.
DATE_ONLY_FORMAT = "yyyy-mm-dd"

# VBA named date/time formats, which are whole-string names rather than token sequences.
# They must be matched before tokenizing: "Short Date" is date-only despite containing an
# `h`, and "Long Date" contains an `n`.
_NAMED_DATE_ONLY_FORMATS = frozenset({"long date", "medium date", "short date"})
_NAMED_TEMPORAL_FORMATS = _NAMED_DATE_ONLY_FORMATS | frozenset(
    {"general date", "long time", "medium time", "short time"}
)

# Time-of-day tokens in a VBA-style (model-level) format string. `n` is minutes and `h`
# is hours; `m` is deliberately absent because in VBA-style formats `m` means *month*
# except when it directly follows an hour token.
_TIME_FORMAT_TOKEN_RE = re.compile(r"[hns]|AM/PM|A/P|am/pm", re.IGNORECASE)
_DATE_FORMAT_TOKEN_RE = re.compile(r"[ymd]")

# Quoted literals and backslash escapes inside a format string are display text, not
# tokens, and must be removed before looking for tokens.
_FORMAT_LITERAL_RE = re.compile(r'"[^"]*"|\\.')


class ConversionError(Exception):
    """Raised when an input cannot be converted."""


def warn(scope, msg):
    """Report a lossy or skipped conversion step.

    Every construct the converter cannot carry across faithfully goes through here, so
    a caller can turn warnings into errors (``warnings.simplefilter("error")``) and get
    a hard guarantee that a conversion was lossless. The same message is also logged to
    the ``ossie_microsoft`` logger for applications that consume a log rather than
    warning filters.

    Args:
        scope: Where the loss happened, e.g. ``table 'Sales'`` -- always specific enough
            to locate the construct in the source model.
        msg: What was lost or changed, and what the converter did instead.
    """
    message = f"[{scope}] {msg}"
    LOGGER.warning(message)
    # A synthetic location keeps notebook warning renderers from appending the
    # converter's internal multi-line `warn(` call as source context.
    warnings.warn_explicit(
        message,
        UserWarning,
        filename="ossie_microsoft",
        lineno=1,
        module="ossie_microsoft",
        registry=_WARNING_REGISTRY,
    )


def warn_unsupported(scope, present, catalogue, counterpart, fate):
    """Report each construct in `present` that this converter cannot represent.

    Args:
        scope: Where the constructs were found.
        present: An iterable of keys actually present on the source object.
        catalogue: Maps a key to a human description of the construct.
        counterpart: The model that has nowhere to put it, e.g. "Apache Ossie".
        fate: What happened to it, e.g. "preserved for round trip but not represented
            in the Apache Ossie document".

    Returns:
        The sorted list of reported keys, for callers that want to record them.
    """
    reported = sorted(set(present) & set(catalogue))
    for key in reported:
        warn(scope, f"{catalogue[key]} ('{key}') has no {counterpart} counterpart; {fate}")
    return reported


def text(value):
    """Normalize a TMSL string property.

    TMSL allows any multi-line string (descriptions, DAX expressions, Power Query) to be
    stored either as a plain string or as an array of lines.
    """
    if isinstance(value, list):
        return "\n".join(str(line) for line in value)
    return str(value)


def is_date_only_format(format_string):
    """Return True if a model-level format string renders a date with no time-of-day.

    Power BI has no date-only data type, so an Apache Ossie `Date` field is recognised
    on import (and reproduced on export) through the format string instead.
    """
    if not format_string:
        return False
    normalized = text(format_string).strip()
    if normalized.lower() in _NAMED_TEMPORAL_FORMATS:
        return normalized.lower() in _NAMED_DATE_ONLY_FORMATS
    stripped = _FORMAT_LITERAL_RE.sub("", normalized)
    if not _DATE_FORMAT_TOKEN_RE.search(stripped):
        return False
    return not _TIME_FORMAT_TOKEN_RE.search(stripped)


def read_stash(obj):
    """Return the POWER_BI `custom_extensions` payload attached to `obj`, or {}."""
    for ext in (obj or {}).get("custom_extensions") or []:
        if not isinstance(ext, dict) or ext.get("vendor_name") != VENDOR:
            continue
        try:
            data = json.loads(ext.get("data") or "{}")
        except json.JSONDecodeError as e:
            raise ConversionError(
                f"{VENDOR} custom_extensions data is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise ConversionError(
                f"{VENDOR} custom_extensions data must be a JSON object")
        version = data.pop("_v", STASH_VERSION)
        if not isinstance(version, int) or isinstance(version, bool):
            raise ConversionError(
                f"{VENDOR} custom_extensions data has a non-integer version "
                f"'_v': {version!r}")
        if version > STASH_VERSION:
            # Replaying a payload written by a newer converter could silently
            # mean something different, so refuse rather than guess.
            raise ConversionError(
                f"{VENDOR} custom_extensions data was written by a newer "
                f"converter (format version {version}, this converter "
                f"understands {STASH_VERSION}); upgrade ossie-microsoft")
        return data
    return {}


def write_stash(obj, data):
    """Attach a POWER_BI `custom_extensions` entry holding `data` (a dict).

    No-op when `data` is empty, so a model that uses no Power BI-specific features
    converts to clean, vendor-neutral Apache Ossie. Merges into an existing POWER_BI
    entry if one is already present.
    """
    if not data:
        return
    payload = {"_v": STASH_VERSION}
    payload.update(data)
    blob = json.dumps(payload, sort_keys=True)
    exts = obj.setdefault("custom_extensions", [])
    for ext in exts:
        if ext.get("vendor_name") == VENDOR:
            ext["data"] = blob
            return
    exts.append({"vendor_name": VENDOR, "data": blob})


def foreign_vendor_extensions(obj):
    """Return non-POWER_BI custom_extensions (dropped on export, with a warning)."""
    return [
        ext
        for ext in (obj or {}).get("custom_extensions") or []
        if isinstance(ext, dict) and ext.get("vendor_name") != VENDOR
    ]


def dialect_expressions(expression):
    """Return an Apache Ossie expression's {dialect: expression} mapping."""
    result = {}
    for entry in (expression or {}).get("dialects") or []:
        if not isinstance(entry, dict):
            continue
        dialect, expr = entry.get("dialect"), entry.get("expression")
        if expr is not None and not isinstance(expr, str):
            raise ConversionError(
                f"expression must be a string, got {type(expr).__name__}")
        if dialect and expr:
            result[dialect] = expr
    return result


def make_expression(expression, dialect):
    """Build an Apache Ossie single-dialect expression object."""
    return {"dialects": [{"dialect": dialect, "expression": expression}]}


def prune(mapping):
    """Drop keys whose value is None or an empty string/list/dict.

    Keeps generated TMSL free of empty properties without hiding legitimately falsy
    values such as ``0`` or ``False``.
    """
    return {
        key: value
        for key, value in mapping.items()
        if value is not None and value != "" and value != [] and value != {}
    }
