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

"""Converts a WisdomAI domain export (format 1.0) into an Ossie Document.

The input is the JSON produced by wisdom's ``exportDomain`` RPC: a wrapper
holding the domain ZSheet, one ZSheet per table, and connection metadata,
all serialized as protobuf-JSON (camelCase keys).
"""

import re
from typing import Dict, List, Optional, Set, Tuple

from ossie import (
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
from ossie_wisdom.converter_issues import ConverterIssue, ConverterIssueType, ConverterResult

_DIALECT_MAP: Dict[str, OSIDialect] = {
    "snowflake": OSIDialect.SNOWFLAKE,
    "databricks": OSIDialect.DATABRICKS,
    "bigquery": OSIDialect.BIGQUERY,
    "ansi": OSIDialect.ANSI_SQL,
    "ansi_sql": OSIDialect.ANSI_SQL,
}

_BACKTICK_DIALECTS = {OSIDialect.DATABRICKS, OSIDialect.BIGQUERY}

_TIME_DATA_TYPES = {"DATE", "DATETIME", "TIMESTAMP"}

_SIMPLE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WisdomToOSIConverter:
    """Converts a wisdom domain export dict into an Ossie Document."""

    def convert(self, export: dict) -> ConverterResult[OSIDocument]:
        issues: List[ConverterIssue] = []

        domain = export.get("domain", {}).get("zsheet_json", {})
        dialect_by_connection = self._build_dialect_index(export, issues)

        datasets = []
        metrics: List[OSIMetric] = []
        for table in export.get("tables", []):
            zsheet = table.get("zsheet_json", {})
            dialect = self._resolve_dialect(zsheet, dialect_by_connection)
            datasets.append(self._convert_table(zsheet, dialect, issues))
            metrics.extend(self._convert_measures(zsheet, dialect, issues))
        if not datasets:
            raise ValueError("Export contains no tables; an Ossie semantic model requires at least one dataset.")
        metrics = self._dedupe_metrics(metrics, issues)

        dataset_names = {d.name for d in datasets}
        relationships = self._convert_relationships(domain, dataset_names, issues)

        model = OSISemanticModel(
            name=domain.get("ref", {}).get("name") or export.get("export_metadata", {}).get("domain_name", "domain"),
            description=domain.get("description") or None,
            ai_context=self._build_ai_context(domain),
            datasets=datasets,
            relationships=relationships or None,
            metrics=[metric for _, metric in metrics] or None,
        )
        return ConverterResult(output=OSIDocument(semantic_model=[model]), issues=issues)

    def _build_dialect_index(self, export: dict, issues: List[ConverterIssue]) -> Dict[str, OSIDialect]:
        index: Dict[str, OSIDialect] = {}
        for connection in export.get("connections", []):
            dialect_name = connection.get("dialect", "").lower()
            dialect = _DIALECT_MAP.get(dialect_name)
            if dialect is None:
                dialect = OSIDialect.ANSI_SQL
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.UNSUPPORTED_DIALECT,
                        element_name=f"{connection.get('name', connection.get('connection_id', '?'))} ({dialect_name})",
                    )
                )
            index[connection.get("connection_id", "")] = dialect
        return index

    def _resolve_dialect(self, zsheet: dict, dialect_by_connection: Dict[str, OSIDialect]) -> OSIDialect:
        connection_id = zsheet.get("location", {}).get("connectionId", "")
        return dialect_by_connection.get(connection_id, OSIDialect.ANSI_SQL)

    def _build_ai_context(self, domain: dict) -> Optional[str]:
        parts: List[str] = []
        system_instructions = domain.get("domainSystemInstructions", "").strip()
        if system_instructions:
            parts.append(system_instructions)
        for knowledge in domain.get("knowledge", []):
            content = knowledge.get("content", "").strip()
            if content:
                parts.append(f"- {content}")
        return "\n".join(parts) or None

    def _convert_table(self, zsheet: dict, dialect: OSIDialect, issues: List[ConverterIssue]) -> OSIDataset:
        name = zsheet.get("ref", {}).get("name", "")
        location = zsheet.get("location", {})
        source = ".".join(
            part for part in (location.get("database"), location.get("schema"), location.get("dbTable")) if part
        )

        fields: List[OSIField] = []
        seen: Set[str] = set()
        for column in zsheet.get("columns", []):
            field = self._convert_column(column, dialect)
            if field.name in seen:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.DUPLICATE_FIELD_DROPPED, element_name=f"{name}.{field.name}"
                    )
                )
                continue
            seen.add(field.name)
            fields.append(field)
        for formula in zsheet.get("formulas", []):
            field = self._convert_formula(formula, dialect)
            if field is None:
                continue
            if field.name in seen:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.DUPLICATE_FIELD_DROPPED, element_name=f"{name}.{field.name}"
                    )
                )
                continue
            seen.add(field.name)
            fields.append(field)

        return OSIDataset(
            name=name,
            source=source,
            primary_key=self._extract_primary_key(zsheet),
            description=zsheet.get("description") or None,
            fields=fields or None,
        )

    def _extract_primary_key(self, zsheet: dict) -> Optional[List[str]]:
        primary_key = zsheet.get("primaryKey", {}).get("columns")
        if primary_key:
            return list(primary_key)
        flagged = [
            column["name"]
            for column in zsheet.get("columns", [])
            if column.get("properties", {}).get("isPrimaryKey")
        ]
        return flagged or None

    def _convert_column(self, column: dict, dialect: OSIDialect) -> OSIField:
        properties = column.get("properties", {})
        return OSIField(
            name=column.get("name", ""),
            expression=self._make_expression(self._quote_identifier(column.get("name", ""), dialect), dialect),
            dimension=OSIDimension(is_time=True) if properties.get("dataType") in _TIME_DATA_TYPES else None,
            label=properties.get("displayName") or None,
            description=column.get("description") or None,
        )

    def _convert_formula(self, formula: dict, dialect: OSIDialect) -> Optional[OSIField]:
        name = formula.get("name", "")
        expression = formula.get("expression", "")
        if not name or not expression:
            return None
        properties = formula.get("properties", {})
        return OSIField(
            name=name,
            expression=self._make_expression(expression, dialect),
            dimension=OSIDimension(is_time=True) if properties.get("dataType") in _TIME_DATA_TYPES else None,
            label=properties.get("displayName") or None,
            description=formula.get("description") or None,
        )

    def _convert_measures(
        self, zsheet: dict, dialect: OSIDialect, issues: List[ConverterIssue]
    ) -> List[Tuple[str, OSIMetric]]:
        table_name = zsheet.get("ref", {}).get("name", "")
        metrics: List[Tuple[str, OSIMetric]] = []
        for measure in zsheet.get("measures", []):
            name = measure.get("name", "")
            expression = measure.get("expression", "")
            if not name or not expression:
                continue
            if measure.get("staleReason"):
                issues.append(
                    ConverterIssue(issue_type=ConverterIssueType.STALE_MEASURE, element_name=f"{table_name}.{name}")
                )
            metrics.append(
                (
                    table_name,
                    OSIMetric(
                        name=name,
                        expression=self._make_expression(expression, dialect),
                        description=measure.get("description") or None,
                    ),
                )
            )
        return metrics

    def _dedupe_metrics(
        self, metrics: List[Tuple[str, OSIMetric]], issues: List[ConverterIssue]
    ) -> List[Tuple[str, OSIMetric]]:
        result: List[Tuple[str, OSIMetric]] = []
        seen: Set[str] = set()
        for table_name, metric in metrics:
            name = metric.name
            if name in seen:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.METRIC_NAME_COLLISION, element_name=f"{table_name}.{name}"
                    )
                )
                name = f"{table_name}_{name}"
                suffix = 2
                while name in seen:
                    name = f"{table_name}_{metric.name}_{suffix}"
                    suffix += 1
                metric = metric.model_copy(update={"name": name})
            seen.add(name)
            result.append((table_name, metric))
        return result

    def _convert_relationships(
        self, domain: dict, dataset_names: Set[str], issues: List[ConverterIssue]
    ) -> List[OSIRelationship]:
        relationships: List[OSIRelationship] = []
        seen_names: Set[str] = set()
        edges = domain.get("relationshipGraph", {}).get("relationships", [])
        for edge in edges:
            left = edge.get("leftDataSource", {}).get("zsheet", {}).get("name")
            right = edge.get("rightDataSource", {}).get("zsheet", {}).get("name")
            properties = edge.get("properties", {})
            column_pairs = self._extract_column_pairs(properties)
            if not left or not right or column_pairs is None:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.RELATIONSHIP_DROPPED,
                        element_name=f"{left or '?'} <-> {right or '?'}",
                    )
                )
                continue
            if left not in dataset_names or right not in dataset_names:
                issues.append(
                    ConverterIssue(
                        issue_type=ConverterIssueType.RELATIONSHIP_DROPPED, element_name=f"{left} <-> {right}"
                    )
                )
                continue

            relationship_type = properties.get("relationshipType", "")
            ai_context: Optional[str] = None
            # Ossie encodes cardinality by direction: `from` is the many side, `to` the one side.
            if relationship_type == "ONE_TO_MANY":
                from_dataset, to_dataset = right, left
                from_columns = [pair[1] for pair in column_pairs]
                to_columns = [pair[0] for pair in column_pairs]
            else:
                from_dataset, to_dataset = left, right
                from_columns = [pair[0] for pair in column_pairs]
                to_columns = [pair[1] for pair in column_pairs]
                if relationship_type == "ONE_TO_ONE":
                    ai_context = "one-to-one relationship"
                elif relationship_type == "MANY_TO_MANY":
                    ai_context = "many-to-many relationship; cardinality is not representable in Ossie"
                    issues.append(
                        ConverterIssue(
                            issue_type=ConverterIssueType.CARDINALITY_LOSS, element_name=f"{left} <-> {right}"
                        )
                    )

            name = f"{from_dataset}_to_{to_dataset}"
            suffix = 2
            while name in seen_names:
                name = f"{from_dataset}_to_{to_dataset}_{suffix}"
                suffix += 1
            seen_names.add(name)

            relationships.append(
                OSIRelationship(
                    name=name,
                    from_dataset=from_dataset,
                    to=to_dataset,
                    from_columns=from_columns,
                    to_columns=to_columns,
                    ai_context=ai_context,
                )
            )
        return relationships

    def _extract_column_pairs(self, properties: dict) -> Optional[List[Tuple[str, str]]]:
        """Returns (left_column, right_column) pairs, or None when the condition cannot be represented."""
        condition = properties.get("joinCondition")
        if condition is not None:
            pair = self._simple_condition_pair(condition)
            return [pair] if pair else None
        compound = properties.get("compoundJoinCondition")
        if compound is not None:
            return self._flatten_compound_condition(compound)
        return None

    def _simple_condition_pair(self, condition: dict) -> Optional[Tuple[str, str]]:
        left = condition.get("leftColumn", {}).get("name")
        right = condition.get("rightColumn", {}).get("name")
        # The only join operator wisdom emits is EQUAL (proto default, omitted in JSON).
        if condition.get("operator") not in (None, "EQUAL") or not left or not right:
            return None
        return (left, right)

    def _flatten_compound_condition(self, compound: dict) -> Optional[List[Tuple[str, str]]]:
        """Flattens an AND-of-equals compound condition into column pairs; None for anything else."""
        simple = compound.get("simpleCondition")
        if simple is not None:
            pair = self._simple_condition_pair(simple)
            return [pair] if pair else None
        nested = compound.get("nestedCondition")
        if nested is not None:
            if nested.get("logicalOperator") != "AND":
                return None
            pairs: List[Tuple[str, str]] = []
            for child in nested.get("conditions", []):
                child_pairs = self._flatten_compound_condition(child)
                if child_pairs is None:
                    return None
                pairs.extend(child_pairs)
            return pairs or None
        return None

    def _quote_identifier(self, name: str, dialect: OSIDialect) -> str:
        """Quotes a column name for use as a SQL expression when it is not a plain identifier."""
        if _SIMPLE_IDENTIFIER.match(name):
            return name
        if dialect in _BACKTICK_DIALECTS:
            return "`" + name.replace("`", "``") + "`"
        return '"' + name.replace('"', '""') + '"'

    def _make_expression(self, expression: str, dialect: OSIDialect) -> OSIExpression:
        return OSIExpression(dialects=[OSIDialectExpression(dialect=dialect, expression=expression)])
