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

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .analysis import (
    ExportAnalysis,
    MetricAnalysis,
    RelationshipAnalysis,
    RelationshipDirection,
)


class ExportAssignment:
    """Assign Ossie entities with ambiguous scope to Hex entities."""

    def __init__(self) -> None:
        self._metric_assignments: dict[str, MetricAssignment] = {}
        self._relationship_assignments: dict[str, list[RelationshipAssignment]] = {}

    def for_metric(self, name: str) -> MetricAssignment | None:
        return self._metric_assignments.get(name)

    def for_relationship(self, name: str) -> list[RelationshipAssignment]:
        return self._relationship_assignments.get(name, [])

    def set_for_relationship(self, assignment: RelationshipAssignment | None) -> None:
        if assignment is None:
            return
        self._relationship_assignments[assignment.name] = [assignment]

    def set_for_metric(self, assignment: MetricAssignment) -> None:
        self._metric_assignments[assignment.name] = assignment

    def decide_all(self, analysis: ExportAnalysis) -> None:
        """Choose metric parents and every relationship direction to emit."""

        relationship_analyses = analysis.for_relationships()
        for metric_analysis in analysis.for_metrics():
            assignment = self._assign_metric(metric_analysis, relationship_analyses)
            if assignment is None:
                continue
            metric_assignment, relationship_assignment = assignment
            self.set_for_metric(metric_assignment)
            self.set_for_relationship(relationship_assignment)

        for relationship_analysis in analysis.for_relationships():
            assignments = self._relationship_assignments.get(
                relationship_analysis.name, []
            )
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
            self._relationship_assignments[relationship_analysis.name] = assignments

    def _assign_metric(
        self,
        analysis: MetricAnalysis,
        relationship_analyses: Iterable[RelationshipAnalysis],
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
            eligible_relationship_assignments: list[RelationshipAssignment] = []
            for dataset_name in analysis.dataset_names:
                # for each referenced dataset, treat it as the parent dataset,
                # and find an eligible relationship assignment
                source = dataset_name
                target = next(name for name in analysis.dataset_names if name != source)
                for relationship_analysis in relationship_analyses:
                    for edge in relationship_analysis.edges:
                        if edge.source != source or edge.target != target:
                            continue
                        relationship_assignment = RelationshipAssignment(
                            name=relationship_analysis.name,
                            source=edge.source,
                            target=edge.target,
                            direction=edge.direction,
                        )
                        eligible_relationship_assignments.append(
                            relationship_assignment
                        )
                        break
                if len(eligible_relationship_assignments) == 0:
                    return None

            if len(eligible_relationship_assignments) == 0:
                # TODO: missing relationship
                return None
            elif len(eligible_relationship_assignments) == 1:
                relationship_assignment = eligible_relationship_assignments[0]
            else:
                # TODO: ambiguous relationship
                relationship_assignment = eligible_relationship_assignments[0]

            metric_assignment = MetricAssignment(
                name=analysis.name,
                source=relationship_assignment.source,
                relationship=relationship_assignment,
            )
            return metric_assignment, relationship_assignment
        elif len(analysis.dataset_names) > 2:
            return None

        return None


@dataclass(frozen=True)
class MetricAssignment:
    """The assignment of a metric to a parent dataset and optional relationship."""

    name: str
    """The name of the Ossie metric."""
    source: str
    """The name of the Ossie dataset that should be the parent of the metric."""
    relationship: RelationshipAssignment | None
    """The assignment for the Ossie relationship that's required for the metric, if any."""


@dataclass(frozen=True)
class RelationshipAssignment:
    """The assignment of a relationship from a parent dataset to a target dataset."""

    name: str
    """The name of the Ossie relationship."""
    direction: RelationshipDirection
    """A direction of the Ossie relationship (from -> to or to -> from)."""
    source: str
    """The name of the Ossie dataset that is the start of the directed edge."""
    target: str
    """The name of the Ossie dataset that is the end of the directed edge."""
