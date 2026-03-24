#!/usr/bin/env python3
"""
OSI Semantic Model Validator

Validates OSI YAML files against:
1. JSON Schema (structure, types, enums)
2. Unique names (semantic models, datasets, fields, metrics, relationships, dialects)
3. Valid relationship references and column count consistency
4. Custom extension JSON validity
5. SQL syntax (using sqlglot)

Usage:
    python validation/validate.py <yaml_file>
    python validation/validate.py examples/tpcds_semantic_model.yaml
"""

import json
import sys
from pathlib import Path

try:
    import yaml
    from jsonschema import Draft202012Validator
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install pyyaml jsonschema")
    sys.exit(1)

try:
    import sqlglot
    from sqlglot.errors import ParseError
    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False

# Map OSI dialects to sqlglot dialects
DIALECT_MAP = {
    "ANSI_SQL": None,  # sqlglot default
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
    "MDX": None,  # Not supported by sqlglot, skip validation
    "TABLEAU": None,  # Not supported by sqlglot, skip validation
}

# Dialects that sqlglot cannot parse
SKIP_SQL_VALIDATION = {"MDX", "TABLEAU"}


def validate_schema(data: dict, schema: dict) -> list[str]:
    """Validate against JSON Schema."""
    validator = Draft202012Validator(schema)
    errors = []
    for error in validator.iter_errors(data):
        path = " -> ".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
        errors.append(f"[Schema] {path}: {error.message}")
    return errors


def find_duplicates(items: list[str]) -> list[str]:
    """Find duplicate items in a list."""
    seen = set()
    duplicates = []
    for item in items:
        if item in seen:
            duplicates.append(item)
        seen.add(item)
    return duplicates


def validate_unique_names(data: dict) -> list[str]:
    """Validate unique names for datasets, fields, metrics, relationships."""
    errors = []

    # Check unique semantic model names
    model_names = [m.get("name") for m in data.get("semantic_model", []) if m.get("name")]
    for dup in find_duplicates(model_names):
        errors.append(f"[Unique] Duplicate semantic model name '{dup}'")

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")

        # Check unique dataset names
        dataset_names = [d.get("name") for d in model.get("datasets", []) if d.get("name")]
        for dup in find_duplicates(dataset_names):
            errors.append(f"[Unique] Duplicate dataset name '{dup}' in model '{model_name}'")

        # Check unique field names within each dataset
        for dataset in model.get("datasets", []):
            dataset_name = dataset.get("name", "<unnamed>")
            field_names = [f.get("name") for f in dataset.get("fields", []) if f.get("name")]
            for dup in find_duplicates(field_names):
                errors.append(f"[Unique] Duplicate field name '{dup}' in dataset '{dataset_name}'")

        # Check unique metric names
        metric_names = [m.get("name") for m in model.get("metrics", []) if m.get("name")]
        for dup in find_duplicates(metric_names):
            errors.append(f"[Unique] Duplicate metric name '{dup}' in model '{model_name}'")

        # Check unique relationship names
        rel_names = [r.get("name") for r in model.get("relationships", []) if r.get("name")]
        for dup in find_duplicates(rel_names):
            errors.append(f"[Unique] Duplicate relationship name '{dup}' in model '{model_name}'")

    return errors


def validate_references(data: dict) -> list[str]:
    """Validate that relationships reference existing datasets."""
    errors = []

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")
        dataset_names = {d.get("name") for d in model.get("datasets", []) if d.get("name")}

        for rel in model.get("relationships", []):
            rel_name = rel.get("name", "<unnamed>")
            from_ds = rel.get("from")
            to_ds = rel.get("to")

            if from_ds and from_ds not in dataset_names:
                errors.append(f"[Reference] Relationship '{rel_name}' references unknown dataset '{from_ds}'")
            if to_ds and to_ds not in dataset_names:
                errors.append(f"[Reference] Relationship '{rel_name}' references unknown dataset '{to_ds}'")

            # Check from_columns and to_columns have the same length
            from_cols = rel.get("from_columns", [])
            to_cols = rel.get("to_columns", [])
            if from_cols and to_cols and len(from_cols) != len(to_cols):
                errors.append(
                    f"[Reference] Relationship '{rel_name}': from_columns has {len(from_cols)} column(s) "
                    f"but to_columns has {len(to_cols)} column(s)"
                )

    return errors


def validate_custom_extensions(data: dict) -> list[str]:
    """Validate that custom extension 'data' fields contain valid JSON."""
    errors = []

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")

        # Collect all (context, extensions) pairs to check
        extension_sources = [(f"model '{model_name}'", model.get("custom_extensions", []))]

        for dataset in model.get("datasets", []):
            ds_name = dataset.get("name", "<unnamed>")
            extension_sources.append((f"dataset '{ds_name}'", dataset.get("custom_extensions", [])))
            for field in dataset.get("fields", []):
                field_name = field.get("name", "<unnamed>")
                extension_sources.append(
                    (f"field '{ds_name}.{field_name}'", field.get("custom_extensions", []))
                )

        for metric in model.get("metrics", []):
            metric_name = metric.get("name", "<unnamed>")
            extension_sources.append((f"metric '{metric_name}'", metric.get("custom_extensions", [])))

        for rel in model.get("relationships", []):
            rel_name = rel.get("name", "<unnamed>")
            extension_sources.append((f"relationship '{rel_name}'", rel.get("custom_extensions", [])))

        for context, extensions in extension_sources:
            for ext in extensions or []:
                vendor = ext.get("vendor_name", "<unknown>")
                ext_data = ext.get("data")
                if ext_data is not None:
                    try:
                        json.loads(ext_data)
                    except (json.JSONDecodeError, TypeError) as e:
                        errors.append(
                            f"[Extension] Custom extension for vendor '{vendor}' in {context} "
                            f"has invalid JSON in 'data': {e}"
                        )

    return errors


def validate_duplicate_dialects(data: dict) -> list[str]:
    """Validate that no expression has duplicate dialect entries."""
    errors = []

    for model in data.get("semantic_model", []):
        # Check field expressions
        for dataset in model.get("datasets", []):
            ds_name = dataset.get("name", "<unnamed>")
            for field in dataset.get("fields", []):
                field_name = field.get("name", "<unnamed>")
                dialects = [
                    d.get("dialect")
                    for d in field.get("expression", {}).get("dialects", [])
                    if d.get("dialect")
                ]
                for dup in find_duplicates(dialects):
                    errors.append(f"[Unique] Duplicate dialect '{dup}' in field '{ds_name}.{field_name}'")

        # Check metric expressions
        for metric in model.get("metrics", []):
            metric_name = metric.get("name", "<unnamed>")
            dialects = [
                d.get("dialect")
                for d in metric.get("expression", {}).get("dialects", [])
                if d.get("dialect")
            ]
            for dup in find_duplicates(dialects):
                errors.append(f"[Unique] Duplicate dialect '{dup}' in metric '{metric_name}'")

    return errors


def validate_sql_expression(expr: str, dialect: str, context: str) -> str | None:
    """Validate a single SQL expression. Returns error message or None if valid."""
    if not SQLGLOT_AVAILABLE:
        return None

    if dialect in SKIP_SQL_VALIDATION:
        return None

    sqlglot_dialect = DIALECT_MAP.get(dialect)

    try:
        # Try parsing as expression first (for field expressions like "column_name")
        sqlglot.parse_one(expr, dialect=sqlglot_dialect)
        return None
    except ParseError:
        pass

    try:
        # Try wrapping in SELECT for simple column references
        sqlglot.parse_one(f"SELECT {expr}", dialect=sqlglot_dialect)
        return None
    except ParseError as e:
        return f"[SQL] {context}: {str(e).split(chr(10))[0]}"


def validate_sql(data: dict) -> list[str]:
    """Validate SQL expressions in fields and metrics."""
    if not SQLGLOT_AVAILABLE:
        return ["[SQL] Warning: sqlglot not installed, skipping SQL validation. Install with: pip install sqlglot"]

    errors = []

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")

        # Validate field expressions
        for dataset in model.get("datasets", []):
            dataset_name = dataset.get("name", "<unnamed>")
            for field in dataset.get("fields", []):
                field_name = field.get("name", "<unnamed>")
                expression = field.get("expression", {})
                for dialect_expr in expression.get("dialects", []):
                    dialect = dialect_expr.get("dialect", "ANSI_SQL")
                    expr = dialect_expr.get("expression", "")
                    if expr:
                        context = f"Field '{dataset_name}.{field_name}' ({dialect})"
                        error = validate_sql_expression(expr, dialect, context)
                        if error:
                            errors.append(error)

        # Validate metric expressions
        for metric in model.get("metrics", []):
            metric_name = metric.get("name", "<unnamed>")
            expression = metric.get("expression", {})
            for dialect_expr in expression.get("dialects", []):
                dialect = dialect_expr.get("dialect", "ANSI_SQL")
                expr = dialect_expr.get("expression", "")
                if expr:
                    context = f"Metric '{metric_name}' ({dialect})"
                    error = validate_sql_expression(expr, dialect, context)
                    if error:
                        errors.append(error)

    return errors


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    yaml_path = Path(sys.argv[1])
    schema_path = Path(__file__).parent.parent / "core-spec" / "osi-schema.json"

    if not yaml_path.exists():
        print(f"Error: File not found: {yaml_path}")
        sys.exit(1)

    if not schema_path.exists():
        print(f"Error: Schema not found: {schema_path}")
        sys.exit(1)

    # Load files
    with open(schema_path) as f:
        schema = json.load(f)

    with open(yaml_path) as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"Error: Invalid YAML: {e}")
            sys.exit(1)

    # Run validations
    errors = []
    errors.extend(validate_schema(data, schema))
    errors.extend(validate_unique_names(data))
    errors.extend(validate_references(data))
    errors.extend(validate_custom_extensions(data))
    errors.extend(validate_duplicate_dialects(data))
    errors.extend(validate_sql(data))

    # Report results
    if errors:
        # Separate warnings from errors
        warnings = [e for e in errors if "Warning:" in e]
        actual_errors = [e for e in errors if "Warning:" not in e]

        for warning in warnings:
            print(f"  {warning}")

        if actual_errors:
            print(f"\nValidation FAILED with {len(actual_errors)} error(s):\n")
            for error in actual_errors:
                print(f"  {error}")
            sys.exit(1)
        else:
            print(f"Validation PASSED: {yaml_path.name}")
            sys.exit(0)
    else:
        print(f"Validation PASSED: {yaml_path.name}")
        sys.exit(0)


if __name__ == "__main__":
    main()
