#!/usr/bin/env python3
#
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "jsonschema>=4.26.0",
#     "pyyaml>=6.0.3",
#     "sqlglot>=30.12.0",
# ]
# ///

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
from collections.abc import Hashable
from pathlib import Path

try:
    import yaml
    from jsonschema import Draft202012Validator
    from yaml.constructor import ConstructorError
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install pyyaml jsonschema")
    sys.exit(1)

try:
    import sqlglot
    from sqlglot.errors import ParseError, TokenError
    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False

# Map Ossie dialects to sqlglot dialects
DIALECT_MAP = {
    "ANSI_SQL": None,  # sqlglot default
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
    "BIGQUERY": "bigquery",
    "MDX": None,  # Not supported by sqlglot, skip validation
    "TABLEAU": None,  # Not supported by sqlglot, skip validation
    "MAQL": None,  # Not supported by sqlglot, skip validation
    "THOUGHTSPOT": None,  # Not supported by sqlglot, skip validation
}

# Dialects that sqlglot cannot parse
SKIP_SQL_VALIDATION = {"MDX", "TABLEAU", "MAQL", "THOUGHTSPOT"}


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate explicit mapping keys."""

    MERGE_TAG = "tag:yaml.org,2002:merge"

    def construct_document(self, node: yaml.Node):
        # Validate the composed node graph before SafeConstructor touches it:
        # flatten_mapping() rewrites mapping nodes in place while expanding "<<",
        # so a check that runs during construction sees merged, not authored, keys.
        self._check_unique_keys(node, set())
        return super().construct_document(node)

    def _check_unique_keys(self, node: yaml.Node, visited: set) -> None:
        if id(node) in visited:  # alias or recursive anchor: check the node once
            return
        visited.add(id(node))

        if isinstance(node, yaml.MappingNode):
            seen = set()
            merge_key = object()
            for key_node, value_node in node.value:
                if key_node.tag == self.MERGE_TAG:
                    key, display_key = merge_key, "<<"
                elif isinstance(key_node, yaml.ScalarNode):
                    key = display_key = self.construct_object(key_node, deep=True)
                else:
                    # Collection keys are unhashable under SafeLoader. Reject them
                    # here rather than constructing them, which would run
                    # flatten_mapping() on the graph this walk must not disturb.
                    raise ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        "found an unhashable key",
                        key_node.start_mark,
                    )

                if not isinstance(key, Hashable):
                    raise ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        "found an unhashable key",
                        key_node.start_mark,
                    )

                if key in seen:
                    raise ConstructorError(
                        "while constructing a mapping",
                        node.start_mark,
                        f"found duplicate key {display_key!r}",
                        key_node.start_mark,
                    )
                seen.add(key)

                self._check_unique_keys(key_node, visited)
                self._check_unique_keys(value_node, visited)
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                self._check_unique_keys(child, visited)


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
    """Validate that relationships reference existing datasets and that
    to_columns covers a declared key of the 'to' dataset."""
    errors = []

    for model in data.get("semantic_model", []):
        model_name = model.get("name", "<unnamed>")
        datasets = {d.get("name"): d for d in model.get("datasets", []) if d.get("name")}

        for rel in model.get("relationships", []):
            rel_name = rel.get("name", "<unnamed>")
            from_ds = rel.get("from")
            to_ds = rel.get("to")

            if from_ds and from_ds not in datasets:
                errors.append(f"[Reference] Relationship '{rel_name}' in model '{model_name}' references unknown dataset '{from_ds}'")
            if to_ds and to_ds not in datasets:
                errors.append(f"[Reference] Relationship '{rel_name}' in model '{model_name}' references unknown dataset '{to_ds}'")

            # The spec defines to_columns as "Primary/unique key columns in the
            # 'to' dataset". Coverage (superset of a key) still guarantees the
            # many-to-one join, and declared keys may be incomplete since
            # primary_key and unique_keys are optional — so accept any
            # to_columns that covers a declared key, report a warning rather
            # than an error, and skip datasets that declare no keys.
            # Shape guards keep semantic checks from crashing on documents
            # that already fail schema validation.
            dataset = datasets.get(to_ds)
            to_columns = rel.get("to_columns")
            if dataset and isinstance(to_columns, list) and to_columns:
                candidate_keys = [dataset.get("primary_key")] + list(dataset.get("unique_keys") or [])
                declared_keys = [k for k in candidate_keys if isinstance(k, list) and k]
                to_column_set = set(to_columns)
                if declared_keys and not any(set(key) <= to_column_set for key in declared_keys):
                    errors.append(f"[Reference] Warning: Relationship '{rel_name}' in model '{model_name}': to_columns {to_columns} does not cover the primary key or a unique key of dataset '{to_ds}'")

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
    except (ParseError, TokenError):
        pass

    try:
        # Try wrapping in SELECT for simple column references
        sqlglot.parse_one(f"SELECT {expr}", dialect=sqlglot_dialect)
        return None
    except (ParseError, TokenError) as e:
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
                        context = f"Field '{dataset_name}.{field_name}' in model '{model_name}' ({dialect})"
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
                    context = f"Metric '{metric_name}' in model '{model_name}' ({dialect})"
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
            data = yaml.load(f, Loader=UniqueKeyLoader)
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
