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

"""Convert Lightdash semantic definitions into an OSI document.

The input is a dbt ``schema.yml``-shaped dictionary whose ``meta`` blocks
carry Lightdash dimensions, metrics and joins. Structural information becomes
first-class OSI vocabulary (datasets, fields, metrics, relationships);
Lightdash presentation attributes without OSI vocabulary (``format``,
``round``, ``group_label``, ``hidden``, ...) are preserved in
``custom_extensions`` entries with ``vendor_name: "lightdash"`` so that the
export direction can reproduce them exactly.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ossie import (
    OSICustomExtension,
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDimension,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIMetric,
    OSIRelationship,
    OSISemanticModel,
)

from ossie_lightdash.converter_issues import (
    ConverterIssue,
    ConverterIssueType,
    ConverterResult,
)
from ossie_lightdash.expression_utils import (
    build_aggregation,
    lightdash_sql_to_osi,
)

LIGHTDASH_VENDOR_NAME = "lightdash"

# Keys that are structurally encoded in OSI vocabulary and therefore must NOT
# be duplicated into the extension (a stale copy would win on export).
# ``type`` stays in the extension only for metric types whose semantics OSI
# expressions cannot express faithfully (currently ``percentile``).
_STRUCTURAL_METRIC_KEYS = {"sql", "description"}
_STRUCTURAL_DIMENSION_KEYS = {"label", "sql"}

_TIME_DIMENSION_TYPES = {"date", "timestamp"}

_JOIN_PAIR_RE = re.compile(
    r"\$\{(\w+)\.(\w+)\}\s*=\s*\$\{(\w+)\.(\w+)\}",
)


def _ansi(expression: str) -> OSIExpression:
    return OSIExpression(
        dialects=[
            OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression=expression)
        ]
    )


def _type_needs_extension(lightdash_type: str) -> bool:
    """True for metric types an OSI expression cannot encode faithfully.

    ``number`` is fully described by its SQL and typed aggregations are
    recovered by parsing the expression, so only the remaining types
    (currently ``percentile``) must survive inside the extension.
    """
    if lightdash_type == "number":
        return False
    return build_aggregation(lightdash_type, "_", "_") is None


def _lightdash_extension(data: Dict[str, Any]) -> List[OSICustomExtension]:
    if not data:
        return []
    return [
        OSICustomExtension(
            vendor_name=LIGHTDASH_VENDOR_NAME,
            data=json.dumps(data, ensure_ascii=False, sort_keys=True),
        )
    ]


class LightdashToOSIConverter:
    """Converts a Lightdash-flavoured dbt schema.yml dict into an OSIDocument."""

    def convert(
        self,
        schema_yml: Dict[str, Any],
        *,
        database: Optional[str] = None,
        schema: Optional[str] = None,
        semantic_model_name: str = "lightdash_semantic_model",
    ) -> ConverterResult[OSIDocument]:
        issues: List[ConverterIssue] = []
        datasets: List[OSIDataset] = []
        metrics: List[OSIMetric] = []
        relationships: List[OSIRelationship] = []

        for model in schema_yml.get("models") or []:
            dataset, model_metrics, model_relationships = self._convert_model(
                model, database=database, schema=schema, issues=issues
            )
            datasets.append(dataset)
            metrics.extend(model_metrics)
            relationships.extend(model_relationships)

        document = OSIDocument(
            version="0.2.0.dev0",
            semantic_model=[
                OSISemanticModel(
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
    ) -> Tuple[OSIDataset, List[OSIMetric], List[OSIRelationship]]:
        name = model["name"]
        source = ".".join(part for part in [database, schema, name] if part)
        if schema is None:
            issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.SOURCE_UNQUALIFIED,
                    element_name=name,
                )
            )

        fields: List[OSIField] = []
        metrics: List[OSIMetric] = []
        for column in model.get("columns") or []:
            field, column_metrics = self._convert_column(column, dataset_name=name)
            fields.append(field)
            metrics.extend(column_metrics)

        model_meta = model.get("meta") or {}
        for metric_name, definition in (model_meta.get("metrics") or {}).items():
            metrics.append(
                self._convert_sql_metric(metric_name, definition, dataset_name=name)
            )

        relationships = self._convert_joins(
            model_meta.get("joins") or [], from_model=name, issues=issues
        )

        dataset = OSIDataset(
            name=name,
            source=source,
            description=model.get("description"),
            fields=fields or None,
        )
        return dataset, metrics, relationships

    def _convert_column(
        self, column: Dict[str, Any], *, dataset_name: str
    ) -> Tuple[OSIField, List[OSIMetric]]:
        column_name = column["name"]
        meta = column.get("meta") or {}
        dimension_meta = meta.get("dimension")

        expression = column_name
        dimension: Optional[OSIDimension] = None
        label: Optional[str] = None
        extension_data: Dict[str, Any] = {}
        if dimension_meta is not None:
            label = dimension_meta.get("label")
            if dimension_meta.get("sql"):
                expression = lightdash_sql_to_osi(dimension_meta["sql"], dataset_name)
            dimension = OSIDimension(
                is_time=dimension_meta.get("type") in _TIME_DIMENSION_TYPES
            )
            extension_data = {
                key: value
                for key, value in dimension_meta.items()
                if key not in _STRUCTURAL_DIMENSION_KEYS
            }

        field = OSIField(
            name=column_name,
            expression=_ansi(expression),
            dimension=dimension,
            label=label,
            description=column.get("description"),
            custom_extensions=_lightdash_extension(extension_data) or None,
        )

        metrics = [
            self._convert_column_metric(
                metric_name, definition, dataset_name=dataset_name, column=column_name
            )
            for metric_name, definition in (meta.get("metrics") or {}).items()
        ]
        return field, metrics

    def _convert_column_metric(
        self,
        metric_name: str,
        definition: Dict[str, Any],
        *,
        dataset_name: str,
        column: str,
    ) -> OSIMetric:
        lightdash_type = definition.get("type", "number")
        expression = build_aggregation(lightdash_type, dataset_name, column)
        if expression is None:
            sql = definition.get("sql")
            if sql:
                expression = lightdash_sql_to_osi(sql, dataset_name)
            else:
                expression = f"{dataset_name}.{column}"

        return self._build_metric(
            metric_name,
            definition,
            expression=expression,
            keep_type_in_extension=_type_needs_extension(lightdash_type),
        )

    def _convert_sql_metric(
        self, metric_name: str, definition: Dict[str, Any], *, dataset_name: str
    ) -> OSIMetric:
        sql = definition.get("sql") or ""
        expression = lightdash_sql_to_osi(sql, dataset_name)
        return self._build_metric(
            metric_name,
            definition,
            expression=expression,
            keep_type_in_extension=_type_needs_extension(definition.get("type", "number")),
        )

    @staticmethod
    def _build_metric(
        metric_name: str,
        definition: Dict[str, Any],
        *,
        expression: str,
        keep_type_in_extension: bool,
    ) -> OSIMetric:
        excluded = set(_STRUCTURAL_METRIC_KEYS)
        if not keep_type_in_extension:
            excluded.add("type")
        extension_data = {
            key: value for key, value in definition.items() if key not in excluded
        }
        return OSIMetric(
            name=metric_name,
            expression=_ansi(expression),
            description=definition.get("description"),
            custom_extensions=_lightdash_extension(extension_data) or None,
        )

    @staticmethod
    def _convert_joins(
        joins: List[Dict[str, Any]],
        *,
        from_model: str,
        issues: List[ConverterIssue],
    ) -> List[OSIRelationship]:
        relationships: List[OSIRelationship] = []
        for join in joins:
            to_model = join.get("join")
            pairs = _JOIN_PAIR_RE.findall(join.get("sql_on") or "")
            from_columns: List[str] = []
            to_columns: List[str] = []
            for left_table, left_column, right_table, right_column in pairs:
                if left_table == from_model and right_table == to_model:
                    from_columns.append(left_column)
                    to_columns.append(right_column)
                elif left_table == to_model and right_table == from_model:
                    from_columns.append(right_column)
                    to_columns.append(left_column)
            if not to_model or not from_columns:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.JOIN_SQL_UNPARSED,
                        element_name=f"{from_model} -> {to_model or '<unknown>'}",
                    )
                )
                continue
            relationships.append(
                OSIRelationship.model_validate(
                    {
                        "name": f"{from_model}_to_{to_model}",
                        "from": from_model,
                        "to": to_model,
                        "from_columns": from_columns,
                        "to_columns": to_columns,
                    }
                )
            )
        return relationships
