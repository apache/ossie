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

"""Apache Ossie document -> generated semantido model code.

This direction is code generation: it emits a Python module of
``@semantic_table``-decorated SQLAlchemy models. Constructs with no
semantido equivalent (metrics, non-ANSI dialect expressions) are dropped
with a recorded ConverterIssue and listed in a TODO comment block at the
top of the generated file.
"""

import json
from typing import List

from ossie import OSIDataset, OSIDocument, OSIField

from ossie_semantido.constants import VENDOR_NAME
from ossie_semantido.converter_issues import (
    ConverterIssue,
    ConverterIssueType,
    ConverterResult,
)

_TYPE_MAP = {
    "VARCHAR": "String",
    "CHAR": "String",
    "TEXT": "String",
    "INTEGER": "Integer",
    "BIGINT": "Integer",
    "SMALLINT": "Integer",
    "NUMERIC": "Numeric",
    "DECIMAL": "Numeric",
    "FLOAT": "Float",
    "DATE": "Date",
    "DATETIME": "DateTime",
    "TIMESTAMP": "DateTime",
    "BOOLEAN": "Boolean",
}


def _vendor_payload(entity) -> dict:
    for ext in entity.custom_extensions or []:
        if ext.vendor_name == VENDOR_NAME:
            try:
                return json.loads(ext.data)
            except (TypeError, ValueError):
                return {}
    return {}


def _sqlalchemy_type(field: OSIField, issues: List[ConverterIssue]) -> str:
    data_type = _vendor_payload(field).get("data_type", "")
    base = data_type.split("(")[0].strip().upper()
    if base in _TYPE_MAP:
        return _TYPE_MAP[base]
    if base:
        issues.append(
            ConverterIssue(
                ConverterIssueType.UNMAPPED_DATA_TYPE, f"{field.name}:{base}"
            )
        )
    return "String"


def _ai(entity):
    ctx = entity.ai_context
    if ctx is None or isinstance(ctx, str):
        return None
    return ctx


def _class_name(dataset_name: str) -> str:
    return "".join(part.capitalize() for part in dataset_name.split("_"))


def _emit_dataset(dataset: OSIDataset, issues: List[ConverterIssue]) -> str:
    payload = _vendor_payload(dataset)
    ctx = _ai(dataset)

    decorator_kwargs = []
    if dataset.description:
        decorator_kwargs.append(f"    description={dataset.description!r},")
    if ctx and ctx.instructions:
        decorator_kwargs.append(f"    business_context={ctx.instructions!r},")
    if ctx and ctx.synonyms:
        decorator_kwargs.append(f"    synonyms={list(ctx.synonyms)!r},")
    if payload.get("sql_filters"):
        decorator_kwargs.append(f"    sql_filters={payload['sql_filters']!r},")
    if payload.get("time_dimension"):
        decorator_kwargs.append(f"    time_dimension={payload['time_dimension']!r},")

    lines = ["@semantic_table("] + decorator_kwargs + [")"]
    lines.append(f"class {_class_name(dataset.name)}(SemanticDeclarativeBase):")
    lines.append(f'    __tablename__ = "{dataset.name}"')
    lines.append("")

    primary_keys = set(dataset.primary_key or [])
    for field in dataset.fields or []:
        field_payload = _vendor_payload(field)
        field_ctx = _ai(field)
        sa_type = _sqlalchemy_type(field, issues)
        pk = ", primary_key=True" if field.name in primary_keys else ""
        lines.append(f"    {field.name} = Column({sa_type}{pk})")
        if field.description:
            lines.append(f"    {field.name}_description = {field.description!r}")
        if field_payload.get("privacy_level"):
            level = field_payload["privacy_level"].upper()
            lines.append(f"    {field.name}_privacy_level = PrivacyLevel.{level}")
        if field_ctx and field_ctx.examples:
            lines.append(
                f"    {field.name}_sample_values = {[str(e) for e in field_ctx.examples]!r}"
            )
        if field_payload.get("time_grain"):
            grain = field_payload["time_grain"].upper()
            lines.append(f"    {field.name}_time_grain = TimeGrain.{grain}")
    return "\n".join(lines)


def osi_to_semantido_source(document: OSIDocument) -> ConverterResult[str]:
    """Generate semantido model source code from an Ossie document."""
    issues: List[ConverterIssue] = []
    blocks = []

    for model in document.semantic_model:
        for metric in model.metrics or []:
            issues.append(
                ConverterIssue(ConverterIssueType.METRIC_DROPPED, metric.name)
            )
        for dataset in model.datasets:
            blocks.append(_emit_dataset(dataset, issues))

    todo_block = ""
    if issues:
        todo_lines = "\n".join(
            f"#   {i.issue_type.value}: {i.element_name}" for i in issues
        )
        todo_block = (
            "# TODO(ossie-semantido): the following Ossie constructs have no\n"
            "# semantido equivalent yet and were not converted:\n" + todo_lines + "\n\n"
        )

    header = (
        '"""semantido models generated by ossie-semantido. Review before use."""\n\n'
        "from sqlalchemy import Boolean, Column, Date, DateTime, Float, Integer, Numeric, String\n\n"
        "from semantido import SemanticDeclarativeBase, semantic_table\n"
        "from semantido.generators.semantic_layer import PrivacyLevel, TimeGrain\n\n\n"
    )
    source = header + todo_block + "\n\n\n".join(blocks) + "\n"
    return ConverterResult(output=source, issues=issues)
