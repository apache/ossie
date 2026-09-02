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

from collections.abc import Iterator

import pytest
from inline_snapshot import snapshot
from ossie import OssieAIContextObject, OssieCustomExtension

from ossie_hex.hex import HexRelationType
from ossie_hex.ossie_to_hex.context import (
    ExportContext,
    RelationshipAssignment,
)
from ossie_hex.ossie_to_hex.convert_ossie_relationship import (
    analyze_ossie_relationship,
    convert_ossie_relationship,
)
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> Iterator[ExportContext]:
    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    with ctx.semantic_model_scope("model"):
        ctx.hex_ids.set_for_dataset("bar", "bar")
        ctx.hex_ids.set_for_dataset("baz", "baz")
        ctx.hex_ids.set_for_relationship("foo", "foo")
        assignment = RelationshipAssignment("foo", "from_to", "bar", "baz")
        ctx.assignment.set_for_relationship(assignment)
        yield ctx


def test_warns_about_ai_context(ctx: ExportContext) -> None:
    ai_context = OssieAIContextObject(synonyms=("bar", "baz"))
    foo = Quick.relationship(
        "foo",
        "bar",
        "baz",
        ["qux"],
        ["qoz"],
        ai_context=ai_context,
    )

    convert_ossie_relationship(foo, ctx=ctx)

    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_custom_extensions(ctx: ExportContext) -> None:
    assignment = RelationshipAssignment("foo", "from_to", "bar", "baz")
    ctx.assignment.set_for_relationship(assignment)

    custom_extension = OssieCustomExtension(vendor_name="foo", data="bar")
    foo = Quick.relationship(
        "foo",
        "bar",
        "baz",
        ["qux"],
        ["qoz"],
        custom_extensions=[custom_extension],
    )

    convert_ossie_relationship(foo, ctx=ctx)

    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_analysis(ctx: ExportContext) -> None:
    foo = Quick.relationship("foo", "bar", "baz", ["qux"], ["qoz"])

    analysis = analyze_ossie_relationship(foo, ctx=ctx)

    assert analysis is not None
    from_to = next(edge for edge in analysis.edges if edge.direction == "from_to")
    assert from_to is not None
    assert from_to.direction == "from_to"
    assert from_to.source == "bar"
    assert from_to.target == "baz"
    to_from = next(edge for edge in analysis.edges if edge.direction == "to_from")
    assert to_from is not None
    assert to_from.direction == "to_from"
    assert to_from.source == "baz"
    assert to_from.target == "bar"

    assert not ctx.problems


def test_converts_unused_relationship_once(ctx: ExportContext) -> None:
    foo = Quick.relationship("foo", "bar", "baz", ["qux"], ["qoz"])
    analysis = analyze_ossie_relationship(foo, ctx=ctx)
    ctx.analysis.set_for_relationship(analysis)
    ctx.assignment.decide_all(ctx.analysis)

    result = convert_ossie_relationship(foo, ctx=ctx)

    assert result is not None
    assert len(result) == 1
    hex_relation, _ = result[0]
    assert hex_relation.id == "foo"

    assert not ctx.problems


def test_direction_from_to(ctx: ExportContext) -> None:
    foo = Quick.relationship("foo", "bar", "baz", ["qux"], ["qoz"])

    assignment = RelationshipAssignment("foo", "from_to", "bar", "baz")
    ctx.assignment.set_for_relationship(assignment)

    result = convert_ossie_relationship(foo, ctx=ctx)

    assert result is not None
    assert len(result) == 1
    hex_relation, hex_model_id = result[0]
    assert hex_model_id == "bar"
    assert hex_relation.target == "baz"
    assert hex_relation.type == HexRelationType.MANY_TO_ONE
    assert hex_relation.join_sql == snapshot("qux = ${foo}.qoz")


def test_direction_to_from(ctx: ExportContext) -> None:
    foo = Quick.relationship("foo", "bar", "baz", ["qux"], ["qoz"])

    assignment = RelationshipAssignment("foo", "to_from", "baz", "bar")
    ctx.assignment.set_for_relationship(assignment)

    result = convert_ossie_relationship(foo, ctx=ctx)

    assert result is not None
    assert len(result) == 1
    hex_relation, hex_model_id = result[0]
    assert hex_model_id == "baz"
    assert hex_relation.target == "bar"
    assert hex_relation.type == HexRelationType.ONE_TO_MANY
    assert hex_relation.join_sql == snapshot("qoz = ${foo}.qux")


def test_reports_missing_source_model() -> None:
    foo = Quick.relationship("foo", "bar", "baz", ["qux"], ["qoz"])

    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    with ctx.semantic_model_scope("model"):
        ctx.hex_ids.set_for_dataset("bar", "bar")
        ctx.hex_ids.set_for_relationship("foo", "foo")
        assignment = RelationshipAssignment("foo", "to_from", "baz", "bar")
        ctx.assignment.set_for_relationship(assignment)

        result = convert_ossie_relationship(foo, ctx=ctx)

        assert result is not None
        assert problems_snapshot(ctx.problems) == snapshot(
            "[ERROR] Source model not available: baz"
        )


def test_reports_missing_target_model() -> None:
    foo = Quick.relationship("foo", "bar", "baz", ["qux"], ["qoz"])

    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    with ctx.semantic_model_scope("model"):
        ctx.hex_ids.set_for_dataset("bar", "bar")
        ctx.hex_ids.set_for_relationship("foo", "foo")
        assignment = RelationshipAssignment("foo", "from_to", "bar", "baz")
        ctx.assignment.set_for_relationship(assignment)

        result = convert_ossie_relationship(foo, ctx=ctx)

        assert result is not None
        assert problems_snapshot(ctx.problems) == snapshot(
            "[ERROR] Target model not available: baz"
        )
