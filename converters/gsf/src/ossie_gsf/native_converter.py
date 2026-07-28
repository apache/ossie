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

"""Apache Ossie ↔ native NVIDIA GSF model-document conversion."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

import yaml
from sqlglot import exp, parse_one
from sqlglot.errors import ParseError, TokenError

OSSIE_VERSION = "0.2.0.dev0"
# Any release in this major.minor series is accepted on input. The spec is
# still on a .dev line, so pinning the exact string would reject every real
# model as soon as the patch or dev suffix moves.
OSSIE_SERIES = tuple(int(part) for part in OSSIE_VERSION.split(".")[:2])
NVIDIA_GSF_VENDOR = "NVIDIA_GSF"
GSF_VENDOR_ALIASES = {NVIDIA_GSF_VENDOR, "GSF"}
_ID_NAMESPACE = UUID("03d14261-6432-50fe-b099-77e8061af4f9")
_SQL_GROUPS = ("manual", "table", "sql", "bridge_table")
_SIMPLE_COLUMN = re.compile(
    r"^(?:(?P<qualifier>[A-Za-z_][A-Za-z0-9_]*)\.)?"
    r"(?P<column>[A-Za-z_][A-Za-z0-9_]*)$"
)
# sqlglot's default parser first, since it is closest to ANSI, then the
# dialects GSF connections commonly report.
_SQL_DIALECTS = (
    "",
    "snowflake",
    "databricks",
    "bigquery",
    "tsql",
    "postgres",
    "mysql",
    "duckdb",
    "spark",
    "oracle",
    "sqlite",
)
_DATE_PART_UNITS = frozenset(
    {
        "year",
        "years",
        "yy",
        "yyyy",
        "quarter",
        "quarters",
        "qq",
        "q",
        "month",
        "months",
        "mm",
        "mon",
        "week",
        "weeks",
        "wk",
        "ww",
        "isoweek",
        "day",
        "days",
        "dd",
        "dayofyear",
        "doy",
        "dy",
        "dayofweek",
        "dow",
        "weekday",
        "hour",
        "hours",
        "hh",
        "minute",
        "minutes",
        "mi",
        "second",
        "seconds",
        "ss",
        "millisecond",
        "milliseconds",
        "ms",
        "microsecond",
        "microseconds",
        "us",
        "nanosecond",
        "nanoseconds",
        "ns",
        "epoch",
    }
)
# Functions whose *first* argument is a date-part unit rather than data.
# LAST_DAY, TRUNC and EXTRACT are deliberately absent: the first two take data
# there, and EXTRACT's unit uses syntax sqlglot does not parse as a column.
_UNIT_FIRST_FUNCTIONS = frozenset(
    {
        "datediff",
        "date_diff",
        "datetime_diff",
        "timestampdiff",
        "timestamp_diff",
        "timediff",
        "time_diff",
        "dateadd",
        "date_add",
        "datetime_add",
        "timestampadd",
        "timestamp_add",
        "datesub",
        "date_sub",
        "timestampsub",
        "timestamp_sub",
        "date_trunc",
        "datetrunc",
        "datetime_trunc",
        "timestamp_trunc",
        "time_trunc",
        "date_part",
        "datepart",
        "datename",
    }
)
# Typed sqlglot nodes that hold the unit in their ``this`` slot. DATE_ADD and
# friends are excluded: they keep the unit in ``unit`` and put data in ``this``.
_UNIT_IN_THIS_FUNCTIONS = tuple(
    node
    for node in (
        getattr(exp, name, None)
        for name in ("DateDiff", "TimestampDiff", "DatetimeDiff", "TimeDiff")
    )
    if isinstance(node, type)
)
# Ossie names only a few dialects; everything else has no equivalent.
_GSF_TO_OSSIE_DIALECT = {
    "snowflake": "SNOWFLAKE",
    "databricks": "DATABRICKS",
    "bigquery": "BIGQUERY",
}
# A GSF column carries the physical type its connection reports. Ossie names ten
# logical types, so the mapping is deliberately coarse in that direction and
# canonical in the other, which is what lets a datatype survive a full cycle.
_OSSIE_DATATYPE_BY_SQL_TYPE = {
    "VARCHAR": "String",
    "VARCHAR2": "String",
    "NVARCHAR": "String",
    "NVARCHAR2": "String",
    "CHAR": "String",
    "NCHAR": "String",
    "CHARACTER": "String",
    "CHARACTER VARYING": "String",
    "TEXT": "String",
    "STRING": "String",
    "CLOB": "String",
    "NCLOB": "String",
    "INT": "Integer",
    "INTEGER": "Integer",
    "BIGINT": "Integer",
    "SMALLINT": "Integer",
    "TINYINT": "Integer",
    "BYTEINT": "Integer",
    "INT2": "Integer",
    "INT4": "Integer",
    "INT8": "Integer",
    "DEC": "Decimal",
    "DECIMAL": "Decimal",
    "NUMERIC": "Decimal",
    "NUMBER": "Decimal",
    "MONEY": "Decimal",
    "FLOAT": "Float",
    "FLOAT4": "Float",
    "FLOAT8": "Float",
    "REAL": "Float",
    "DOUBLE": "Float",
    "DOUBLE PRECISION": "Float",
    "BINARY_FLOAT": "Float",
    "BINARY_DOUBLE": "Float",
    "BOOL": "Boolean",
    "BOOLEAN": "Boolean",
    "DATE": "Date",
    "TIME": "Time",
    "TIME WITHOUT TIME ZONE": "Time",
    "DATETIME": "DateTime",
    "DATETIME2": "DateTime",
    "SMALLDATETIME": "DateTime",
    "TIMESTAMP": "DateTime",
    "TIMESTAMP_NTZ": "DateTime",
    "TIMESTAMP WITHOUT TIME ZONE": "DateTime",
    "DATETIMEOFFSET": "DateTimeTz",
    "TIMESTAMPTZ": "DateTimeTz",
    "TIMESTAMP_LTZ": "DateTimeTz",
    "TIMESTAMP_TZ": "DateTimeTz",
    "TIMESTAMP WITH LOCAL TIME ZONE": "DateTimeTz",
    "TIMESTAMP WITH TIME ZONE": "DateTimeTz",
}
# Opaque is absent on purpose: it names a type outside Ossie's vocabulary, so
# there is nothing to write back and no physical type worth inventing.
_SQL_TYPE_BY_OSSIE_DATATYPE = {
    "String": "TEXT",
    "Integer": "BIGINT",
    "Decimal": "DECIMAL",
    "Float": "DOUBLE",
    "Boolean": "BOOLEAN",
    "Date": "DATE",
    "Time": "TIME",
    "DateTime": "TIMESTAMP",
    "DateTimeTz": "TIMESTAMP WITH TIME ZONE",
}


class GSFConversionError(Exception):
    """Raised when a document cannot be converted safely."""


def convert_ossie_to_gsf(
    ossie_yaml: str,
    *,
    database_name: str | None = None,
) -> str:
    """Convert one Apache Ossie model to a native ``GsfModelDocument``."""
    _, model = _parse_ossie(ossie_yaml)
    source_datasets = model.get("datasets") or []
    if not isinstance(source_datasets, list) or not source_datasets:
        raise GSFConversionError(
            "The Ossie semantic model must contain at least one dataset"
        )

    native = _native_snapshot(model)
    preserved = _index_native_document(native)
    datasets: dict[str, dict[str, Any]] = {}
    for item in source_datasets:
        if not isinstance(item, dict) or not item.get("name"):
            raise GSFConversionError(
                "Every Ossie dataset must be a mapping with a name"
            )
        name = str(item["name"])
        if name in datasets:
            raise GSFConversionError(f"Duplicate dataset name {name!r}")
        source = _parse_source(item.get("source"), database_name)
        if not source["database"] or not source["schema"]:
            raise GSFConversionError(
                f"Dataset {name!r} source must resolve to database.schema.table"
            )
        datasets[name] = {
            "source": source,
            "original": item,
            "columns": [],
            "column_set": set(),
            "simple_fields": [],
            "computed_fields": [],
        }
        preserved_table = preserved["tables"].get(_source_key(source), {})
        for column in preserved_table.get("columns") or []:
            if isinstance(column, dict) and column.get("name"):
                _add_catalog_column(datasets[name], str(column["name"]))

    relationships = [
        _validate_relationship(item, datasets)
        for item in model.get("relationships") or []
    ]
    for relationship in relationships:
        for side, key in (("from", "from_columns"), ("to", "to_columns")):
            for column in relationship[key]:
                _add_catalog_column(datasets[str(relationship[side])], str(column))

    for name, context in datasets.items():
        dataset = context["original"]
        for column in dataset.get("primary_key") or []:
            _add_catalog_column(context, str(column))
        for key in dataset.get("unique_keys") or []:
            for column in key:
                _add_catalog_column(context, str(column))

        field_names: set[str] = set()
        for field in dataset.get("fields") or []:
            if not isinstance(field, dict) or not field.get("name"):
                raise GSFConversionError(
                    f"Every field in dataset {name!r} needs a name"
                )
            field_name = str(field["name"])
            if field_name in field_names:
                raise GSFConversionError(
                    f"Duplicate field name {field_name!r} in dataset {name!r}"
                )
            field_names.add(field_name)
            expressions = _normalize_expressions(field.get("expression"), field_name)
            selected = _pick_expression(expressions, field_name)
            source_column = _simple_source_column(
                selected, name, str(context["source"]["table"])
            )
            if source_column:
                _add_catalog_column(context, source_column)
                context["simple_fields"].append((field, source_column))
            else:
                refs = _field_table_refs(field, selected, name, datasets)
                context["computed_fields"].append((field, expressions, selected, refs))
                _collect_expression_columns(selected, name, refs, datasets, preserved)

    metrics: list[tuple[dict[str, Any], list[dict[str, str]], str, list[str]]] = []
    metric_names: set[str] = set()
    for metric in model.get("metrics") or []:
        if not isinstance(metric, dict) or not metric.get("name"):
            raise GSFConversionError("Every Ossie metric must be a mapping with a name")
        name = str(metric["name"])
        if name in metric_names:
            raise GSFConversionError(f"Duplicate metric name {name!r}")
        metric_names.add(name)
        expressions = _normalize_expressions(metric.get("expression"), name)
        selected = _pick_expression(expressions, name)
        refs = _metric_table_refs(metric, selected, datasets)
        _collect_expression_columns(selected, None, refs, datasets, preserved)
        metrics.append((metric, expressions, selected, refs))

    databases, table_ids, column_ids = _build_catalog(datasets, preserved)
    if native:
        databases = _merge_catalog_databases(
            native.get("data_layer", {}).get("databases"), databases
        )
        for column_id in _catalog_column_ids(databases):
            column_ids.setdefault(("__native__", column_id), column_id)
    terms: list[dict[str, Any]] = []
    term_ids: dict[str, str] = {}
    attribute_ids: dict[tuple[str, str], str] = {}

    for name, context in datasets.items():
        dataset = context["original"]
        source_key = _source_key(context["source"])
        preserved_term = preserved["terms"].get((name, source_key), {})
        term_id = str(preserved_term.get("id") or _stable_id("term", name, *source_key))
        term_ids[name] = term_id
        term: dict[str, Any] = {
            "id": term_id,
            "name": name,
            "description": str(dataset.get("description") or ""),
            "represents": [table_ids[name]],
            "columns_attributes": [],
        }
        for field, source_column in context["simple_fields"]:
            field_name = str(field["name"])
            preserved_attr = preserved["column_attributes"].get(
                (name, field_name, source_key, source_column), {}
            )
            attr_id = str(
                preserved_attr.get("id")
                or _stable_id(
                    "column-attribute", name, field_name, *source_key, source_column
                )
            )
            attribute_ids[(name, source_column)] = attr_id
            term["columns_attributes"].append(
                {
                    "id": attr_id,
                    "name": field_name,
                    "description": str(field.get("description") or ""),
                    "column_id": column_ids[(name, source_column)],
                }
            )
        terms.append(term)

    joins: list[dict[str, Any]] = []
    foreign_keys: list[dict[str, str]] = []
    semantic_fks: list[dict[str, str]] = []
    for relationship in relationships:
        from_name = str(relationship["from"])
        to_name = str(relationship["to"])
        joins.append(
            {
                "source_table_id": table_ids[from_name],
                "target_table_id": table_ids[to_name],
                "join_columns": [
                    {"source": str(source), "target": str(target)}
                    for source, target in zip(
                        relationship["from_columns"],
                        relationship["to_columns"],
                        strict=True,
                    )
                ],
            }
        )
        for source, target in zip(
            relationship["from_columns"],
            relationship["to_columns"],
            strict=True,
        ):
            source_name = str(source)
            target_name = str(target)
            foreign_keys.append(
                {
                    "source_column_id": column_ids[(from_name, source_name)],
                    "target_column_id": column_ids[(to_name, target_name)],
                }
            )
            target_attribute_id = attribute_ids.get((to_name, target_name))
            if target_attribute_id:
                semantic_fks.append(
                    {
                        "column_attribute_id": target_attribute_id,
                        "column_id": column_ids[(from_name, source_name)],
                    }
                )

    sql_groups: dict[str, list[dict[str, Any]]] = {key: [] for key in _SQL_GROUPS}
    for name, context in datasets.items():
        for field, _, selected, refs in context["computed_fields"]:
            field_name = str(field["name"])
            extension = _gsf_extension_data(field)
            preserved_attr = preserved["sql_attributes"].get((name, field_name), {})
            expression_unchanged = _expression_matches(
                selected, extension.get("ossie_expression")
            )
            source_group = str(
                extension.get("sql_source")
                or preserved_attr.get("source_group")
                or "manual"
            )
            if source_group not in sql_groups:
                source_group = "manual"
            full_sql = str(
                extension.get("sql")
                if expression_unchanged and extension.get("sql")
                else _wrap_expression(
                    selected,
                    field_name,
                    refs,
                    datasets,
                    relationships,
                )
            )
            resolved_column_ids = _sql_column_ids(
                selected, name, refs, datasets, column_ids
            )
            preserved_column_ids = extension.get(
                "sql_column_is", preserved_attr.get("sql_column_is")
            )
            if expression_unchanged and _all_resolvable_ids(
                preserved_column_ids, column_ids
            ):
                resolved_column_ids = list(preserved_column_ids)
            sql_groups[source_group].append(
                {
                    "id": str(
                        extension.get("id")
                        or preserved_attr.get("id")
                        or _stable_id("sql-attribute", name, field_name)
                    ),
                    "name": field_name,
                    "description": str(field.get("description") or ""),
                    "sql": full_sql,
                    "sql_column_is": resolved_column_ids,
                    "term_id": term_ids[name],
                }
            )

    custom_analyses: list[dict[str, Any]] = []
    for metric, _, selected, refs in metrics:
        name = str(metric["name"])
        extension = _gsf_extension_data(metric)
        preserved_analysis = preserved["custom_analyses"].get(name, {})
        expression_unchanged = _expression_matches(
            selected, extension.get("ossie_expression")
        )
        full_sql = str(
            extension.get("sql")
            if expression_unchanged and extension.get("sql")
            else _wrap_expression(selected, name, refs, datasets, relationships)
        )
        referenced_column_ids = _sql_column_ids(
            selected, None, refs, datasets, column_ids
        )
        preserved_column_ids = extension.get(
            "sql_column_is", preserved_analysis.get("sql_column_is")
        )
        if expression_unchanged and _all_resolvable_ids(
            preserved_column_ids, column_ids
        ):
            referenced_column_ids = list(preserved_column_ids)
        if not referenced_column_ids:
            referenced_column_ids = _first_resolvable_column_ids(
                refs, datasets, column_ids
            )
        if not referenced_column_ids:
            raise GSFConversionError(
                f"Metric {name!r} has no resolvable catalog column for "
                "custom-analysis SQL validation"
            )
        custom_analyses.append(
            {
                "id": str(
                    extension.get("id")
                    or preserved_analysis.get("id")
                    or _stable_id("custom-analysis", str(model["name"]), name)
                ),
                "name": name,
                "description": str(metric.get("description") or ""),
                "sql": full_sql,
                "sql_column_is": referenced_column_ids,
            }
        )

    if native:
        foreign_keys, joins, semantic_fks = _reconcile_native_relationships(
            native,
            represented_table_ids=set(table_ids.values()),
            foreign_keys=foreign_keys,
            joins=joins,
            semantic_fks=semantic_fks,
        )

    output = {
        "data_layer": {
            "databases": databases,
            "foreign_keys": foreign_keys,
            "joins": joins,
        },
        "semantic_layer": {
            "terms": terms,
            "semantic_fks": semantic_fks,
            "sql_attributes": sql_groups,
            "custom_analyses": custom_analyses,
        },
        "zones": deepcopy(native.get("zones") or []) if native else [],
    }
    return _dump_yaml(output)


def convert_gsf_to_ossie(
    gsf_yaml: str,
    *,
    model_name: str | None = None,
) -> str:
    """Convert a native ``GsfModelDocument`` to one Apache Ossie model."""
    root = _parse_gsf(gsf_yaml)
    catalog = _read_catalog(root)
    dialects = _dialects_by_database(root)
    semantic = root["semantic_layer"]
    terms = semantic["terms"]
    if not terms:
        raise GSFConversionError(
            "GSF document has no representable terms; Ossie requires at least "
            "one dataset"
        )

    term_by_id: dict[str, dict[str, Any]] = {}
    term_id_by_name: dict[str, str] = {}
    datasets_by_table: dict[str, list[str]] = defaultdict(list)
    datasets: list[dict[str, Any]] = []
    fields_by_term: dict[str, list[dict[str, Any]]] = defaultdict(list)
    field_names_by_term: dict[str, set[str]] = defaultdict(set)
    term_columns: dict[str, set[str]] = defaultdict(set)
    attr_owner: dict[str, tuple[str, dict[str, Any]]] = {}

    for term in terms:
        term_id = _required_id(term, "GSF term")
        if term_id in term_by_id:
            raise GSFConversionError(f"Duplicate GSF term id {term_id!r}")
        represents = term.get("represents") or []
        if len(represents) != 1:
            raise GSFConversionError(
                f"Converter supports only GSF terms that represent exactly one "
                f"table; term {term.get('name')!r} represents {len(represents)}"
            )
        table_id = str(represents[0])
        table = catalog["tables"].get(table_id)
        if table is None:
            raise GSFConversionError(
                f"GSF term {term.get('name')!r} represents unknown table {table_id!r}"
            )
        name = str(term.get("name") or "")
        if not name:
            raise GSFConversionError("Every GSF term needs a non-empty name")
        if name in term_id_by_name:
            raise GSFConversionError(f"Duplicate GSF term name {name!r}")
        term_id_by_name[name] = term_id
        datasets_by_table[table_id].append(name)
        term_by_id[term_id] = term
        dataset: dict[str, Any] = {
            "name": name,
            "source": ".".join(table["source"]),
        }
        if term.get("description"):
            dataset["description"] = str(term["description"])
        if table["item"].get("pk"):
            dataset["primary_key"] = list(table["item"]["pk"])
        unique_columns = [
            str(column.get("name"))
            for column in table["item"].get("columns") or []
            if column.get("is_unique") and column.get("name")
        ]
        if unique_columns:
            dataset["unique_keys"] = [[column] for column in unique_columns]

        for attribute in term.get("columns_attributes") or []:
            if not isinstance(attribute, dict) or not attribute.get("id"):
                raise GSFConversionError(
                    f"Term {name!r} contains a column attribute without an id"
                )
            column_id = str(attribute.get("column_id") or "")
            column = catalog["columns"].get(column_id)
            if column is None:
                raise GSFConversionError(
                    f"Column attribute {attribute.get('name')!r} references "
                    f"unknown column {column_id!r}"
                )
            field_name = str(attribute.get("name") or column["name"])
            if field_name in field_names_by_term[term_id]:
                raise GSFConversionError(
                    f"Duplicate field name {field_name!r} in GSF term {name!r}"
                )
            field_names_by_term[term_id].add(field_name)
            term_columns[name].add(str(column["name"]))
            field: dict[str, Any] = {
                "name": field_name,
                "expression": _ossie_expression(str(column["name"])),
            }
            datatype = _ossie_datatype(column["item"].get("type"))
            if datatype:
                field["datatype"] = datatype
            if attribute.get("description"):
                field["description"] = str(attribute["description"])
            fields_by_term[term_id].append(field)
            attr_owner[str(attribute["id"])] = (term_id, attribute)

        datasets.append(dataset)

    for source_group in _SQL_GROUPS:
        for attribute in semantic["sql_attributes"][source_group]:
            attribute_id = _required_id(attribute, "GSF SQL attribute")
            term_id = str(attribute.get("term_id") or "")
            if term_id not in term_by_id:
                raise GSFConversionError(
                    f"SQL attribute {attribute.get('name')!r} references "
                    f"unknown term {term_id!r}"
                )
            sql = str(attribute.get("sql") or "")
            name = str(attribute.get("name") or "")
            if not name or not sql:
                raise GSFConversionError(
                    "Every GSF SQL attribute requires non-empty name and sql"
                )
            term_name = str(term_by_id[term_id].get("name") or "")
            if name in field_names_by_term[term_id]:
                raise GSFConversionError(
                    f"Duplicate field name {name!r} in GSF term {term_name!r}"
                )
            field_names_by_term[term_id].add(name)
            represented_table_id = str(term_by_id[term_id]["represents"][0])
            sql_databases = _validate_gsf_sql_databases(
                f"SQL attribute {name!r}",
                sql,
                attribute.get("sql_column_is") or [],
                catalog,
                attached_table_id=represented_table_id,
            )
            ossie_expression = _expression_from_sql(sql)
            field = {
                "name": name,
                "expression": _ossie_expression(
                    ossie_expression,
                    _ossie_dialect(dialects, sql_databases),
                ),
                "custom_extensions": [
                    _gsf_extension(
                        {
                            "entity": "sql_attribute",
                            "id": attribute_id,
                            "sql": sql,
                            "sql_source": source_group,
                            "sql_column_is": list(attribute.get("sql_column_is") or []),
                            "term_id": term_id,
                            "ossie_expression": ossie_expression,
                        }
                    )
                ],
            }
            if attribute.get("description"):
                field["description"] = str(attribute["description"])
            fields_by_term[term_id].append(field)

    for dataset in datasets:
        term_id = term_id_by_name[dataset["name"]]
        if fields_by_term[term_id]:
            dataset["fields"] = fields_by_term[term_id]

    metrics: list[dict[str, Any]] = []
    for analysis in semantic.get("custom_analyses") or []:
        analysis_id = _required_id(analysis, "GSF custom analysis")
        sql = str(analysis.get("sql") or "")
        name = str(analysis.get("name") or "")
        if not name or not sql:
            raise GSFConversionError(
                "Every GSF custom analysis requires non-empty name and sql"
            )
        sql_databases = _validate_gsf_sql_databases(
            f"Custom analysis {name!r}",
            sql,
            analysis.get("sql_column_is") or [],
            catalog,
        )
        ossie_expression = _expression_from_sql(sql)
        metric: dict[str, Any] = {
            "name": name,
            "expression": _ossie_expression(
                ossie_expression,
                _ossie_dialect(dialects, sql_databases),
            ),
            "custom_extensions": [
                _gsf_extension(
                    {
                        "entity": "custom_analysis",
                        "id": analysis_id,
                        "sql": sql,
                        "sql_column_is": list(analysis.get("sql_column_is") or []),
                        "ossie_expression": ossie_expression,
                    }
                )
            ],
        }
        if analysis.get("description"):
            metric["description"] = str(analysis["description"])
        metrics.append(metric)

    relationships = _relationships_from_gsf(
        root,
        catalog,
        datasets_by_table,
        term_columns,
        attr_owner,
        term_by_id,
    )
    database_names = {
        source[0]
        for source in (table["source"] for table in catalog["tables"].values())
    }
    inferred_name = (
        model_name
        or (next(iter(database_names)) if len(database_names) == 1 else None)
        or "gsf_model"
    )
    semantic_model: dict[str, Any] = {
        "name": inferred_name,
        "datasets": datasets,
        "custom_extensions": [
            _gsf_extension(
                {
                    "model_name": inferred_name,
                    "native_document": root,
                }
            )
        ],
    }
    if relationships:
        semantic_model["relationships"] = relationships
    if metrics:
        semantic_model["metrics"] = metrics
    return _dump_yaml({"version": OSSIE_VERSION, "semantic_model": [semantic_model]})


def _build_catalog(
    datasets: Mapping[str, dict[str, Any]],
    preserved: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, str], dict[tuple[str, str], str]]:
    db_tree: dict[str, dict[str, dict[str, Any]]] = {}
    table_ids: dict[str, str] = {}
    column_ids: dict[tuple[str, str], str] = {}
    source_groups: dict[tuple[str, str, str], list[tuple[str, dict[str, Any]]]] = (
        defaultdict(list)
    )
    for dataset_name, context in datasets.items():
        source_groups[_source_key(context["source"])].append((dataset_name, context))

    for source_key, contexts in source_groups.items():
        database, schema, table = source_key
        preserved_table = preserved["tables"].get(source_key, {})
        preserved_schema = preserved["schemas"].get((database, schema), {})
        preserved_db = preserved["databases"].get(database, {})
        db_entry = db_tree.setdefault(
            database,
            {
                "id": str(preserved_db.get("id") or _stable_id("database", database)),
                "dialect": str(preserved_db.get("dialect") or ""),
                "schemas": {},
            },
        )
        schema_entry = db_entry["schemas"].setdefault(
            schema,
            {
                "id": str(
                    preserved_schema.get("id") or _stable_id("schema", database, schema)
                ),
                "name": schema,
                "database_name": database,
                "tables": [],
            },
        )
        table_id = str(
            preserved_table.get("id") or _stable_id("table", database, schema, table)
        )
        for dataset_name, _ in contexts:
            table_ids[dataset_name] = table_id
        pk = list(
            dict.fromkeys(
                str(value)
                for _, context in contexts
                for value in context["original"].get("primary_key") or []
            )
        )
        unique_keys = [
            [str(value) for value in key]
            for _, context in contexts
            for key in context["original"].get("unique_keys") or []
        ]
        catalog_columns = list(
            dict.fromkeys(
                column for _, context in contexts for column in context["columns"]
            )
        )
        # A field states the logical type of the column behind it, which is the
        # only type information an Ossie-origin catalog has to offer.
        declared_types: dict[str, str] = {}
        for _, context in contexts:
            for field, column_name in context["simple_fields"]:
                sql_type = _gsf_column_type(field.get("datatype"))
                if sql_type:
                    declared_types.setdefault(column_name, sql_type)

        columns: list[dict[str, Any]] = []
        for column_name in catalog_columns:
            preserved_column = preserved["columns"].get((*source_key, column_name), {})
            column_id = str(
                preserved_column.get("id")
                or _stable_id("column", database, schema, table, column_name)
            )
            for dataset_name, _ in contexts:
                column_ids[(dataset_name, column_name)] = column_id
            single_unique = [column_name] in unique_keys or (
                len(pk) == 1 and pk[0] == column_name
            )
            columns.append(
                {
                    "id": column_id,
                    "name": column_name,
                    "description": str(preserved_column.get("description") or ""),
                    "type": str(
                        preserved_column.get("type")
                        or declared_types.get(column_name, "")
                    ),
                    "sample_values": list(preserved_column.get("sample_values") or []),
                    "is_nullable": bool(
                        preserved_column.get("is_nullable", column_name not in pk)
                    ),
                    "is_unique": bool(preserved_column.get("is_unique", single_unique)),
                }
            )
        schema_entry["tables"].append(
            {
                "id": table_id,
                "name": table,
                "description": str(
                    preserved_table.get("description")
                    if preserved_table
                    else next(
                        (
                            context["original"].get("description")
                            for _, context in contexts
                            if context["original"].get("description")
                        ),
                        "",
                    )
                ),
                "pk": pk,
                "type": str(preserved_table.get("type") or ""),
                "columns": columns,
            }
        )

    result: list[dict[str, Any]] = []
    for database in sorted(db_tree):
        db_entry = db_tree[database]
        schemas = [db_entry["schemas"][name] for name in sorted(db_entry["schemas"])]
        for schema in schemas:
            schema["tables"].sort(key=lambda item: (item["name"], item["id"]))
        result.append(
            {
                "id": db_entry["id"],
                "dialect": db_entry["dialect"],
                "schemas": schemas,
            }
        )
    return result, table_ids, column_ids


def _merge_catalog_databases(
    preserved: Any,
    generated: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep catalog objects that Ossie cannot represent directly."""
    result = deepcopy(generated)
    databases_by_id = {str(item["id"]): item for item in result}
    for preserved_database in preserved or []:
        if not isinstance(preserved_database, dict):
            continue
        database_id = str(preserved_database.get("id") or "")
        database = databases_by_id.get(database_id)
        if database is None:
            copied = deepcopy(preserved_database)
            result.append(copied)
            databases_by_id[database_id] = copied
            continue
        schemas_by_id = {
            str(item["id"]): item for item in database.get("schemas") or []
        }
        for preserved_schema in preserved_database.get("schemas") or []:
            schema_id = str(preserved_schema.get("id") or "")
            schema = schemas_by_id.get(schema_id)
            if schema is None:
                database["schemas"].append(deepcopy(preserved_schema))
                continue
            tables_by_id = {
                str(item["id"]): item for item in schema.get("tables") or []
            }
            for preserved_table in preserved_schema.get("tables") or []:
                table_id = str(preserved_table.get("id") or "")
                table = tables_by_id.get(table_id)
                if table is None:
                    schema["tables"].append(deepcopy(preserved_table))
                    continue
                known_column_ids = {
                    str(item["id"]) for item in table.get("columns") or []
                }
                table["columns"].extend(
                    deepcopy(column)
                    for column in preserved_table.get("columns") or []
                    if str(column.get("id") or "") not in known_column_ids
                )
    return result


def _catalog_column_ids(databases: Iterable[Mapping[str, Any]]) -> set[str]:
    return {
        str(column["id"])
        for database in databases
        for schema in database.get("schemas") or []
        for table in schema.get("tables") or []
        for column in table.get("columns") or []
        if column.get("id")
    }


def _index_native_document(root: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "databases": {},
        "schemas": {},
        "tables": {},
        "columns": {},
        "terms": {},
        "column_attributes": {},
        "sql_attributes": {},
        "custom_analyses": {},
    }
    if not root:
        return result
    catalog = _read_native_catalog(root)
    for database in root["data_layer"].get("databases") or []:
        database_names = {
            str(schema.get("database_name") or "")
            for schema in database.get("schemas") or []
            if schema.get("database_name")
        }
        for name in database_names:
            result["databases"][name] = database
        for schema in database.get("schemas") or []:
            database_name = str(schema.get("database_name") or "")
            schema_name = str(schema.get("name") or "")
            result["schemas"][(database_name, schema_name)] = schema
            for table in schema.get("tables") or []:
                source = (database_name, schema_name, str(table.get("name") or ""))
                result["tables"][source] = table
                for column in table.get("columns") or []:
                    result["columns"][(*source, str(column.get("name") or ""))] = column
    table_source = {
        table_id: tuple(table["source"])
        for table_id, table in catalog["tables"].items()
    }
    for term in root["semantic_layer"].get("terms") or []:
        represents = term.get("represents") or []
        if len(represents) != 1 or str(represents[0]) not in table_source:
            continue
        source = table_source[str(represents[0])]
        term_name = str(term.get("name") or "")
        result["terms"][(term_name, source)] = term
        for attribute in term.get("columns_attributes") or []:
            column = catalog["columns"].get(str(attribute.get("column_id") or ""))
            if column:
                result["column_attributes"][
                    (
                        term_name,
                        str(attribute.get("name") or ""),
                        source,
                        column["name"],
                    )
                ] = attribute
    term_names = {
        str(term.get("id")): str(term.get("name") or "")
        for term in root["semantic_layer"].get("terms") or []
    }
    for group in _SQL_GROUPS:
        for attribute in (root["semantic_layer"].get("sql_attributes") or {}).get(
            group, []
        ):
            item = dict(attribute)
            item["source_group"] = group
            result["sql_attributes"][
                (
                    term_names.get(str(attribute.get("term_id") or ""), ""),
                    str(attribute.get("name") or ""),
                )
            ] = item
    for analysis in root["semantic_layer"].get("custom_analyses") or []:
        result["custom_analyses"][str(analysis.get("name") or "")] = analysis
    return result


def _read_native_catalog(root: dict[str, Any]) -> dict[str, Any]:
    """Read the catalog out of a preserved native snapshot.

    Both the indexing and the relationship-reconciliation paths go through
    here so a hand-edited extension fails the same way in either, rather than
    silently dropping the preserved identifiers in one of them.
    """
    try:
        return _read_catalog(root)
    except GSFConversionError as exc:
        raise GSFConversionError(
            f"Malformed {NVIDIA_GSF_VENDOR} 'native_document' extension: {exc}"
        ) from exc


def _read_catalog(root: dict[str, Any]) -> dict[str, Any]:
    tables: dict[str, dict[str, Any]] = {}
    columns: dict[str, dict[str, Any]] = {}
    database_ids: set[str] = set()
    schema_ids: set[str] = set()
    for database in root["data_layer"]["databases"]:
        database_id = _required_id(database, "GSF database")
        if database_id in database_ids:
            raise GSFConversionError(f"Duplicate GSF database id {database_id!r}")
        database_ids.add(database_id)
        for schema in database.get("schemas") or []:
            schema_id = _required_id(schema, "GSF schema")
            if schema_id in schema_ids:
                raise GSFConversionError(f"Duplicate GSF schema id {schema_id!r}")
            schema_ids.add(schema_id)
            database_name = str(schema.get("database_name") or "")
            schema_name = str(schema.get("name") or "")
            if not database_name:
                raise GSFConversionError(
                    f"GSF schema {schema_name!r} requires database_name"
                )
            for table in schema.get("tables") or []:
                table_id = str(table.get("id") or "")
                if not table_id or table_id in tables:
                    raise GSFConversionError(
                        f"Every GSF table needs a globally unique id; got {table_id!r}"
                    )
                table_name = str(table.get("name") or "")
                tables[table_id] = {
                    "item": table,
                    "source": (database_name, schema_name, table_name),
                }
                for column in table.get("columns") or []:
                    column_id = str(column.get("id") or "")
                    if not column_id or column_id in columns:
                        raise GSFConversionError(
                            "Every GSF column needs a globally unique id; "
                            f"got {column_id!r}"
                        )
                    columns[column_id] = {
                        "item": column,
                        "name": str(column.get("name") or ""),
                        "table_id": table_id,
                    }
    return {"tables": tables, "columns": columns}


def _validate_gsf_sql_databases(
    context: str,
    sql: str,
    sql_column_ids: Iterable[Any],
    catalog: Mapping[str, Any],
    *,
    attached_table_id: str | None = None,
) -> set[str]:
    """Validate the SQL resolves to one database, and return the databases."""
    databases: set[str] = set()
    if attached_table_id:
        table = catalog["tables"].get(attached_table_id)
        if table:
            databases.add(str(table["source"][0]))
    for column_id in sql_column_ids:
        column = catalog["columns"].get(str(column_id))
        if column:
            databases.add(str(catalog["tables"][column["table_id"]]["source"][0]))

    parsed = _parse_sql(sql)
    for sql_table in parsed.find_all(exp.Table) if parsed is not None else []:
        if sql_table.catalog:
            databases.add(sql_table.catalog)
        matches = [
            table
            for table in catalog["tables"].values()
            if sql_table.name == table["source"][2]
            and (not sql_table.db or sql_table.db == table["source"][1])
            and (not sql_table.catalog or sql_table.catalog == table["source"][0])
            and (sql_table.catalog or not databases or table["source"][0] in databases)
        ]
        databases.update(str(table["source"][0]) for table in matches)
    if len(databases) > 1:
        raise GSFConversionError(
            f"{context} spans multiple databases ({', '.join(sorted(databases))}); "
            "the GSF importer validates each SQL object against one database"
        )
    return databases


def _relationships_from_gsf(
    root: dict[str, Any],
    catalog: Mapping[str, Any],
    datasets_by_table: Mapping[str, list[str]],
    term_columns: Mapping[str, set[str]],
    attr_owner: Mapping[str, tuple[str, dict[str, Any]]],
    term_by_id: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    pairs: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    covered_fk_pairs: set[tuple[str, str]] = set()
    for join in root["data_layer"].get("joins") or []:
        source_table_id = str(join.get("source_table_id") or "")
        target_table_id = str(join.get("target_table_id") or "")
        if (
            source_table_id not in datasets_by_table
            or target_table_id not in datasets_by_table
        ):
            continue
        source_columns: list[tuple[str, str]] = []
        for item in join.get("join_columns") or []:
            if not isinstance(item, dict):
                continue
            source_name = _join_column_name(
                item.get("source"), source_table_id, catalog
            )
            target_name = _join_column_name(
                item.get("target"), target_table_id, catalog
            )
            if source_name and target_name:
                source_columns.append((source_name, target_name))
        if not source_columns:
            source_columns = _fk_columns_for_tables(
                root, source_table_id, target_table_id, catalog
            )
        if source_columns:
            key = (
                _relationship_dataset(
                    source_table_id,
                    [source for source, _ in source_columns],
                    datasets_by_table,
                    term_columns,
                ),
                _relationship_dataset(
                    target_table_id,
                    [target for _, target in source_columns],
                    datasets_by_table,
                    term_columns,
                ),
            )
            pairs[key].extend(source_columns)
            covered_fk_pairs.add((source_table_id, target_table_id))

    fk_groups: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for foreign_key in root["data_layer"].get("foreign_keys") or []:
        source = catalog["columns"].get(str(foreign_key.get("source_column_id") or ""))
        target = catalog["columns"].get(str(foreign_key.get("target_column_id") or ""))
        if not source or not target:
            continue
        table_pair = (source["table_id"], target["table_id"])
        if table_pair in covered_fk_pairs:
            continue
        if table_pair[0] in datasets_by_table and table_pair[1] in datasets_by_table:
            fk_groups[table_pair].append((source["name"], target["name"]))
    for table_pair, columns in fk_groups.items():
        source_name = _relationship_dataset(
            table_pair[0],
            [source for source, _ in columns],
            datasets_by_table,
            term_columns,
        )
        target_name = _relationship_dataset(
            table_pair[1],
            [target for _, target in columns],
            datasets_by_table,
            term_columns,
        )
        pairs[(source_name, target_name)].extend(columns)

    for semantic_fk in root["semantic_layer"].get("semantic_fks") or []:
        source = catalog["columns"].get(str(semantic_fk.get("column_id") or ""))
        owner = attr_owner.get(str(semantic_fk.get("column_attribute_id") or ""))
        if not source or not owner:
            continue
        target_term_id, target_attr = owner
        target_column = catalog["columns"].get(str(target_attr.get("column_id") or ""))
        target_term = term_by_id.get(target_term_id)
        if not target_column or not target_term:
            continue
        if source["table_id"] not in datasets_by_table:
            continue
        from_name = _relationship_dataset(
            source["table_id"],
            [source["name"]],
            datasets_by_table,
            term_columns,
        )
        to_name = str(target_term.get("name") or "")
        if not to_name:
            continue
        pair = (source["name"], target_column["name"])
        if pair not in pairs[(from_name, to_name)]:
            pairs[(from_name, to_name)].append(pair)

    relationships: list[dict[str, Any]] = []
    used_names: dict[str, int] = defaultdict(int)
    for (from_name, to_name), columns in pairs.items():
        unique_columns = list(dict.fromkeys(columns))
        base_name = f"{from_name}_to_{to_name}"
        used_names[base_name] += 1
        suffix = "" if used_names[base_name] == 1 else f"_{used_names[base_name]}"
        relationships.append(
            {
                "name": base_name + suffix,
                "from": from_name,
                "to": to_name,
                "from_columns": [source for source, _ in unique_columns],
                "to_columns": [target for _, target in unique_columns],
            }
        )
    return relationships


def _relationship_dataset(
    table_id: str,
    columns: list[str],
    datasets_by_table: Mapping[str, list[str]],
    term_columns: Mapping[str, set[str]],
) -> str:
    candidates = datasets_by_table.get(table_id) or []
    if len(candidates) == 1:
        return candidates[0]
    matching = [
        name for name in candidates if set(columns) <= term_columns.get(name, set())
    ]
    if len(matching) == 1:
        return matching[0]
    raise GSFConversionError(
        f"Cannot map relationship on table {table_id!r} and columns "
        f"{', '.join(columns)} to exactly one represented term; candidates: "
        f"{', '.join(candidates) or 'none'}"
    )


def _fk_columns_for_tables(
    root: Mapping[str, Any],
    source_table_id: str,
    target_table_id: str,
    catalog: Mapping[str, Any],
) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for foreign_key in root["data_layer"].get("foreign_keys") or []:
        source = catalog["columns"].get(str(foreign_key.get("source_column_id") or ""))
        target = catalog["columns"].get(str(foreign_key.get("target_column_id") or ""))
        if (
            source
            and target
            and source["table_id"] == source_table_id
            and target["table_id"] == target_table_id
        ):
            result.append((source["name"], target["name"]))
    return result


def _join_column_name(
    value: Any,
    table_id: str,
    catalog: Mapping[str, Any],
) -> str | None:
    text = str(value or "")
    column = catalog["columns"].get(text)
    if column and column["table_id"] == table_id:
        return str(column["name"])
    for item in catalog["columns"].values():
        if item["table_id"] == table_id and item["name"] == text:
            return text
    return None


def _required_id(item: Any, context: str) -> str:
    if not isinstance(item, dict) or not item.get("id"):
        raise GSFConversionError(f"{context} requires a non-empty id")
    return str(item["id"])


def _parse_gsf(value: str) -> dict[str, Any]:
    root = _load_yaml(value, "GSF")
    expected = {"data_layer", "semantic_layer", "zones"}
    unknown = sorted(set(root) - expected)
    if unknown:
        raise GSFConversionError(
            "Unsupported GSF root properties: " + ", ".join(unknown)
        )
    root.setdefault("data_layer", {})
    root.setdefault("semantic_layer", {})
    root.setdefault("zones", [])
    for key in ("data_layer", "semantic_layer"):
        if not isinstance(root[key], dict):
            raise GSFConversionError(f"GSF {key!r} must be a mapping")
    if not isinstance(root["zones"], list):
        raise GSFConversionError("GSF 'zones' must be a list")
    data_layer = root["data_layer"]
    semantic_layer = root["semantic_layer"]
    for key in ("databases", "foreign_keys", "joins"):
        data_layer.setdefault(key, [])
        if not isinstance(data_layer.get(key), list):
            raise GSFConversionError(f"GSF data_layer.{key} must be a list")
    for key in ("terms", "semantic_fks", "custom_analyses"):
        semantic_layer.setdefault(key, [])
        if not isinstance(semantic_layer.get(key), list):
            raise GSFConversionError(f"GSF semantic_layer.{key} must be a list")
    semantic_layer.setdefault("sql_attributes", {})
    sql_attributes = semantic_layer["sql_attributes"]
    if not isinstance(sql_attributes, dict):
        raise GSFConversionError("GSF semantic_layer.sql_attributes must be a mapping")
    for key in _SQL_GROUPS:
        sql_attributes.setdefault(key, [])
        if not isinstance(sql_attributes.get(key), list):
            raise GSFConversionError(
                f"GSF semantic_layer.sql_attributes.{key} must be a list"
            )
    return root


def _parse_ossie(value: str) -> tuple[dict[str, Any], dict[str, Any]]:
    root = _load_yaml(value, "Ossie")
    unknown = sorted(set(root) - {"version", "semantic_model"})
    if unknown:
        raise GSFConversionError(
            "Unsupported Ossie root properties: " + ", ".join(unknown)
        )
    _check_ossie_version(root.get("version"))
    models = root.get("semantic_model")
    if not isinstance(models, list) or len(models) != 1:
        raise GSFConversionError("Ossie input must contain exactly one semantic model")
    model = models[0]
    if not isinstance(model, dict) or not model.get("name"):
        raise GSFConversionError("Ossie semantic model requires a name")
    return root, model


def _check_ossie_version(value: Any) -> None:
    """Accept any Ossie version in the supported major.minor series."""
    series = re.match(r"^\s*(\d+)\.(\d+)", str(value or ""))
    if not series or (int(series.group(1)), int(series.group(2))) != OSSIE_SERIES:
        expected = ".".join(str(part) for part in OSSIE_SERIES)
        raise GSFConversionError(
            f"Unsupported Ossie version {value!r}; expected {expected}.x"
        )


def _load_yaml(value: str, label: str) -> dict[str, Any]:
    try:
        root = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        raise GSFConversionError(f"Invalid {label} YAML: {exc}") from exc
    if not isinstance(root, dict):
        raise GSFConversionError(f"Invalid {label} YAML: expected a root mapping")
    return root


def _validate_relationship(
    relationship: Any,
    datasets: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(relationship, dict) or not relationship.get("name"):
        raise GSFConversionError("Every Ossie relationship needs a name")
    from_name = relationship.get("from")
    to_name = relationship.get("to")
    if from_name not in datasets or to_name not in datasets:
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
    return relationship


def _normalize_expressions(value: Any, name: str) -> list[dict[str, str]]:
    if not isinstance(value, dict):
        raise GSFConversionError(f"{name!r} has no valid expression")
    dialects = value.get("dialects")
    if not isinstance(dialects, list) or not dialects:
        raise GSFConversionError(f"{name!r} requires at least one expression dialect")
    result = [
        {
            "dialect": str(item["dialect"]),
            "expression": str(item["expression"]),
        }
        for item in dialects
        if isinstance(item, dict)
        and item.get("dialect")
        and item.get("expression") is not None
    ]
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


def _field_table_refs(
    field: Mapping[str, Any],
    expression: str,
    owner: str,
    datasets: Mapping[str, Any],
) -> list[str]:
    extension = _gsf_extension_data(field)
    extension_refs = extension.get("table_refs")
    expression_unchanged = _expression_matches(
        expression, extension.get("ossie_expression")
    )
    reference_sql = (
        str(extension["sql"])
        if extension.get("sql") and expression_unchanged
        else expression
    )
    use_extension_refs = not extension.get("entity") or expression_unchanged
    if use_extension_refs and isinstance(extension_refs, list) and extension_refs:
        refs = [str(item) for item in extension_refs]
        _validate_refs(refs, datasets, str(field.get("name")))
        _validate_single_database_refs(
            refs,
            datasets,
            f"SQL attribute {field.get('name')!r}",
            sql=reference_sql,
        )
        return refs
    refs = _referenced_datasets(reference_sql, datasets)
    result = list(dict.fromkeys([owner, *refs]))
    _validate_single_database_refs(
        result,
        datasets,
        f"SQL attribute {field.get('name')!r}",
        sql=reference_sql,
    )
    return result


def _metric_table_refs(
    metric: Mapping[str, Any],
    expression: str,
    datasets: Mapping[str, Any],
) -> list[str]:
    extension = _gsf_extension_data(metric)
    extension_refs = extension.get("table_refs")
    expression_unchanged = _expression_matches(
        expression, extension.get("ossie_expression")
    )
    reference_sql = (
        str(extension["sql"])
        if extension.get("sql") and expression_unchanged
        else expression
    )
    use_extension_refs = not extension.get("entity") or expression_unchanged
    if use_extension_refs and isinstance(extension_refs, list) and extension_refs:
        refs = [str(item) for item in extension_refs]
        _validate_refs(refs, datasets, str(metric.get("name")))
        _validate_single_database_refs(
            refs,
            datasets,
            f"Custom analysis {metric.get('name')!r}",
            sql=reference_sql,
        )
        return refs
    refs = _referenced_datasets(reference_sql, datasets)
    if refs:
        _validate_single_database_refs(
            refs,
            datasets,
            f"Custom analysis {metric.get('name')!r}",
            sql=reference_sql,
        )
        return refs
    if len(datasets) == 1:
        refs = [next(iter(datasets))]
        _validate_single_database_refs(
            refs,
            datasets,
            f"Custom analysis {metric.get('name')!r}",
            sql=reference_sql,
        )
        return refs
    if extension.get("entity") == "custom_analysis" and extension.get("sql"):
        refs = [next(iter(datasets))]
        _validate_single_database_refs(
            refs,
            datasets,
            f"Custom analysis {metric.get('name')!r}",
            sql=reference_sql,
        )
        return refs
    raise GSFConversionError(
        f"Metric {metric.get('name')!r} does not identify a source dataset; "
        "qualify a referenced column or add NVIDIA_GSF table_refs"
    )


def _validate_refs(
    refs: Iterable[str],
    datasets: Mapping[str, Any],
    name: str,
) -> None:
    unknown = [ref for ref in refs if ref not in datasets]
    if unknown:
        raise GSFConversionError(
            f"{name!r} has unknown NVIDIA_GSF table_refs: {', '.join(unknown)}"
        )


def _validate_single_database_refs(
    refs: Iterable[str],
    datasets: Mapping[str, Any],
    context: str,
    *,
    sql: str,
) -> None:
    databases = {
        str(datasets[ref]["source"]["database"]) for ref in refs if ref in datasets
    }
    databases.update(table.catalog for table in _sql_tables(sql) if table.catalog)
    if len(databases) > 1:
        raise GSFConversionError(
            f"{context} spans multiple databases ({', '.join(sorted(databases))}); "
            "the GSF importer validates each SQL object against one database"
        )


def _referenced_datasets(
    sql: str,
    datasets: Mapping[str, Any],
) -> list[str]:
    references: list[str] = []
    parsed = _parse_sql(sql)
    if parsed is None:
        return references
    for table in parsed.find_all(exp.Table):
        matches = [
            name
            for name, context in datasets.items()
            if table.name == str(context["source"]["table"])
            and (not table.db or table.db == str(context["source"]["schema"]))
            and (
                not table.catalog or table.catalog == str(context["source"]["database"])
            )
        ]
        if matches:
            match = matches[0]
            if match not in references:
                references.append(match)
    for column in parsed.find_all(exp.Column):
        qualifier = column.table
        if not qualifier:
            continue
        matches = [
            name
            for name, context in datasets.items()
            if qualifier in (name, str(context["source"]["table"]))
        ]
        if len(matches) == 1 and matches[0] not in references:
            references.append(matches[0])
    return references


def _collect_expression_columns(
    sql: str,
    owner: str | None,
    refs: list[str],
    datasets: Mapping[str, dict[str, Any]],
    preserved: Mapping[str, Any],
) -> None:
    for column in _sql_columns(sql):
        dataset_name = _column_dataset(column, owner, refs, datasets)
        if not dataset_name:
            continue
        context = datasets[dataset_name]
        source_key = _source_key(context["source"])
        known_table = source_key in preserved["tables"]
        if known_table and (*source_key, column.name) not in preserved["columns"]:
            # A GSF-sourced catalog is authoritative: an identifier that is not
            # already a column of the table is SQL syntax, not physical data.
            continue
        _add_catalog_column(context, column.name)


def _sql_column_ids(
    sql: str,
    owner: str | None,
    refs: list[str],
    datasets: Mapping[str, dict[str, Any]],
    column_ids: Mapping[tuple[str, str], str],
) -> list[str]:
    result: list[str] = []
    for column in _sql_columns(sql):
        dataset_name = _column_dataset(column, owner, refs, datasets)
        column_id = (
            column_ids.get((dataset_name, column.name)) if dataset_name else None
        )
        if column_id and column_id not in result:
            result.append(column_id)
    return result


def _column_dataset(
    column: exp.Column,
    owner: str | None,
    refs: list[str],
    datasets: Mapping[str, Any],
) -> str | None:
    qualifier = column.table
    if qualifier:
        matches = [
            name
            for name, context in datasets.items()
            if name in refs and qualifier in (name, str(context["source"]["table"]))
        ]
        return matches[0] if len(matches) == 1 else None
    if owner:
        return owner
    return refs[0] if len(refs) == 1 else None


def _sql_columns(sql: str) -> list[exp.Column]:
    parsed = _parse_sql(sql)
    if parsed is None:
        return []
    return [
        column
        for column in parsed.find_all(exp.Column)
        if not _is_date_part_unit(column)
    ]


def _sql_tables(sql: str) -> list[exp.Table]:
    parsed = _parse_sql(sql)
    return list(parsed.find_all(exp.Table)) if parsed is not None else []


def _is_date_part_unit(column: exp.Column) -> bool:
    """Report whether a parsed column is really a date-part keyword.

    ``DATEDIFF(day, a, b)`` puts the unit where sqlglot parses an unqualified
    column, which would otherwise be mistaken for a physical catalog column.
    Only the unit slot itself counts, so a column genuinely named ``day``
    elsewhere in the same call still resolves as data.
    """
    parent = column.parent
    if column.table or column.name.lower() not in _DATE_PART_UNITS:
        return False
    if isinstance(parent, exp.Anonymous):
        if str(parent.this).lower() not in _UNIT_FIRST_FUNCTIONS:
            return False
        arguments = parent.args.get("expressions") or []
        return bool(arguments) and arguments[0] is column
    if isinstance(parent, _UNIT_IN_THIS_FUNCTIONS):
        # MySQL's two-argument DATEDIFF(ended, started) has no unit, so its
        # first argument is data rather than a keyword.
        return column.arg_key == "this" and _argument_count(parent) >= 3
    return False


def _argument_count(func: exp.Expression) -> int:
    return sum(1 for value in func.args.values() if value is not None)


def _parse_sql(sql: str) -> exp.Expression | None:
    """Parse *sql*, trying each candidate dialect, or return ``None``.

    Preserved GSF SQL carries whatever dialect its connection reported, so a
    single parser is not enough. SQL that no candidate can parse is treated as
    opaque: it is still carried through verbatim, and only the parse-derived
    enrichment (column and table discovery) is skipped.
    """
    for dialect in _SQL_DIALECTS:
        try:
            return parse_one(sql, dialect=dialect or None)
        except (ParseError, TokenError, ValueError):
            continue
    return None


def _first_resolvable_column_ids(
    refs: list[str],
    datasets: Mapping[str, dict[str, Any]],
    column_ids: Mapping[tuple[str, str], str],
) -> list[str]:
    for ref in refs:
        for column in datasets[ref]["columns"]:
            column_id = column_ids.get((ref, column))
            if column_id:
                return [column_id]
    return []


def _wrap_expression(
    expression: str,
    name: str,
    refs: list[str],
    datasets: Mapping[str, dict[str, Any]],
    relationships: list[dict[str, Any]],
) -> str:
    stripped = expression.strip()
    if stripped.upper().startswith(("SELECT ", "SELECT\n", "WITH ", "WITH\n")):
        return stripped
    if not refs:
        raise GSFConversionError(f"Cannot determine a source table for {name!r}")
    anchor_name = refs[0]
    from_sql = (
        f"{_qualified_table(datasets[anchor_name]['source'])} "
        f"AS {_quote_identifier(anchor_name)}"
    )
    joined = {anchor_name}
    remaining = set(refs[1:])
    joins: list[str] = []
    while remaining:
        matched = False
        for relationship in relationships:
            left = str(relationship["from"])
            right = str(relationship["to"])
            if left in joined and right in remaining:
                new_name = right
            elif right in joined and left in remaining:
                new_name = left
            else:
                continue
            conditions = [
                f"{_quote_identifier(left)}.{_quote_identifier(str(left_column))} = "
                f"{_quote_identifier(right)}.{_quote_identifier(str(right_column))}"
                for left_column, right_column in zip(
                    relationship["from_columns"],
                    relationship["to_columns"],
                    strict=True,
                )
            ]
            joins.append(
                f"JOIN {_qualified_table(datasets[new_name]['source'])} "
                f"AS {_quote_identifier(new_name)} ON {' AND '.join(conditions)}"
            )
            joined.add(new_name)
            remaining.remove(new_name)
            matched = True
            break
        if not matched:
            missing = min(remaining)
            raise GSFConversionError(
                f"{name!r} references disconnected dataset {missing!r}; "
                "declare a relationship connecting all referenced datasets"
            )
    suffix = f" {' '.join(joins)}" if joins else ""
    return f"SELECT {stripped} AS {_quote_identifier(name)} FROM {from_sql}{suffix}"


def _expression_from_sql(sql: str) -> str:
    parsed = _parse_sql(sql)
    if parsed is None:
        return sql
    select = parsed.find(exp.Select)
    if select is None or not select.expressions:
        return sql
    expression = select.expressions[0]
    if isinstance(expression, exp.Alias):
        expression = expression.this
    return expression.sql()


def _expression_matches(current: str, emitted: Any) -> bool:
    if not isinstance(emitted, str) or not emitted.strip():
        return False
    parsed_current = _parse_sql(current)
    parsed_emitted = _parse_sql(emitted)
    if parsed_current is None or parsed_emitted is None:
        return current.strip() == emitted.strip()
    return parsed_current.sql() == parsed_emitted.sql()


def _ossie_expression(expression: str, dialect: str = "ANSI_SQL") -> dict[str, Any]:
    return {
        "dialects": [
            {
                "dialect": dialect,
                "expression": expression,
            }
        ]
    }


def _ossie_datatype(sql_type: Any) -> str | None:
    """Map a GSF column's physical type onto Ossie's logical vocabulary.

    A type Ossie cannot name becomes ``Opaque``, as the spec prescribes for a
    known type outside the portable vocabulary. An absent type stays unset
    rather than being guessed.
    """
    base, scale = _split_sql_type(sql_type)
    if not base:
        return None
    datatype = _OSSIE_DATATYPE_BY_SQL_TYPE.get(base)
    if datatype is None:
        return "Opaque"
    if datatype == "Decimal" and scale == 0:
        # NUMBER(38,0) and friends are exact integers.
        return "Integer"
    return datatype


def _split_sql_type(sql_type: Any) -> tuple[str, int | None]:
    """Split a physical type into its base name and declared scale."""
    text = " ".join(str(sql_type or "").upper().split())
    if not text:
        return "", None
    scale: int | None = None
    parameters = re.search(r"\(([^)]*)\)", text)
    if parameters:
        parts = [part.strip() for part in parameters.group(1).split(",")]
        if len(parts) > 1 and parts[1].isdigit():
            scale = int(parts[1])
    return " ".join(re.sub(r"\([^)]*\)", " ", text).split()), scale


def _gsf_column_type(datatype: Any) -> str:
    """Map an Ossie logical datatype onto a physical type for a new column."""
    return _SQL_TYPE_BY_OSSIE_DATATYPE.get(str(datatype or ""), "")


def _dialects_by_database(root: Mapping[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for database in root["data_layer"].get("databases") or []:
        dialect = str(database.get("dialect") or "")
        if not dialect:
            continue
        for schema in database.get("schemas") or []:
            name = str(schema.get("database_name") or "")
            if name:
                result[name] = dialect
    return result


def _ossie_dialect(dialects: Mapping[str, str], databases: Iterable[str]) -> str:
    """Map a GSF connection dialect onto the Ossie dialect enum.

    Ossie names only a few dialects, so anything else stays ANSI_SQL rather
    than being labelled inaccurately.
    """
    names = {str(dialects.get(database, "")).lower() for database in databases}
    labels = {
        _GSF_TO_OSSIE_DIALECT[name] for name in names if name in _GSF_TO_OSSIE_DIALECT
    }
    return labels.pop() if len(labels) == 1 else "ANSI_SQL"


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
        raise GSFConversionError("Every dataset needs a source")
    if value.upper().startswith(("SELECT ", "SELECT\n", "WITH ", "WITH\n")):
        raise GSFConversionError("GSF terms must identify physical tables")
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


def _source_key(source: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(str(source[key]) for key in ("database", "schema", "table"))  # type: ignore[return-value]


def _add_catalog_column(context: dict[str, Any], name: str) -> None:
    if name and name not in context["column_set"]:
        context["column_set"].add(name)
        context["columns"].append(name)


def _all_resolvable_ids(
    values: Any,
    column_ids: Mapping[tuple[str, str], str],
) -> bool:
    return (
        isinstance(values, list)
        and bool(values)
        and all(str(value) in column_ids.values() for value in values)
    )


def _reconcile_native_relationships(
    native: dict[str, Any],
    *,
    represented_table_ids: set[str],
    foreign_keys: list[dict[str, str]],
    joins: list[dict[str, Any]],
    semantic_fks: list[dict[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, Any]], list[dict[str, str]]]:
    catalog = _read_native_catalog(native)
    native_joins = [
        item
        for item in native.get("data_layer", {}).get("joins") or []
        if not (
            str(item.get("source_table_id") or "") in represented_table_ids
            and str(item.get("target_table_id") or "") in represented_table_ids
        )
    ]
    native_foreign_keys = []
    for item in native.get("data_layer", {}).get("foreign_keys") or []:
        source = catalog["columns"].get(str(item.get("source_column_id") or ""))
        target = catalog["columns"].get(str(item.get("target_column_id") or ""))
        if (
            source
            and target
            and source["table_id"] in represented_table_ids
            and target["table_id"] in represented_table_ids
        ):
            continue
        native_foreign_keys.append(item)

    attribute_tables: dict[str, str] = {}
    for term in native.get("semantic_layer", {}).get("terms") or []:
        represents = term.get("represents") or []
        if len(represents) != 1:
            continue
        for attribute in term.get("columns_attributes") or []:
            if attribute.get("id"):
                attribute_tables[str(attribute["id"])] = str(represents[0])
    native_semantic_fks = []
    for item in native.get("semantic_layer", {}).get("semantic_fks") or []:
        source = catalog["columns"].get(str(item.get("column_id") or ""))
        target_table_id = attribute_tables.get(
            str(item.get("column_attribute_id") or "")
        )
        if (
            source
            and source["table_id"] in represented_table_ids
            and target_table_id in represented_table_ids
        ):
            continue
        native_semantic_fks.append(item)

    return (
        _merge_records(native_foreign_keys, foreign_keys),
        _merge_records(native_joins, joins),
        _merge_records(native_semantic_fks, semantic_fks),
    )


def _merge_records(
    preserved: Any, generated: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = [deepcopy(item) for item in preserved or [] if isinstance(item, dict)]
    serialized = {json.dumps(item, sort_keys=True) for item in result}
    for item in generated:
        marker = json.dumps(item, sort_keys=True)
        if marker not in serialized:
            result.append(item)
            serialized.add(marker)
    return result


def _stable_id(kind: str, *parts: str) -> str:
    return str(uuid5(_ID_NAMESPACE, "/".join((kind, *map(str, parts)))))


def _gsf_extension_data(item: Mapping[str, Any]) -> dict[str, Any]:
    for extension in item.get("custom_extensions") or []:
        if not isinstance(extension, dict):
            continue
        if extension.get("vendor_name") not in GSF_VENDOR_ALIASES:
            continue
        try:
            data = json.loads(str(extension.get("data") or "{}"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _native_snapshot(model: Mapping[str, Any]) -> dict[str, Any] | None:
    snapshot = _gsf_extension_data(model).get("native_document")
    return snapshot if isinstance(snapshot, dict) else None


def _gsf_extension(data: Mapping[str, Any]) -> dict[str, str]:
    return {
        "vendor_name": NVIDIA_GSF_VENDOR,
        "data": json.dumps(data, separators=(",", ":"), sort_keys=True),
    }


def _dump_yaml(value: dict[str, Any]) -> str:
    return yaml.safe_dump(
        value,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert Apache Ossie YAML and native NVIDIA GSF model YAML"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    export_parser = subparsers.add_parser(
        "export",
        help="Convert Ossie YAML to a native GSF model document",
    )
    export_parser.add_argument("-i", "--input", type=Path, required=True)
    export_parser.add_argument("-o", "--output", type=Path)
    export_parser.add_argument(
        "--database-name",
        help="Default database for Ossie schema.table sources",
    )
    import_parser = subparsers.add_parser(
        "import",
        help="Convert a native GSF model document to Ossie YAML",
    )
    import_parser.add_argument("-i", "--input", type=Path, required=True)
    import_parser.add_argument("-o", "--output", type=Path)
    import_parser.add_argument(
        "--name",
        help="Override the inferred Ossie semantic-model name",
    )
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
            output = convert_gsf_to_ossie(source, model_name=args.name)
        if args.output is None:
            print(output, end="")
        else:
            args.output.write_text(output, encoding="utf-8")
    except (GSFConversionError, OSError, UnicodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
