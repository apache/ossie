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

from ossie import OssieRelationship

from ..hex import HexEntityId, HexRelation, HexRelationType
from .context import (
    RELATIONSHIP_DIRECTIONS,
    ExportContext,
    RelationshipAnalysis,
    RelationshipAnalysisEdge,
    RelationshipAssignment,
)

# ASSUMPTION: it's kind of ambiguous whether the spec is defining "columns" as
# physical or logical columns (i.e. a column on the underlying table/view/query
# or a field on the respective dataset). For now, we assume a physical column.
# In the case that it can be a logical column, we would need to resolve each
# value to the corresponding HexDimension id and wrap in Hex semantic reference
# syntax (${}) like the relation_id


def analyze_ossie_relationship(
    ossie_relationship: OssieRelationship,
    *,
    ctx: ExportContext,
) -> RelationshipAnalysis | None:
    """Return both possible directions of a valid Ossie relationship."""

    with ctx.problem_scope(ossie_relationship.name):
        if (
            ctx.hex_ids.for_relationship(ossie_relationship.name) is None
            or ctx.hex_ids.for_dataset(ossie_relationship.from_dataset) is None
            or ctx.hex_ids.for_dataset(ossie_relationship.to) is None
        ):
            return None

        edges: list[RelationshipAnalysisEdge] = []
        for direction in RELATIONSHIP_DIRECTIONS:
            if direction == "from_to":
                source = ossie_relationship.from_dataset
                target = ossie_relationship.to
            else:
                source = ossie_relationship.to
                target = ossie_relationship.from_dataset
            edges.append(
                RelationshipAnalysisEdge(
                    direction=direction,
                    source=source,
                    target=target,
                )
            )
        analysis = RelationshipAnalysis(
            name=ossie_relationship.name,
            edges=edges,
        )
        return analysis


def convert_ossie_relationship(
    ossie_relationship: OssieRelationship,
    *,
    ctx: ExportContext,
) -> list[tuple[HexRelation, HexEntityId]] | None:
    """Convert and assign every planned direction of a relationship."""

    with ctx.problem_scope(ossie_relationship.name):
        hex_relation_id = ctx.hex_ids.for_relationship(ossie_relationship.name)
        results: list[tuple[HexRelation, HexEntityId]] = []

        with ctx.problem_scope("ai_context"):
            if ossie_relationship.ai_context is not None:
                ctx.warn("Not supported", code="ai-context")

        with ctx.problem_scope("custom_extensions"):
            if ossie_relationship.custom_extensions is not None:
                ctx.warn("Not supported", code="custom-extensions")

        if not hex_relation_id:
            return None

        assignments = ctx.assignment.for_relationship(ossie_relationship.name)
        if not assignments:
            ctx.error("No assignments found")
            return None
        for assignment in assignments:
            source_model_id = ctx.hex_ids.for_dataset(assignment.source)
            if not source_model_id:
                ctx.error(f"Source model not available: {assignment.source}")
                continue
            target_model_id = ctx.hex_ids.for_dataset(assignment.target)
            if not target_model_id:
                ctx.error(f"Target model not available: {assignment.target}")
                continue
            relation_type = _hex_relation_type(assignment)
            local_columns, remote_columns = _ordered_columns(
                ossie_relationship, assignment
            )
            join_sql = _hex_join_sql(hex_relation_id, local_columns, remote_columns)

            spec: dict[str, Any] = {
                "id": hex_relation_id,
                "type": relation_type,
                "join_sql": join_sql,
            }
            if target_model_id != hex_relation_id:
                spec["target"] = target_model_id
            hex_relation = HexRelation(**spec)
            results.append((hex_relation, source_model_id))

        # Attributes not set:
        # - HexRelation.visibility: Ossie does not support this concept

        return results


def assign_ossie_relationship(
    ossie_relationship: OssieRelationship,
    conversion: list[tuple[HexRelation, str]] | None,
    *,
    ctx: ExportContext,
) -> None:
    if conversion is None:
        return
    with ctx.problem_scope(ossie_relationship.name):
        for hex_relation, hex_model_id in conversion:
            hex_model = ctx.hex_model_by_id(hex_model_id)
            if hex_model is None:
                ctx.error(
                    f"Unable to find model to attach relation. Model: {hex_model_id}"
                )
                return
            hex_model.relations.append(hex_relation)


def _hex_relation_type(assignment: RelationshipAssignment) -> HexRelationType:
    if assignment.direction == "from_to":
        return HexRelationType.MANY_TO_ONE
    else:
        return HexRelationType.ONE_TO_MANY


def _ordered_columns(
    ossie_relationship: OssieRelationship, assignment: RelationshipAssignment
) -> tuple[list[str], list[str]]:
    if assignment.direction == "from_to":
        local_columns = ossie_relationship.from_columns
        remote_columns = ossie_relationship.to_columns
    else:
        local_columns = ossie_relationship.to_columns
        remote_columns = ossie_relationship.from_columns
    return local_columns, remote_columns


def _hex_join_sql(
    hex_relation_id: HexEntityId,
    local_columns: list[str],
    remote_columns: list[str],
) -> str:
    join_sql = " AND ".join(
        f"{local} = ${{{hex_relation_id}}}.{remote}"
        for local, remote in zip(local_columns, remote_columns, strict=True)
    )
    return join_sql
