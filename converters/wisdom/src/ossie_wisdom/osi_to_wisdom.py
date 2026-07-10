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

"""Converts an Ossie Document into a WisdomAI domain export (format 1.0).

The output mirrors the JSON produced by wisdom's ``exportDomain`` RPC so it
can be fed to ``importDomain``. Inverse of :mod:`ossie_wisdom.wisdom_to_osi`:
model ``ai_context`` splits back into system instructions and knowledge items,
fields split back into columns and formulas, relationship direction is read as
many-to-one (with the ai_context notes restoring one-to-one/many-to-many), and
metrics attach to the table their expression references.

IDs are derived deterministically from element names, and connections are
per-dialect placeholders expected to be remapped when the domain is imported.
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from ossie import (
    OSIAIContextObject,
    OSIDataset,
    OSIDialect,
    OSIDocument,
    OSIExpression,
    OSISemanticModel,
)
from ossie_wisdom.converter_issues import ConverterIssue, ConverterIssueType, ConverterResult

_WISDOM_DIALECT = {
    OSIDialect.SNOWFLAKE: "snowflake",
    OSIDialect.DATABRICKS: "databricks",
    OSIDialect.ANSI_SQL: "ansi",
}


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.md5(value.encode('utf-8')).hexdigest()}"


class OSIToWisdomConverter:
    """Converts an Ossie Document into a wisdom domain export dict."""

    def convert(self, document: OSIDocument, exported_at: Optional[str] = None) -> ConverterResult[dict]:
        issues: List[ConverterIssue] = []

        model = document.semantic_model[0]
        for extra in document.semantic_model[1:]:
            issues.append(ConverterIssue(issue_type=ConverterIssueType.EXTRA_MODEL_DROPPED, element_name=extra.name))
        self._report_custom_extensions(model, issues)

        domain_uuid = _stable_id("ET_DOMAIN", model.name)
        zsheet_refs = {
            dataset.name: {"uuid": _stable_id("ET_ZSHEET", dataset.name), "name": dataset.name, "version": "1"}
            for dataset in model.datasets
        }
        dataset_dialects = {dataset.name: self._infer_dataset_dialect(dataset) for dataset in model.datasets}
        measures_by_dataset = self._assign_metrics(model, dataset_dialects, issues)

        tables = []
        table_metadata = []
        for dataset in model.datasets:
            zsheet = self._convert_dataset(
                dataset, zsheet_refs, dataset_dialects[dataset.name], measures_by_dataset.get(dataset.name, []), issues
            )
            tables.append({"zsheet_uuid": zsheet_refs[dataset.name]["uuid"], "zsheet_json": zsheet})
            location = zsheet["location"]
            table_metadata.append(
                {
                    "zsheet_uuid": zsheet_refs[dataset.name]["uuid"],
                    "connection_id": location["connectionId"],
                    "database": location["database"],
                    "schema": location["schema"],
                    "table_name": location["dbTable"],
                }
            )

        instructions, knowledge = self._split_ai_context(model, issues)
        domain_zsheet: dict = {
            "ref": {"uuid": domain_uuid, "name": model.name, "version": "1"},
            "zsheetType": "DOMAIN",
            "relationshipGraph": {
                "zsheets": list(zsheet_refs.values()),
                "relationships": self._convert_relationships(model, zsheet_refs, issues),
            },
        }
        if model.description:
            domain_zsheet["description"] = model.description
        if instructions:
            domain_zsheet["domainSystemInstructions"] = instructions
        if knowledge:
            domain_zsheet["knowledge"] = knowledge

        connections = [
            {"connection_id": f"et-connection-{dialect}", "dialect": dialect, "name": dialect}
            for dialect in sorted({_WISDOM_DIALECT[d] for d in dataset_dialects.values()})
        ]

        export = {
            "version": "1.0",
            "export_metadata": {
                "exported_at": exported_at or datetime.now(timezone.utc).isoformat(),
                "source_domain_id": domain_uuid,
                "domain_name": model.name,
            },
            "domain": {"zsheet_json": domain_zsheet},
            "tables": tables,
            "connections": connections,
            "reviewed_queries": {"ref": None, "items_json": "{}"},
            "synonym_sets": {"ref": None, "items_json": "{}"},
            "table_metadata": table_metadata,
            "recommended_questions": [],
        }
        return ConverterResult(output=export, issues=issues)

    def _report_custom_extensions(self, model: OSISemanticModel, issues: List[ConverterIssue]) -> None:
        elements = [(model.name, model.custom_extensions)]
        for dataset in model.datasets:
            elements.append((dataset.name, dataset.custom_extensions))
            for field in dataset.fields or []:
                elements.append((f"{dataset.name}.{field.name}", field.custom_extensions))
        for relationship in model.relationships or []:
            elements.append((relationship.name, relationship.custom_extensions))
        for metric in model.metrics or []:
            elements.append((metric.name, metric.custom_extensions))
        for name, extensions in elements:
            if extensions:
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.CUSTOM_EXTENSION_DROPPED, element_name=name)
                )

    def _infer_dataset_dialect(self, dataset: OSIDataset) -> OSIDialect:
        for field in dataset.fields or []:
            for entry in field.expression.dialects:
                if entry.dialect in (OSIDialect.SNOWFLAKE, OSIDialect.DATABRICKS):
                    return entry.dialect
        return OSIDialect.ANSI_SQL

    def _split_ai_context(self, model: OSISemanticModel, issues: List[ConverterIssue]) -> Tuple[str, List[dict]]:
        ai_context = model.ai_context
        if ai_context is None:
            return "", []
        if isinstance(ai_context, OSIAIContextObject):
            if ai_context.synonyms or ai_context.examples:
                issues.append(ConverterIssue(issue_type=ConverterIssueType.AI_CONTEXT_DROPPED, element_name=model.name))
            text = ai_context.instructions or ""
        else:
            text = ai_context

        instruction_lines: List[str] = []
        contents: List[str] = []
        current: Optional[str] = None
        for line in text.split("\n"):
            if line.startswith("- "):
                if current is not None:
                    contents.append(current)
                current = line[2:]
            elif current is not None:
                current += "\n" + line
            else:
                instruction_lines.append(line)
        if current is not None:
            contents.append(current)

        knowledge = [
            {"name": content, "content": content, "id": _stable_id("ET_UNSTRUCTURED_KNOWLEDGE", content)}
            for content in contents
            if content.strip()
        ]
        return "\n".join(instruction_lines).strip(), knowledge

    def _convert_dataset(
        self,
        dataset: OSIDataset,
        zsheet_refs: Dict[str, dict],
        dialect: OSIDialect,
        measures: List[dict],
        issues: List[ConverterIssue],
    ) -> dict:
        ref = zsheet_refs[dataset.name]
        ref_lite = {"uuid": ref["uuid"], "name": ref["name"]}
        database, schema, table = self._split_source(dataset.source)

        columns: List[dict] = []
        formulas: List[dict] = []
        for field in dataset.fields or []:
            expression = self._pick_expression(field.expression, dialect, f"{dataset.name}.{field.name}", issues)
            if field.ai_context is not None:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.AI_CONTEXT_DROPPED, element_name=f"{dataset.name}.{field.name}"
                    )
                )
            properties: dict = {}
            if field.label:
                properties["displayName"] = field.label
            if field.dimension and field.dimension.is_time:
                properties["dataType"] = "TIMESTAMP"
            if self._is_bare_column(expression, field.name):
                column: dict = {"name": field.name}
                if field.description:
                    column["description"] = field.description
                if properties:
                    column["properties"] = properties
                column["location"] = {"name": field.name, "zsheetRef": ref_lite}
                columns.append(column)
            else:
                formula: dict = {"name": field.name, "expression": expression}
                if field.description:
                    formula["description"] = field.description
                if properties:
                    formula["properties"] = properties
                formula["location"] = {"name": field.name, "zsheetRef": ref_lite}
                formula["id"] = _stable_id("FORMULA", f"{dataset.name}.{field.name}")
                formulas.append(formula)

        zsheet: dict = {
            "ref": ref,
            "location": {
                "database": database,
                "schema": schema,
                "dbTable": table,
                "connectionId": f"et-connection-{_WISDOM_DIALECT[dialect]}",
            },
            "source": {"alias": dataset.name, "zsheet": ref_lite},
            "columns": columns,
        }
        if dataset.description:
            zsheet["description"] = dataset.description
        if formulas:
            zsheet["formulas"] = formulas
        if measures:
            zsheet["measures"] = measures
        if dataset.primary_key:
            zsheet["primaryKey"] = {"columns": list(dataset.primary_key)}
        if dataset.unique_keys:
            issues.append(ConverterIssue(issue_type=ConverterIssueType.UNIQUE_KEYS_DROPPED, element_name=dataset.name))
        if dataset.ai_context is not None:
            content = self._ai_context_text(dataset.ai_context, dataset.name, issues)
            if content:
                zsheet["knowledge"] = [
                    {"name": content, "content": content, "id": _stable_id("ET_UNSTRUCTURED_KNOWLEDGE", content)}
                ]
        return zsheet

    def _ai_context_text(self, ai_context, element_name: str, issues: List[ConverterIssue]) -> str:
        if isinstance(ai_context, OSIAIContextObject):
            if ai_context.synonyms or ai_context.examples:
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.AI_CONTEXT_DROPPED, element_name=element_name)
                )
            return ai_context.instructions or ""
        return ai_context

    def _split_source(self, source: str) -> Tuple[str, str, str]:
        parts = source.split(".")
        if len(parts) >= 3:
            return parts[0], parts[1], ".".join(parts[2:])
        if len(parts) == 2:
            return "", parts[0], parts[1]
        return "", "", source

    def _pick_expression(
        self, expression: OSIExpression, dialect: OSIDialect, element_name: str, issues: List[ConverterIssue]
    ) -> str:
        by_dialect = {entry.dialect: entry.expression for entry in expression.dialects}
        if dialect in by_dialect:
            return by_dialect[dialect]
        if OSIDialect.ANSI_SQL in by_dialect:
            return by_dialect[OSIDialect.ANSI_SQL]
        issues.append(
            ConverterIssue(issue_type=ConverterIssueType.MISSING_DIALECT_EXPRESSION, element_name=element_name)
        )
        return expression.dialects[0].expression

    def _is_bare_column(self, expression: str, name: str) -> bool:
        return expression in (name, f'"{name}"', f"`{name}`")

    def _assign_metrics(
        self, model: OSISemanticModel, dataset_dialects: Dict[str, OSIDialect], issues: List[ConverterIssue]
    ) -> Dict[str, List[dict]]:
        measures: Dict[str, List[dict]] = {}
        dataset_names = [dataset.name for dataset in model.datasets]
        for metric in model.metrics or []:
            if metric.ai_context is not None:
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.AI_CONTEXT_DROPPED, element_name=metric.name)
                )
            # Attach to a dataset first so the expression can be picked in that dataset's dialect.
            reference_text = " ".join(entry.expression for entry in metric.expression.dialects)
            dataset_name = self._find_referenced_dataset(reference_text, dataset_names)
            if dataset_name is None:
                dataset_name = dataset_names[0]
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.METRIC_TABLE_UNRESOLVED, element_name=metric.name)
                )
            expression = self._pick_expression(
                metric.expression, dataset_dialects[dataset_name], metric.name, issues
            )
            ref = {"uuid": _stable_id("ET_ZSHEET", dataset_name), "name": dataset_name}
            measure: dict = {"name": metric.name, "expression": expression}
            if metric.description:
                measure["description"] = metric.description
            measure["location"] = {"name": metric.name, "zsheetRef": ref}
            measure["id"] = _stable_id("MEASURE", f"{dataset_name}.{metric.name}")
            measures.setdefault(dataset_name, []).append(measure)
        return measures

    def _find_referenced_dataset(self, expression: str, dataset_names: List[str]) -> Optional[str]:
        best: Optional[str] = None
        best_position = len(expression) + 1
        for name in dataset_names:
            pattern = re.compile(r'(?<![A-Za-z0-9_"`])["`]?' + re.escape(name) + r'["`]?\s*\.')
            match = pattern.search(expression)
            if match and match.start() < best_position:
                best = name
                best_position = match.start()
        return best

    def _convert_relationships(
        self, model: OSISemanticModel, zsheet_refs: Dict[str, dict], issues: List[ConverterIssue]
    ) -> List[dict]:
        edges = []
        for relationship in model.relationships or []:
            left = relationship.from_dataset
            right = relationship.to
            if left not in zsheet_refs or right not in zsheet_refs:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.RELATIONSHIP_DROPPED, element_name=relationship.name
                    )
                )
                continue
            relationship_type = "MANY_TO_ONE"
            if isinstance(relationship.ai_context, str):
                if relationship.ai_context.startswith("one-to-one"):
                    relationship_type = "ONE_TO_ONE"
                elif relationship.ai_context.startswith("many-to-many"):
                    relationship_type = "MANY_TO_MANY"
                else:
                    issues.append(
                        ConverterIssue(
                            issue_type=ConverterIssueType.AI_CONTEXT_DROPPED, element_name=relationship.name
                        )
                    )
            elif relationship.ai_context is not None:
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.AI_CONTEXT_DROPPED, element_name=relationship.name)
                )

            left_ref = {"uuid": zsheet_refs[left]["uuid"], "name": left}
            right_ref = {"uuid": zsheet_refs[right]["uuid"], "name": right}
            conditions = [
                {
                    "leftColumn": {"name": from_column, "zsheetRef": left_ref},
                    "rightColumn": {"name": to_column, "zsheetRef": right_ref},
                }
                for from_column, to_column in zip(relationship.from_columns, relationship.to_columns)
            ]
            properties: dict = {"relationshipType": relationship_type}
            if len(conditions) == 1:
                properties["joinCondition"] = conditions[0]
            else:
                properties["compoundJoinCondition"] = {
                    "nestedCondition": {
                        "logicalOperator": "AND",
                        "conditions": [{"simpleCondition": condition} for condition in conditions],
                    }
                }
            edges.append(
                {
                    "properties": properties,
                    "leftDataSource": {"zsheet": left_ref},
                    "rightDataSource": {"zsheet": right_ref},
                }
            )
        return edges
