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

from .context import (
    ExportContext,
    MetricAnalysis,
    MetricAssignment,
    RelationshipAssignment,
)


def build_assignments(*, ctx: ExportContext) -> None:
    """Choose metric parents and every relationship direction to emit."""

    for metric_analysis in ctx.analysis.for_metrics():
        assignment = _assign_metric(metric_analysis, ctx=ctx)
        if assignment is None:
            continue
        metric_assignment, relationship_assignment = assignment
        ctx.assignment.set_for_metric(metric_assignment)
        ctx.assignment.set_for_relationship(relationship_assignment)

    for relationship_analysis in ctx.analysis.for_relationships():
        assignments = ctx.assignment.for_relationship(relationship_analysis.name)
        if assignments:
            continue
        # An unused relationship can live on either endpoint.
        # Arbitrarily choose the first edge.
        edge = relationship_analysis.edges[0]
        relationship_assignment = RelationshipAssignment(
            name=relationship_analysis.name,
            source=edge.source,
            target=edge.target,
            direction=edge.direction,
        )
        assignments.append(relationship_assignment)
        ctx.assignment.set_for_relationship(relationship_assignment)


def _assign_metric(
    analysis: MetricAnalysis,
    *,
    ctx: ExportContext,
) -> tuple[MetricAssignment, RelationshipAssignment | None] | None:
    if len(analysis.dataset_names) == 0:
        return None
    elif len(analysis.dataset_names) == 1:
        relationship_assignment = None
        metric_assignment = MetricAssignment(
            name=analysis.name,
            source=analysis.dataset_names[0],
            relationship=relationship_assignment,
        )
        return metric_assignment, relationship_assignment
    elif len(analysis.dataset_names) == 2:
        eligible_relationship_names: set[str] = set()
        eligible_relationship_assignments: list[RelationshipAssignment] = []
        source_target_pairs: set[tuple[str, str]] = set()
        for dataset_name in analysis.dataset_names:
            # for each referenced dataset, treat it as the parent dataset,
            # and find an eligible relationship assignment
            source = dataset_name
            target = next(name for name in analysis.dataset_names if name != source)
            source_target_pair = (source, target)
            source_target_pairs.add(source_target_pair)
        for relationship_analysis in ctx.analysis.for_relationships():
            for edge in relationship_analysis.edges:
                if relationship_analysis.name in eligible_relationship_names:
                    continue
                if (edge.source, edge.target) not in source_target_pairs:
                    continue
                relationship_assignment = RelationshipAssignment(
                    name=relationship_analysis.name,
                    source=edge.source,
                    target=edge.target,
                    direction=edge.direction,
                )
                eligible_relationship_assignments.append(relationship_assignment)
                eligible_relationship_names.add(relationship_analysis.name)
                break

        if len(eligible_relationship_assignments) == 0:
            ctx.error(
                "Cannot assign metric."
                + f" Unable to find a relationship between referenced datasets: {', '.join(analysis.dataset_names)}.",
                path=["metrics", analysis.name],
            )
            return None
        elif len(eligible_relationship_assignments) == 1:
            relationship_assignment = eligible_relationship_assignments[0]
        else:
            ctx.warn(
                "Ambiguous metric assignment."
                + f" Multiple relationships found between referenced datasets: {', '.join(analysis.dataset_names)}."
                + f" Possible relationships: {', '.join([r.name for r in eligible_relationship_assignments])}.",
                path=["metrics", analysis.name],
            )
            # prefer from_to direction
            relationship_assignment = next(
                (
                    r
                    for r in eligible_relationship_assignments
                    if r.direction == "from_to"
                ),
                eligible_relationship_assignments[0],
            )

        metric_assignment = MetricAssignment(
            name=analysis.name,
            source=relationship_assignment.source,
            relationship=relationship_assignment,
        )
        return metric_assignment, relationship_assignment
    elif len(analysis.dataset_names) > 2:
        return None

    return None
