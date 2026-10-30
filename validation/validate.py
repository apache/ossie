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

Validates Ossie YAML or JSON documents containing one semantic model against:
1. JSON Schema (structure, types, enums)
2. Unique names (datasets, fields, metrics, relationships)
3. Valid relationship references
4. Relationship column arity (from_columns and to_columns lengths match)
5. SQL syntax (using sqlglot)

Ontology documents (--schema ontology/ontology.json) are checked for unique
concept and relationship names, and for extends, identify_by, roles and QName
prefixes that resolve to something declared.

Mapping documents (--schema ontology/mapping.json) are cross-checked against the
ontology and semantic model each iri points at: the reference names the document
it resolves to, every concept the mapping names is declared there or built in,
every relationship a referent or link mapping names is declared on its concept
or inherited through extends, with a role for nested referents to resolve on and
at the link level its arity calls for, and every dataset.field an expression
names is a declared field of the model, identifiers compared as
expression_language.md compares them. A referenced document is checked against
its own schema first.

Usage:
    python validation/validate.py [--schema <schema_file>] [--] <file> [<file> ...]
    python validation/validate.py <file> --schema ontology/ontology.json
    python validation/validate.py examples/tpcds_semantic_model.yaml

Every file is validated and reported, including one whose checks fail
unexpectedly; the exit status is non-zero if any of them failed. Put -- before
a file name that starts with a dash. The schema defaults to core-spec/ossie-schema.json in this
repository's layout. When the script is vendored elsewhere, install the Python
SDK (pip install /path/to/ossie/python) for its shared YAML loader and point
OSSIE_SCHEMA or --schema at a copy of the schema.
"""

import difflib
import importlib.util
import json
import os
import stat
import sys
import traceback
from collections import Counter
from collections.abc import Hashable, Iterator
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal, NamedTuple
from urllib.parse import unquote, urlsplit

try:
    import yaml
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import SchemaError
    from referencing import Registry
    from referencing.exceptions import NoSuchResource, Unresolvable
    from referencing.retrieval import to_cached_resource
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install pyyaml jsonschema")
    sys.exit(1)

# Use the checkout's loader without importing the SDK's model dependencies.
# A vendored copy of this script uses the installed SDK instead.
_LOADER_PATH = Path(__file__).resolve().parents[1] / "python/src/ossie/_loading.py"
if _LOADER_PATH.is_file():
    _loader_spec = importlib.util.spec_from_file_location("ossie_loading", _LOADER_PATH)
    _loader_module = importlib.util.module_from_spec(_loader_spec)
    _loader_spec.loader.exec_module(_loader_module)
    UniqueKeyLoader = _loader_module.UniqueKeyLoader
else:
    from ossie._loading import UniqueKeyLoader

try:
    import sqlglot
    from sqlglot import exp
    from sqlglot.dialects.dialect import Dialect
    from sqlglot.errors import ParseError, TokenError
    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False

# The dialect the portable `sql` form is written in; the only one this
# specification defines.
PORTABLE_DIALECT = "OSSIE_SQL_2026"

# Ossie dialects sqlglot can parse, each mapped to the sqlglot dialect to parse it
# as. None is sqlglot's default (ANSI) dialect.
SQLGLOT_DIALECTS = {
    "ANSI_SQL": None,
    PORTABLE_DIALECT: None,
    "SNOWFLAKE": "snowflake",
    "DATABRICKS": "databricks",
    "BIGQUERY": "bigquery",
    "TRINO": "trino",
}

# Known non-SQL dialects: sqlglot cannot parse them, so their expressions are
# carried but not validated.
NON_SQL_DIALECTS = {
    "MDX",
    "TABLEAU",
    "MAQL",
    "SIGMA",
    "THOUGHTSPOT",
    "DAX",
    "HOLISTICS_AQL",
}

# Every dialect this validator knows, for close-match suggestions on a typo.
KNOWN_DIALECTS = (*SQLGLOT_DIALECTS, *NON_SQL_DIALECTS)

# Concepts every ontology includes implicitly (ontology.md, "Built-in concepts")
BUILT_IN_CONCEPTS = {"Any", "Boolean", "Date", "DateTime", "Decimal", "Float", "Integer", "String"}


class ValidationWarning(str):
    """An explicitly nonfatal diagnostic, compatible with existing string callers."""


def load_yaml_named(text: str, name: str):
    """Parse text with UniqueKeyLoader, naming the source in any parser mark.

    yaml.load() names a str source "<unicode string>", which drops the path from
    malformed-YAML and duplicate-key diagnostics. Naming the loader restores it
    and keeps the offending line, which PyYAML only records for a str source:
    Reader.get_mark() passes its buffer to the Mark only when the input was not
    a stream, so a named StringIO would name the file but lose the snippet.
    """
    try:
        loader = UniqueKeyLoader(text)
    except yaml.reader.ReaderError as e:
        # Reader.__init__ rejects an unprintable character (a NUL decodes as
        # valid UTF-8 and gets this far) before the name below can be set.
        e.name = name
        raise
    loader.name = name
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


# Ossie schemas reference one another by raw GitHub URL or canonical $id.
# Resolve those URLs onto files in this checkout only when a reference needs
# them. The decorator parses and caches each retrieved schema across calls.
_REPO_ROOT = Path(__file__).parent.parent.resolve()
_SCHEMA_BASES = (
    "https://raw.githubusercontent.com/apache/ossie/main/",
    "https://github.com/apache/ossie/",
)


@to_cached_resource()
def _retrieve_local_schema(uri: str) -> str:
    """Read a referenced Ossie schema from this checkout."""
    for base in _SCHEMA_BASES:
        if uri.startswith(base):
            path = (_REPO_ROOT / uri[len(base):]).resolve()
            if path.is_relative_to(_REPO_ROOT):
                return path.read_text(encoding="utf-8")
            break
    raise NoSuchResource(ref=uri)


_SCHEMA_REGISTRY = Registry(retrieve=_retrieve_local_schema)


def validate_schema(data: dict, schema: dict) -> list[str]:
    """Validate against JSON Schema, resolving Ossie schema references locally."""
    validator = Draft202012Validator(schema, registry=_SCHEMA_REGISTRY)
    errors = []
    try:
        for error in validator.iter_errors(data):
            path = " -> ".join(str(p) for p in error.absolute_path) if error.absolute_path else "(root)"
            errors.append(f"[Schema] {path}: {error.message}")
    except Unresolvable as error:
        errors.append(f"[Schema] Cannot resolve schema reference: {error.ref}")
    return errors


def find_duplicates(items: list[str]) -> list[str]:
    """Return the items that appear more than once, each reported once.

    A name repeated three times is one problem, not two: appending per extra
    occurrence would emit the same message twice and inflate the error count.
    Order follows first appearance, so a document's diagnostics are stable.
    """
    return [item for item, count in Counter(items).items() if count > 1]


def dataset_field_names(dataset: dict) -> frozenset[str]:
    """Case-folded field names declared on a dataset.

    Name Resolution (spec.md) treats primary_key, unique_keys, and relationship
    from_columns/to_columns as field names of that dataset. Comparison is
    case-insensitive, matching dataset-scoped metric scoping.
    """
    names: list[str] = []
    for field in dataset.get("fields") or []:
        if not isinstance(field, dict):
            continue
        name = field.get("name")
        if isinstance(name, str) and name:
            names.append(name.casefold())
    return frozenset(names)


def undeclared_field_names(used: object, declared: frozenset[str]) -> list[str]:
    """Names in used that are not declared fields, original spelling, first seen."""
    if not isinstance(used, list):
        return []
    seen: set[str] = set()
    missing: list[str] = []
    for name in used:
        # "" is not a declared field. Schema items for these arrays have no
        # minLength, so skipping empty would print Validation PASSED.
        if not isinstance(name, str):
            continue
        folded = name.casefold()
        if folded not in declared and folded not in seen:
            seen.add(folded)
            missing.append(name)
    return missing


def _undeclared_fields_error(
    prefix: str, where: str, names: list[str], dataset_label: str
) -> str:
    listed = ", ".join(repr(n) for n in sorted(names))
    noun = "field" if len(names) == 1 else "fields"
    return (
        f"[Reference] {prefix}: {where} references undeclared {noun} {listed}. "
        f"{where} must be field names of {dataset_label}."
    )


def validate_unique_names(data: dict) -> list[str]:
    """Validate unique names for datasets, fields, metrics, relationships."""
    if not isinstance(data, dict) or "datasets" not in data:
        return []

    model = data
    errors = []

    model_name = model.get("name", "<unnamed>")

    # Check unique dataset names
    dataset_names = [d.get("name") for d in model.get("datasets", []) if d.get("name")]
    for dup in find_duplicates(dataset_names):
        errors.append(f"[Unique] Duplicate dataset name '{dup}' in model '{model_name}'")

    # Check unique field names within each dataset, and that dataset-scoped
    # metric names do not duplicate one another or collide with a field name of
    # the same dataset: a dataset's fields and dataset-scoped metrics share one
    # namespace.
    for dataset in model.get("datasets", []):
        dataset_name = dataset.get("name", "<unnamed>")
        field_names = [f.get("name") for f in dataset.get("fields", []) if f.get("name")]
        for dup in find_duplicates(field_names):
            errors.append(f"[Unique] Duplicate field name '{dup}' in dataset '{dataset_name}'")

        ds_metric_names = [m.get("name") for m in dataset.get("metrics", []) if m.get("name")]
        for dup in find_duplicates(ds_metric_names):
            errors.append(
                f"[Unique] Duplicate metric name '{dup}' in dataset '{dataset_name}'"
            )
        for name in sorted(set(field_names) & set(ds_metric_names)):
            errors.append(
                f"[Unique] Name '{name}' in dataset '{dataset_name}' is used by both a field "
                f"and a dataset-scoped metric; a dataset's fields and metrics share one namespace"
            )

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
    """Validate relationship and key names against declared fields.

    Unknown from/to datasets are errors. from_columns, to_columns, primary_key
    and unique_keys must name fields of those datasets (spec.md Name
    Resolution); missing names are errors. JSON Schema cannot express that.
    to_columns that does not cover a declared key of the to dataset is a
    warning. Coverage uses only keys whose names are themselves declared
    fields, compared case-insensitively.
    """
    if not isinstance(data, dict) or "datasets" not in data:
        return []

    model = data
    errors = []

    model_name = model.get("name", "<unnamed>")
    datasets = {d.get("name"): d for d in model.get("datasets", []) if d.get("name")}

    for dataset in model.get("datasets", []):
        dataset_name = dataset.get("name", "<unnamed>")
        declared = dataset_field_names(dataset)
        prefix = f"Dataset '{dataset_name}' in model '{model_name}'"
        missing_pk = undeclared_field_names(dataset.get("primary_key"), declared)
        if missing_pk:
            errors.append(
                _undeclared_fields_error(
                    prefix, "primary_key", missing_pk, f"dataset '{dataset_name}'"
                )
            )
        unique_keys = dataset.get("unique_keys")
        used_uk: list[str] = []
        if isinstance(unique_keys, list):
            for key in unique_keys:
                if isinstance(key, list):
                    used_uk.extend(name for name in key if isinstance(name, str))
        missing_uk = undeclared_field_names(used_uk, declared)
        if missing_uk:
            errors.append(
                _undeclared_fields_error(
                    prefix, "unique_keys", missing_uk, f"dataset '{dataset_name}'"
                )
            )

    for rel in model.get("relationships", []):
        rel_name = rel.get("name", "<unnamed>")
        from_ds = rel.get("from")
        to_ds = rel.get("to")
        prefix = f"Relationship '{rel_name}' in model '{model_name}'"

        if from_ds and from_ds not in datasets:
            errors.append(
                f"[Reference] {prefix} references unknown dataset '{from_ds}'"
            )
        if to_ds and to_ds not in datasets:
            errors.append(
                f"[Reference] {prefix} references unknown dataset '{to_ds}'"
            )

        from_dataset = datasets.get(from_ds)
        from_columns = rel.get("from_columns")
        if from_dataset is not None and isinstance(from_columns, list):
            missing_from = undeclared_field_names(
                from_columns, dataset_field_names(from_dataset)
            )
            if missing_from:
                errors.append(
                    _undeclared_fields_error(
                        prefix,
                        "from_columns",
                        missing_from,
                        f"the from dataset '{from_ds}'",
                    )
                )

        to_dataset = datasets.get(to_ds)
        to_columns = rel.get("to_columns")
        missing_to: list[str] = []
        if to_dataset is not None and isinstance(to_columns, list):
            missing_to = undeclared_field_names(
                to_columns, dataset_field_names(to_dataset)
            )
            if missing_to:
                errors.append(
                    _undeclared_fields_error(
                        prefix,
                        "to_columns",
                        missing_to,
                        f"the to dataset '{to_ds}'",
                    )
                )

        # Coverage (superset of a key) still guarantees the many-to-one join.
        # primary_key and unique_keys are optional, so skip datasets that
        # declare no usable keys. Keys whose names are not fields are not
        # usable; to_columns that already failed the field check is not
        # coverage-checked. Shape guards keep semantic checks from crashing
        # on documents that already fail schema validation.
        if (
            to_dataset is not None
            and isinstance(to_columns, list)
            and to_columns
            and not missing_to
        ):
            declared = dataset_field_names(to_dataset)
            candidate_keys = [to_dataset.get("primary_key")] + list(
                to_dataset.get("unique_keys") or []
            )
            declared_keys = []
            for key in candidate_keys:
                if not isinstance(key, list) or not key:
                    continue
                folded = [
                    name.casefold()
                    for name in key
                    if isinstance(name, str) and name
                ]
                if len(folded) == len(key) and all(name in declared for name in folded):
                    declared_keys.append(set(folded))
            to_column_set = {
                name.casefold()
                for name in to_columns
                if isinstance(name, str) and name
            }
            if declared_keys and not any(key <= to_column_set for key in declared_keys):
                errors.append(ValidationWarning(
                    f"[Reference] Warning: {prefix}: to_columns {to_columns} does not cover the primary key or a unique key of dataset '{to_ds}'"
                ))

    return errors


def validate_relationship_column_arity(data: dict) -> list[str]:
    """Validate that from_columns and to_columns have the same length.

    The spec requires the two arrays to correspond positionally, so their
    lengths must match. JSON Schema cannot express this, so it is checked here.
    """
    if not isinstance(data, dict) or "datasets" not in data:
        return []

    model = data
    errors = []

    model_name = model.get("name", "<unnamed>")

    for rel in model.get("relationships", []):
        rel_name = rel.get("name", "<unnamed>")
        from_columns = rel.get("from_columns")
        to_columns = rel.get("to_columns")

        # Skip anything that already failed schema validation.
        if not isinstance(from_columns, list) or not isinstance(to_columns, list):
            continue

        if len(from_columns) != len(to_columns):
            errors.append(
                f"[Arity] Relationship '{rel_name}' in model '{model_name}': "
                f"from_columns ({len(from_columns)}) and "
                f"to_columns ({len(to_columns)}) must have the same number of columns"
            )

    return errors


def declared_fields(model: dict) -> dict[str, set[str]]:
    """The case-folded names of the fields each dataset declares, as
    dataset_field_names reads them, by case-folded dataset name.

    A dataset without a fields list declares none: model-level constructs do
    not reach through a dataset to its source (spec.md, Name Resolution).
    """
    fields: dict[str, set[str]] = {}
    for d in as_list(model.get("datasets")):
        if not isinstance(d, dict) or not isinstance(d.get("name"), str):
            continue
        # Datasets whose names differ only by case pool their fields rather
        # than hide each other's.
        fields.setdefault(d["name"].casefold(), set()).update(dataset_field_names(d))
    return fields


# Date functions some parser leaves untyped; their first argument is the unit.
# DATE_ADD, DATEDIFF, DATE_DIFF and TIMESTAMPDIFF always parse to typed nodes.
DATE_PART_FUNCTIONS = {"DATEADD", "DATE_PART", "DATEPART", "TIMESTAMPADD"}

# Typed date functions whose unit the default parser does not know: it keeps
# the written unit in `this` and puts the last argument in `unit`.
UNIT_SLOT_FUNCTIONS = (
    (exp.DateAdd, exp.DateDiff, exp.TimestampDiff) if SQLGLOT_AVAILABLE else ()
)


def is_date_part_name(name: str) -> bool:
    """Whether a name is a date part or one of its aliases (day, dd, days, ...)."""
    parts = Dialect.DATE_PART_MAPPING
    return name.upper() in parts or name.upper() in parts.values()


def is_date_part(column) -> bool:
    """Whether a bare name is the unit argument of a date function.

    Only the unit position counts, since the alias table also holds short
    names such as q, w and ms that are ordinary column names elsewhere. A
    parser that knows the function reads the unit into ``unit`` and the date
    arguments into ``this`` and ``expression``; the default parser does not
    know the unit of DATEDIFF or DATE_ADD, so it leaves the written unit as the
    column in ``this`` and reads the last argument as ``unit`` instead. The two
    are told apart by whether ``unit`` names a date part; when the last
    argument is itself named like one, the two readings cannot be told apart
    and the column in ``this`` is reported. Functions the parser leaves
    anonymous carry the unit as their first argument. DATE_TRUNC and its kin
    never parse the unit as a column.
    """
    parent = column.parent
    if column.table or not is_date_part_name(column.name):
        return False
    if isinstance(parent, UNIT_SLOT_FUNCTIONS):
        unit = parent.args.get("unit")
        unit_consumed = isinstance(unit, exp.Var) and is_date_part_name(unit.name)
        return column.arg_key == "this" and unit is not None and not unit_consumed
    if isinstance(parent, exp.Anonymous) and parent.name.upper() in DATE_PART_FUNCTIONS:
        return bool(parent.expressions) and parent.expressions[0] is column
    return False


def iter_columns(tree) -> Iterator["exp.Column"]:
    """The column references of a parsed expression, read the same way by both
    reference checks: `*` references no column, the unit of a date function
    is not one, and a unit slot the parser filled with a column is read as that
    column. The default parser does not know DATEDIFF's unit, so in
    `DATEDIFF(day, opened_at, closed_at)` it leaves `day` in `this` as a column
    and reads `closed_at` into `unit` as a Var. When `this` is a bare column
    named like a date part and `unit` is a Var that is not one, the Var is the
    column, in the upper-cased spelling the parser keeps; a literal there, or a
    unit the alias table lacks under a parser that knows the function, is left
    alone.
    """
    for column in tree.find_all(exp.Column):
        if isinstance(column.this, exp.Star) or is_date_part(column):
            continue
        yield column
    for node in tree.find_all(*UNIT_SLOT_FUNCTIONS):
        first, unit = node.this, node.args.get("unit")
        if (
            isinstance(first, exp.Column)
            and not first.table
            and is_date_part_name(first.name)
            and isinstance(unit, exp.Var)
            and not is_date_part_name(unit.name)
            and unit.name.isidentifier()
        ):
            yield exp.column(unit.name)


def validate_metric_references(data: dict) -> list[str]:
    """Warn when a metric expression refers to anything but a declared field
    written as dataset.field (spec.md, Name Resolution): an unqualified name,
    another metric, a dataset the model does not declare or a field the dataset
    does not. Dialects sqlglot cannot parse are skipped and an expression that
    does not parse is left to the SQL check."""
    if not SQLGLOT_AVAILABLE or not isinstance(data, dict) or "datasets" not in data:
        return []
    model_name = data.get("name", "<unnamed>")
    fields = declared_fields(data)
    field_names = set().union(*fields.values()) if fields else set()
    metrics = [m for m in as_list(data.get("metrics")) if isinstance(m, dict)]
    declared_metrics = {m["name"].casefold(): m["name"] for m in metrics if isinstance(m.get("name"), str)}
    warnings = []

    for metric in metrics:
        metric_name = metric.get("name", "<unnamed>")
        own_name = metric_name.casefold() if isinstance(metric.get("name"), str) else None
        # A bare name that is another metric, and not also a field or this
        # metric itself, was most likely meant as that metric.
        other_metrics = {
            folded: name for folded, name in declared_metrics.items()
            if folded not in field_names and folded != own_name
        }
        for expression in read_expressions(metric, "model_metric"):
            context = expression.context(model_name)
            tree = parse_tree(expression.text, expression.dialect, context)
            if tree is None:
                continue
            seen = set()
            for column in iter_columns(tree):
                key = tuple(part.name.casefold() for part in column.parts)
                if key in seen:
                    continue
                seen.add(key)
                name, table = column.name.casefold(), column.table.casefold()
                if column.db:
                    written = ".".join(part.name for part in column.parts)
                    message = (
                        f"'{written}' has more than two parts; metric expressions refer to "
                        f"fields as dataset.field"
                    )
                elif not column.table and name in other_metrics:
                    message = (
                        f"'{column.name}' names the metric '{other_metrics[name]}'; "
                        f"metric expressions cannot refer to other metrics"
                    )
                elif not column.table:
                    message = (
                        f"'{column.name}' is not qualified; metric expressions refer to fields "
                        f"as dataset.field"
                    )
                elif table not in fields:
                    message = (
                        f"'{column.table}.{column.name}' references unknown dataset "
                        f"'{column.table}'"
                    )
                elif name not in fields[table]:
                    message = (
                        f"'{column.table}.{column.name}' is not a declared field of dataset "
                        f"'{column.table}'"
                    )
                else:
                    continue
                warnings.append(ValidationWarning(f"[Reference] Warning: {context}: {message}"))
    return warnings


def validate_field_shadowing(data: dict) -> list[str]:
    """Warn when a field expression names another field of the same dataset.

    A bare name in a field expression is a column of the dataset's source
    (spec.md, Name Resolution), so when another field carries that name the
    author may have meant the field. Nothing is reported when that field is
    the same-named column passed through, since both readings then agree, when
    it has no SQL definition the validator can read, or when a field names its
    own column.
    """
    if not SQLGLOT_AVAILABLE or not isinstance(data, dict) or "datasets" not in data:
        return []
    model_name = data.get("name", "<unnamed>")
    warnings = []

    def passes_through(tree, name: str) -> bool:
        while isinstance(tree, (exp.Paren, exp.Alias)):
            tree = tree.this
        return isinstance(tree, exp.Column) and not tree.table and tree.name.casefold() == name.casefold()

    for dataset in as_list(data.get("datasets")):
        if not isinstance(dataset, dict):
            continue
        dataset_name = dataset.get("name", "<unnamed>")
        declared = [
            f for f in as_list(dataset.get("fields"))
            if isinstance(f, dict) and isinstance(f.get("name"), str)
        ]
        # Of two fields whose names differ only by case, the first declared is
        # the one other expressions are compared against.
        by_name: dict[str, dict] = {}
        for f in declared:
            by_name.setdefault(f["name"].casefold(), f)
        for field in declared:
            for expression in read_expressions(field, "field", dataset_name):
                context = expression.context(model_name)
                tree = parse_tree(expression.text, expression.dialect, context)
                if tree is None:
                    continue
                names = [c.name for c in iter_columns(tree) if not c.table]
                for name in dict.fromkeys(names):
                    other = by_name.get(name.casefold())
                    if other is None or other["name"].casefold() == field["name"].casefold():
                        continue
                    definitions = (
                        read_expressions(other, "field", dataset_name, expression.dialect)
                        or read_expressions(other, "field", dataset_name)
                    )
                    # A definition that does not parse gives nothing to compare.
                    parsed = [
                        (d.text, tree)
                        for d in definitions
                        if (tree := parse_tree(d.text, d.dialect, d.context(model_name)))
                        is not None
                    ]
                    if not parsed or any(passes_through(t, name) for _, t in parsed):
                        continue
                    warnings.append(ValidationWarning(
                        f"[Reference] Warning: {context}: '{name}' is the source column, not the "
                        f"field '{other['name']}' defined as '{parsed[0][0]}'"
                    ))
    return warnings


def _parse_sql_expression(
    expr: str, dialect: str, context: str
) -> tuple[object | None, str | None]:
    """Parse a SQL expression once and return its tree and any error.

    The expression is tried bare first (so a field expression like `column_name`
    parses), then wrapped in `SELECT {expr}` for bare column references. The parsed
    sqlglot tree is returned together with a validation error message; each may be
    None. A valid expression yields `(tree, None)`; a syntax, empty, or
    multi-statement error yields `(None, error)`.

    Callers that only surface errors read the error (see `validate_sql_expression`);
    callers that inspect the parsed columns read the tree (see
    `validate_metric_scoping`). Both go through this single parse, so the statement
    handling below and any later parser fix reach both without a second copy.

    Returns `(None, None)` when there is nothing to parse: sqlglot is unavailable or
    the dialect is one sqlglot cannot parse.
    """
    if not SQLGLOT_AVAILABLE or dialect not in SQLGLOT_DIALECTS:
        return None, None

    sqlglot_dialect = SQLGLOT_DIALECTS[dialect]

    def statement_count_error(sql: str) -> str | None:
        """Reject a parseable string that holds more than one statement.

        `parse_one` accepts a statement list and returns one `Block` spanning it,
        so `amount; DROP TABLE t` parses and would otherwise validate as an
        expression. A semicolon inside a string literal or comment does not
        split, so this only rejects a real statement list. Stray semicolons parse
        as empty statements and are not counted, so `amount;` and `amount;;` are
        both a single expression. Parse failures return None; the checks below
        report those.
        """
        try:
            statements = sqlglot.parse(sql, dialect=sqlglot_dialect)
        except (ParseError, TokenError, RecursionError):
            return None
        count = len([statement for statement in statements if statement is not None])
        if count > 1:
            return (
                f"[SQL] {context}: expected a single expression but found "
                f"{count} statements"
            )
        return None

    def holds_no_statement(sql: str) -> bool:
        """True when there is nothing to validate: an empty string, whitespace,
        a lone comment or only semicolons.

        Those parse to a list with no statement in it. `parse_one` raises on
        them, and the `SELECT {expr}` retry below would then parse a bare
        `SELECT` and report the expression as valid, so they are caught before
        the retry. Parse failures return False; the retry reports those.
        """
        try:
            statements = sqlglot.parse(sql, dialect=sqlglot_dialect)
        except (ParseError, TokenError, RecursionError):
            return False
        return all(statement is None for statement in statements)

    try:
        # Try parsing as expression first (for field expressions like "column_name")
        tree = sqlglot.parse_one(expr, dialect=sqlglot_dialect)
    except (ParseError, TokenError, RecursionError):
        # A bare column reference fails to parse alone; retry it wrapped in
        # SELECT below. RecursionError (deeply nested input) is included so the
        # retry reports it instead of crashing, while genuine errors such as a
        # non-string expr raising TypeError still surface.
        if holds_no_statement(expr):
            return None, f"[SQL] {context}: expression is empty"
    else:
        return tree, statement_count_error(expr)

    try:
        # Try wrapping in SELECT for simple column references
        tree = sqlglot.parse_one(f"SELECT {expr}", dialect=sqlglot_dialect)
    except (ParseError, TokenError) as e:
        return None, f"[SQL] {context}: {str(e).split(chr(10))[0]}"
    except RecursionError:
        # Deeply nested input exhausts the recursion limit rather than raising a
        # parser error; report it instead of letting it abort validation.
        return None, f"[SQL] {context}: expression is too deeply nested to parse"
    return tree, statement_count_error(f"SELECT {expr}")


def validate_sql_expression(expr: str, dialect: str, context: str) -> str | None:
    """Validate a single SQL expression. Returns error message or None if valid."""
    _tree, error = _parse_sql_expression(expr, dialect, context)
    return error


def parse_tree(expr: str, dialect: str, context: str):
    """The parsed tree of one expression, or None when it does not parse: for the
    checks that read the columns and leave the error to the SQL check."""
    tree, _error = _parse_sql_expression(expr, dialect, context)
    return tree


ExpressionKind = Literal["field", "dataset_metric", "model_metric"]


@dataclass(frozen=True)
class Expression:
    """An expression and its context within a document.

    `kind` identifies the declaring element, and `name` identifies the field
    or metric. `dialect` is OSSIE_SQL_2026 for portable `sql` expressions,
    or the dialect's label otherwise. `field_names` contains the enclosing
    dataset's field names, or is empty for model-scoped metrics.
    `superseded_by` is the authoritative top-level `sql` text when this is an
    OSSIE_SQL_2026 dialect entry that `sql` outranks, else None: such an entry
    is non-authoritative, since a client reads the `sql` instead.
    """

    kind: ExpressionKind
    dataset_name: str
    name: str
    dialect: str
    text: str
    field_names: frozenset[str]
    superseded_by: str | None = None

    def context(self, model_name: str) -> str:
        if self.kind == "field":
            label = f"Field '{self.dataset_name}.{self.name}'"
        elif self.kind == "dataset_metric":
            label = f"Metric '{self.dataset_name}.{self.name}'"
        else:
            label = f"Metric '{self.name}'"
        return f"{label} in model '{model_name}' ({self.dialect})"


def _expressions_in(
    member: dict, kind: ExpressionKind, dataset_name: str, field_names: frozenset[str]
) -> Iterator[Expression]:
    # Shapes the schema rejects are skipped, not read, so a check called directly
    # on an unvetted document reports nothing rather than failing.
    expression = member.get("expression")
    if not isinstance(expression, dict):
        return
    name = member.get("name", "<unnamed>")
    sql = expression.get("sql")
    if sql is not None:
        yield Expression(
            kind=kind,
            dataset_name=dataset_name,
            name=name,
            dialect=PORTABLE_DIALECT,
            text=sql,
            field_names=field_names,
        )
    for dialect_expr in as_list(expression.get("dialects")):
        if not isinstance(dialect_expr, dict):
            continue
        text = dialect_expr.get("expression")
        dialect = dialect_expr.get("dialect")
        if text is None or not isinstance(dialect, str):
            continue
        yield Expression(
            kind=kind,
            dataset_name=dataset_name,
            name=name,
            dialect=dialect,
            text=text,
            field_names=field_names,
            # A top-level `sql` is the authoritative OSSIE_SQL_2026 form, so an
            # OSSIE_SQL_2026 dialect entry alongside it is non-authoritative.
            superseded_by=sql if dialect == PORTABLE_DIALECT and sql is not None else None,
        )


def iter_expressions(data: dict) -> Iterator[Expression]:
    """Yield every expression in a core semantic model document. A missing
    expression is skipped; an empty one is yielded, so a caller decides whether to
    report it."""
    for dataset in data.get("datasets", []):
        dataset_name = dataset.get("name", "<unnamed>")
        field_names = dataset_field_names(dataset)
        for field in dataset.get("fields", []):
            yield from _expressions_in(field, "field", dataset_name, field_names)
        for metric in dataset.get("metrics", []):
            yield from _expressions_in(metric, "dataset_metric", dataset_name, field_names)
    for metric in data.get("metrics", []):
        yield from _expressions_in(metric, "model_metric", "", frozenset())


def read_expressions(
    member: dict, kind: ExpressionKind, dataset_name: str = "", dialect: str | None = None
) -> list[Expression]:
    """The expressions of one field or metric that the reference checks read: the
    top-level `sql`, as OSSIE_SQL_2026, and the dialect entries, less an
    OSSIE_SQL_2026 entry that a top-level `sql` supersedes. Given a dialect, only
    the expressions in it. Whether one parses is parse_tree's call.
    """
    return [
        expression
        for expression in _expressions_in(member, kind, dataset_name, frozenset())
        if isinstance(expression.text, str)
        and expression.text
        and expression.superseded_by is None
        and (dialect is None or expression.dialect == dialect)
    ]


def validate_sql(data: dict) -> list[str]:
    """Validate SQL expressions in fields and metrics."""
    # Only core semantic model documents contain a root datasets property.
    if not isinstance(data, dict) or "datasets" not in data:
        return []

    if not SQLGLOT_AVAILABLE:
        return [ValidationWarning(
            "[SQL] Warning: sqlglot not installed, skipping SQL validation. Install with: pip install sqlglot"
        )]

    model_name = data.get("name", "<unnamed>")
    errors = []
    # A dialect outside both sets is one this validator does not know. The two
    # sets are pinned to extensions/dialects.md, so this is a token the registry
    # does not have either; it is still legitimate (dialect is free-form), so
    # warn once that its expressions went unchecked rather than treat it as an
    # error.
    unsupported_dialects = Counter()

    for expression in iter_expressions(data):
        dialect = expression.dialect
        if dialect in NON_SQL_DIALECTS:
            continue
        if dialect not in SQLGLOT_DIALECTS:
            unsupported_dialects[dialect] += 1
            continue
        error = validate_sql_expression(
            expression.text, dialect, expression.context(model_name)
        )
        if expression.superseded_by is not None:
            # A top-level `sql` is authoritative, so this OSSIE_SQL_2026 dialect
            # entry does not fail the document. Warn if it is dead text -- invalid,
            # or differing from the `sql` a client reads instead -- and stay silent
            # when it merely duplicates the `sql`.
            if error:
                reason = "is not valid SQL"
            elif expression.text != expression.superseded_by:
                reason = "differs from the top-level 'sql'"
            else:
                continue
            errors.append(ValidationWarning(
                f"[SQL] Warning: {expression.context(model_name)}: dialect entry {reason} "
                f"and is ignored; the top-level 'sql' is authoritative"
            ))
        elif error:
            errors.append(error)

    for dialect in sorted(unsupported_dialects):
        count = unsupported_dialects[dialect]
        plural = "s" if count != 1 else ""
        message = (
            f"[SQL] Warning: dialect '{dialect}' is not supported by this validator; "
            f"{count} expression{plural} not syntax-checked"
        )
        # Only a close match is suggested, so a legitimate new dialect gets no
        # spurious "did you mean".
        suggestion = difflib.get_close_matches(dialect, KNOWN_DIALECTS, n=1, cutoff=0.8)
        if suggestion:
            message += f". Did you mean '{suggestion[0]}'?"
        errors.append(ValidationWarning(message))

    return errors


def validate_metric_scoping(data: dict) -> list[str]:
    """Validate the field references in each dataset-scoped metric expression.

    A dataset-scoped metric references the declaring dataset's declared fields by
    single-part (unqualified) name. Two rules are enforced:

    * No qualified reference (`dataset.field`): that is model-scoped and belongs
      in the model-root metrics.
    * Every field a dataset-scoped metric references must be a declared field of
      the dataset; referencing an undeclared name (e.g. a raw source column) is
      not yet allowed. A bare name is compared case-insensitively; `COUNT(*)` and
      literals reference no field and are fine.

    Checking the expression needs sqlglot; dialects it cannot parse are skipped
    rather than reported.
    """
    if not isinstance(data, dict) or "datasets" not in data:
        return []

    model_name = data.get("name", "<unnamed>")
    errors = []

    def scope_errors(expression: Expression) -> list[str]:
        tree, _ = _parse_sql_expression(
            expression.text, expression.dialect, expression.context(model_name)
        )
        if tree is None:
            return []

        qualifiers = set()
        undeclared = set()
        for col in tree.find_all(exp.Column):
            parts = [part.name for part in col.parts]
            if len(parts) > 1 and parts[0]:
                qualifiers.add(parts[0])
            elif parts and parts[0].casefold() not in expression.field_names:
                undeclared.add(parts[0])

        prefix = (
            f"[Scope] Dataset-scoped metric '{expression.dataset_name}.{expression.name}' "
            f"in model '{model_name}' ({expression.dialect})"
        )
        out = []
        if qualifiers:
            names = ", ".join(repr(q) for q in sorted(qualifiers))
            out.append(
                f"{prefix} uses qualified reference(s) {names}. A dataset-scoped metric "
                f"expression must reference the dataset's fields by single-part "
                f"(unqualified) name; a metric that must reference another dataset "
                f"belongs in the model-root metrics."
            )
        if undeclared:
            names = ", ".join(repr(n) for n in sorted(undeclared))
            out.append(
                f"{prefix} references undeclared field(s) {names}. A dataset-scoped "
                f"metric may reference only the dataset's declared fields."
            )
        return out

    for expression in iter_expressions(data):
        # A superseded OSSIE_SQL_2026 dialect entry is non-authoritative; the
        # top-level `sql` it defers to is scope-checked in its place.
        if (
            expression.kind != "dataset_metric"
            or not expression.text
            or expression.superseded_by is not None
        ):
            continue
        errors.extend(scope_errors(expression))

    return errors


def as_list(value: object) -> list:
    """The items of a list, or nothing for shapes that already failed the schema."""
    return value if isinstance(value, list) else []


# URI schemes whose IRIs have no "//" and may have a single colon (mailto:a@b.org,
# tel:+1555..., urn:x). Without this list they look like prefix:local QNames.
URI_SCHEMES = frozenset(
    {"http", "https", "urn", "mailto", "tel", "file", "ftp", "data", "did", "doi", "geo", "ldap", "news", "sms", "tag"}
)


def undeclared_qname_prefix(iri: object, prefixes: dict) -> str | None:
    """Return the prefix of a QName iri that prefixes does not declare."""
    if not isinstance(iri, str) or ":" not in iri:
        return None
    prefix, local = iri.split(":", 1)
    # ontology.md allows a full IRI or a prefix:local QName. A scheme followed
    # by "//" (http://...), a second colon (urn:isbn:...) or a known URI scheme
    # marks a full IRI; anything else is read as a QName.
    if local.startswith("//") or ":" in local or prefix.lower() in URI_SCHEMES:
        return None
    return None if prefix in prefixes else prefix


def find_extends_cycles(supertypes: dict[str, list[str]]) -> list[list[str]]:
    """Find cycles in the extends graph, each reported once as a path."""
    cycles = []
    state = {}  # concept -> "active" while on the current path, "done" after

    def visit(concept: str, path: list[str]) -> None:
        state[concept] = "active"
        path.append(concept)
        for supertype in supertypes.get(concept, []):
            if state.get(supertype) == "active":
                cycles.append(path[path.index(supertype):] + [supertype])
            elif supertype not in state:
                visit(supertype, path)
        path.pop()
        state[concept] = "done"

    for concept in supertypes:
        if concept not in state:
            visit(concept, [])
    return cycles


def validate_ontology(data: dict) -> list[str]:
    """Validate the concepts and relationships of an ontology document.

    The same kind of checks the core side gets from validate_unique_names and
    validate_references: concept names are unique within the ontology,
    relationship names are unique within their concept, and extends,
    identify_by, roles and QName prefixes resolve to something declared.
    ontology_mappings are left to schema validation.
    """
    if not isinstance(data, dict) or not isinstance(data.get("ontology"), list):
        return []

    errors = []

    ontology_name = data.get("name", "<unnamed>")
    components = [c for c in data["ontology"] if isinstance(c, dict)]
    prefixes = data.get("prefixes") if isinstance(data.get("prefixes"), dict) else {}

    concept_names = [c.get("concept") for c in components if c.get("concept")]
    for dup in find_duplicates(concept_names):
        errors.append(f"[Unique] Duplicate concept '{dup}' in ontology '{ontology_name}'")

    declared = set(concept_names) | BUILT_IN_CONCEPTS
    supertypes = {}
    relationships = {}
    for component in components:
        concept = component.get("concept", "<unnamed>")
        rel_names = [
            r.get("name") for r in as_list(component.get("relationships"))
            if isinstance(r, dict) and r.get("name")
        ]
        for dup in find_duplicates(rel_names):
            errors.append(f"[Unique] Duplicate relationship '{dup}' in concept '{concept}'")
        # Merged across duplicate components, so a duplicate concept is reported
        # once rather than again as every reference to the first declaration
        # failing. Reference checks below read each component directly.
        supertypes.setdefault(concept, []).extend(
            s for s in as_list(component.get("extends")) if isinstance(s, str)
        )
        relationships.setdefault(concept, set()).update(rel_names)

    def inherited_relationships(concept: str) -> set[str]:
        # A concept can identify itself by a relationship declared on a supertype,
        # which is how the reference parser resolves identify_by as well.
        names, pending, seen = set(), [concept], set()
        while pending:
            current = pending.pop()
            if current in seen:
                continue
            seen.add(current)
            names |= relationships.get(current, set())
            pending.extend(supertypes.get(current, []))
        return names

    for component in components:
        concept = component.get("concept", "<unnamed>")

        for supertype in as_list(component.get("extends")):
            if isinstance(supertype, str) and supertype not in declared:
                errors.append(f"[Reference] Concept '{concept}' extends unknown concept '{supertype}'")

        for identifier in as_list(component.get("identify_by")):
            if isinstance(identifier, str) and identifier not in inherited_relationships(concept):
                errors.append(
                    f"[Reference] Concept '{concept}' identify_by references unknown relationship '{identifier}'"
                )

        prefix = undeclared_qname_prefix(component.get("iri"), prefixes)
        if prefix:
            errors.append(f"[Reference] Concept '{concept}' iri uses undeclared prefix '{prefix}'")

        for relationship in as_list(component.get("relationships")):
            if not isinstance(relationship, dict):
                continue
            rel_name = f"{concept}.{relationship.get('name', '<unnamed>')}"
            for role in as_list(relationship.get("roles")):
                role_concept = role.get("concept") if isinstance(role, dict) else None
                if role_concept and role_concept not in declared:
                    errors.append(
                        f"[Reference] Relationship '{rel_name}' has a role played by unknown concept '{role_concept}'"
                    )
            prefix = undeclared_qname_prefix(relationship.get("iri"), prefixes)
            if prefix:
                errors.append(f"[Reference] Relationship '{rel_name}' iri uses undeclared prefix '{prefix}'")

    for cycle in find_extends_cycles(supertypes):
        errors.append(f"[Reference] Concept '{cycle[0]}' extends itself: {' -> '.join(cycle)}")

    return errors


class FieldReference(NamedTuple):
    """A dataset.field pair a mapping expression names.

    written holds the two identifiers as the author wrote them, quotes
    included, and normalized holds them the way expression_language.md
    compares identifiers: a regular identifier upper-cased, a quoted one as
    written with its quotes stripped and its escapes undone.
    """

    written: tuple[str, str]
    normalized: tuple[str, str]

    @property
    def text(self) -> str:
        return ".".join(self.written)


def normalized_identifier(identifier) -> str:
    """The normalized form of a parsed identifier (expression_language.md)."""
    return identifier.name if identifier.quoted else identifier.name.upper()


def declared_name(name: str) -> str:
    """The normalized form of a name declared in a document.

    A name the parser reads unquoted as the second part of a reference, such
    as order_id, été or from, is a regular identifier and is upper-cased. Any
    other name can only be written quoted and is kept as written. A keyword
    that cannot open a reference unquoted, such as alter, is still upper-cased,
    so a dataset of that name is referenced as "ALTER". Needs sqlglot.
    """
    try:
        column = sqlglot.parse_one(f"x.{name}", into=sqlglot.exp.Condition)
    except Exception:
        return name
    parts = column.parts if isinstance(column, sqlglot.exp.Column) else []
    regular = (
        len(parts) == 2
        and isinstance(parts[1], sqlglot.exp.Identifier)
        and not parts[1].quoted
        and parts[1].name == name
    )
    return name.upper() if regular else name


def expression_is_empty(expression: str) -> bool:
    """True when an expression holds nothing: empty, whitespace, a comment or
    only semicolons, which the core checks report as empty. Without sqlglot,
    only an empty or whitespace-only expression is.
    """
    if not SQLGLOT_AVAILABLE:
        return not expression.strip()
    try:
        return all(statement is None for statement in sqlglot.parse(expression))
    except Exception:
        return False


def field_references(expression: str) -> list[FieldReference] | None:
    """The dataset.field pairs a mapping expression names, each once.

    Mapping expressions reach the semantic model through two-part identifiers
    (ontology.md, Object mappings), so the expression is parsed and every
    two-part column in it is a reference. A one-part column, a longer chain,
    a type name, a function name and a lambda parameter are not. A column
    inside a subquery whose table part names something bound in that query,
    or in a query around it, is left to that query, while a correlated
    reference to a dataset is a reference. Mapping expressions declare no dialect,
    so the expression is parsed with the sqlglot default, as an expression
    rather than a statement, and one that does not parse comes back as None.
    A dataset named like many keywords, as in use.x, then reads as a column. A
    statement does not parse, and two expressions separated by a semicolon
    come back as None too. Needs sqlglot.
    """
    try:
        tree = sqlglot.parse_one(expression, into=sqlglot.exp.Condition)
    except Exception:
        # ParseError and TokenError for input sqlglot rejects, RecursionError
        # for deep nesting, and the parser raises AttributeError from inside
        # on token soup such as `{:}`. Every one means it did not parse.
        return None
    exp = sqlglot.exp
    if isinstance(tree, exp.Block):
        return None
    visible = _names_visible_in_queries(tree)
    references = []
    for column in tree.find_all(exp.Column):
        parts = column.parts
        if len(parts) != 2 or not all(isinstance(p, exp.Identifier) for p in parts):
            continue
        dataset, field = (normalized_identifier(p) for p in parts)
        query = column.find_ancestor(exp.Select)
        if query is not None and dataset in visible[id(query)]:
            continue
        references.append(FieldReference(
            written=(parts[0].sql(), parts[1].sql()), normalized=(dataset, field)
        ))
    return list(dict.fromkeys(references))


def _names_visible_in_queries(tree) -> dict[int, set[str]]:
    """For each SELECT in the tree, keyed by id, the names bound for a table part.

    A query binds the names of its own FROM, JOIN and WITH clauses: a table
    by its name, or by its alias when it has one, and a derived table, table
    function, VALUES list, PIVOT or CTE by its alias. An inner query also sees
    the names of the queries around it; a name bound in some other query, a
    sibling or a nested one, is not visible. Names are normalized the way the
    references are (expression_language.md).
    """
    exp = sqlglot.exp
    # Each binding node belongs to the nearest query around it. An aliased
    # table is bound by its TableAlias node, so its own name is skipped.
    bound: dict[int, set[str]] = {}
    for node in tree.find_all(exp.Table, exp.TableAlias):
        aliased_table = isinstance(node, exp.Table) and node.args.get("alias")
        if not aliased_table and isinstance(node.this, exp.Identifier):
            query = node.find_ancestor(exp.Select)
            bound.setdefault(id(query), set()).add(normalized_identifier(node.this))
    # find_all walks breadth-first, so a query comes after the queries around it.
    visible: dict[int, set[str]] = {}
    for query in tree.find_all(exp.Select):
        outer = query.find_ancestor(exp.Select)
        visible[id(query)] = bound.get(id(query), set()) | visible.get(id(outer), set())
    return visible


def resolve_document_reference(
    reference: dict, base: Path | None, label: str
) -> tuple[dict | None, list[str]]:
    """Follow a mapping document's DocumentReference and return the document.

    The iri is read as a URI reference. One with a scheme or an authority is
    left to a catalog, and the path of any other is followed, relative to
    base, the mapping document's own directory, as ontology.md specifies. A
    reference with no iri, with a scheme, or with a relative path and no base
    comes back as None with a ValidationWarning. A path that is not a regular
    file, such as a directory, a device or a pipe, is an error and is not
    opened. So is a path that does not read as a YAML document, and a
    document not named as the reference says, but that document is still
    returned for the checks that follow.
    """
    name = reference.get("name")
    iri = reference.get("iri")
    skipped = f"cross-document checks for {label} skipped"
    if not isinstance(iri, str) or not iri:
        return None, [ValidationWarning(
            f"[Reference] Warning: {label} '{name}' has no iri, so only a catalog can "
            f"resolve it: {skipped}"
        )]
    try:
        parts = urlsplit(iri)
    except ValueError as e:
        return None, [f"[Reference] {label} iri '{iri}' is not a valid URI reference: {e}"]
    if parts.scheme or parts.netloc:
        return None, [ValidationWarning(
            f"[Reference] Warning: {label} iri '{iri}' is not a path reference, so it is "
            f"not read: {skipped}"
        )]
    path = Path(unquote(parts.path))
    if not path.is_absolute():
        if base is None:
            return None, [ValidationWarning(
                f"[Reference] Warning: {label} iri '{iri}' is relative but the mapping "
                f"document has no location to resolve it against: {skipped}"
            )]
        path = base / path

    try:
        if not stat.S_ISREG(path.stat().st_mode):
            return None, [f"[Reference] {label} iri '{iri}' does not name a regular file"]
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, [f"[Reference] {label} iri '{iri}' does not resolve: {e.strerror or e}"]
    except UnicodeDecodeError:
        return None, [f"[Reference] {label} iri '{iri}' is not valid UTF-8 text"]
    except UnicodeEncodeError:
        # A lone surrogate, from a \ud800 escape in the iri, cannot name a file.
        return None, [f"[Reference] {label} iri '{iri}' is not a valid file name"]
    except ValueError:
        # A path with an embedded NUL, from %00 in the iri, is rejected before it is opened,
        # in words that vary across Python versions.
        return None, [f"[Reference] {label} iri '{iri}' does not resolve: embedded null byte"]
    try:
        document = load_yaml_named(text, str(path))
    except yaml.YAMLError as e:
        return None, [f"[Reference] {label} iri '{iri}' is not valid YAML: {str(e).split(chr(10))[0]}"]
    except RecursionError:
        return None, [f"[Reference] {label} iri '{iri}' is too deeply nested to parse"]
    except (ValueError, AttributeError, LookupError) as e:
        # SafeLoader raises these, not YAMLError, for a scalar it cannot build:
        # 2024-02-30 or !!int abc, !!timestamp abc, !!bool abc, !!int ''.
        return None, [f"[Reference] {label} iri '{iri}' holds a value YAML cannot build: {e}"]
    if not isinstance(document, dict):
        return None, [f"[Reference] {label} iri '{iri}' does not hold a document"]

    errors = []
    if document.get("name") != name:
        errors.append(
            f"[Reference] {label} names '{name}' but the document at iri '{iri}' is named "
            f"'{document.get('name', '<unnamed>')}'"
        )
    return document, errors


@lru_cache(maxsize=None)
def _schema_at(path: Path) -> dict | None:
    """The schema in a file, or None where the script runs without one."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def schema_checked_document(
    document: dict | None, schema_file: str, label: str, iri: object, half: str
) -> tuple[dict | None, list[str]]:
    """Validate a referenced document against the schema its reference promises.

    The checks that read the document assume the shapes that schema
    guarantees, so a document that fails it is dropped with one error that
    names the reference, the schema and the first schema error. The schema
    is read from this checkout, like the ones validate_schema resolves, and
    a copy of the script without it skips that half with a warning.
    """
    if document is None:
        return None, []
    schema = _schema_at(_REPO_ROOT / schema_file)
    if schema is None:
        return None, [ValidationWarning(
            f"[Reference] Warning: {schema_file} is not next to this script, so the {half} "
            f"of {label} are not checked"
        )]
    schema_errors = validate_schema(document, schema)
    if not schema_errors:
        return document, []
    first = schema_errors[0].removeprefix("[Schema] ")
    return None, [
        f"[Reference] {label} iri '{iri}' does not validate against {schema_file} "
        f"({len(schema_errors)} error(s), the first at {first}), so its {half} are not checked"
    ]


class OntologyRelationships:
    """The relationships an ontology declares, looked up through extends.

    A name resolves on the concept or on a concept it extends, the way
    validate_ontology resolves identify_by.
    """

    def __init__(self, components: list[dict]):
        self._declared: dict[str, dict[str, dict]] = {}
        self._supertypes: dict[str, list[str]] = {}
        for component in components:
            concept = component["concept"]
            self._declared.setdefault(concept, {}).update(
                (r["name"], r) for r in component.get("relationships", [])
            )
            self._supertypes.setdefault(concept, []).extend(component.get("extends", []))

    def ancestors(self, concept: str) -> list[str]:
        """The concept followed by every concept it extends, each once."""
        found, pending = [], [concept]
        while pending:
            current = pending.pop()
            if current in found:
                continue
            found.append(current)
            pending.extend(self._supertypes.get(current, []))
        return found

    def find(self, concept: str, name: str) -> dict | None:
        for current in self.ancestors(concept):
            if name in self._declared.get(current, {}):
                return self._declared[current][name]
        return None


def validate_mapping(data: dict, base: Path | None) -> list[str]:
    """Cross-check a mapping document against the two documents it references.

    ontology_ref and semantic_model_ref must name the documents they point at,
    every concept the mapping names must be declared in that ontology or be a
    built-in concept, every relationship a referent or link mapping names must
    be declared on the concept whose mapping names it, directly or through
    extends, with a role for nested referents to resolve on and at the link
    level its arity calls for, and every dataset.field an expression names
    must be a field of that semantic model, comparing identifiers the way
    expression_language.md does: regular ones without regard to case, quoted
    ones as written, and the model's names the way a reference to them reads
    (declared_name). A dataset that declares no fields list exposes no fields
    (spec.md, Name Resolution). An empty expression is an error, as in the core
    checks, and one that does not parse as a SQL expression is skipped with a
    warning, as is every one when sqlglot is missing.
    Relative iri are read from base, the mapping document's own directory. A
    reference that cannot be followed, or whose document fails its schema,
    skips its half of the checks, see resolve_document_reference and
    schema_checked_document.
    """
    if not isinstance(data, dict) or "concept_mappings" not in data:
        return []

    errors = []
    ontology_ref = data.get("ontology_ref") or {}
    model_ref = data.get("semantic_model_ref") or {}
    ontology, diagnostics = resolve_document_reference(ontology_ref, base, "ontology_ref")
    errors.extend(diagnostics)
    ontology, diagnostics = schema_checked_document(
        ontology, "ontology/ontology.json", "ontology_ref", ontology_ref.get("iri"), "concepts"
    )
    errors.extend(diagnostics)
    model, diagnostics = resolve_document_reference(model_ref, base, "semantic_model_ref")
    errors.extend(diagnostics)
    model, diagnostics = schema_checked_document(
        model, "core-spec/ossie-schema.json", "semantic_model_ref", model_ref.get("iri"), "fields"
    )
    errors.extend(diagnostics)

    # None means that half of the checks is off: the document was not followed
    # or is not the kind of document the reference promises.
    concepts = None
    relationships = None
    if ontology is not None:
        concepts = {c["concept"] for c in ontology["ontology"]} | BUILT_IN_CONCEPTS
        relationships = OntologyRelationships(ontology["ontology"])
    # Keyed by the normalized dataset name: the name as first declared and its
    # normalized field names. Datasets whose names differ only in case are one
    # dataset to a regular identifier, so their fields are pooled.
    datasets = None
    if model is not None and not SQLGLOT_AVAILABLE:
        errors.append(ValidationWarning(
            "[Reference] Warning: sqlglot is not installed, so the fields the mapping "
            "expressions name are not checked. Install with: pip install sqlglot"
        ))
    elif model is not None:
        datasets = {}
        for dataset in model["datasets"]:
            key = declared_name(dataset["name"])
            name, fields = datasets.get(key, (dataset["name"], set()))
            fields = fields | {declared_name(f["name"]) for f in dataset.get("fields", [])}
            datasets[key] = (name, fields)

    def visit(node: object, path: str) -> None:
        if isinstance(node, dict):
            concept = node.get("concept")
            if concepts is not None and isinstance(concept, str) and concept not in concepts:
                errors.append(f"[Reference] {path}: concept '{concept}' is not declared in the ontology")
            expression = node.get("expression")
            if isinstance(expression, str) and expression_is_empty(expression):
                errors.append(f"[Reference] {path}: expression is empty")
            elif datasets is not None and isinstance(expression, str):
                references = field_references(expression)
                if references is None:
                    errors.append(ValidationWarning(
                        f"[Reference] Warning: {path}: expression does not parse as a SQL "
                        f"expression, so the fields it names are not checked"
                    ))
                for reference in references or []:
                    dataset, field = reference.normalized
                    declared = datasets.get(dataset)
                    if declared is None:
                        errors.append(
                            f"[Reference] {path}: expression names '{reference.text}' but "
                            f"the semantic model declares no dataset '{reference.written[0]}'"
                        )
                    elif field not in declared[1]:
                        errors.append(
                            f"[Reference] {path}: expression names '{reference.text}' but "
                            f"dataset '{declared[0]}' declares no field '{reference.written[1]}'"
                        )
            for key, value in node.items():
                visit(value, f"{path} -> {key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{path} -> {index}")

    visit(data["concept_mappings"], "concept_mappings")
    if relationships is not None:
        errors.extend(
            mapping_relationship_errors(data["concept_mappings"], concepts, relationships)
        )
    return errors


def mapping_relationship_errors(
    concept_mappings: object, concepts: set, relationships: OntologyRelationships
) -> list[str]:
    """Report the relationships a mapping names that its ontology does not declare.

    A referent mapping names a relationship of the concept being mapped, and its
    nested referent mappings name relationships of the concept that plays the
    last role of that relationship. A link mapping names a relationship of the
    concept mapping's concept, at the level that matches the relationship's
    arity. Its root maps that concept and a child maps the objects in the last
    position of its relationship's links, and an object mapping that names a
    concept itself maps that concept instead, at the root as well (ontology.md,
    Object mappings and Link mappings). A relationship written as Concept.name
    names the concept being mapped or one it extends, as in the spec examples.
    """
    errors = []

    def role_concept(relationship: dict | None) -> str | None:
        roles = as_list(relationship.get("roles")) if relationship else []
        last = roles[-1] if roles else None
        concept = last.get("concept") if isinstance(last, dict) else None
        return concept if isinstance(concept, str) else None

    def resolve(concept: str | None, name: object, path: str) -> dict | None:
        if concept is None or not isinstance(name, str):
            return None
        written = name
        if "." in name:
            prefix, name = name.split(".", 1)
            if prefix not in concepts:
                errors.append(
                    f"[Reference] {path}: relationship '{written}' names concept '{prefix}', "
                    f"which is not declared in the ontology"
                )
                return None
            if prefix not in relationships.ancestors(concept):
                errors.append(
                    f"[Reference] {path}: relationship '{written}' names concept '{prefix}' but "
                    f"this mapping maps '{concept}'"
                )
                return None
            concept = prefix
        relationship = relationships.find(concept, name)
        if relationship is None:
            errors.append(
                f"[Reference] {path}: relationship '{written}' is not declared on concept "
                f"'{concept}' or a concept it extends"
            )
        return relationship

    def check_referents(items: object, concept: str | None, path: str) -> None:
        for index, referent in enumerate(as_list(items)):
            if not isinstance(referent, dict):
                continue
            here = f"{path} -> {index}"
            relationship = resolve(concept, referent.get("relationship"), here)
            nested = as_list(referent.get("referent_mappings"))
            target = role_concept(relationship)
            if relationship is not None and nested and target is None:
                errors.append(
                    f"[Reference] {here}: relationship '{referent['relationship']}' has no role "
                    f"for its referent_mappings to resolve on"
                )
            check_referents(nested, target, f"{here} -> referent_mappings")

    def check_object_mapping(node: object, concept: str | None, path: str) -> None:
        if not isinstance(node, dict):
            return
        named = node.get("concept")
        if isinstance(named, str):
            # An undeclared concept is reported by the concept check alone.
            if named not in concepts:
                return
            concept = named
        check_referents(node.get("referent_mappings"), concept, f"{path} -> referent_mappings")

    def check_link_mapping(node: object, concept: str, path: str, level: int) -> None:
        if not isinstance(node, dict):
            return
        relationship = resolve(concept, node.get("relationship"), path)
        if relationship is not None:
            arity = len(as_list(relationship.get("roles"))) + 1
            if arity != level:
                errors.append(
                    f"[Reference] {path}: relationship '{node['relationship']}' has arity "
                    f"{arity} but is named at level {level}"
                )
        object_concept = concept if level == 1 else role_concept(relationship)
        check_object_mapping(
            node.get("object_mapping"), object_concept, f"{path} -> object_mapping"
        )
        for index, child in enumerate(as_list(node.get("children"))):
            check_link_mapping(child, concept, f"{path} -> children -> {index}", level + 1)

    for index, concept_mapping in enumerate(as_list(concept_mappings)):
        if not isinstance(concept_mapping, dict):
            continue
        concept = concept_mapping.get("concept")
        # An undeclared concept is reported by the concept check and has nothing to resolve on.
        if not isinstance(concept, str) or concept not in concepts:
            continue
        path = f"concept_mappings -> {index}"
        for j, node in enumerate(as_list(concept_mapping.get("object_mappings"))):
            check_object_mapping(node, concept, f"{path} -> object_mappings -> {j}")
        for j, node in enumerate(as_list(concept_mapping.get("link_mappings"))):
            check_link_mapping(node, concept, f"{path} -> link_mappings -> {j}", level=1)
    return errors


USAGE = "Usage: python validation/validate.py [--schema <schema_file>] [--] <file> [<file> ...]"


def read_text_or_report(path: Path, description: str) -> str | None:
    """Read path as UTF-8 text, or report why it could not be read and return None."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"Error: Could not read {description}: {e}")
        return None
    except UnicodeDecodeError as e:
        print(f"Error: {description} is not valid UTF-8 text: {e}")
        return None


def default_schema_path() -> Path:
    """OSSIE_SCHEMA if set, else the schema in this repository's layout."""
    env = os.environ.get("OSSIE_SCHEMA")
    if env:
        return Path(env)
    return Path(__file__).parent.parent / "core-spec" / "ossie-schema.json"


def load_schema_or_report(schema_path: Path) -> dict | None:
    """Read and metaschema-check a schema file, or report what is wrong and return None."""
    # Read first, then parse. exists() passes for a path that cannot be read as text — a
    # directory, one without read permission, or a binary file — so reading is guarded
    # to report like every other bad input rather than raise.
    schema_text = read_text_or_report(schema_path, f"schema {schema_path}")
    if schema_text is None:
        return None
    try:
        schema = json.loads(schema_text)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in schema {schema_path}: {e}")
        return None

    # json.loads accepts any JSON value, but a schema has to be an object or a
    # boolean. Handing the validator anything else raises AttributeError from inside
    # referencing, which names neither the schema nor the file it came from. Checking
    # against the metaschema also catches an object whose keywords are the wrong
    # shape, such as a string "required", which otherwise validates the model against
    # nonsense without complaint.
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        location = " -> ".join(str(part) for part in e.absolute_path) if e.absolute_path else "(root)"
        print(f"Error: Invalid schema {schema_path}: {location}: {e.message}")
        return None
    return schema


def validate_document(data, schema: dict, base: Path | None = None) -> list[str]:
    """Run every check on a parsed document and return the diagnostics.

    base is the directory the document was read from. A mapping document
    follows its relative references from there, and without it those checks
    are skipped with a warning.
    """
    errors = []
    errors.extend(validate_schema(data, schema))

    def clean() -> bool:
        # A warning is in the same list but must not stop the checks that follow.
        return not any(not isinstance(e, ValidationWarning) for e in errors)

    # Semantic checks rely on valid structure; let schema validation report
    # malformed inputs (including legacy arrays) without traversing them.
    if clean() and isinstance(data, dict) and "datasets" in data:
        errors.extend(validate_unique_names(data))
        errors.extend(validate_references(data))
        errors.extend(validate_relationship_column_arity(data))
        errors.extend(validate_metric_references(data))
        errors.extend(validate_field_shadowing(data))
        errors.extend(validate_metric_scoping(data))
        errors.extend(validate_sql(data))
    if clean() and isinstance(data, dict) and "ontology" in data:
        errors.extend(validate_ontology(data))
    if clean() and isinstance(data, dict) and "concept_mappings" in data:
        errors.extend(validate_mapping(data, base))

    return errors


def validate_file(yaml_path: Path, schema: dict) -> bool:
    """Validate one document, print its report and return True if it passed."""
    if not yaml_path.exists():
        print(f"Error: File not found: {yaml_path}")
        return False

    model_text = read_text_or_report(yaml_path, str(yaml_path))
    if model_text is None:
        return False
    try:
        data = load_yaml_named(model_text, str(yaml_path))
    except yaml.YAMLError as e:
        print(f"Error: Invalid YAML: {e}")
        return False
    except RecursionError:
        # Deeply nested input surfaces as RecursionError, not YAMLError.
        print("Error: Invalid YAML: input is too deeply nested to parse")
        return False

    try:
        errors = validate_document(data, schema, yaml_path.parent)
    except Exception as e:
        # A check that raises on this document is a bug in the validator, not in the
        # model. Report it like any other failure so the remaining files still run,
        # and keep the traceback on stderr for whoever fixes the check.
        print(f"Error: {type(e).__name__} while validating {yaml_path}: {e}")
        traceback.print_exc()
        return False

    # Severity must not depend on user-controlled text in a diagnostic.
    warnings = [e for e in errors if isinstance(e, ValidationWarning)]
    actual_errors = [e for e in errors if not isinstance(e, ValidationWarning)]

    for warning in warnings:
        print(f"  {warning}")

    if actual_errors:
        print(f"\nValidation FAILED with {len(actual_errors)} error(s):\n")
        for error in actual_errors:
            print(f"  {error}")
        return False

    print(f"Validation PASSED: {yaml_path.name}")
    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    paths: list[Path] = []
    schema_arg: str | None = None
    args = sys.argv[1:]
    while args:
        arg = args.pop(0)
        if arg == "--schema":
            if not args or schema_arg is not None:
                print(USAGE)
                sys.exit(1)
            schema_arg = args.pop(0)
        elif arg == "--":
            paths.extend(Path(rest) for rest in args)
            break
        elif arg.startswith("-"):
            print(USAGE)
            sys.exit(1)
        else:
            paths.append(Path(arg))
    if not paths:
        print(USAGE)
        sys.exit(1)

    schema_path = Path(schema_arg) if schema_arg else default_schema_path()
    if not schema_path.exists():
        print(f"Error: Schema not found: {schema_path}")
        if schema_arg is None:
            print("Pass --schema <schema_file> or set OSSIE_SCHEMA to point at ossie-schema.json.")
        sys.exit(1)

    schema = load_schema_or_report(schema_path)
    if schema is None:
        sys.exit(1)

    failed = 0
    for yaml_path in paths:
        if len(paths) > 1:
            print(f"== {yaml_path}")
        if not validate_file(yaml_path, schema):
            failed += 1

    if len(paths) > 1:
        print(f"\n{len(paths) - failed} of {len(paths)} file(s) passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
