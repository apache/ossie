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

import pytest
from inline_snapshot import snapshot

from ossie_hex.ossie_to_hex.build_assignments import build_assignments
from ossie_hex.ossie_to_hex.context import (
    ExportContext,
    MetricAnalysis,
    MetricAssignment,
    RelationshipAnalysis,
    RelationshipAnalysisEdge,
    RelationshipAssignment,
)
from ossie_hex.util.parse_sql import parse_one
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    ctx = ExportContext()
    analysis = MetricAnalysis(
        "foo", parse_one("bar.qux + baz.qoz"), None, ("bar", "baz")
    )
    ctx.analysis.set_for_metric(analysis.name, analysis)
    return ctx


def test_assigns_metric_with_only_reverse_edge(ctx: ExportContext) -> None:
    # The real analyzer emits both edges; this isolates reverse-edge planning.
    ctx.analysis.set_for_relationship(
        RelationshipAnalysis(
            "relation", [RelationshipAnalysisEdge("to_from", "baz", "bar")]
        )
    )

    build_assignments(ctx=ctx)

    relationship = RelationshipAssignment("relation", "to_from", "baz", "bar")
    assert ctx.assignment.for_metric("foo") == MetricAssignment(
        "foo", "baz", relationship
    )
    assert ctx.assignment.for_relationship("relation") == [relationship]
    assert not ctx.problems


def test_reports_missing_relationship(ctx: ExportContext) -> None:
    build_assignments(ctx=ctx)

    assert ctx.assignment.for_metric("foo") is None
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[ERROR] Cannot assign metric. Unable to find a relationship between referenced datasets: bar, baz.
Cause: ['metrics', 'foo']""")


def test_warns_about_ambiguity_and_prefers_forward_edge(ctx: ExportContext) -> None:
    ctx.analysis.set_for_relationship(
        RelationshipAnalysis(
            "reverse", [RelationshipAnalysisEdge("to_from", "baz", "bar")]
        )
    )
    ctx.analysis.set_for_relationship(
        RelationshipAnalysis(
            "forward", [RelationshipAnalysisEdge("from_to", "bar", "baz")]
        )
    )

    build_assignments(ctx=ctx)

    relationship = RelationshipAssignment("forward", "from_to", "bar", "baz")
    assert ctx.assignment.for_metric("foo") == MetricAssignment(
        "foo", "bar", relationship
    )
    assert ctx.assignment.for_relationship("forward") == [relationship]
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[WARNING] Ambiguous metric assignment. Multiple relationships found between referenced datasets: bar, baz. Possible relationships: reverse, forward.
Cause: ['metrics', 'foo']""")


def test_assigns_multiple_metrics_using_same_relationship(ctx: ExportContext) -> None:
    analysis = MetricAnalysis(
        "second", parse_one("bar.qux - baz.qoz"), None, ("bar", "baz")
    )
    ctx.analysis.set_for_metric(analysis.name, analysis)
    ctx.analysis.set_for_relationship(
        RelationshipAnalysis(
            "relation",
            [
                RelationshipAnalysisEdge("from_to", "bar", "baz"),
                RelationshipAnalysisEdge("to_from", "baz", "bar"),
            ],
        )
    )

    build_assignments(ctx=ctx)

    relationship = RelationshipAssignment("relation", "from_to", "bar", "baz")
    for name in ("foo", "second"):
        assert ctx.assignment.for_metric(name) == MetricAssignment(
            name, "bar", relationship
        )
    assert ctx.assignment.for_relationship("relation") == [relationship]
    assert not ctx.problems
