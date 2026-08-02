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

"""semantido SemanticLayer -> Apache Ossie document conversion.

Builds the Ossie model through the typed ``apache-ossie`` objects, so the
output is schema-conformant by construction. Governance metadata that has
no Ossie core field (privacy levels, SQL filters, time grain, the primary
time axis) is preserved losslessly in ``custom_extensions`` under the
``SEMANTIDO`` vendor name, with ``data`` as a serialized JSON string as
the specification requires.
"""

import json
import re
from typing import List, Optional

from ossie import (
    OSIAIContextObject,
    OSICustomExtension,
    OSIDataset,
    OSIDialect,
    OSIDialectExpression,
    OSIDimension,
    OSIDocument,
    OSIExpression,
    OSIField,
    OSIRelationship,
    OSISemanticModel,
)
from semantido.generators.semantic_layer import (
    Column,
    Relationship,
    SemanticLayer,
    Table,
)

from ossie_semantido.constants import VENDOR_NAME
from ossie_semantido.converter_issues import (
    ConverterIssue,
    ConverterIssueType,
    ConverterResult,
)

_JOIN_RE = re.compile(r"^\s*(\w+)\.(\w+)\s*=\s*(\w+)\.(\w+)\s*$")


def _extension(payload: dict) -> OSICustomExtension:
    """Wrap semantido-specific metadata as a spec-conformant custom extension."""
    return OSICustomExtension(
        vendor_name=VENDOR_NAME,
        data=json.dumps(payload, sort_keys=True, default=str),
    )


def _ansi(expression: str) -> OSIExpression:
    return OSIExpression(
        dialects=[
            OSIDialectExpression(dialect=OSIDialect.ANSI_SQL, expression=expression)
        ]
    )


def _column_to_field(column: Column) -> OSIField:
    ai_kwargs = {}
    if column.synonyms:
        ai_kwargs["synonyms"] = tuple(column.synonyms)
    if column.sample_values:
        ai_kwargs["examples"] = tuple(str(v) for v in column.sample_values)
    if column.application_rules:
        ai_kwargs["instructions"] = " ".join(column.application_rules)

    extension_payload = {}
    if column.privacy_level is not None:
        extension_payload["privacy_level"] = column.privacy_level.value
    if column.time_grain is not None:
        extension_payload["time_grain"] = column.time_grain.value
    if column.data_type:
        extension_payload["data_type"] = column.data_type
    if column.references:
        extension_payload["references"] = column.references
    if column.concept:  # semantido v0.4.0+
        extension_payload["concept"] = column.concept
    if column.application_rules:
        # Also joined into ai_context.instructions above for Ossie-native
        # consumers; kept verbatim here so the reverse direction can
        # regenerate the authored list.
        extension_payload["application_rules"] = list(column.application_rules)

    return OSIField(
        name=column.name,
        expression=_ansi(column.name),
        dimension=OSIDimension(is_time=True) if column.is_time_dimension else None,
        description=column.description or None,
        ai_context=OSIAIContextObject(**ai_kwargs) if ai_kwargs else None,
        custom_extensions=[_extension(extension_payload)]
        if extension_payload
        else None,
    )


def _table_to_dataset(table: Table) -> OSIDataset:
    instructions_parts = [
        p for p in (table.business_context, table.application_context) if p
    ]

    ai_kwargs = {}
    if instructions_parts:
        ai_kwargs["instructions"] = " ".join(instructions_parts)
    if table.synonyms:
        ai_kwargs["synonyms"] = tuple(table.synonyms)

    extension_payload = {}
    if table.sql_filters:
        extension_payload["sql_filters"] = table.sql_filters
    if table.time_dimension:
        extension_payload["time_dimension"] = table.time_dimension
    if table.concept:  # semantido v0.4.0+
        extension_payload["concept"] = table.concept
    if table.unique_keys:  # semantido v0.5.0+
        extension_payload["unique_keys"] = [list(k) for k in table.unique_keys]
    # ai_context.instructions joins business + application context for
    # Ossie-native consumers; the split is preserved here, so the reverse
    # direction regenerates the authored attributes rather than merging.
    if table.business_context:
        extension_payload["business_context"] = table.business_context
    if table.application_context:
        extension_payload["application_context"] = table.application_context

    return OSIDataset(
        name=table.name,
        source=f"{table.schema}.{table.name}" if table.schema else table.name,
        primary_key=table.primary_key if table.primary_key else None,
        description=table.description or None,
        ai_context=OSIAIContextObject(**ai_kwargs) if ai_kwargs else None,
        fields=[_column_to_field(c) for c in table.columns] or None,
        custom_extensions=[_extension(extension_payload)]
        if extension_payload
        else None,
    )


def _relationship_to_ossie(
    rel: Relationship, issues: List[ConverterIssue]
) -> Optional[OSIRelationship]:
    match = _JOIN_RE.match(rel.join_condition or "")
    if not match:
        issues.append(
            ConverterIssue(
                issue_type=ConverterIssueType.RELATIONSHIP_JOIN_UNPARSED,
                element_name=f"{rel.from_table}->{rel.to_table}",
            )
        )
        return None

    left_table, left_col, right_table, right_col = match.groups()
    # Normalize so from/to match the declared direction regardless of
    # which side of the equality each table appeared on.
    if left_table == rel.from_table:
        from_columns, to_columns = [left_col], [right_col]
    elif right_table == rel.from_table:
        from_columns, to_columns = [right_col], [left_col]
    else:
        # Neither side of the join references rel.from_table (e.g., aliases)
        # Treat this as unparseable rather than silently swapping columns.
        issues.append(
            ConverterIssue(
                issue_type=ConverterIssueType.RELATIONSHIP_JOIN_UNPARSED,
                element_name=f"{rel.from_table}->{rel.to_table}",
            )
        )
        return None

    ai_kwargs = {}
    if rel.description:
        ai_kwargs["instructions"] = rel.description

    return OSIRelationship.model_validate(
        {
            "name": f"{rel.from_table}_to_{rel.to_table}",
            "from": rel.from_table,
            "to": rel.to_table,
            "from_columns": from_columns,
            "to_columns": to_columns,
            "ai_context": ai_kwargs or None,
            "custom_extensions": [
                _extension(
                    {"relationship_type": rel.relationship_type.value}
                ).model_dump()
            ],
        }
    )


def semantic_layer_to_ossie(
    layer: SemanticLayer, model_name: str, description: Optional[str] = None
) -> ConverterResult[OSIDocument]:
    """Convert a synced semantido SemanticLayer into an Ossie document."""
    issues: List[ConverterIssue] = []

    datasets = [_table_to_dataset(t) for t in layer.tables.values()]

    relationships = []
    for rel in layer.relationships:
        converted = _relationship_to_ossie(rel, issues)
        if converted is not None:
            relationships.append(converted)

    model_payload = {}
    if layer.application_glossary:
        model_payload["application_glossary"] = layer.application_glossary
    if layer.concept_registry is not None:  # semantido v0.4.0+
        # The registry is the meaning artifact: definitions, relations,
        # external mappings, and grain (v0.5.0). Serialized whole so a
        # round-trip regenerates the authoring code.
        model_payload["concept_registry"] = layer.concept_registry.to_dict()
    model_extensions = [_extension(model_payload)] if model_payload else None

    model = OSISemanticModel(
        name=model_name,
        description=description,
        datasets=datasets,
        relationships=relationships or None,
        custom_extensions=model_extensions,
    )
    return ConverterResult(output=OSIDocument(semantic_model=[model]), issues=issues)
