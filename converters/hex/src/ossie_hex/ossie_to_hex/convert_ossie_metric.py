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

from typing import Any

from ossie import OssieMetric

from ..hex import HexDataType, HexEntityId, HexMeasure, HexSql
from ..util.parse_sql import exp
from .context import ExportContext, MetricAnalysis, MetricAssignment
from .convert_ossie_datatype import convert_ossie_datatype
from .convert_ossie_dialect_expression import (
    convert_parsed_ossie_dialect_expression,
)
from .convert_ossie_expression import pick_ossie_expression
from .load_ossie_dialect_expression import parse_ossie_dialect_expression


def analyze_ossie_metric(
    ossie_metric: OssieMetric,
    *,
    ctx: ExportContext,
) -> MetricAnalysis | None:
    """Return expression data needed to assign and convert a metric."""

    with ctx.problem_scope(ossie_metric.name):
        with ctx.problem_scope("expression"):
            ossie_dialect_expression = pick_ossie_expression(
                ossie_metric.expression,
                ctx=ctx,
            )
            if ossie_dialect_expression is None:
                return
            parsed = parse_ossie_dialect_expression(
                ossie_dialect_expression,
                ctx=ctx,
            )
            if parsed is None:
                ctx.error("Unable to parse expression")
                return
            elif not _references_have_hex_ids(parsed.expr, ctx=ctx):
                ctx.error("Unable to resolve all references to Hex IDs")
                return

            dataset_names = _find_dataset_names(parsed.expr)
            if len(dataset_names) == 0:
                ctx.error("Referencing no datasets is not supported.")
            elif len(dataset_names) > 2:
                ctx.error(
                    "Referencing more than two datasets is not supported."
                    + f" Found {len(dataset_names)}: {', '.join(dataset_names)}."
                )

        return MetricAnalysis(
            name=ossie_metric.name,
            expr=parsed.expr,
            dialect=parsed.dialect,
            dataset_names=dataset_names,
        )


def convert_ossie_metric(
    ossie_metric: OssieMetric,
    *,
    ctx: ExportContext,
) -> tuple[HexMeasure, HexEntityId] | None:
    """Convert and assign an analyzed Ossie metric when possible."""

    with ctx.problem_scope(ossie_metric.name):
        spec: dict[str, Any] = {}
        source_model_id: HexEntityId | None = None

        spec["id"] = ctx.hex_ids.for_metric(ossie_metric.name)

        if ossie_metric.description:
            spec["description"] = ossie_metric.description

        with ctx.problem_scope("datatype"):
            spec["type"] = convert_ossie_datatype(
                ossie_metric.datatype,
                HexDataType.NUMBER,
            )

        analysis = ctx.analysis.for_metric(ossie_metric.name)
        assignment = ctx.assignment.for_metric(ossie_metric.name)
        if analysis is None:
            ctx.fatal("Unexpected error.", internal_message="Should have been analyzed")
            return None

        if analysis is not None and assignment is not None:
            source_dataset_name = assignment.source
            source_model_id = ctx.hex_ids.for_dataset(source_dataset_name)
            if source_model_id is None:
                ctx.error(f"Source model not available: {source_dataset_name}")
                return None
        with ctx.problem_scope("expression"):
            spec["func_sql"] = _convert_metric_expression(
                analysis,
                assignment,
                ctx=ctx,
            )

        with ctx.problem_scope("ai_context"):
            if ossie_metric.ai_context is not None:
                ctx.warn("Not supported", code="ai-context")

        with ctx.problem_scope("custom_extensions"):
            if ossie_metric.custom_extensions is not None:
                ctx.warn("Not supported", code="custom-extensions")

        if spec["id"] is None or spec["func_sql"] is None or source_model_id is None:
            return None

        # not set:
        # - name: Ossie does not support display names
        # - visibility: Ossie does not support visibility
        # - func_calc: Ossie does not express Hex calculation language

        return HexMeasure(**spec), source_model_id


def assign_ossie_metric(
    ossie_metric: OssieMetric,
    conversion: tuple[HexMeasure, HexEntityId] | None,
    *,
    ctx: ExportContext,
) -> None:
    if conversion is None:
        return
    with ctx.problem_scope(ossie_metric.name):
        hex_measure, hex_model_id = conversion
        hex_model = ctx.hex_model_by_id(hex_model_id)
        if hex_model is None:
            ctx.error(f"Unable to find model to attach measure. Model: {hex_model_id}")
            return
        hex_model.measures.append(hex_measure)


def _convert_metric_expression(
    analysis: MetricAnalysis | None,
    assignment: MetricAssignment | None,
    *,
    ctx: ExportContext,
) -> HexSql | None:
    if analysis is None or assignment is None:
        return None

    # Metric references are local dimensions or dimensions reached through the
    # exact relationship selected by assignment planning.
    def resolve(dataset_name: str, field_name: str) -> tuple[str | None, str]:
        dimension_id = ctx.hex_ids.for_field(dataset_name, field_name)
        if dimension_id is None:
            raise ResolveError(
                f"Unable to resolve field {dataset_name}.{field_name} to a Hex ID"
            )
        if dataset_name == assignment.source:
            return None, dimension_id
        if assignment.relationship is None:
            raise ResolveError(
                f"Metric assignment has no relationship to {dataset_name!r}"
            )
        relation_id = ctx.hex_ids.for_relationship(assignment.relationship.name)
        if relation_id is None:
            raise ResolveError(
                f"Relationship {assignment.relationship.name!r} has no Hex ID"
            )
        return relation_id, dimension_id

    try:
        return convert_parsed_ossie_dialect_expression(
            analysis.expr,
            analysis.dialect,
            resolve=resolve,
        )
    except ResolveError as e:
        ctx.error(f"Unable to convert expression: {e}")
        return None


class ResolveError(ValueError):
    """Internal logic error. Unable to resolve field to a Hex ID."""


def _find_dataset_names(expr: exp.Expression) -> tuple[str, ...]:
    """Return unique, referenced dataset names in order."""

    return tuple(
        dict.fromkeys(
            node.table
            for node in expr.walk(bfs=False)
            if isinstance(node, exp.Column) and node.table
        )
    )


def _references_have_hex_ids(
    expr: exp.Expression,
    *,
    ctx: ExportContext,
) -> bool:
    """Return whether every validated Ossie reference has a Hex ID."""

    return all(
        column.table
        and ctx.hex_ids.for_dataset(column.table) is not None
        and ctx.hex_ids.for_field(column.table, column.name) is not None
        for column in expr.find_all(exp.Column)
    )
