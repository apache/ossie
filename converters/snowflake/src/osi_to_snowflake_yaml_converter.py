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
Converts an Ossie (Open Semantic Interchange) YAML semantic model to a Snowflake
Cortex Analyst semantic model YAML. Pure offline conversion — no Snowflake
connection required.

Usage:
    python3 osi_to_snowflake_yaml_converter.py -i input.yaml -o output.yaml
"""

import argparse
import sys
import warnings

import yaml


SUPPORTED_VERSION = "0.2.0.dev0"

_TIME_DATATYPES = frozenset({"date", "time", "timestamp", "timestamp_tz"})


class OsiConversionError(Exception):
    """Raised when an Ossie YAML cannot be converted to Snowflake format."""


def convert_osi_to_snowflake(osi_yaml_str):
    """Top-level entry point. Parses Ossie YAML, validates, converts, returns
    Snowflake YAML string.

    Expects the standard Ossie wrapped format::

        version: "0.2.0.dev0"
        semantic_model:
          - name: ...

    Args:
        osi_yaml_str: Ossie YAML as a string.

    Returns:
        Snowflake Cortex Analyst semantic model YAML string.

    Raises:
        OsiConversionError: If the input cannot be converted.
    """
    root = yaml.safe_load(osi_yaml_str)
    if not isinstance(root, dict):
        raise OsiConversionError("Invalid Ossie YAML: expected a mapping at the root")

    version_str = str(root.get("version", ""))
    if version_str != SUPPORTED_VERSION:
        raise OsiConversionError(
            f"Unsupported Ossie specification version '{version_str}'. "
            f"Supported: {SUPPORTED_VERSION}"
        )

    semantic_model = root.get("semantic_model")
    if not isinstance(semantic_model, list) or len(semantic_model) == 0:
        raise OsiConversionError(
            "Invalid Ossie YAML: 'semantic_model' must be a non-empty list"
        )

    if len(semantic_model) > 1:
        warnings.warn(
            f"Ossie YAML contains {len(semantic_model)} semantic models; "
            f"only the first will be converted"
        )

    ossie = semantic_model[0]
    if not isinstance(ossie, dict):
        raise OsiConversionError(
            "Invalid Ossie YAML: 'semantic_model' entries must be mappings"
        )

    snowflake_model = _convert_model(ossie)

    return yaml.dump(
        snowflake_model,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _convert_model(ossie):
    """Converts the root Ossie model dict to a Snowflake semantic model dict."""
    name = ossie.get("name")
    if not name:
        raise OsiConversionError("Missing required 'name' field in semantic model")

    result = {}
    result["name"] = name

    description = ossie.get("description")
    ai_context = ossie.get("ai_context")
    if isinstance(ai_context, str) and ai_context:
        description = f"{description}\n{ai_context}" if description else ai_context
    if description:
        result["description"] = description

    # datasets -> tables
    datasets = ossie.get("datasets")
    if datasets:
        tables = [_convert_dataset(ds) for ds in datasets]
        if tables:
            result["tables"] = tables

    # relationships
    relationships = ossie.get("relationships")
    if relationships:
        converted_rels = [_convert_relationship(rel) for rel in relationships]
        if converted_rels:
            result["relationships"] = converted_rels

    # metrics
    metrics = ossie.get("metrics")
    if metrics:
        converted_metrics = []
        for m in metrics:
            converted = _convert_named_expr(m, "metric")
            if converted is not None:
                converted_metrics.append(converted)
        if converted_metrics:
            result["metrics"] = converted_metrics

    dropped_ai = ["ai_context"] if isinstance(ai_context, dict) and ai_context else []
    _warn_dropped_fields(ossie, "model", extra_dropped=dropped_ai)

    return result


def _convert_dataset(dataset):
    """Converts an Ossie dataset dict to a Snowflake table dict."""
    result = {}
    name = dataset.get("name")
    if not name:
        raise OsiConversionError("Missing required 'name' field in dataset")
    result["name"] = name

    # source -> base_table
    source = dataset.get("source")
    base_table = _parse_source(source)
    if base_table is not None:
        result["base_table"] = base_table

    # primary_key: [col,...] -> primary_key: {columns: [col,...]}
    pk = dataset.get("primary_key")
    if pk:
        result["primary_key"] = {"columns": pk}

    # unique_keys: [[...], ...] -> unique_keys: [{columns: [...]}, ...]
    uks = dataset.get("unique_keys")
    if uks:
        result["unique_keys"] = [{"columns": uk} for uk in uks]

    description = dataset.get("description")
    ai_context = dataset.get("ai_context")
    if isinstance(ai_context, str) and ai_context:
        description = f"{description}\n{ai_context}" if description else ai_context
    if description:
        result["description"] = description

    # Extract synonyms from ai_context
    synonyms = _extract_synonyms(ai_context)
    if synonyms:
        result["synonyms"] = synonyms

    # Classify fields into dimensions, time_dimensions, facts
    fields = dataset.get("fields")
    if fields:
        dimensions = []
        time_dimensions = []
        facts = []

        for field in fields:
            classification = _classify_field(field)
            converted = _convert_named_expr(field, "field")
            if converted is None:
                continue
            if classification == "time_dimension":
                time_dimensions.append(converted)
            elif classification == "dimension":
                dimensions.append(converted)
            else:
                facts.append(converted)

        if dimensions:
            result["dimensions"] = dimensions
        if time_dimensions:
            result["time_dimensions"] = time_dimensions
        if facts:
            result["facts"] = facts

    dropped_ai = []
    if isinstance(ai_context, dict):
        non_synonym_keys = [k for k in ai_context if k != "synonyms"]
        if non_synonym_keys:
            dropped_ai = [f"ai_context ({', '.join(non_synonym_keys)})"]
    _warn_dropped_fields(dataset, f"dataset '{name}'", extra_dropped=dropped_ai)

    return result


def _classify_field(field):
    """Classify a field as 'fact', 'dimension', or 'time_dimension'.

    ``datatype`` declares the field's data type; ``dimension.is_time`` is
    an independent temporal-role marker. Classification rules:

    - A field with no ``dimension`` block is a ``fact`` regardless of
      ``datatype`` (data type does not imply role).
    - Explicit ``dimension.is_time`` always wins: ``True`` classifies as
      ``time_dimension``; ``False`` classifies as ``dimension`` even when
      ``datatype`` is temporal (author opt-out for e.g. audit timestamps).
    - When ``dimension.is_time`` is unset, it defaults to ``True`` for
      temporal ``datatype`` values (``date``, ``time``, ``timestamp``,
      ``timestamp_tz``) and ``False`` otherwise.
    """
    dimension = field.get("dimension")
    if dimension is None:
        return "fact"
    is_time = dimension.get("is_time") if isinstance(dimension, dict) else None
    if is_time is True:
        return "time_dimension"
    if is_time is False:
        return "dimension"
    # is_time is unset; default from datatype
    datatype = field.get("datatype")
    if datatype in _TIME_DATATYPES:
        return "time_dimension"
    return "dimension"


def _convert_named_expr(entry, kind):
    """Converts an Ossie field or metric dict to a Snowflake entry with name, expr,
    description, and synonyms.

    Args:
        entry: The Ossie field or metric dict.
        kind: Human-readable type for error messages (e.g., "field", "metric").
    """
    name = entry.get("name")
    if not name:
        raise OsiConversionError(f"Missing required 'name' in {kind}")

    expr_str = _extract_expression(entry.get("expression"), name)
    if expr_str is None:
        return None

    result = {}
    result["name"] = name
    result["expr"] = expr_str

    description = entry.get("description")
    ai_context = entry.get("ai_context")
    if isinstance(ai_context, str) and ai_context:
        description = f"{description}\n{ai_context}" if description else ai_context
    if description:
        result["description"] = description

    synonyms = _extract_synonyms(ai_context)
    if synonyms:
        result["synonyms"] = synonyms

    dropped_ai = []
    if isinstance(ai_context, dict):
        non_synonym_keys = [k for k in ai_context if k != "synonyms"]
        if non_synonym_keys:
            dropped_ai = [f"ai_context ({', '.join(non_synonym_keys)})"]
    _warn_dropped_fields(entry, f"{kind} '{name}'", extra_dropped=dropped_ai)

    return result


def _convert_relationship(rel):
    """Converts an Ossie relationship dict to a Snowflake relationship dict."""
    result = {}
    rel_name = rel.get("name")
    if not rel_name:
        raise OsiConversionError("Missing required 'name' field in relationship")
    result["name"] = rel_name

    left_table = rel.get("from")
    if not left_table:
        raise OsiConversionError(
            f"Relationship '{rel_name}': missing required 'from' field"
        )
    right_table = rel.get("to")
    if not right_table:
        raise OsiConversionError(
            f"Relationship '{rel_name}': missing required 'to' field"
        )
    result["left_table"] = left_table
    result["right_table"] = right_table

    from_cols = rel.get("from_columns", [])
    to_cols = rel.get("to_columns", [])

    if len(from_cols) != len(to_cols):
        raise OsiConversionError(
            f"Relationship '{rel_name}': from_columns and to_columns must have the "
            f"same length (got {len(from_cols)} and {len(to_cols)})"
        )

    relationship_columns = [
        {"left_column": fc, "right_column": tc}
        for fc, tc in zip(from_cols, to_cols)
    ]
    if relationship_columns:
        result["relationship_columns"] = relationship_columns

    dropped_ai = ["ai_context"] if rel.get("ai_context") else []
    _warn_dropped_fields(rel, f"relationship '{rel_name}'", extra_dropped=dropped_ai)

    return result


def _extract_expression(expression, field_name):
    """Selects the best dialect expression for Snowflake.

    Returns the expression string, or None if only unsupported dialects are
    present (the field should be skipped). Raises OsiConversionError if the
    expression or dialects list is missing entirely.
    """
    if expression is None or not isinstance(expression, dict):
        raise OsiConversionError(
            f"Missing or malformed expression for field/metric '{field_name}'"
        )

    dialects = expression.get("dialects")
    if not dialects:
        raise OsiConversionError(
            f"Missing expression for field/metric '{field_name}'"
        )

    snowflake_expr = None
    ansi_expr = None

    for d in dialects:
        dialect_name = (d.get("dialect") or "").upper()
        if dialect_name == "SNOWFLAKE":
            snowflake_expr = d.get("expression")
        elif dialect_name == "ANSI_SQL":
            ansi_expr = d.get("expression")

    if snowflake_expr is not None:
        return snowflake_expr
    if ansi_expr is not None:
        return ansi_expr

    dialect_names = [d.get("dialect", "") for d in dialects]
    warnings.warn(
        f"Skipping field/metric '{field_name}': no Snowflake-compatible expression "
        f"(has dialects: {', '.join(dialect_names)}; requires SNOWFLAKE or ANSI_SQL)"
    )
    return None


def _normalize_identifier(identifier):
    """Uppercases an unquoted Snowflake identifier; strips and preserves quoted ones."""
    stripped = identifier.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        return stripped
    return stripped.upper()


def _parse_source(source):
    """Parses an Ossie dataset source string into a Snowflake base_table dict.

    Returns None if source is empty/None. Returns {"definition": source} for
    subqueries. Otherwise splits into 3-part db.schema.table.
    """
    if not source:
        return None

    source_stripped = str(source).strip()
    if not source_stripped:
        return None

    # Detect subqueries — require whitespace after the keyword to avoid false
    # positives on table names like WITH_TABLE or SELECT_RESULTS.
    upper = source_stripped.upper()
    if upper.startswith(("SELECT ", "SELECT\n", "SELECT\t",
                          "WITH ", "WITH\n", "WITH\t")):
        return {"definition": source_stripped}

    # Strict 3-part rule: source must be db.schema.table. This may be relaxed
    # in the future to allow 1- or 2-part names.
    # TODO: Quoted identifiers (e.g., "my.db"."my schema"."my table") are not
    # handled. Basic dot-splitting only.
    parts = source_stripped.split(".")
    if len(parts) == 3:
        # Only uppercase unquoted identifiers; preserve quoted ones as-is.
        return {
            "database": _normalize_identifier(parts[0]),
            "schema": _normalize_identifier(parts[1]),
            "table": _normalize_identifier(parts[2]),
        }

    raise OsiConversionError(
        f"Source '{source}' must be a fully qualified db.schema.table or a subquery"
    )


def _extract_synonyms(ai_context):
    """Extracts synonyms list from a structured ai_context object.

    If ai_context is a dict with a 'synonyms' key, returns the synonyms list.
    If ai_context is a plain string or None, returns None.
    """
    if isinstance(ai_context, dict):
        synonyms = ai_context.get("synonyms")
        if isinstance(synonyms, list) and synonyms:
            return list(synonyms)
    return None


def _warn_dropped_fields(source, context, extra_dropped=None):
    """Warns about Ossie fields that have no Snowflake counterpart and are dropped.

    Checks for universally-dropped fields (custom_extensions, label, version).
    Callers handle ai_context warnings themselves since consumption logic varies.

    Args:
        source: The Ossie dict being converted.
        context: Human-readable description (e.g., "field 'col1'").
        extra_dropped: Optional list of additional field descriptions to report
            as dropped (e.g., ai_context details computed by the caller).
    """
    dropped = list(extra_dropped) if extra_dropped else []

    if source.get("custom_extensions"):
        dropped.append("custom_extensions")

    if source.get("version"):
        dropped.append("version")

    if source.get("label"):
        dropped.append("label")

    if dropped:
        warnings.warn(
            f"Dropped from {context} (no Snowflake counterpart): "
            + ", ".join(dropped)
        )


def main():
    parser = argparse.ArgumentParser(
        description="Convert Ossie YAML semantic model to Snowflake Cortex Analyst YAML"
    )
    parser.add_argument(
        "-i", "--input", required=True, help="Path to the Ossie YAML input file"
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Path to write the Snowflake YAML output"
    )
    args = parser.parse_args()

    with open(args.input, "r") as f:
        osi_yaml_str = f.read()

    try:
        snowflake_yaml_str = convert_osi_to_snowflake(osi_yaml_str)
    except OsiConversionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "w") as f:
        f.write(snowflake_yaml_str)

    print(f"Converted {args.input} -> {args.output}")


if __name__ == "__main__":
    main()
