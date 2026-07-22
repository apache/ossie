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

"""Offline Apache Ossie ↔ standalone NVIDIA GSF YAML conversion."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import yaml

OSSIE_VERSION = "0.2.0.dev0"
GSF_VERSION = "1.0"
NVIDIA_GSF_VENDOR = "NVIDIA_GSF"
GSF_VENDOR_ALIASES = {NVIDIA_GSF_VENDOR, "GSF"}

_SIMPLE_COLUMN = re.compile(
    r"^(?:(?P<qualifier>[A-Za-z_][A-Za-z0-9_]*)\.)?"
    r"(?P<column>[A-Za-z_][A-Za-z0-9_]*)$"
)
_TERM_REFERENCE = re.compile(
    r"\b(?P<term>[A-Za-z_][A-Za-z0-9_]*)\."
    r"(?P<column>[A-Za-z_][A-Za-z0-9_]*)\b"
)


class GSFConversionError(Exception):
    """Raised when a document cannot be converted safely."""


def convert_ossie_to_gsf(
    ossie_yaml: str,
    *,
    database_name: str | None = None,
) -> str:
    """Convert Apache Ossie YAML to standalone GSF semantic-model YAML."""
    root, model = _parse_ossie(ossie_yaml)
    source_datasets = model.get("datasets") or []
    if not isinstance(source_datasets, list) or not source_datasets:
        raise GSFConversionError(
            "The Ossie semantic model must contain at least one dataset"
        )

    datasets: dict[str, dict[str, Any]] = {}
    terms: list[dict[str, Any]] = []
    resolved_databases: set[str] = set()
    for dataset in source_datasets:
        if not isinstance(dataset, dict) or not dataset.get("name"):
            raise GSFConversionError(
                "Every Ossie dataset must be a mapping with a name"
            )
        name = str(dataset["name"])
        if name in datasets:
            raise GSFConversionError(f"Duplicate dataset name {name!r}")
        source = _parse_source(dataset.get("source"), database_name)
        if not source["database"] or not source["schema"]:
            raise GSFConversionError(
                f"Dataset {name!r} source must resolve to database.schema.table"
            )
        resolved_databases.add(str(source["database"]))
        datasets[name] = {"source": source, "original": dataset}

        term: dict[str, Any] = {
            "name": name,
            "source": source,
        }
        _copy_optional(dataset, term, "description")
        ai_context = dataset.get("ai_context")
        if isinstance(ai_context, dict):
            cleaned_ai_context = {
                key: value for key, value in ai_context.items() if key != "synonyms"
            }
            if cleaned_ai_context:
                term["ai_context"] = cleaned_ai_context
        elif ai_context is not None:
            term["ai_context"] = ai_context
        synonyms = _synonyms(ai_context)
        if synonyms:
            term["synonyms"] = synonyms
        _copy_optional(dataset, term, "primary_key")
        _copy_optional(dataset, term, "unique_keys")
        _copy_ossie_metadata(dataset, term)
        terms.append(term)

    if len(resolved_databases) != 1:
        raise GSFConversionError(
            "One GSF model file must target exactly one database; found: "
            + ", ".join(sorted(resolved_databases))
        )
    resolved_database = next(iter(resolved_databases))

    relationships = [
        _relationship_to_gsf(relationship, datasets)
        for relationship in model.get("relationships") or []
    ]
    term_by_name = {term["name"]: term for term in terms}
    sql_names: list[str] = []

    for dataset_name, context in datasets.items():
        dataset = context["original"]
        term = term_by_name[dataset_name]
        column_attributes: list[dict[str, Any]] = []
        sql_attributes: list[dict[str, Any]] = []
        used_columns: set[str] = set()

        for field in dataset.get("fields") or []:
            if not isinstance(field, dict) or not field.get("name"):
                raise GSFConversionError(
                    f"Every field in dataset {dataset_name!r} needs a name"
                )
            name = str(field["name"])
            expressions = _normalize_expressions(field.get("expression"), name)
            selected = _pick_expression(expressions, name)
            source_column = _simple_source_column(
                selected,
                dataset_name,
                str(context["source"]["table"]),
            )
            if source_column is not None:
                if source_column in used_columns:
                    raise GSFConversionError(
                        f"Multiple fields in dataset {dataset_name!r} map to "
                        f"physical column {source_column!r}"
                    )
                used_columns.add(source_column)
                attribute: dict[str, Any] = {
                    "name": name,
                    "source_column": source_column,
                    "expressions": expressions,
                }
                _copy_optional(field, attribute, "description")
                _copy_optional(field, attribute, "ai_context")
                _copy_optional(field, attribute, "dimension")
                _copy_ossie_metadata(field, attribute)
                column_attributes.append(attribute)
            else:
                extension_data = _gsf_extension_data(field)
                refs = extension_data.get("table_refs") or [dataset_name]
                if not isinstance(refs, list) or any(
                    ref not in datasets for ref in refs
                ):
                    raise GSFConversionError(
                        f"Computed field {name!r} has invalid GSF table_refs"
                    )
                sources = [(str(ref), datasets[str(ref)]["source"]) for ref in refs]
                relevant_relationships = [
                    relationship
                    for relationship in relationships
                    if relationship["from_term"] in refs
                    or relationship["to_term"] in refs
                ]
                attribute = {
                    "name": name,
                    "kind": "field",
                    "expressions": expressions,
                    "sql": str(extension_data.get("sql") or "")
                    or _wrap_expression(
                        selected, name, sources, relevant_relationships
                    ),
                    "table_refs": list(refs),
                }
                _copy_optional(field, attribute, "description")
                _copy_optional(field, attribute, "ai_context")
                _copy_optional(field, attribute, "dimension")
                _copy_ossie_metadata(field, attribute)
                sql_attributes.append(attribute)
                sql_names.append(name)

        if column_attributes:
            term["column_attributes"] = column_attributes
        if sql_attributes:
            term["sql_attributes"] = sql_attributes

    for metric in model.get("metrics") or []:
        if not isinstance(metric, dict) or not metric.get("name"):
            raise GSFConversionError("Every Ossie metric must be a mapping with a name")
        name = str(metric["name"])
        expressions = _normalize_expressions(metric.get("expression"), name)
        selected = _pick_expression(expressions, name)
        referenced_terms = _referenced_terms(selected, datasets)
        owner = _metric_term(metric, referenced_terms, datasets)
        refs = referenced_terms or [owner]
        sources = [(term_name, datasets[term_name]["source"]) for term_name in refs]
        relevant_relationships = [
            relationship
            for relationship in relationships
            if relationship["from_term"] in refs or relationship["to_term"] in refs
        ]
        extension_data = _gsf_extension_data(metric)
        preserved_refs = extension_data.get("table_refs")
        if isinstance(preserved_refs, list) and preserved_refs:
            if any(ref not in datasets for ref in preserved_refs):
                raise GSFConversionError(f"Metric {name!r} has invalid GSF table_refs")
            refs = [str(ref) for ref in preserved_refs]
            sources = [(term_name, datasets[term_name]["source"]) for term_name in refs]
            relevant_relationships = [
                relationship
                for relationship in relationships
                if relationship["from_term"] in refs or relationship["to_term"] in refs
            ]
        attribute = {
            "name": name,
            "kind": "metric",
            "expressions": expressions,
            "sql": str(extension_data.get("sql") or "")
            or _wrap_expression(selected, name, sources, relevant_relationships),
            "table_refs": refs,
        }
        _copy_optional(metric, attribute, "description")
        _copy_optional(metric, attribute, "ai_context")
        _copy_ossie_metadata(metric, attribute)
        term_by_name[owner].setdefault("sql_attributes", []).append(attribute)
        sql_names.append(name)

    duplicate_sql = sorted(
        name for name, count in Counter(sql_names).items() if count > 1
    )
    if duplicate_sql:
        raise GSFConversionError(
            "GSF SqlAttribute names are global; duplicate computed field or "
            "metric names: " + ", ".join(duplicate_sql)
        )

    gsf_model: dict[str, Any] = {
        "name": str(model["name"]),
        "database": resolved_database,
    }
    _copy_optional(model, gsf_model, "description")
    _copy_optional(model, gsf_model, "ai_context")
    _copy_ossie_metadata(model, gsf_model)

    output: dict[str, Any] = {
        "version": GSF_VERSION,
        "model": gsf_model,
        "terms": terms,
    }
    if relationships:
        output["semantic_foreign_keys"] = relationships
    return _dump_yaml(output)


def convert_gsf_to_ossie(
    gsf_yaml: str,
    *,
    model_name: str | None = None,
) -> str:
    """Convert standalone GSF semantic-model YAML to Apache Ossie YAML."""
    root = _parse_gsf(gsf_yaml)
    source_model = root["model"]
    terms = root["terms"]
    term_names = {
        str(term.get("name"))
        for term in terms
        if isinstance(term, dict) and term.get("name")
    }
    if len(term_names) != len(terms):
        raise GSFConversionError("Every GSF term needs a unique non-empty name")

    datasets: list[dict[str, Any]] = []
    metrics: list[dict[str, Any]] = []
    for term in terms:
        term_name = str(term["name"])
        source = _parse_source(
            term.get("source"),
            source_model.get("database"),
        )
        if not source["database"] or not source["schema"]:
            raise GSFConversionError(
                f"Term {term_name!r} source must be fully qualified"
            )
        dataset: dict[str, Any] = {
            "name": term_name,
            "source": ".".join(
                str(source[key]) for key in ("database", "schema", "table")
            ),
        }
        _copy_optional(term, dataset, "description")
        _copy_optional(term, dataset, "primary_key")
        _copy_optional(term, dataset, "unique_keys")
        ai_context = term.get("ai_context")
        synonyms = term.get("synonyms") or []
        merged_ai = _merge_synonyms(ai_context, synonyms)
        if merged_ai is not None:
            dataset["ai_context"] = merged_ai
        dataset_extensions = _native_extensions(term)
        if dataset_extensions:
            dataset["custom_extensions"] = dataset_extensions

        fields: list[dict[str, Any]] = []
        for attribute in term.get("column_attributes") or []:
            _validate_attribute(attribute, term_name, kind="column")
            field: dict[str, Any] = {
                "name": str(attribute["name"]),
                "expression": {
                    "dialects": attribute.get("expressions")
                    or [
                        {
                            "dialect": "ANSI_SQL",
                            "expression": str(attribute["source_column"]),
                        }
                    ]
                },
            }
            _copy_optional(attribute, field, "description")
            _copy_optional(attribute, field, "ai_context")
            _copy_optional(attribute, field, "dimension")
            field_extensions = _native_extensions(attribute)
            if field_extensions:
                field["custom_extensions"] = field_extensions
            fields.append(field)

        for attribute in term.get("sql_attributes") or []:
            _validate_attribute(attribute, term_name, kind="sql")
            sql_kind = attribute.get("kind")
            if sql_kind not in {"field", "metric"}:
                raise GSFConversionError(
                    f"SQL attribute {attribute['name']!r} kind must be "
                    "'field' or 'metric'"
                )
            expression = {
                "dialects": _normalize_native_expressions(
                    attribute.get("expressions"),
                    str(attribute["name"]),
                )
            }
            item: dict[str, Any] = {
                "name": str(attribute["name"]),
                "expression": expression,
            }
            _copy_optional(attribute, item, "description")
            _copy_optional(attribute, item, "ai_context")
            if sql_kind == "field":
                _copy_optional(attribute, item, "dimension")
                item["custom_extensions"] = _native_extensions(
                    attribute,
                    gsf_data={
                        "kind": "field",
                        "sql": attribute.get("sql"),
                        "table_refs": attribute.get("table_refs"),
                    },
                )
                fields.append(item)
            else:
                item["custom_extensions"] = _native_extensions(
                    attribute,
                    gsf_data={
                        "term": term_name,
                        "sql": attribute.get("sql"),
                        "table_refs": attribute.get("table_refs"),
                    },
                )
                metrics.append(item)

        if fields:
            dataset["fields"] = fields
        datasets.append(dataset)

    relationships: list[dict[str, Any]] = []
    for relationship in root.get("semantic_foreign_keys") or []:
        if not isinstance(relationship, dict) or not relationship.get("name"):
            raise GSFConversionError("Every semantic foreign key must have a name")
        from_term = relationship.get("from_term")
        to_term = relationship.get("to_term")
        if from_term not in term_names or to_term not in term_names:
            raise GSFConversionError(
                f"Semantic foreign key {relationship['name']!r} references "
                "an unknown term"
            )
        from_columns = relationship.get("from_columns") or []
        to_columns = relationship.get("to_columns") or []
        if not from_columns or len(from_columns) != len(to_columns):
            raise GSFConversionError(
                f"Semantic foreign key {relationship['name']!r} must have "
                "equal, non-empty column lists"
            )
        converted_relationship: dict[str, Any] = {
            "name": str(relationship["name"]),
            "from": from_term,
            "to": to_term,
            "from_columns": list(from_columns),
            "to_columns": list(to_columns),
        }
        _copy_optional(
            relationship,
            converted_relationship,
            "ai_context",
        )
        relationship_extensions = _native_extensions(relationship)
        if relationship_extensions:
            converted_relationship["custom_extensions"] = relationship_extensions
        relationships.append(converted_relationship)

    semantic_model: dict[str, Any] = {
        "name": model_name or str(source_model["name"]),
        "datasets": datasets,
    }
    _copy_optional(source_model, semantic_model, "description")
    _copy_optional(source_model, semantic_model, "ai_context")
    model_extensions = _native_extensions(source_model)
    if model_extensions:
        semantic_model["custom_extensions"] = model_extensions
    if relationships:
        semantic_model["relationships"] = relationships
    if metrics:
        semantic_model["metrics"] = metrics
    return _dump_yaml(
        {
            "version": OSSIE_VERSION,
            "semantic_model": [semantic_model],
        }
    )


def _parse_ossie(value: str) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _load_yaml(value, "Ossie")
    unknown = sorted(set(root) - {"version", "semantic_model"})
    if unknown:
        raise GSFConversionError(
            "Unsupported Ossie root properties: " + ", ".join(unknown)
        )
    if str(root.get("version", "")) != OSSIE_VERSION:
        raise GSFConversionError(
            f"Unsupported Ossie version {root.get('version')!r}; "
            f"supported version is {OSSIE_VERSION!r}"
        )
    models = root.get("semantic_model")
    if not isinstance(models, list) or len(models) != 1:
        raise GSFConversionError("Ossie input must contain exactly one semantic model")
    model = models[0]
    if not isinstance(model, dict) or not model.get("name"):
        raise GSFConversionError("Ossie semantic model requires a name")
    return root, model


def _parse_gsf(value: str) -> dict[str, Any]:
    root = _load_yaml(value, "GSF")
    unknown = sorted(set(root) - {"version", "model", "terms", "semantic_foreign_keys"})
    if unknown:
        raise GSFConversionError(
            "Unsupported GSF root properties: " + ", ".join(unknown)
        )
    if str(root.get("version", "")) != GSF_VERSION:
        raise GSFConversionError(
            f"Unsupported GSF version {root.get('version')!r}; "
            f"supported version is {GSF_VERSION!r}"
        )
    model = root.get("model")
    if not isinstance(model, dict) or not model.get("name"):
        raise GSFConversionError("GSF model requires 'model.name'")
    terms = root.get("terms")
    if not isinstance(terms, list) or not terms:
        raise GSFConversionError("'terms' must be a non-empty list")
    relationships = root.get("semantic_foreign_keys", [])
    if not isinstance(relationships, list):
        raise GSFConversionError("'semantic_foreign_keys' must be a list")
    return root


def _load_yaml(value: str, label: str) -> dict[str, Any]:
    try:
        root = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        raise GSFConversionError(f"Invalid {label} YAML: {exc}") from exc
    if not isinstance(root, dict):
        raise GSFConversionError(f"Invalid {label} YAML: expected a root mapping")
    return root


def _relationship_to_gsf(
    relationship: Any,
    datasets: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(relationship, dict) or not relationship.get("name"):
        raise GSFConversionError("Every Ossie relationship needs a name")
    from_term = relationship.get("from")
    to_term = relationship.get("to")
    if from_term not in datasets or to_term not in datasets:
        raise GSFConversionError(
            f"Relationship {relationship['name']!r} references an unknown dataset"
        )
    from_columns = relationship.get("from_columns") or []
    to_columns = relationship.get("to_columns") or []
    if not from_columns or len(from_columns) != len(to_columns):
        raise GSFConversionError(
            f"Relationship {relationship['name']!r} must have equal, "
            "non-empty column lists"
        )
    converted: dict[str, Any] = {
        "name": str(relationship["name"]),
        "from_term": from_term,
        "to_term": to_term,
        "from_columns": list(from_columns),
        "to_columns": list(to_columns),
    }
    _copy_optional(relationship, converted, "ai_context")
    _copy_ossie_metadata(relationship, converted)
    return converted


def _normalize_expressions(value: Any, name: str) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        raise GSFConversionError(f"{name!r} has no valid expression")
    return _normalize_native_expressions(value.get("dialects"), name)


def _normalize_native_expressions(
    value: Any,
    name: str,
) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise GSFConversionError(f"{name!r} requires at least one expression dialect")
    result: list[dict[str, str]] = []
    for item in value:
        if (
            isinstance(item, dict)
            and item.get("dialect")
            and item.get("expression") is not None
        ):
            result.append(
                {
                    "dialect": str(item["dialect"]),
                    "expression": str(item["expression"]),
                }
            )
    if not result:
        raise GSFConversionError(f"{name!r} has no usable expression dialect")
    return result


def _pick_expression(expressions: list[dict[str, str]], name: str) -> str:
    for expression in expressions:
        if expression["dialect"].upper() == "ANSI_SQL":
            return expression["expression"]
    if expressions:
        return expressions[0]["expression"]
    raise GSFConversionError(f"{name!r} has no usable expression")


def _simple_source_column(
    expression: str,
    dataset_name: str,
    table_name: str,
) -> str | None:
    match = _SIMPLE_COLUMN.fullmatch(expression.strip())
    if not match:
        return None
    qualifier = match.group("qualifier")
    if qualifier and qualifier not in (dataset_name, table_name):
        return None
    return match.group("column")


def _referenced_terms(
    expression: str,
    datasets: Mapping[str, Any],
) -> list[str]:
    result: list[str] = []
    for match in _TERM_REFERENCE.finditer(expression):
        name = match.group("term")
        if name in datasets and name not in result:
            result.append(name)
    return result


def _metric_term(
    metric: dict[str, Any],
    referenced_terms: list[str],
    datasets: Mapping[str, Any],
) -> str:
    term = _gsf_extension_data(metric).get("term")
    if term in datasets:
        return str(term)
    if referenced_terms:
        return referenced_terms[0]
    if len(datasets) == 1:
        return next(iter(datasets))
    raise GSFConversionError(
        f"Metric {metric.get('name')!r} does not identify an owning dataset. "
        "Add an NVIDIA_GSF extension with data "
        '{"term": "dataset_name"}.'
    )


def _gsf_extension_data(item: Mapping[str, Any]) -> dict[str, Any]:
    for extension in item.get("custom_extensions") or []:
        if extension.get("vendor_name") not in GSF_VENDOR_ALIASES:
            continue
        try:
            data = json.loads(str(extension.get("data") or "{}"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _wrap_expression(
    expression: str,
    name: str,
    sources: list[tuple[str, Mapping[str, Any]]],
    relationships: list[dict[str, Any]],
) -> str:
    stripped = expression.strip()
    if stripped.upper().startswith(("SELECT ", "SELECT\n", "WITH ", "WITH\n")):
        return stripped
    if not sources:
        raise GSFConversionError(f"Cannot determine a source table for {name!r}")
    source_map = dict(sources)
    anchor_name, anchor = sources[0]
    from_sql = f"{_qualified_table(anchor)} AS {_quote_identifier(anchor_name)}"
    joined = {anchor_name}
    remaining = {term_name for term_name, _ in sources[1:]}
    joins: list[str] = []
    while remaining:
        matched = False
        for relationship in relationships:
            left = str(relationship["from_term"])
            right = str(relationship["to_term"])
            if left in joined and right in remaining:
                new_name = right
            elif right in joined and left in remaining:
                new_name = left
            else:
                continue
            conditions = [
                f"{_quote_identifier(left)}.{_quote_identifier(left_column)} = "
                f"{_quote_identifier(right)}."
                f"{_quote_identifier(right_column)}"
                for left_column, right_column in zip(
                    relationship["from_columns"],
                    relationship["to_columns"],
                    strict=True,
                )
            ]
            joins.append(
                f"JOIN {_qualified_table(source_map[new_name])} AS "
                f"{_quote_identifier(new_name)} ON {' AND '.join(conditions)}"
            )
            joined.add(new_name)
            remaining.remove(new_name)
            matched = True
            break
        if not matched:
            missing = sorted(remaining)[0]
            raise GSFConversionError(
                f"{name!r} references disconnected dataset {missing!r}; "
                "declare a relationship connecting all referenced datasets"
            )
    return f"SELECT {stripped} AS {_quote_identifier(name)} FROM {from_sql}" + (
        f" {' '.join(joins)}" if joins else ""
    )


def _parse_source(
    source: Any,
    default_database: str | None,
) -> dict[str, str | None]:
    if isinstance(source, dict):
        database = source.get("database") or default_database
        schema = source.get("schema")
        table = source.get("table")
        if not table:
            raise GSFConversionError("Source mapping requires 'table'")
        return {
            "database": str(database) if database else None,
            "schema": str(schema) if schema else None,
            "table": str(table),
        }
    value = str(source or "").strip()
    if not value:
        raise GSFConversionError("Every dataset/term needs a source")
    upper = value.upper()
    if upper.startswith(("SELECT ", "SELECT\n", "WITH ", "WITH\n")):
        raise GSFConversionError("GSF term sources must identify physical tables")
    parts = _split_identifier(value)
    if len(parts) == 3:
        database, schema, table = parts
    elif len(parts) == 2:
        database, (schema, table) = default_database, parts
    elif len(parts) == 1:
        database, schema, table = default_database, None, parts[0]
    else:
        raise GSFConversionError(
            f"Source {value!r} must be table, schema.table, or database.schema.table"
        )
    return {"database": database, "schema": schema, "table": table}


def _split_identifier(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for char in value:
        if char in ('"', "`"):
            quote = None if quote == char else char if quote is None else quote
        elif char == "." and quote is None:
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    parts.append("".join(current).strip())
    return [
        part[1:-1]
        if len(part) > 1 and part[0] == part[-1] and part[0] in ('"', "`")
        else part
        for part in parts
    ]


def _qualified_table(source: Mapping[str, Any]) -> str:
    return ".".join(
        _quote_identifier(str(source[key])) for key in ("database", "schema", "table")
    )


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _validate_attribute(
    attribute: Any,
    term_name: str,
    *,
    kind: str,
) -> None:
    if not isinstance(attribute, dict) or not attribute.get("name"):
        raise GSFConversionError(
            f"Every {kind} attribute in term {term_name!r} needs a name"
        )
    if kind == "column" and not attribute.get("source_column"):
        raise GSFConversionError(
            f"Column attribute {attribute['name']!r} needs source_column"
        )


def _synonyms(ai_context: Any) -> list[str]:
    if not isinstance(ai_context, dict):
        return []
    return [str(value) for value in ai_context.get("synonyms") or [] if value]


def _merge_synonyms(ai_context: Any, synonyms: Any) -> Any:
    clean = [str(value) for value in synonyms if value]
    if not clean:
        return ai_context
    if isinstance(ai_context, dict):
        result = dict(ai_context)
        result["synonyms"] = clean
        return result
    if isinstance(ai_context, str) and ai_context:
        return {"instructions": ai_context, "synonyms": clean}
    return {"synonyms": clean}


def _copy_optional(
    source: Mapping[str, Any],
    target: dict[str, Any],
    key: str,
) -> None:
    if source.get(key) is not None:
        target[key] = source[key]


def _copy_ossie_metadata(
    source: Mapping[str, Any],
    target: dict[str, Any],
) -> None:
    extension_data = _gsf_extension_data(source)
    metadata = extension_data.get("metadata")
    if isinstance(metadata, dict):
        target["metadata"] = dict(metadata)
    preserved_extensions = [
        extension
        for extension in source.get("custom_extensions") or []
        if extension.get("vendor_name") not in GSF_VENDOR_ALIASES
    ]
    if preserved_extensions:
        native_metadata = dict(target.get("metadata") or {})
        ossie_metadata = dict(native_metadata.get("apache_ossie") or {})
        ossie_metadata["custom_extensions"] = preserved_extensions
        native_metadata["apache_ossie"] = ossie_metadata
        target["metadata"] = native_metadata


def _native_extensions(
    source: Mapping[str, Any],
    *,
    gsf_data: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    metadata = source.get("metadata")
    extensions: list[dict[str, Any]] = []
    if isinstance(metadata, dict):
        ossie_metadata = metadata.get("apache_ossie")
        if isinstance(ossie_metadata, dict):
            extensions.extend(ossie_metadata.get("custom_extensions") or [])
        remaining_metadata = {
            key: value for key, value in metadata.items() if key != "apache_ossie"
        }
    else:
        remaining_metadata = {}
    data = dict(gsf_data or {})
    if remaining_metadata:
        data["metadata"] = remaining_metadata
    if data:
        extensions.append(
            {
                "vendor_name": NVIDIA_GSF_VENDOR,
                "data": json.dumps(data),
            }
        )
    return extensions


def _dump_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Apache Ossie and standalone GSF YAML files"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export",
        help="Convert Ossie YAML to standalone GSF YAML",
    )
    export_parser.add_argument("-i", "--input", type=Path, required=True)
    export_parser.add_argument("-o", "--output", type=Path)
    export_parser.add_argument("--database-name")

    import_parser = subparsers.add_parser(
        "import",
        help="Convert standalone GSF YAML to Ossie YAML",
    )
    import_parser.add_argument("-i", "--input", type=Path, required=True)
    import_parser.add_argument("-o", "--output", type=Path)
    import_parser.add_argument("--name")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    try:
        source = args.input.read_text(encoding="utf-8")
        if args.command == "export":
            output = convert_ossie_to_gsf(
                source,
                database_name=args.database_name,
            )
        else:
            output = convert_gsf_to_ossie(
                source,
                model_name=args.name,
            )
        if args.output is None:
            print(output, end="")
        else:
            args.output.write_text(output, encoding="utf-8")
    except (GSFConversionError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
