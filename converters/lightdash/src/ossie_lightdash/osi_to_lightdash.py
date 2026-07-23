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

"""Convert an OSI document into Lightdash semantic definitions.

The output is a dbt ``schema.yml``-shaped dictionary whose ``meta`` blocks
carry Lightdash dimensions, metrics and joins, ready to be merged into a dbt
project that Lightdash reads. Lightdash-specific presentation attributes that
have no OSI vocabulary round-trip through ``custom_extensions`` entries with
``vendor_name: "lightdash"``; their keys are overlaid onto the generated
definitions and therefore always win.
"""

import json
from typing import Any, Dict, List, Optional

from ossie import OSIDataset, OSIDialect, OSIDocument, OSIMetric, OSISemanticModel

from ossie_lightdash.converter_issues import (
    ConverterIssue,
    ConverterIssueType,
    ConverterResult,
)
from ossie_lightdash.expression_utils import (
    osi_sql_to_lightdash,
    parse_simple_aggregation,
    qualifier_of,
    referenced_datasets,
    strip_qualifier,
)

LIGHTDASH_VENDOR_NAME = "lightdash"


def _pick_expression(osi_expression: Any, dialect: OSIDialect) -> str:
    """Return the expression for the preferred dialect (fallback: first available)."""
    for dialect_expression in osi_expression.dialects:
        if dialect_expression.dialect is dialect:
            return dialect_expression.expression
    return osi_expression.dialects[0].expression if osi_expression.dialects else ""


def _lightdash_extension_data(element: Any, issues: List[ConverterIssue]) -> Dict[str, Any]:
    """Return the ``lightdash`` vendor extension data of an OSI element, if any."""
    data: Dict[str, Any] = {}
    for extension in element.custom_extensions or []:
        if extension.vendor_name == LIGHTDASH_VENDOR_NAME:
            try:
                data.update(json.loads(extension.data))
            except (TypeError, ValueError):
                pass
        else:
            issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.FOREIGN_EXTENSION_IGNORED,
                    element_name=getattr(element, "name", "<unnamed>"),
                )
            )
    return data


def _model_name_for(dataset: OSIDataset) -> str:
    """A Lightdash table is addressed by its dbt model name = the source's table part."""
    return dataset.source.rsplit(".", 1)[-1]


class OSIToLightdashConverter:
    """Converts an OSIDocument into a Lightdash-flavoured dbt schema.yml dict."""

    def __init__(self, dialect: OSIDialect = OSIDialect.ANSI_SQL) -> None:
        self._dialect = dialect

    def convert(self, document: OSIDocument) -> ConverterResult[Dict[str, Any]]:
        issues: List[ConverterIssue] = []
        models: List[Dict[str, Any]] = []
        for semantic_model in document.semantic_model:
            models.extend(self._convert_semantic_model(semantic_model, issues))
        return ConverterResult(output={"version": 2, "models": models}, issues=issues)

    def _convert_semantic_model(
        self, semantic_model: OSISemanticModel, issues: List[ConverterIssue]
    ) -> List[Dict[str, Any]]:
        datasets = semantic_model.datasets or []
        dataset_names = {dataset.name for dataset in datasets}
        model_name_by_dataset = {
            dataset.name: _model_name_for(dataset) for dataset in datasets
        }

        models_by_dataset: Dict[str, Dict[str, Any]] = {}
        columns_by_dataset: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for dataset in datasets:
            model, columns = self._convert_dataset(dataset, issues)
            models_by_dataset[dataset.name] = model
            columns_by_dataset[dataset.name] = columns

        for metric in semantic_model.metrics or []:
            self._convert_metric(
                metric,
                dataset_names,
                models_by_dataset,
                columns_by_dataset,
                issues,
            )

        for relationship in semantic_model.relationships or []:
            from_model = models_by_dataset.get(relationship.from_dataset)
            to_model_name = model_name_by_dataset.get(relationship.to)
            from_model_name = model_name_by_dataset.get(relationship.from_dataset)
            if from_model is None or to_model_name is None:
                continue
            sql_on = " AND ".join(
                f"${{{from_model_name}.{from_column}}} = ${{{to_model_name}.{to_column}}}"
                for from_column, to_column in zip(
                    relationship.from_columns, relationship.to_columns
                )
            )
            joins = from_model.setdefault("meta", {}).setdefault("joins", [])
            joins.append({"join": to_model_name, "sql_on": sql_on})

        return [models_by_dataset[dataset.name] for dataset in datasets]

    def _convert_dataset(
        self, dataset: OSIDataset, issues: List[ConverterIssue]
    ) -> tuple:
        columns_by_name: Dict[str, Dict[str, Any]] = {}
        for field in dataset.fields or []:
            column: Dict[str, Any] = {"name": field.name}
            if field.description:
                column["description"] = field.description

            dimension: Dict[str, Any] = {}
            if field.label:
                dimension["label"] = field.label
            if field.dimension is not None and field.dimension.is_time:
                dimension["type"] = "date"
            expression = _pick_expression(field.expression, self._dialect)
            if expression and expression != field.name:
                dimension["sql"] = osi_sql_to_lightdash(expression, dataset.name)
            dimension.update(_lightdash_extension_data(field, issues))
            # An empty dict still marks dimension-ness: a field OSI declares as a
            # categorical dimension must not degrade to a plain column on export,
            # or the import direction could not reconstruct it.
            if dimension or field.dimension is not None:
                column["meta"] = {"dimension": dimension}
            columns_by_name[field.name] = column

        model: Dict[str, Any] = {"name": _model_name_for(dataset)}
        if dataset.description:
            model["description"] = dataset.description
        model["columns"] = list(columns_by_name.values())
        return model, columns_by_name

    def _convert_metric(
        self,
        metric: OSIMetric,
        dataset_names: set,
        models_by_dataset: Dict[str, Dict[str, Any]],
        columns_by_dataset: Dict[str, Dict[str, Dict[str, Any]]],
        issues: List[ConverterIssue],
    ) -> None:
        expression = _pick_expression(metric.expression, self._dialect)
        extension_data = _lightdash_extension_data(metric, issues)

        target_dataset = self._resolve_target_dataset(expression, dataset_names)
        if target_dataset is None:
            issues.append(
                ConverterIssue(
                    issue_type=ConverterIssueType.CROSS_DATASET_METRIC_DROPPED,
                    element_name=metric.name,
                )
            )
            return

        definition: Dict[str, Any] = {}
        if metric.description:
            definition["description"] = metric.description

        target_column: Optional[str] = None
        parsed = parse_simple_aggregation(expression)
        if parsed is not None:
            lightdash_type, column_ref = parsed
            qualifier = qualifier_of(column_ref)
            if qualifier in (None, target_dataset):
                target_column = strip_qualifier(column_ref)
                definition["type"] = lightdash_type
        if target_column is None or target_column not in columns_by_dataset[target_dataset]:
            definition["type"] = extension_data.get("type", "number")
            definition["sql"] = osi_sql_to_lightdash(expression, target_dataset)
            target_column = None

        definition.update(extension_data)

        if target_column is not None:
            column = columns_by_dataset[target_dataset][target_column]
            metrics = (
                column.setdefault("meta", {}).setdefault("metrics", {})
            )
            metrics[metric.name] = definition
        else:
            model = models_by_dataset[target_dataset]
            metrics = model.setdefault("meta", {}).setdefault("metrics", {})
            metrics[metric.name] = definition

    @staticmethod
    def _resolve_target_dataset(expression: str, dataset_names: set) -> Optional[str]:
        referenced = referenced_datasets(expression, dataset_names)
        if len(referenced) == 1:
            return next(iter(referenced))
        if len(referenced) == 0 and len(dataset_names) == 1:
            return next(iter(dataset_names))
        return None
