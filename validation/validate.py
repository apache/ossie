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
import sys
import traceback
from collections import Counter
from collections.abc import Hashable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
    """Validate that relationships reference existing datasets and that
    to_columns covers a declared key of the 'to' dataset."""
    if not isinstance(data, dict) or "datasets" not in data:
        return []

    model = data
    errors = []

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
                errors.append(ValidationWarning(
                    f"[Reference] Warning: Relationship '{rel_name}' in model '{model_name}': to_columns {to_columns} does not cover the primary key or a unique key of dataset '{to_ds}'"
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
    expression = member.get("expression") or {}
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
    for dialect_expr in expression.get("dialects", []):
        text = dialect_expr.get("expression")
        if text is None:
            continue
        dialect = dialect_expr["dialect"]
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
        field_names = frozenset(
            f["name"].casefold() for f in dataset.get("fields", []) if f.get("name")
        )
        for field in dataset.get("fields", []):
            yield from _expressions_in(field, "field", dataset_name, field_names)
        for metric in dataset.get("metrics", []):
            yield from _expressions_in(metric, "dataset_metric", dataset_name, field_names)
    for metric in data.get("metrics", []):
        yield from _expressions_in(metric, "model_metric", "", frozenset())


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
    # A dialect outside both sets is one this validator cannot parse. It may be a
    # legitimate entry from extensions/dialects.md, so warn once that its
    # expressions went unchecked rather than treat it as an error.
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


def validate_document(data, schema: dict) -> list[str]:
    """Run every check on a parsed document and return the diagnostics."""
    errors = []
    errors.extend(validate_schema(data, schema))

    # Semantic checks rely on valid structure; let schema validation report
    # malformed inputs (including legacy arrays) without traversing them.
    if not errors and isinstance(data, dict) and "datasets" in data:
        errors.extend(validate_unique_names(data))
        errors.extend(validate_references(data))
        errors.extend(validate_relationship_column_arity(data))
        errors.extend(validate_metric_scoping(data))
        errors.extend(validate_sql(data))
    if not errors and isinstance(data, dict) and "ontology" in data:
        errors.extend(validate_ontology(data))

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
        errors = validate_document(data, schema)
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
