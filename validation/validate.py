#!/usr/bin/env python3
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
Ossie Semantic Model Validator

Validates Ossie YAML files against:
1. JSON Schema (structure, types, enums)
2. Unique names (datasets, fields, metrics, relationships)
3. Valid relationship references
4. SQL syntax (using sqlglot)

Usage:
    python validation/validate.py <yaml_file>
    python validation/validate.py <yaml_file> --schema ontology/ontology.json
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

# Map Ossie dialects to sqlglot dialects
DIALECT_MAP = {
    "ANSI_SQL": None,  # sqlglot default
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
    "MDX": None,  # Not supported by sqlglot, skip validation
    "TABLEAU": None,  # Not supported by sqlglot, skip validation
    "MAQL": None,  # Not supported by sqlglot, skip validation
}

# Dialects that sqlglot cannot parse
SKIP_SQL_VALIDATION = {"MDX", "TABLEAU", "MAQL"}


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
    # Only semantic model files contain SQL expressions to validate.
    if not data.get("semantic_model"):
        return []

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

    args = sys.argv[1:]
    yaml_path = Path(args[0])

    schema_path = Path(__file__).parent.parent / "core-spec" / "ossie-schema.json"
    if len(args) > 1:
        if len(args) == 3 and args[1] == "--schema":
            schema_path = Path(args[2])
        else:
            print("Usage: python validation/validate.py <yaml_file> [--schema <schema_file>]")
            sys.exit(1)

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

    # Run semantic-model-specific checks only for semantic model payloads.
    if data.get("semantic_model"):
        errors.extend(validate_unique_names(data))
        errors.extend(validate_references(data))
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
