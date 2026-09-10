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
"""Convert Lightdash semantic definitions into an Ossie document.

The input is a dbt ``schema.yml``-shaped dictionary whose ``meta`` blocks
carry Lightdash dimensions, metrics and joins. Structural information becomes
first-class Ossie vocabulary (datasets, fields, metrics, relationships);
Lightdash presentation attributes without Ossie vocabulary (``format``,
``round``, ``group_label``, ``hidden``, ...) are preserved in
``custom_extensions`` entries with ``vendor_name: LIGHTDASH`` so that the
export direction can reproduce them exactly.
"""

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from ossie import (
    OssieCustomExtension,
    OssieDataset,
    OssieDialect,
    OssieDialectExpression,
    OssieDimension,
    OssieDocument,
    OssieExpression,
    OssieField,
    OssieMetric,
    OssieRelationship,
    OssieSemanticModel,
)

from ossie_lightdash.converter_issues import (
    ConverterIssue,
    ConverterIssueType,
    ConverterResult,
)
from ossie_lightdash.catalog import Catalog, warehouse_type_to_datatype
from ossie_lightdash.datatype_utils import (
    datatype_to_lightdash_type,
    lightdash_type_to_datatype,
    metric_datatype,
)
from ossie_lightdash.expression_utils import (
    AGGREGATE_TYPES,
    build_aggregation,
    has_non_portable_reference,
    lightdash_sql_to_ossie,
    qualify_bare_columns,
)

LIGHTDASH_VENDOR_NAME = "LIGHTDASH"

# Keys that are structurally encoded in Ossie vocabulary and therefore must NOT
# be duplicated into the extension (a stale copy would win on export).
_STRUCTURAL_METRIC_KEYS = {"sql", "description", "ai_hint", "name", "model"}
_STRUCTURAL_DIMENSION_KEYS = {"label", "sql", "ai_hint", "hidden"}
_STRUCTURAL_JOIN_KEYS = {"join", "sql_on"}
# Model meta with Ossie vocabulary; everything else is stashed on the dataset.
_HANDLED_MODEL_KEYS = {"metrics", "joins", "primary_key", "ai_hint", "sql_from"}
_HANDLED_COLUMN_KEYS = {"dimension", "metrics"}
# Model meta that changes query results, not just presentation.
_ROW_FILTER_KEYS = ("sql_filter", "sql_where", "required_filters")

_JOIN_PAIR_RE = re.compile(
    r"\$\{(\w+)\.(\w+)\}\s*=\s*\$\{(\w+)\.(\w+)\}",
)


class _Edge:
    """A relationship derived from a join, before de-duplication."""

    def __init__(self, from_model, to_model, from_columns, to_columns, reference, extras):
        self.from_model = from_model
        self.to_model = to_model
        self.from_columns = from_columns
        self.to_columns = to_columns
        self.reference = reference
        self.extras = extras

    @property
    def columns_key(self):
        return (self.from_model, self.to_model, tuple(self.from_columns), tuple(self.to_columns))

    @property
    def key(self):
        # An aliased join is a distinct relationship even on the same columns.
        return (*self.columns_key, self.reference)


def _expression(expression: str, dialect: OssieDialect) -> OssieExpression:
    return OssieExpression(
        dialects=[OssieDialectExpression(dialect=dialect, expression=expression)]
    )


def _lightdash_extension(data: Dict[str, Any]) -> List[OssieCustomExtension]:
    if not data:
        return []
    return [
        OssieCustomExtension(
            vendor_name=LIGHTDASH_VENDOR_NAME,
            data=json.dumps(data, ensure_ascii=False, sort_keys=True),
        )
    ]


def _ai_context(ai_hint: Any) -> Optional[str]:
    """Lightdash `ai_hint` (a string or a list of strings) as Ossie `ai_context`."""
    if isinstance(ai_hint, list):
        return "\n".join(str(hint) for hint in ai_hint) or None
    if isinstance(ai_hint, str):
        return ai_hint or None
    return None


def _primary_key(primary_key: Any) -> Optional[List[str]]:
    if isinstance(primary_key, str):
        return [primary_key]
    if isinstance(primary_key, list) and primary_key:
        return [str(column) for column in primary_key]
    return None


def _merge_meta(base: Any, override: Any) -> Dict[str, Any]:
    """Deep-merge two meta blocks; keys in ``override`` win."""
    merged: Dict[str, Any] = dict(base) if isinstance(base, dict) else {}
    if isinstance(override, dict):
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge_meta(merged[key], value)
            else:
                merged[key] = value
    return merged


def lightdash_meta(node: Dict[str, Any]) -> Dict[str, Any]:
    """The Lightdash meta of a dbt model or column.

    dbt 1.10+ moved ``meta`` under ``config``; Lightdash reads both and lets
    ``config.meta`` win, so the converter merges them the same way.
    """
    return _merge_meta(node.get("meta"), (node.get("config") or {}).get("meta"))


def _unique_name(base: str, used: Set[str]) -> str:
    name = base
    suffix = 2
    while name in used:
        name = f"{base}_{suffix}"
        suffix += 1
    used.add(name)
    return name


class _ModelContext:
    """Expression rewriting for one model: alias resolution, metric inlining
    and non-portable reference detection, with the issues they raise."""

    def __init__(
        self,
        dataset_name: str,
        aliases: Dict[str, str],
        definitions: Dict[str, Tuple[Dict[str, Any], Optional[str]]],
        issues: List[ConverterIssue],
    ) -> None:
        self.dataset_name = dataset_name
        self.aliases = aliases
        self.definitions = definitions
        self.issues = issues
        self.column_types: Dict[str, str] = {}
        self.column_names: List[str] = []
        # Datatypes from --catalog, consulted when a column has no authored type.
        self.catalog_datatypes: Dict[str, "OssieDataType"] = {}
        self._expressions: Dict[str, Optional[str]] = {}
        self._resolving: Set[str] = set()

    def rewrite(self, sql: str, element_name: str) -> Optional[str]:
        """Rewrite Lightdash SQL into an Ossie expression, or None (with an
        issue) when it references parameters or user attributes."""
        if has_non_portable_reference(sql):
            self.issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.EXPRESSION_NOT_PORTABLE,
                    element_name=element_name,
                )
            )
            return None
        result = lightdash_sql_to_ossie(
            sql,
            self.dataset_name,
            aliases=self.aliases,
            resolve_metric=self.metric_expression,
        )
        result.expression = qualify_bare_columns(
            result.expression, self.dataset_name, self.column_names
        )
        for _ in result.inlined_metrics:
            self.issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.METRIC_REFERENCE_INLINED,
                    element_name=element_name,
                )
            )
        for _ in result.flattened_aliases:
            self.issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.ALIAS_REFERENCE_FLATTENED,
                    element_name=element_name,
                )
            )
        return result.expression

    def metric_expression(self, name: str) -> Optional[str]:
        """The Ossie expression of one of this model's metrics, or None when the
        name is not a metric, the metric is not portable, or it references
        itself."""
        if name not in self.definitions:
            return None
        if name in self._expressions:
            return self._expressions[name]
        if name in self._resolving:
            return None
        self._resolving.add(name)
        definition, column = self.definitions[name]
        expression = self._build_expression(name, definition, column)
        self._resolving.discard(name)
        self._expressions[name] = expression
        return expression

    def _build_expression(
        self, name: str, definition: Dict[str, Any], column: Optional[str]
    ) -> Optional[str]:
        sql = definition.get("sql")
        if sql:
            inner = self.rewrite(sql, name)
            if inner is None:
                return None
        elif column is not None:
            inner = f"{self.dataset_name}.{column}"
        else:
            return None
        lightdash_type = definition.get("type", "number")
        return (
            build_aggregation(lightdash_type, inner, definition.get("percentile"))
            or inner
        )


class LightdashToOssieConverter:
    """Converts a Lightdash-flavoured dbt schema.yml dict into an OssieDocument.

    Lightdash SQL is written for the project's warehouse; ``dialect`` labels the
    emitted expressions accordingly (``ANSI_SQL`` when the warehouse has no
    Ossie dialect, e.g. Postgres or Redshift).
    """

    def __init__(self, dialect: OssieDialect = OssieDialect.ANSI_SQL) -> None:
        self._dialect = dialect

    def convert(
        self,
        schema_yml: Dict[str, Any],
        *,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        semantic_model_name: str = "lightdash_semantic_model",
        catalog: Optional[Catalog] = None,
    ) -> ConverterResult[OssieDocument]:
        issues: List[ConverterIssue] = []
        datasets: List[OssieDataset] = []
        metrics: List[OssieMetric] = []
        relationships: List[OssieRelationship] = []
        relationship_names: Set[str] = set()
        metric_names: Set[str] = set()
        direct_edges: List[_Edge] = []
        derived_edges: List[_Edge] = []

        # Seeds are tables to Lightdash just like models.
        nodes = [*(schema_yml.get("models") or []), *(schema_yml.get("seeds") or [])]
        for model in nodes:
            dataset, model_metrics, model_direct, model_derived = self._convert_model(
                model,
                database=database,
                schema=schema,
                issues=issues,
                metric_names=metric_names,
                catalog=catalog,
            )
            datasets.append(dataset)
            metrics.extend(model_metrics)
            direct_edges.extend(model_direct)
            derived_edges.extend(model_derived)

        # Edges a model declares itself come first, so an edge that a chained
        # join merely passes through does not shadow the declared one.
        dataset_names = {dataset.name for dataset in datasets}
        seen_edges: Set[tuple] = set()
        seen_columns: Set[tuple] = set()
        for edge in [*direct_edges, *derived_edges]:
            if edge.to_model not in dataset_names or edge.from_model not in dataset_names:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.JOIN_TARGET_UNKNOWN,
                        element_name=f"{edge.from_model} -> {edge.to_model}",
                    )
                )
                continue
            derived = edge in derived_edges
            # A derived edge is redundant with any declared edge on the same
            # columns; a declared edge is redundant only with an identical one.
            if (derived and edge.columns_key in seen_columns) or edge.key in seen_edges:
                continue
            seen_edges.add(edge.key)
            seen_columns.add(edge.columns_key)
            relationships.append(
                OssieRelationship.model_validate(
                    {
                        "name": _unique_name(
                            f"{edge.from_model}_to_{edge.reference}", relationship_names
                        ),
                        "from": edge.from_model,
                        "to": edge.to_model,
                        "from_columns": edge.from_columns,
                        "to_columns": edge.to_columns,
                        "custom_extensions": _lightdash_extension(edge.extras) or None,
                    }
                )
            )

        document = OssieDocument(
            version="0.2.0.dev0",
            semantic_model=[
                OssieSemanticModel(
                    name=semantic_model_name,
                    datasets=datasets,
                    metrics=metrics or None,
                    relationships=relationships or None,
                )
            ],
        )
        return ConverterResult(output=document, issues=issues)

    def _convert_model(
        self,
        model: Dict[str, Any],
        *,
        database: Optional[str],
        schema: Optional[str],
        issues: List[ConverterIssue],
        metric_names: Set[str],
        catalog: Optional[Catalog] = None,
    ) -> Tuple[OssieDataset, List[OssieMetric], List[_Edge], List[_Edge]]:
        name = model["name"]
        model_meta = lightdash_meta(model)
        # A model that names its own relation (Lightdash model files, or
        # `meta.sql_from` in dbt) is the source verbatim; otherwise the source
        # is assembled from the flags.
        if isinstance(model_meta.get("sql_from"), str) and model_meta["sql_from"].strip():
            source = model_meta["sql_from"].strip()
        else:
            source = ".".join(part for part in [database, schema, name] if part)
            if schema is None:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.SOURCE_UNQUALIFIED,
                        element_name=name,
                    )
                )

        joins = model_meta.get("joins") or []
        aliases = {
            join["alias"]: join["join"]
            for join in joins
            if join.get("alias") and join.get("join")
        }

        # Metric definitions are collected before any SQL is rewritten so that
        # `${metric}` references can be inlined.
        definitions: Dict[str, Tuple[Dict[str, Any], Optional[str]]] = {}
        for column in model.get("columns") or []:
            column_meta = lightdash_meta(column)
            for metric_name, definition in (column_meta.get("metrics") or {}).items():
                definitions[metric_name] = (definition, column["name"])
        for metric_name, definition in (model_meta.get("metrics") or {}).items():
            definitions[metric_name] = (definition, None)

        context = _ModelContext(name, aliases, definitions, issues)
        context.column_names = [column["name"] for column in model.get("columns") or []]
        if catalog is not None:
            if name in catalog:
                for column_name, warehouse_type in catalog[name].items():
                    datatype = warehouse_type_to_datatype(warehouse_type)
                    if datatype is not None:
                        context.catalog_datatypes[column_name] = datatype
            else:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.CATALOG_MODEL_MISSING,
                        element_name=name,
                    )
                )
        for column in model.get("columns") or []:
            dimension_meta = lightdash_meta(column).get("dimension") or {}
            # Authored types are intent and win; the catalog fills the gaps.
            if dimension_meta.get("type"):
                context.column_types[column["name"]] = dimension_meta["type"]
            elif column["name"].lower() in context.catalog_datatypes:
                lightdash_type = datatype_to_lightdash_type(
                    context.catalog_datatypes[column["name"].lower()]
                )
                if lightdash_type is not None:
                    context.column_types[column["name"]] = lightdash_type

        fields: List[OssieField] = []
        for column in model.get("columns") or []:
            field = self._convert_column(column, context)
            if field is not None:
                fields.append(field)

        metrics: List[OssieMetric] = []
        for metric_name, (definition, column_name) in definitions.items():
            metric = self._convert_metric(
                metric_name, definition, column_name, context, metric_names
            )
            if metric is not None:
                metrics.append(metric)

        direct_edges, derived_edges, stashed_joins = self._convert_joins(
            joins, from_model=name, aliases=aliases, issues=issues
        )

        # Model meta without Ossie vocabulary, and joins Ossie cannot reproduce
        # exactly, travel on the dataset so export restores the explore as is.
        stash = {
            key: value for key, value in model_meta.items() if key not in _HANDLED_MODEL_KEYS
        }
        if stashed_joins:
            stash["joins"] = stashed_joins
        if any(model_meta.get(key) for key in _ROW_FILTER_KEYS):
            issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.ROW_FILTER_NOT_PORTABLE,
                    element_name=name,
                )
            )

        dataset = OssieDataset(
            name=name,
            source=source,
            description=model.get("description"),
            primary_key=_primary_key(model_meta.get("primary_key")),
            ai_context=_ai_context(model_meta.get("ai_hint")),
            fields=fields or None,
            custom_extensions=_lightdash_extension(stash) or None,
        )
        return dataset, metrics, direct_edges, derived_edges

    def _convert_column(
        self, column: Dict[str, Any], context: _ModelContext
    ) -> Optional[OssieField]:
        column_name = column["name"]
        column_meta = lightdash_meta(column)
        dimension_meta = column_meta.get("dimension")

        expression = column_name
        datatype = None
        label: Optional[str] = None
        ai_context: Optional[str] = None
        extension_data: Dict[str, Any] = {}
        # Every dbt column is a Lightdash dimension unless it is hidden; a
        # hidden column is the closest Lightdash comes to a measure-only field.
        hidden = bool((dimension_meta or {}).get("hidden"))
        dimension: Optional[OssieDimension] = None if hidden else OssieDimension()
        if dimension_meta is None:
            datatype = context.catalog_datatypes.get(column_name.lower())
        if dimension_meta is not None:
            label = dimension_meta.get("label")
            ai_context = _ai_context(dimension_meta.get("ai_hint"))
            if dimension_meta.get("sql"):
                rewritten = context.rewrite(dimension_meta["sql"], column_name)
                if rewritten is None:
                    return None
                # `${TABLE}.col` on column `col` is the plain column.
                expression = (
                    column_name
                    if rewritten == f"{context.dataset_name}.{column_name}"
                    else rewritten
                )
            datatype = lightdash_type_to_datatype(dimension_meta.get("type"))
            if datatype is None:
                datatype = context.catalog_datatypes.get(column_name.lower())
            # `is_time` is a role marker in Ossie, not a type. Lightdash's only
            # role marker is `time_intervals: OFF`, which withdraws a temporal
            # column from the time axis; otherwise `is_time` is left unset so
            # the datatype decides.
            excluded = set(_STRUCTURAL_DIMENSION_KEYS)
            time_intervals = dimension_meta.get("time_intervals")
            if not hidden and (time_intervals is False or time_intervals == "OFF"):
                dimension = OssieDimension(is_time=False)
                excluded.add("time_intervals")
            extension_data = {
                key: value
                for key, value in dimension_meta.items()
                if key not in excluded
            }
        column_extras = {
            key: value for key, value in column_meta.items() if key not in _HANDLED_COLUMN_KEYS
        }
        if column_extras:
            extension_data["column_meta"] = column_extras

        return OssieField(
            name=column_name,
            expression=_expression(expression, self._dialect),
            dimension=dimension,
            datatype=datatype,
            label=label,
            description=column.get("description"),
            ai_context=ai_context,
            custom_extensions=_lightdash_extension(extension_data) or None,
        )

    def _convert_metric(
        self,
        metric_name: str,
        definition: Dict[str, Any],
        column: Optional[str],
        context: _ModelContext,
        metric_names: Set[str],
    ) -> Optional[OssieMetric]:
        if column is None and not definition.get("sql"):
            context.issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.METRIC_SQL_MISSING,
                    element_name=metric_name,
                )
            )
            return None
        expression = context.metric_expression(metric_name)
        if expression is None:
            return None
        if definition.get("filters"):
            context.issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.METRIC_FILTER_NOT_PORTABLE,
                    element_name=metric_name,
                )
            )

        # Typed aggregations (and their percentile) are recovered from the
        # expression on export; only types an expression cannot encode
        # (`boolean`, `string`, `date`, ...) travel in the extension.
        lightdash_type = definition.get("type", "number")
        excluded = set(_STRUCTURAL_METRIC_KEYS)
        if lightdash_type == "number" or lightdash_type in AGGREGATE_TYPES:
            excluded.add("type")
        if lightdash_type == "percentile":
            excluded.add("percentile")
        extension_data = {
            key: value for key, value in definition.items() if key not in excluded
        }
        # Lightdash scopes metric names per model; Ossie scopes them per
        # semantic model. The Ossie name is Lightdash's own field id,
        # `<model>_<metric>`, and the bare name travels in the extension so
        # export restores it exactly.
        extension_data["name"] = metric_name
        extension_data["model"] = context.dataset_name
        qualified = f"{context.dataset_name}_{metric_name}"
        ossie_name = _unique_name(qualified, metric_names)
        if ossie_name != qualified:
            context.issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.METRIC_NAME_COLLISION,
                    element_name=metric_name,
                )
            )
        return OssieMetric(
            name=ossie_name,
            expression=_expression(expression, self._dialect),
            datatype=metric_datatype(
                lightdash_type,
                context.column_types.get(column) if not definition.get("sql") else None,
            ),
            description=definition.get("description"),
            ai_context=_ai_context(definition.get("ai_hint")),
            custom_extensions=_lightdash_extension(extension_data) or None,
        )

    @staticmethod
    def _convert_joins(
        joins: List[Dict[str, Any]],
        *,
        from_model: str,
        aliases: Dict[str, str],
        issues: List[ConverterIssue],
    ) -> Tuple[List[_Edge], List[_Edge], List[Dict[str, Any]]]:
        """Relationships and stashed joins for one model's explore.

        A pair ``${M.x} = ${T.y}`` on model M's join to T is a direct edge
        M -> T. A pair through another model already joined in the explore,
        ``${A.x} = ${T.y}``, is a chained join: the edge A -> T is derived and
        the join itself is stashed verbatim, since Ossie relationships cannot
        say which explore includes it. A join whose ``sql_on`` the export
        direction would not rebuild identically (extra conditions, expression
        joins) is stashed the same way.
        """
        direct: List[_Edge] = []
        derived: List[_Edge] = []
        stashed: List[Dict[str, Any]] = []
        joined = {from_model}
        for join in joins:
            to_model = join.get("join")
            if not to_model:
                stashed.append(join)
                continue
            reference = join.get("alias") or to_model
            joined.add(reference)
            pairs = _JOIN_PAIR_RE.findall(join.get("sql_on") or "")
            direct_columns: Tuple[List[str], List[str]] = ([], [])
            chained: Dict[str, Tuple[List[str], List[str]]] = {}
            for left_table, left_column, right_table, right_column in pairs:
                if left_table == reference:
                    other, other_column, target_column = right_table, right_column, left_column
                elif right_table == reference:
                    other, other_column, target_column = left_table, left_column, right_column
                else:
                    continue
                if other == from_model:
                    direct_columns[0].append(other_column)
                    direct_columns[1].append(target_column)
                elif other in joined:
                    columns = chained.setdefault(other, ([], []))
                    columns[0].append(other_column)
                    columns[1].append(target_column)
            extras = {
                key: value for key, value in join.items() if key not in _STRUCTURAL_JOIN_KEYS
            }
            if direct_columns[0]:
                direct.append(
                    _Edge(from_model, to_model, *direct_columns, reference, extras)
                )
            for other, (other_columns, target_columns) in chained.items():
                derived.append(
                    _Edge(aliases.get(other, other), to_model, other_columns, target_columns, to_model, {})
                )
            # Pair order and side order are not semantic: `${T.y} = ${M.x}`
            # rebuilds as `${M.x} = ${T.y}` without needing a stash.
            rebuilt_pairs = {
                (from_model, left, reference, right) for left, right in zip(*direct_columns)
            }
            original_pairs = {
                (a, b, c, d) if a == from_model else (c, d, a, b)
                for a, b, c, d in pairs
            }
            residue = _JOIN_PAIR_RE.sub("", join.get("sql_on") or "")
            reproducible = (
                original_pairs == rebuilt_pairs
                and not residue.replace("AND", "").replace("and", "").strip()
            )
            if not direct_columns[0] and not chained:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.JOIN_SQL_UNPARSED,
                        element_name=f"{from_model} -> {to_model}",
                    )
                )
                stashed.append(join)
            elif chained or not reproducible:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.JOIN_STASHED,
                        element_name=f"{from_model} -> {to_model}",
                    )
                )
                stashed.append(join)
        return direct, derived, stashed
