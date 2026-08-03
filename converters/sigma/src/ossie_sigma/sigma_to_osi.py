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

"""Sigma data model spec (JSON) -> Apache Ossie (OSIDocument)."""

from __future__ import annotations

import json
from typing import Any, Optional

from ossie import (
    OSICustomExtension,
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIMetric,
    OSIRelationship,
    OSISemanticModel,
    OSIVendor,
)

from ossie_sigma.converter_issues import ConverterIssue, ConverterIssueType, ConverterResult
from ossie_sigma.expression_utils import build_expression, qualify
from ossie_sigma.sigma_formula import FormulaParseError, is_plain_column_ref, parse_formula, to_ansi_sql

_MODEL_LEVEL_SPEC_KEYS = (
    "dataModelId",
    "folderId",
    "documentVersion",
    "latestDocumentVersion",
    "schemaVersion",
    "kind",
    "createdAt",
    "createdBy",
    "updatedAt",
    "updatedBy",
    "ownerId",
    "url",
)

# Sigma column `format.kind` -> portable Ossie datatype. Anything not listed here has
# no portable equivalent and becomes `Opaque` (with the original format preserved).
_FORMAT_TO_DATATYPE = {
    "string": "String",
    "text": "String",
    "integer": "Integer",
    "number": "Decimal",
    "currency": "Decimal",
    "percent": "Decimal",
    "boolean": "Boolean",
    "date": "Date",
    "datetime": "DateTime",
    "time": "Time",
}


def _vendor_ext(data: dict[str, Any]) -> OSICustomExtension:
    return OSICustomExtension(vendor_name=OSIVendor.SIGMA.value, data=json.dumps(data, sort_keys=True))


def _column_display_name(column: dict[str, Any]) -> str:
    """The Ossie field name for a Sigma column: its explicit `name`, else derived from formula."""
    if column.get("name"):
        return column["name"]
    formula = column.get("formula", "")
    ref = is_plain_column_ref(formula)
    if ref is not None:
        return ref.column
    return column["id"]


def _folder_for_column(element: dict[str, Any], column_id: str) -> Optional[dict[str, Any]]:
    for folder in element.get("folders") or []:
        if column_id in (folder.get("items") or []):
            return folder
    return None


class _ElementIndex:
    """Resolves a Sigma relationship key (model column id, or a raw
    ``inode-<file>/<PHYSICAL_COLUMN>`` warehouse-column reference) to the Ossie
    field name of a table element."""

    def __init__(self, element: dict[str, Any]) -> None:
        self.element = element
        self.columns_by_id: dict[str, dict[str, Any]] = {c["id"]: c for c in element.get("columns") or []}
        self.physical_by_upper: dict[str, str] = {}
        for column in element.get("columns") or []:
            ref = is_plain_column_ref(column.get("formula", ""))
            if ref is not None:
                self.physical_by_upper[ref.column.upper()] = _column_display_name(column)

    def resolve(self, column_ref_id: str) -> tuple[str, bool]:
        """Return ``(ossie_field_name, resolved)``."""
        if column_ref_id in self.columns_by_id:
            return _column_display_name(self.columns_by_id[column_ref_id]), True
        if column_ref_id.startswith("inode-"):
            physical_name = column_ref_id.rsplit("/", 1)[-1]
            resolved = self.physical_by_upper.get(physical_name.upper())
            if resolved is not None:
                return resolved, True
            return physical_name, False
        return column_ref_id, False


class SigmaToOSIConverter:
    """Converts a Sigma data model spec (as parsed JSON) into an :class:`OSIDocument`."""

    def convert(self, spec: dict[str, Any]) -> ConverterResult[OSIDocument]:
        issues: list[ConverterIssue] = []

        elements: list[tuple[dict[str, Any], dict[str, Any]]] = []  # (page, element)
        for page in spec.get("pages") or []:
            for element in page.get("elements") or []:
                elements.append((page, element))

        table_elements = [(p, e) for p, e in elements if e.get("kind") == "table"]
        other_elements = [(p, e) for p, e in elements if e.get("kind") != "table"]

        element_by_id = {e["id"]: e for _, e in table_elements}
        index_by_id = {e["id"]: _ElementIndex(e) for _, e in table_elements}

        datasets: list[OSIDataset] = []
        relationships: list[OSIRelationship] = []
        metrics: list[OSIMetric] = []

        for page, element in table_elements:
            dataset_name = element.get("name", element["id"])
            source = element.get("source") or {}

            if source.get("kind") == "warehouse-table":
                source_str = ".".join(source.get("path") or [])
            else:
                source_str = f"element:{source.get('elementId', element['id'])}"
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.DERIVED_ELEMENT_NOT_MODELED,
                        dataset_name,
                        "Element is derived from another element rather than a warehouse table; "
                        "Ossie has no first-class 'derived dataset' concept, so the parent "
                        "reference is carried in custom_extensions only.",
                    )
                )

            fields: list[OSIField] = []
            for column in element.get("columns") or []:
                formula = column.get("formula", "")
                field_name = _column_display_name(column)
                expression = build_expression(formula, dataset_alias=dataset_name)
                if not any(d.dialect == OSIDialect.ANSI_SQL for d in expression.dialects):
                    issues.append(
                        ConverterIssue(
                            ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE,
                            f"{dataset_name}.{field_name}",
                            f"Formula {formula!r} has no ANSI SQL equivalent; preserved as SIGMA-dialect text only.",
                        )
                    )

                datatype = None
                fmt = column.get("format") or {}
                fmt_kind = fmt.get("kind")
                ext_data: dict[str, Any] = {"id": column["id"]}
                if column.get("name"):
                    ext_data["explicit_name"] = True
                folder = _folder_for_column(element, column["id"])
                if folder is not None:
                    ext_data["folder_id"] = folder["id"]
                if fmt_kind:
                    mapped = _FORMAT_TO_DATATYPE.get(fmt_kind)
                    if mapped is None:
                        datatype = "Opaque"
                        ext_data["format"] = fmt
                        issues.append(
                            ConverterIssue(
                                ConverterIssueType.OPAQUE_DATATYPE,
                                f"{dataset_name}.{field_name}",
                                f"Sigma column format {fmt_kind!r} has no portable Ossie datatype.",
                            )
                        )
                    else:
                        datatype = mapped

                fields.append(
                    OSIField(
                        name=field_name,
                        expression=expression,
                        description=column.get("description"),
                        datatype=datatype,
                        custom_extensions=[_vendor_ext(ext_data)],
                    )
                )

            dataset_ext: dict[str, Any] = {
                "id": element["id"],
                "page_id": page.get("id"),
                "page_name": page.get("name"),
            }
            if source.get("connectionId"):
                dataset_ext["connectionId"] = source["connectionId"]
            if source.get("kind"):
                dataset_ext["source_kind"] = source["kind"]
            if source.get("elementId"):
                dataset_ext["source_element_id"] = source["elementId"]
            if element.get("folders"):
                dataset_ext["folders"] = element["folders"]
            if element.get("order"):
                dataset_ext["order"] = element["order"]
            if element.get("filters"):
                dataset_ext["filters"] = element["filters"]
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.FILTER_NOT_MODELED,
                        dataset_name,
                        "Sigma named/static element filters have no Ossie equivalent (they are a "
                        "presentation-layer concept, not part of the portable semantic model); "
                        "preserved verbatim in custom_extensions only.",
                    )
                )

            datasets.append(
                OSIDataset(
                    name=dataset_name,
                    source=source_str,
                    description=element.get("description"),
                    fields=fields or None,
                    custom_extensions=[_vendor_ext(dataset_ext)],
                )
            )

            for metric in element.get("metrics") or []:
                formula = metric.get("formula", "")
                metric_name = metric.get("name") or metric["id"]
                try:
                    node = qualify(parse_formula(formula), dataset_name)
                    sql = to_ansi_sql(node, dataset_alias=None)
                except FormulaParseError:
                    sql = None

                dialect_exprs = [OSIDialectExpression(dialect=OSIDialect.SIGMA, expression=formula)]
                if sql is not None:
                    dialect_exprs.append(OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression=sql))
                else:
                    issues.append(
                        ConverterIssue(
                            ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE,
                            f"{dataset_name}.{metric_name}",
                            f"Metric formula {formula!r} has no ANSI SQL equivalent.",
                        )
                    )

                metrics.append(
                    OSIMetric(
                        name=metric_name,
                        expression=OSIExpression(dialects=dialect_exprs),
                        custom_extensions=[_vendor_ext({"id": metric["id"], "element_id": element["id"]})],
                    )
                )

            for rel in element.get("relationships") or []:
                target_id = rel.get("targetElementId")
                target_element = element_by_id.get(target_id)
                target_name = target_element.get("name", target_id) if target_element else target_id
                from_index = index_by_id[element["id"]]
                to_index = index_by_id.get(target_id)

                from_columns: list[str] = []
                to_columns: list[str] = []
                for key in rel.get("keys") or []:
                    from_col, from_resolved = from_index.resolve(key["sourceColumnId"])
                    if to_index is not None:
                        to_col, to_resolved = to_index.resolve(key["targetColumnId"])
                    else:
                        to_col, to_resolved = key["targetColumnId"], False
                    from_columns.append(from_col)
                    to_columns.append(to_col)
                    if not (from_resolved and to_resolved):
                        issues.append(
                            ConverterIssue(
                                ConverterIssueType.RELATIONSHIP_COLUMN_UNRESOLVED,
                                rel.get("name") or rel["id"],
                                "Could not resolve one or both join key columns to a modeled Ossie "
                                "field name; the raw Sigma column reference is preserved in "
                                "custom_extensions for exact round-trip reconstruction.",
                            )
                        )

                rel_ext = {
                    "id": rel["id"],
                    "element_id": element["id"],
                    "raw_keys": rel.get("keys"),
                }
                if rel.get("description"):
                    rel_ext["description"] = rel["description"]

                relationships.append(
                    OSIRelationship(
                        name=rel.get("name") or rel["id"],
                        **{"from": dataset_name},
                        to=target_name,
                        from_columns=from_columns,
                        to_columns=to_columns,
                        custom_extensions=[_vendor_ext(rel_ext)],
                    )
                )

        model_ext: dict[str, Any] = {k: spec[k] for k in _MODEL_LEVEL_SPEC_KEYS if k in spec}
        if other_elements:
            model_ext["non_table_elements"] = [
                {"page_id": page.get("id"), "page_name": page.get("name"), "element": element}
                for page, element in other_elements
            ]
            for _, element in other_elements:
                issues.append(
                    ConverterIssue(
                        ConverterIssueType.CONTROL_ELEMENT_NOT_MODELED,
                        element.get("name") or element.get("controlId") or element["id"],
                        f"Sigma element kind {element.get('kind')!r} (e.g. workbook controls/filters) has "
                        "no equivalent in the Ossie semantic model; preserved verbatim in "
                        "custom_extensions only. See LIMITATIONS.md.",
                    )
                )

        semantic_model = OSISemanticModel(
            name=spec.get("name", "sigma_data_model"),
            datasets=datasets,
            relationships=relationships or None,
            metrics=metrics or None,
            custom_extensions=[_vendor_ext(model_ext)] if model_ext else None,
        )

        document = OSIDocument(
            dialects=[OSIDialect.ANSI_SQL, OSIDialect.SIGMA],
            vendors=[OSIVendor.SIGMA],
            semantic_model=[semantic_model],
        )
        return ConverterResult(output=document, issues=issues)
