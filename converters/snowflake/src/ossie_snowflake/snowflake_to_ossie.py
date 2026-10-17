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

"""
Converts a Snowflake Cortex Analyst semantic model YAML to an Ossie YAML
semantic model. Pure offline conversion -- no Snowflake connection required.

This is the inverse of :mod:`ossie_snowflake.ossie_to_snowflake`. Snowflake properties
fall into three groups:

- mapped onto an Ossie construct (``data_type`` -> ``datatype``, ``synonyms``
  and ``custom_instructions`` -> ``ai_context``, a column's ``unique`` flag ->
  the dataset's ``unique_keys``);
- stashed in ``custom_extensions`` -- a short, explicit allowlist of Snowflake
  content with no Ossie home that is worth carrying (``_*_STASH_KEYS`` below).
  Every stashed entry is one the next converter in the hub has to warn about
  and drop, so the list stays small by design;
- dropped with a warning, matching how the export direction reports the Ossie
  constructs that Snowflake has no place for.

Usage:
    python3 -m ossie_snowflake.cli snowflake-to-ossie -i snowflake.yaml -o ossie.yaml
"""

import warnings

import yaml
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError

from ._common import (
    DIALECT_ANSI,
    DIALECT_SNOWFLAKE,
    OSSIE_VERSION,
    TIME_DATATYPES,
    is_portable_sql,
    parse_snowflake_datatype,
    require_mappings,
    write_stash,
)


class SnowflakeConversionError(Exception):
    """Raised when a Snowflake YAML cannot be converted to Ossie format."""


# Snowflake content with no Ossie home, carried through so it survives a round
# trip. Kept deliberately short: see the module docstring.
_MODEL_STASH_KEYS = ("verified_queries", "module_custom_instructions")
_TABLE_STASH_KEYS = ("filters",)
_FIELD_STASH_KEYS = ("sample_values", "cortex_search_service", "is_enum")

# Recognized Snowflake properties that are neither mapped nor stashed. Naming them
# lets the converter tell "this model uses a Snowflake feature Ossie drops" apart
# from "this model has a key this converter has never heard of".
_MODEL_DROPPED_KEYS = ()
_TABLE_DROPPED_KEYS = ("tags",)
_FIELD_DROPPED_KEYS = ("tags", "labels", "access_modifier")
# An Ossie relationship carries the join columns, not how the join is performed,
# so these are reported as a known gap rather than as something unrecognized.
_RELATIONSHIP_DROPPED_KEYS = ("join_type", "relationship_type")

_MODEL_MAPPED_KEYS = (
    "name", "description", "tables", "relationships", "metrics",
    "custom_instructions",
)
_TABLE_MAPPED_KEYS = (
    "name", "base_table", "primary_key", "unique_keys", "description",
    "synonyms", "dimensions", "time_dimensions", "facts", "metrics",
)
_FIELD_MAPPED_KEYS = ("name", "expr", "description", "synonyms", "data_type", "unique")
_RELATIONSHIP_MAPPED_KEYS = (
    "name", "left_table", "right_table", "relationship_columns",
)


def convert_snowflake_to_ossie(snowflake_yaml_str):
    """Top-level entry point. Parses Snowflake YAML, converts, returns Ossie YAML.

    Args:
        snowflake_yaml_str: Snowflake Cortex Analyst semantic model YAML string.

    Returns:
        Ossie semantic model YAML string.

    Raises:
        SnowflakeConversionError: If the input cannot be converted.
    """
    try:
        root = yaml.safe_load(snowflake_yaml_str)
    except yaml.YAMLError as err:
        raise SnowflakeConversionError(f"Invalid Snowflake YAML: {err}") from err
    if not isinstance(root, dict):
        raise SnowflakeConversionError(
            "Invalid Snowflake YAML: expected a mapping at the root"
        )

    # `version` is Ossie document metadata that the Snowflake side does not carry,
    # so it is supplied here rather than read from the input.
    ossie = {"version": OSSIE_VERSION}
    ossie.update(_convert_model(root))

    return yaml.dump(
        ossie,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _mappings(value, context):
    """Applies ``require_mappings`` with this direction's error type."""
    return require_mappings(value, context, SnowflakeConversionError)


def _convert_model(snowflake):
    """Converts a Snowflake semantic model dict to an Ossie semantic model dict."""
    name = snowflake.get("name")
    if not name:
        raise SnowflakeConversionError(
            "Missing required 'name' field in semantic model"
        )

    result = {"name": name}

    description = snowflake.get("description")
    if description:
        result["description"] = description

    ai_context = _ai_context(
        instructions=snowflake.get("custom_instructions"), synonyms=None
    )
    if ai_context:
        result["ai_context"] = ai_context

    tables = _mappings(snowflake.get("tables"), "'tables'")
    if not tables:
        raise SnowflakeConversionError(
            "Snowflake model has no 'tables'; an Ossie model requires at least "
            "one dataset"
        )

    datasets = []
    # Snowflake scopes metrics to a table as well as to the model; Ossie has only
    # model-level metrics, so a table's own metrics are lifted here.
    lifted_metrics = []
    for table in tables:
        dataset, table_metrics = _convert_table(table)
        datasets.append(dataset)
        lifted_metrics.extend(table_metrics)
    result["datasets"] = datasets

    relationships = _mappings(snowflake.get("relationships"), "'relationships'")
    if relationships:
        result["relationships"] = [_convert_relationship(r) for r in relationships]

    metrics = [
        _convert_named_expr(m, "metric")
        for m in _mappings(snowflake.get("metrics"), "'metrics'")
    ]
    metrics.extend(lifted_metrics)
    if metrics:
        duplicates = sorted(_duplicate_names(metrics))
        if duplicates:
            warnings.warn(
                "Metric names are not unique once table-level metrics are lifted "
                f"to the model: {', '.join(duplicates)}"
            )
        result["metrics"] = metrics

    write_stash(result, _stash_data(snowflake, _MODEL_STASH_KEYS))
    _warn_dropped(
        snowflake, "model", _MODEL_MAPPED_KEYS, _MODEL_STASH_KEYS, _MODEL_DROPPED_KEYS
    )

    return result


def _convert_table(table):
    """Converts a Snowflake table dict to an Ossie dataset dict.

    Returns ``(dataset, table_metrics)``, where ``table_metrics`` are the table's
    own metrics for the caller to lift to the model level.
    """
    name = table.get("name")
    if not name:
        raise SnowflakeConversionError("Missing required 'name' field in table")

    result = {"name": name, "source": _parse_base_table(table.get("base_table"), name)}

    primary_key = table.get("primary_key")
    if isinstance(primary_key, dict) and primary_key.get("columns"):
        result["primary_key"] = list(primary_key["columns"])

    unique_keys = [
        list(uk["columns"])
        for uk in _mappings(table.get("unique_keys"), f"Table '{name}': 'unique_keys'")
        if uk.get("columns")
    ]

    description = table.get("description")
    if description:
        result["description"] = description

    ai_context = _ai_context(instructions=None, synonyms=table.get("synonyms"))
    if ai_context:
        result["ai_context"] = ai_context

    fields = []
    for key, is_time in (
        ("dimensions", False), ("time_dimensions", True), ("facts", None)
    ):
        for entry in _mappings(table.get(key), f"Table '{name}': '{key}'"):
            fields.append(_convert_field(entry, is_time=is_time))
    if fields:
        result["fields"] = fields

    # A column marked `unique` is a single-column unique key, which Ossie records on
    # the dataset rather than on the field.
    for entry in _all_field_entries(table):
        if entry.get("unique") and entry.get("name"):
            candidate = [entry["name"]]
            if candidate not in unique_keys:
                unique_keys.append(candidate)
    if unique_keys:
        result["unique_keys"] = unique_keys

    table_metrics = [
        _convert_named_expr(entry, "metric", qualify_with=name)
        for entry in _mappings(table.get("metrics"), f"Table '{name}': 'metrics'")
    ]

    write_stash(result, _stash_data(table, _TABLE_STASH_KEYS))
    _warn_dropped(
        table, f"table '{name}'", _TABLE_MAPPED_KEYS, _TABLE_STASH_KEYS,
        _TABLE_DROPPED_KEYS,
    )

    return result, table_metrics


def _convert_field(entry, is_time):
    """Converts a Snowflake dimension, time_dimension or fact entry to an Ossie field.

    ``is_time`` is the role the entry's position in the Snowflake table implies:
    ``False`` for ``dimensions``, ``True`` for ``time_dimensions``, and ``None``
    for ``facts``, which carry no ``dimension`` block at all.

    ``dimension.is_time`` is only written out when the role differs from what the
    spec's datatype default would give, so a model that relied on that default
    survives an Ossie -> Snowflake -> Ossie round trip unchanged.
    """
    result = _convert_named_expr(entry, "field")

    if is_time is not None:
        default_is_time = result.get("datatype") in TIME_DATATYPES
        result["dimension"] = (
            {} if is_time == default_is_time else {"is_time": is_time}
        )

    return result


def _convert_named_expr(entry, kind, qualify_with=None):
    """Converts a Snowflake field or metric entry to an Ossie field or metric dict.

    Args:
        entry: The Snowflake dimension, time_dimension, fact or metric dict.
        kind: Human-readable type for error messages (e.g., "field", "metric").
        qualify_with: Dataset name to qualify bare column references with, set
            when a table-level metric is being lifted to the model level.
    """
    name = entry.get("name")
    if not name:
        raise SnowflakeConversionError(f"Missing required 'name' in {kind}")

    expr = entry.get("expr")
    if not expr:
        raise SnowflakeConversionError(f"Missing required 'expr' in {kind} '{name}'")

    if qualify_with:
        expr = _qualify_columns(expr, qualify_with, name)

    # Ossie is a hub, and every exporter picks its own dialect, then ANSI_SQL,
    # then OSSIE_SQL_2026, dropping any field labelled with none of them. A
    # SNOWFLAKE-only label therefore does not merely lose fidelity downstream, it
    # deletes the field from every other spoke, so expressions that are provably
    # standard SQL are labelled ANSI_SQL and the label is narrowed only for SQL
    # this converter cannot vouch for.
    if is_portable_sql(expr):
        dialect = DIALECT_ANSI
    else:
        dialect = DIALECT_SNOWFLAKE
        warnings.warn(
            f"[{kind} '{name}'] expression uses Snowflake-specific SQL; "
            f"labelled {DIALECT_SNOWFLAKE}, so converters that read only "
            f"{DIALECT_ANSI} will drop this {kind}"
        )

    result = {
        "name": name,
        "expression": {"dialects": [{"dialect": dialect, "expression": expr}]},
    }

    description = entry.get("description")
    if description:
        result["description"] = description

    datatype, opaque_type = parse_snowflake_datatype(
        entry.get("data_type"), f"{kind} '{name}'"
    )
    if datatype:
        result["datatype"] = datatype

    ai_context = _ai_context(instructions=None, synonyms=entry.get("synonyms"))
    if ai_context:
        result["ai_context"] = ai_context

    stash = _stash_data(entry, _FIELD_STASH_KEYS)
    if opaque_type:
        # The spec's guidance for `Opaque` is to record the concrete type that it
        # stands in for, so the mapping stays reversible.
        stash["data_type"] = opaque_type
    write_stash(result, stash)

    _warn_dropped(
        entry, f"{kind} '{name}'", _FIELD_MAPPED_KEYS, _FIELD_STASH_KEYS,
        _FIELD_DROPPED_KEYS,
    )

    return result


def _convert_relationship(rel):
    """Converts a Snowflake relationship dict to an Ossie relationship dict."""
    name = rel.get("name")
    if not name:
        raise SnowflakeConversionError(
            "Missing required 'name' field in relationship"
        )

    left_table = rel.get("left_table")
    if not left_table:
        raise SnowflakeConversionError(
            f"Relationship '{name}': missing required 'left_table' field"
        )
    right_table = rel.get("right_table")
    if not right_table:
        raise SnowflakeConversionError(
            f"Relationship '{name}': missing required 'right_table' field"
        )

    columns = rel.get("relationship_columns") or []
    if not isinstance(columns, list):
        raise SnowflakeConversionError(
            f"Relationship '{name}': 'relationship_columns' must be a list, got "
            f"{type(columns).__name__}"
        )
    pairs = [
        (c.get("left_column"), c.get("right_column"))
        for c in columns
        if isinstance(c, dict)
    ]
    from_columns = [left for left, _ in pairs]
    to_columns = [right for _, right in pairs]
    # `len(pairs) != len(columns)` catches entries that are not mappings at all,
    # which would otherwise be filtered out and leave an empty, silently broken join.
    if (
        not pairs
        or len(pairs) != len(columns)
        or not all(from_columns)
        or not all(to_columns)
    ):
        raise SnowflakeConversionError(
            f"Relationship '{name}': 'relationship_columns' must give at least one "
            "pair of left_column and right_column"
        )

    result = {
        "name": name,
        "from": left_table,
        "to": right_table,
        "from_columns": from_columns,
        "to_columns": to_columns,
    }

    _warn_dropped(
        rel,
        f"relationship '{name}'",
        _RELATIONSHIP_MAPPED_KEYS,
        (),
        _RELATIONSHIP_DROPPED_KEYS,
    )

    return result


def _parse_base_table(base_table, table_name):
    """Converts a Snowflake base_table into an Ossie dataset source string.

    The inverse of the export direction's ``_parse_source``: a
    ``{database, schema, table}`` triple becomes a dotted identifier and a
    ``{definition}`` subquery is carried through as written. Ossie requires a
    source, so a table without one is an error rather than a silent omission.
    """
    if not isinstance(base_table, dict) or not base_table:
        raise SnowflakeConversionError(
            f"Table '{table_name}': missing required 'base_table'; an Ossie dataset "
            "requires a source"
        )

    definition = base_table.get("definition")
    if definition:
        return str(definition).strip()

    parts = [base_table.get(key) for key in ("database", "schema", "table")]
    if not all(parts):
        raise SnowflakeConversionError(
            f"Table '{table_name}': 'base_table' must give database, schema and "
            "table, or a 'definition' subquery"
        )
    return ".".join(str(part).strip() for part in parts)


def _qualify_columns(expr, dataset, metric_name):
    """Qualifies the bare column references in a lifted metric expression.

    A Snowflake table-level metric resolves against its own table, so ``SUM(amount)``
    is unambiguous there. Ossie has only model-level metrics, where that same
    expression names no table at all: it becomes ambiguous as soon as two datasets
    share a column name, and it no longer records which dataset the metric came
    from. The spec's reference form is ``dataset.field``
    (``core-spec/expression_language.md``), so lifting a metric carries its table
    into the expression.

    The parse tree is only used to locate the columns; the prefixes are spliced
    into the original text at the offsets it reports, so everything else -- casing,
    spacing, comments, Snowflake-only functions -- survives verbatim.

    Args:
        expr: The metric expression as written in the Snowflake YAML.
        dataset: The Ossie dataset name the metric is being lifted from.
        metric_name: The metric's name, used in the warning message.

    Returns:
        The expression with every bare column reference qualified, or the
        expression unchanged (with a warning) when its columns cannot be located.
    """
    try:
        tree = parse_one(expr, read=DIALECT_SNOWFLAKE.lower())
    except ParseError:
        return _warn_unqualified(expr, metric_name, dataset)

    offsets = set()
    for column in tree.find_all(exp.Column):
        if column.table:
            continue
        # The offset is reported at the opening quote of a quoted identifier, so
        # splicing there qualifies `"Amount"` as `orders."Amount"` and leaves the
        # quoting intact.
        start = column.this.meta.get("start")
        if start is None:
            return _warn_unqualified(expr, metric_name, dataset)
        offsets.add(start)

    # Quoted only when the dataset name cannot stand as a bare identifier, so a
    # name with a space or a leading digit survives and an ordinary name keeps the
    # casing Snowflake would resolve it with.
    prefix = exp.to_identifier(dataset).sql(dialect=DIALECT_SNOWFLAKE.lower())

    # Right to left, so an earlier splice does not shift a later offset.
    for start in sorted(offsets, reverse=True):
        expr = f"{expr[:start]}{prefix}.{expr[start:]}"
    return expr


def _warn_unqualified(expr, metric_name, dataset):
    """Warns that a lifted metric expression kept its bare column references."""
    warnings.warn(
        f"[metric '{metric_name}'] column references could not be located, so they "
        f"were not qualified with '{dataset}'; a bare column name is ambiguous "
        "once the metric is lifted to the model level"
    )
    return expr


def _all_field_entries(table):
    """Yields every dimension, time_dimension and fact entry of a Snowflake table."""
    for key in ("dimensions", "time_dimensions", "facts"):
        for entry in table.get(key) or []:
            if isinstance(entry, dict):
                yield entry


def _ai_context(instructions, synonyms):
    """Builds an Ossie ai_context object, or None when there is nothing to say."""
    result = {}
    if instructions:
        result["instructions"] = instructions
    if isinstance(synonyms, list) and synonyms:
        result["synonyms"] = list(synonyms)
    return result or None


def _duplicate_names(entries):
    """Returns the names that appear more than once across `entries`."""
    seen = set()
    duplicates = set()
    for entry in entries:
        name = entry.get("name")
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return duplicates


def _stash_data(source, stash_keys):
    """Collects the allowlisted Snowflake properties present on `source`."""
    return {key: source[key] for key in stash_keys if source.get(key)}


def _warn_dropped(source, context, mapped_keys, stash_keys, dropped_keys):
    """Warns about Snowflake properties that do not reach the Ossie output.

    Recognized-but-unmappable properties are reported by name; anything this
    converter does not recognize at all is reported separately, since that is the
    signal that the Snowflake model uses something newer than this mapping.
    """
    known = set(mapped_keys) | set(stash_keys) | set(dropped_keys)

    dropped = [key for key in dropped_keys if source.get(key)]
    if dropped:
        warnings.warn(
            f"Dropped from {context} (no Ossie counterpart): " + ", ".join(dropped)
        )

    unknown = sorted(key for key in source if key not in known)
    if unknown:
        warnings.warn(
            f"Dropped from {context} (not recognized by this converter): "
            + ", ".join(unknown)
        )
