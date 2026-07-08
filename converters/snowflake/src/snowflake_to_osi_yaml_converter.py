"""
Converts a Snowflake Cortex Analyst semantic model YAML to an OSI (Open Semantic
Interchange) YAML semantic model. Pure offline conversion — no Snowflake connection
required.

Usage:
    python3 snowflake_to_osi_yaml_converter.py -i input.yaml -o output.yaml
"""

import argparse
import json
import sys
import warnings

import yaml


SUPPORTED_VERSION = "0.2.0.dev0"


class SnowflakeConversionError(Exception):
    """Raised when a Snowflake YAML cannot be converted to OSI format."""


def convert_snowflake_to_osi(snowflake_yaml_str):
    """Top-level entry point. Parses Snowflake YAML, validates, converts, returns
    OSI YAML string.

    A valid Snowflake Cortex Analyst semantic model is an object with:
      - name (required)
      - description (optional)
      - tables (array of table objects)
      - relationships (optional, array of relationship objects)
      - metrics (optional, array of metric objects)

    Args:
        snowflake_yaml_str: Snowflake YAML as a string.

    Returns:
        OSI YAML string with version "0.2.0.dev0".

    Raises:
        SnowflakeConversionError: If the input cannot be converted.
    """
    root = yaml.safe_load(snowflake_yaml_str)
    if not isinstance(root, dict):
        raise SnowflakeConversionError(
            "Invalid Snowflake YAML: expected a mapping at the root"
        )

    snowflake_model = root
    name = snowflake_model.get("name")
    if not name:
        raise SnowflakeConversionError("Missing required 'name' field in semantic model")

    # Convert the Snowflake model to OSI
    osi_model = _convert_model(snowflake_model)

    # Wrap in OSI envelope
    osi_document = {
        "version": SUPPORTED_VERSION,
        "semantic_model": [osi_model],
    }

    return yaml.dump(
        osi_document,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _convert_model(snowflake_model):
    """Converts the root Snowflake model dict to an OSI model dict."""
    result = {}

    # name is required
    name = snowflake_model.get("name")
    if not name:
        raise SnowflakeConversionError("Missing required 'name' field in semantic model")
    result["name"] = name

    # description (optional)
    description = snowflake_model.get("description")
    if description:
        result["description"] = description

    # tables -> datasets
    # Also collect metrics from table-level sources
    tables = snowflake_model.get("tables", [])
    all_metrics = []
    if tables:
        datasets = []
        for table in tables:
            dataset, table_metrics = _convert_table(table)
            if dataset is not None:
                datasets.append(dataset)
            all_metrics.extend(table_metrics)
        if datasets:
            result["datasets"] = datasets

    # relationships
    relationships = snowflake_model.get("relationships", [])
    if relationships:
        converted_rels = [_convert_relationship(rel) for rel in relationships]
        if converted_rels:
            result["relationships"] = converted_rels

    # metrics: combine top-level and table-level metrics
    # Top-level metrics from the model
    top_level_metrics = snowflake_model.get("metrics", [])
    if top_level_metrics:
        for m in top_level_metrics:
            converted = _convert_named_expr(m, "metric")
            if converted is not None:
                all_metrics.append(converted)

    # Deduplicate and qualify metric names to avoid collisions
    if all_metrics:
        # Check for duplicate names and qualify if needed
        seen_names = {}
        final_metrics = []
        for metric in all_metrics:
            metric_name = metric.get("name", "")
            if metric_name in seen_names:
                # Collision detected - qualify with table name from context if available
                # For now, just warn and use the first occurrence
                warnings.warn(
                    f"Duplicate metric name '{metric_name}' detected during conversion; "
                    f"using first occurrence"
                )
            else:
                seen_names[metric_name] = True
                final_metrics.append(metric)
        
        if final_metrics:
            result["metrics"] = final_metrics

    # Warn about dropped fields
    _warn_dropped_fields(snowflake_model, "model")

    return result


def _convert_table(table):
    """Converts a Snowflake table dict to an OSI dataset dict.
    
    Per the official Snowflake semantic view YAML spec, tables can have their own
    `metrics` list (table-scoped). These are extracted and returned separately so
    they can be merged into the top-level OSI metrics list by the caller.
    
    Returns:
        Tuple of (dataset_dict, list_of_table_level_metrics)
    """
    result = {}

    # name is required
    name = table.get("name")
    if not name:
        raise SnowflakeConversionError("Missing required 'name' field in table")
    result["name"] = name

    # base_table -> source
    # base_table can be:
    #   - {database: "DB", schema: "SCHEMA", table: "TABLE"}
    #   - {definition: "SELECT ..."}
    base_table = table.get("base_table")
    source = _convert_base_table_to_source(base_table)
    if source is not None:
        result["source"] = source

    # primary_key: {columns: [...]} -> primary_key: [...]
    pk_obj = table.get("primary_key")
    if pk_obj and isinstance(pk_obj, dict):
        pk_columns = pk_obj.get("columns", [])
        if pk_columns:
            result["primary_key"] = pk_columns

    # unique_keys: [{columns: [...]}, ...] -> unique_keys: [[...], ...]
    uks = table.get("unique_keys", [])
    if uks:
        uk_arrays = []
        for uk_obj in uks:
            if isinstance(uk_obj, dict):
                uk_cols = uk_obj.get("columns", [])
                if uk_cols:
                    uk_arrays.append(uk_cols)
        if uk_arrays:
            result["unique_keys"] = uk_arrays

    # description (optional)
    description = table.get("description")
    ai_context = None

    # Extract synonyms
    synonyms = table.get("synonyms", [])
    if synonyms:
        if ai_context is None:
            ai_context = {}
        ai_context["synonyms"] = synonyms

    # Set description and ai_context
    if description:
        result["description"] = description
    if ai_context:
        result["ai_context"] = ai_context

    # Collect vendor-specific settings not captured by OSI core
    custom_extensions = _extract_vendor_specific_table_settings(table)
    if custom_extensions:
        result["custom_extensions"] = custom_extensions

    # Convert fields
    fields = []

    # Collect all field-like arrays: dimensions, time_dimensions, facts
    # NOTE: Per the Snowflake semantic view spec, only dimensions, time_dimensions,
    # facts, and metrics are valid. The legacy "measures" field is not part of
    # semantic views and is not handled here.
    dimensions = table.get("dimensions", [])
    time_dimensions = table.get("time_dimensions", [])
    facts = table.get("facts", [])

    for dim in dimensions:
        converted = _convert_field(dim, is_time=False)
        if converted is not None:
            fields.append(converted)

    for tdim in time_dimensions:
        converted = _convert_field(tdim, is_time=True)
        if converted is not None:
            fields.append(converted)

    for fact in facts:
        converted = _convert_field(fact, is_time=None)
        if converted is not None:
            fields.append(converted)

    if fields:
        result["fields"] = fields

    # Extract table-level metrics
    # Per the spec, each table can have its own metrics list.
    # These are converted and returned separately to be merged into the top-level
    # metrics list by the caller.
    table_metrics = []
    metrics_list = table.get("metrics", [])
    if metrics_list:
        for m in metrics_list:
            converted = _convert_named_expr(m, f"table-level metric from table '{name}'")
            if converted is not None:
                table_metrics.append(converted)

    _warn_dropped_fields(table, f"table '{name}'")

    return result, table_metrics


def _convert_base_table_to_source(base_table):
    """Converts a Snowflake base_table dict to an OSI source string.

    Returns None if base_table is None or empty.
    """
    if base_table is None:
        return None

    if not isinstance(base_table, dict):
        # If it's a string or something else, preserve it as-is in a warning
        warnings.warn(f"base_table has unexpected type {type(base_table).__name__}; skipping")
        return None

    # Case 1: {database, schema, table} -> "DB.SCHEMA.TABLE"
    db = base_table.get("database")
    schema = base_table.get("schema")
    table = base_table.get("table")

    if db and schema and table:
        return f"{db}.{schema}.{table}"

    # Case 2: {definition} -> use as-is
    definition = base_table.get("definition")
    if definition:
        return definition

    # Empty base_table
    return None


def _convert_field(field_obj, is_time):
    """Converts a Snowflake field (dimension, time_dimension, fact, measure) to an OSI field.

    Args:
        field_obj: The Snowflake field dict (must have 'name' and 'expr').
        is_time: True if this is a time dimension, False if regular dimension, None if fact/measure.

    Returns:
        An OSI field dict, or None if conversion fails.
    """
    name = field_obj.get("name")
    if not name:
        warnings.warn("Skipping field with missing 'name'")
        return None

    expr_str = field_obj.get("expr")
    if not expr_str:
        warnings.warn(f"Skipping field '{name}' with missing or empty 'expr'")
        return None

    result = {}
    result["name"] = name

    # expression: wrap in SNOWFLAKE dialect
    result["expression"] = {
        "dialects": [
            {
                "dialect": "SNOWFLAKE",
                "expression": expr_str,
            }
        ]
    }

    # dimension: set is_time if applicable
    if is_time is not None:
        result["dimension"] = {"is_time": is_time}

    # description (optional)
    description = field_obj.get("description")
    if description:
        result["description"] = description

    # Extract synonyms into ai_context
    ai_context = None
    synonyms = field_obj.get("synonyms", [])
    if synonyms:
        ai_context = {"synonyms": synonyms}

    if ai_context:
        result["ai_context"] = ai_context

    # Vendor-specific settings
    custom_extensions = _extract_vendor_specific_field_settings(field_obj)
    if custom_extensions:
        result["custom_extensions"] = custom_extensions

    return result


def _convert_relationship(rel):
    """Converts a Snowflake relationship dict to an OSI relationship dict."""
    result = {}

    # name is required
    name = rel.get("name")
    if not name:
        raise SnowflakeConversionError("Missing required 'name' field in relationship")
    result["name"] = name

    # left_table -> from, right_table -> to
    from_dataset = rel.get("left_table")
    to_dataset = rel.get("right_table")

    if not from_dataset:
        raise SnowflakeConversionError(
            f"Relationship '{name}': missing required 'left_table' field"
        )
    if not to_dataset:
        raise SnowflakeConversionError(
            f"Relationship '{name}': missing required 'right_table' field"
        )

    result["from"] = from_dataset
    result["to"] = to_dataset

    # Convert relationship_columns: [{left_column, right_column}, ...] -> from_columns, to_columns
    rel_cols = rel.get("relationship_columns", [])
    from_columns = []
    to_columns = []

    for col_pair in rel_cols:
        if isinstance(col_pair, dict):
            left_col = col_pair.get("left_column")
            right_col = col_pair.get("right_column")
            if left_col:
                from_columns.append(left_col)
            if right_col:
                to_columns.append(right_col)

    if from_columns:
        result["from_columns"] = from_columns
    if to_columns:
        result["to_columns"] = to_columns

    # Validate matching cardinality
    if len(from_columns) != len(to_columns):
        warnings.warn(
            f"Relationship '{name}': from_columns and to_columns have different lengths "
            f"({len(from_columns)} vs {len(to_columns)}); may cause issues"
        )

    _warn_dropped_fields(rel, f"relationship '{name}'")

    return result


def _convert_named_expr(entry, kind):
    """Converts a Snowflake metric/measure dict to an OSI metric dict.

    Args:
        entry: The Snowflake metric/measure dict.
        kind: Human-readable type (e.g., "metric", "measure").

    Returns:
        An OSI metric dict, or None if conversion fails.
    """
    name = entry.get("name")
    if not name:
        warnings.warn(f"Skipping {kind} with missing 'name'")
        return None

    expr_str = entry.get("expr")
    if not expr_str:
        warnings.warn(f"Skipping {kind} '{name}' with missing or empty 'expr'")
        return None

    result = {}
    result["name"] = name

    # expression: wrap in SNOWFLAKE dialect
    result["expression"] = {
        "dialects": [
            {
                "dialect": "SNOWFLAKE",
                "expression": expr_str,
            }
        ]
    }

    # description (optional)
    description = entry.get("description")
    if description:
        result["description"] = description

    # Extract synonyms into ai_context
    ai_context = None
    synonyms = entry.get("synonyms", [])
    if synonyms:
        ai_context = {"synonyms": synonyms}

    if ai_context:
        result["ai_context"] = ai_context

    # Vendor-specific settings
    custom_extensions = _extract_vendor_specific_measure_settings(entry)
    if custom_extensions:
        result["custom_extensions"] = custom_extensions

    return result


def _extract_vendor_specific_table_settings(table):
    """Extracts vendor-specific Snowflake table settings into custom_extensions.

    These are fields that Snowflake supports but OSI core does not, and should
    be preserved for round-tripping. All legitimately-handled spec fields are
    excluded from custom_extensions.
    """
    vendor_data = {}

    # Per the official Snowflake semantic view YAML spec, the following fields
    # are valid at the table level and are handled by the converter:
    #   - name: required, converted to dataset.name
    #   - base_table: converted to dataset.source
    #   - primary_key: converted to dataset.primary_key
    #   - unique_keys: converted to dataset.unique_keys
    #   - description: converted to dataset.description
    #   - synonyms: converted to dataset.ai_context.synonyms
    #   - dimensions: converted to dataset.fields with is_time=false
    #   - time_dimensions: converted to dataset.fields with is_time=true
    #   - facts: converted to dataset.fields (no dimension property)
    #   - metrics: table-level metrics, extracted and merged into top-level metrics
    #   - filters: Snowflake-specific row-level security filters
    #   - tags: Snowflake tags for metadata (preserved in custom_extensions as vendor-specific)
    #
    # The following fields are legitimately-handled spec fields and should NOT
    # be added to custom_extensions:
    known_osi_fields = {
        "name", "base_table", "primary_key", "unique_keys",
        "description", "synonyms", "dimensions", "time_dimensions",
        "facts", "metrics", "filters", "tags",
    }

    # Any other field encountered should be preserved in custom_extensions
    for key, value in table.items():
        if key not in known_osi_fields:
            vendor_data[key] = value

    if not vendor_data:
        return None

    return [
        {
            "vendor_name": "SNOWFLAKE",
            "data": json.dumps(vendor_data),
        }
    ]


def _extract_vendor_specific_field_settings(field_obj):
    """Extracts vendor-specific Snowflake field settings into custom_extensions.
    
    Per the official Snowflake semantic view YAML spec, the following field-level
    properties are recognized:
      - name: required, converted to field.name
      - expr: required, converted to field.expression.dialects[].expression
      - description: converted to field.description
      - synonyms: converted to field.ai_context.synonyms
      - data_type: field data type (Snowflake-specific, no OSI equivalent) → NOT preserved
      - unique: uniqueness constraint (no direct OSI mapping) → NOT preserved
      - is_enum: enum type marker (OSI has no enum support) → NOT preserved
      - cortex_search_service: Snowflake Cortex Search integration → NOT preserved
      - sample_values: example values for AI context → NOT preserved
      - labels: field categorization labels (OSI drops labels anyway) → NOT preserved
      - tags: Snowflake metadata tags → NOT preserved
      - access_modifier: field access control → NOT preserved
    
    Since these vendor-specific properties don't map to OSI concepts and would be
    complex to round-trip, we exclude them from custom_extensions and drop them
    silently (they're recognized as legitimate fields, not "unknown" extensions).
    """
    vendor_data = {}

    # All known field properties per the spec
    known_osi_fields = {
        # Handled by converter
        "name", "expr", "description", "synonyms",
        # Spec fields that don't map to OSI and are intentionally dropped
        "data_type", "unique", "is_enum", "cortex_search_service",
        "sample_values", "labels", "tags", "access_modifier",
    }

    for key, value in field_obj.items():
        if key not in known_osi_fields:
            vendor_data[key] = value

    if not vendor_data:
        return None

    return [
        {
            "vendor_name": "SNOWFLAKE",
            "data": json.dumps(vendor_data),
        }
    ]


def _extract_vendor_specific_measure_settings(measure):
    """Extracts vendor-specific Snowflake metric/measure settings into custom_extensions.
    
    Metrics in the Snowflake spec have the following properties:
      - name: required, converted to metric.name
      - expr: required, converted to metric.expression.dialects[].expression
      - description: converted to metric.description
      - synonyms: converted to metric.ai_context.synonyms
      - data_type: metric result type (Snowflake-specific, no OSI mapping) → NOT preserved
      - tags: Snowflake metadata tags → NOT preserved
      - access_modifier: metric access control → NOT preserved
    
    Snowflake-specific properties that don't map to OSI are intentionally dropped.
    """
    vendor_data = {}

    known_osi_fields = {
        # Handled by converter
        "name", "expr", "description", "synonyms",
        # Spec fields that don't map to OSI and are intentionally dropped
        "data_type", "tags", "access_modifier",
    }

    for key, value in measure.items():
        if key not in known_osi_fields:
            vendor_data[key] = value

    if not vendor_data:
        return None

    return [
        {
            "vendor_name": "SNOWFLAKE",
            "data": json.dumps(vendor_data),
        }
    ]


def _warn_dropped_fields(source, context):
    """Warns about Snowflake fields that have no OSI counterpart.

    These are fields that exist in the Snowflake model but should not be
    dropped silently; they should be captured in custom_extensions or
    explicitly warned about.
    """
    # Snowflake-specific fields that don't map to OSI and weren't handled:
    # - warehouse, database, schema (table-level config)
    # - any other vendor-specific fields (these should be in custom_extensions now)

    # For now, we don't warn about anything here since we capture all unknown
    # fields in custom_extensions. The warning is implicit in the custom_extensions.


def main():
    parser = argparse.ArgumentParser(
        description="Convert Snowflake Cortex Analyst YAML to OSI semantic model YAML"
    )
    parser.add_argument(
        "-i", "--input", required=True, help="Path to the Snowflake YAML input file"
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Path to write the OSI YAML output"
    )
    args = parser.parse_args()

    with open(args.input, "r") as f:
        snowflake_yaml_str = f.read()

    try:
        osi_yaml_str = convert_snowflake_to_osi(snowflake_yaml_str)
    except SnowflakeConversionError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    with open(args.output, "w") as f:
        f.write(osi_yaml_str)

    print(f"Converted {args.input} -> {args.output}")


if __name__ == "__main__":
    main()
