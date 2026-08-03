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

"""Apache Ossie (OSIDocument) -> Sigma data model spec (JSON)."""

from __future__ import annotations

import json
from typing import Any, Optional
from uuid import NAMESPACE_URL, uuid5

from ossie import OSICustomExtension, OSIDataset, OSIDocument, OSIField, OSIMetric, OSIRelationship, OSIVendor

from ossie_sigma.converter_issues import ConverterIssue, ConverterIssueType, ConverterResult
from ossie_sigma.expression_utils import ansi_sql_text, infer_single_dataset_qualifier, sigma_dialect_text
from ossie_sigma.sigma_formula import sql_to_sigma_formula

_ID_NAMESPACE = uuid5(NAMESPACE_URL, "ossie.apache.org/converters/sigma")

_DATATYPE_TO_FORMAT = {
    "String": "string",
    "Integer": "integer",
    "Decimal": "number",
    "Float": "number",
    "Boolean": "boolean",
    "Date": "date",
    "Time": "time",
    "DateTime": "datetime",
    "DateTimeTz": "datetime",
}


def _stable_id(*parts: str) -> str:
    """Deterministic id for an object with no preserved native Sigma id.

    Only used for datasets/fields/relationships that originate purely in Ossie (no
    ``SIGMA`` custom_extensions carrying a native id) — anything previously
    round-tripped through Sigma keeps its real id instead, since Sigma ids are
    referenced by other objects (controls, other data models) that this converter
    cannot see or update.
    """
    return str(uuid5(_ID_NAMESPACE, "/".join(parts))).replace("-", "")


def _sigma_ext(item: Any) -> Optional[dict[str, Any]]:
    for ext in item.custom_extensions or []:
        if ext.vendor_name == OSIVendor.SIGMA.value:
            try:
                return json.loads(ext.data)
            except json.JSONDecodeError:
                return None
    return None


def _resolve_formula(
    expression, dataset_alias: str, element_name: str, issues: list[ConverterIssue]
) -> str:
    """Prefer the native Sigma formula text; otherwise best-effort translate ANSI SQL."""
    native = sigma_dialect_text(expression)
    if native is not None:
        return native

    sql = ansi_sql_text(expression)
    if sql is not None:
        translated = sql_to_sigma_formula(sql, dataset_alias=dataset_alias)
        if translated is not None:
            return translated
        issues.append(
            ConverterIssue(
                ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE,
                element_name,
                f"ANSI SQL expression {sql!r} has no Sigma formula equivalent; "
                "field omitted a formula could not be produced.",
            )
        )
    return ""


class OSIToSigmaConverter:
    """Converts an :class:`OSIDocument` into a Sigma data model spec (as plain JSON)."""

    def convert(self, document: OSIDocument) -> ConverterResult[dict[str, Any]]:
        issues: list[ConverterIssue] = []

        if len(document.semantic_model) > 1:
            issues.append(
                ConverterIssue(
                    ConverterIssueType.CONTROL_ELEMENT_NOT_MODELED,
                    "document",
                    "Sigma data models are single semantic models; only semantic_model[0] "
                    f"was converted, {len(document.semantic_model) - 1} additional model(s) were dropped.",
                )
            )
        model = document.semantic_model[0]
        model_ext = _sigma_ext(model) or {}

        spec: dict[str, Any] = {"kind": "data-model", "name": model.name}
        for key in ("dataModelId", "folderId", "documentVersion", "latestDocumentVersion", "schemaVersion"):
            if key in model_ext:
                spec[key] = model_ext[key]

        pages: dict[str, dict[str, Any]] = {}

        def _page(page_id: Optional[str], page_name: Optional[str]) -> dict[str, Any]:
            key = page_id or "page-default"
            if key not in pages:
                pages[key] = {"id": page_id or _stable_id("page", key), "name": page_name or "Page 1", "elements": []}
            return pages[key]

        dataset_names = {d.name for d in model.datasets}
        dataset_element_id: dict[str, str] = {}
        for dataset in model.datasets:
            ext = _sigma_ext(dataset) or {}
            dataset_element_id[dataset.name] = ext.get("id") or _stable_id("element", dataset.name)

        metrics_by_element: dict[str, list[OSIMetric]] = {}
        for metric in model.metrics or []:
            ext = _sigma_ext(metric) or {}
            element_id = ext.get("element_id")
            if element_id is None:
                sql = ansi_sql_text(metric.expression)
                owning_dataset = infer_single_dataset_qualifier(sql, dataset_names) if sql else None
                element_id = dataset_element_id.get(owning_dataset) if owning_dataset else None
                if element_id is None:
                    issues.append(
                        ConverterIssue(
                            ConverterIssueType.CROSS_DATASET_METRIC_DROPPED,
                            metric.name,
                            "Sigma metrics are scoped to a single element; this Ossie metric's "
                            "expression does not unambiguously reference exactly one dataset "
                            "(it may span datasets via a relationship, e.g. a ratio metric), so "
                            "it has no faithful Sigma representation and was dropped.",
                        )
                    )
                    continue
            metrics_by_element.setdefault(element_id, []).append(metric)

        relationships_by_element: dict[str, list[OSIRelationship]] = {}
        for rel in model.relationships or []:
            ext = _sigma_ext(rel) or {}
            element_id = ext.get("element_id") or dataset_element_id.get(rel.from_dataset, "")
            relationships_by_element.setdefault(element_id, []).append(rel)

        for dataset in model.datasets:
            element = self._build_element(
                dataset, dataset_element_id, metrics_by_element, relationships_by_element, issues
            )
            ext = _sigma_ext(dataset) or {}
            page = _page(ext.get("page_id"), ext.get("page_name"))
            page["elements"].append(element)

        for entry in model_ext.get("non_table_elements", []):
            page = _page(entry.get("page_id"), entry.get("page_name"))
            page["elements"].append(entry["element"])

        spec["pages"] = list(pages.values()) or [{"id": _stable_id("page", "default"), "name": "Page 1", "elements": []}]

        return ConverterResult(output=spec, issues=issues)

    def _build_element(
        self,
        dataset: OSIDataset,
        dataset_element_id: dict[str, str],
        metrics_by_element: dict[str, list[OSIMetric]],
        relationships_by_element: dict[str, list[OSIRelationship]],
        issues: list[ConverterIssue],
    ) -> dict[str, Any]:
        ext = _sigma_ext(dataset) or {}
        element_id = dataset_element_id[dataset.name]

        if "source_kind" in ext:
            source: dict[str, Any] = {"kind": ext["source_kind"]}
            if ext["source_kind"] == "warehouse-table":
                source["path"] = dataset.source.split(".")
            if "connectionId" in ext:
                source["connectionId"] = ext["connectionId"]
            if "source_element_id" in ext:
                source["elementId"] = ext["source_element_id"]
        else:
            source = {"kind": "warehouse-table", "path": dataset.source.split(".")}

        field_ids: dict[str, str] = {}
        columns = []
        for field in dataset.fields or []:
            field_ext = _sigma_ext(field) or {}
            col_id = field_ext.get("id") or _stable_id("column", dataset.name, field.name)
            field_ids[field.name] = col_id
            columns.append(self._build_column(dataset, field, col_id, field_ext, issues))

        element: dict[str, Any] = {
            "id": element_id,
            "kind": "table",
            "name": dataset.name,
            "source": source,
            "columns": columns,
        }
        if dataset.description:
            element["description"] = dataset.description
        if "folders" in ext:
            element["folders"] = ext["folders"]
        if "order" in ext:
            element["order"] = ext["order"]
        if "filters" in ext:
            element["filters"] = ext["filters"]

        metrics = metrics_by_element.get(element_id, [])
        if metrics:
            element["metrics"] = [self._build_metric(m, dataset.name, issues) for m in metrics]

        relationships = relationships_by_element.get(element_id, [])
        if relationships:
            element["relationships"] = [
                self._build_relationship(r, dataset_element_id, field_ids) for r in relationships
            ]

        return element

    def _build_column(
        self,
        dataset: OSIDataset,
        field: OSIField,
        col_id: str,
        field_ext: dict[str, Any],
        issues: list[ConverterIssue],
    ) -> dict[str, Any]:
        formula = _resolve_formula(field.expression, dataset.name, f"{dataset.name}.{field.name}", issues)
        if not formula:
            formula = f"[{dataset.name}/{field.name}]"

        column: dict[str, Any] = {"id": col_id, "formula": formula}
        needs_name = f"/{field.name}]" not in formula and f"[{field.name}]" != formula
        if field.name and (needs_name or field_ext.get("explicit_name")):
            column["name"] = field.name
        if field.description:
            column["description"] = field.description

        if field.datatype == "Opaque" and "format" in field_ext:
            column["format"] = field_ext["format"]
        elif field.datatype and field.datatype in _DATATYPE_TO_FORMAT:
            column["format"] = {"kind": _DATATYPE_TO_FORMAT[field.datatype]}
        elif field.datatype == "Opaque":
            issues.append(
                ConverterIssue(
                    ConverterIssueType.OPAQUE_DATATYPE,
                    f"{dataset.name}.{field.name}",
                    "Field has an Opaque datatype with no preserved native Sigma format; "
                    "no format was emitted.",
                )
            )
        return column

    def _build_metric(self, metric: OSIMetric, dataset_name: str, issues: list[ConverterIssue]) -> dict[str, Any]:
        ext = _sigma_ext(metric) or {}
        formula = _resolve_formula(metric.expression, dataset_name, f"{dataset_name}.{metric.name}", issues)
        result = {"id": ext.get("id") or _stable_id("metric", dataset_name, metric.name), "formula": formula}
        if metric.name:
            result["name"] = metric.name
        return result

    def _build_relationship(
        self,
        rel: OSIRelationship,
        dataset_element_id: dict[str, str],
        field_ids: dict[str, str],
    ) -> dict[str, Any]:
        ext = _sigma_ext(rel) or {}
        target_element_id = dataset_element_id.get(rel.to, rel.to)
        result: dict[str, Any] = {
            "id": ext.get("id") or _stable_id("relationship", rel.name),
            "name": rel.name,
            "targetElementId": target_element_id,
        }
        if ext.get("description"):
            result["description"] = ext["description"]

        raw_keys = ext.get("raw_keys")
        if raw_keys is not None:
            result["keys"] = raw_keys
        else:
            result["keys"] = [
                {
                    "sourceColumnId": field_ids.get(from_col, from_col),
                    "targetColumnId": field_ids.get(to_col, to_col),
                }
                for from_col, to_col in zip(rel.from_columns, rel.to_columns)
            ]
        return result
