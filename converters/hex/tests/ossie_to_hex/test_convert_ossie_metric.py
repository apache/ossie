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

from ossie_hex.hex import HexDataType
from ossie_hex.ossie_to_hex.context import (
    ExportContext,
    MetricAnalysis,
    MetricAssignment,
)
from ossie_hex.ossie_to_hex.convert_ossie_metric import (
    analyze_ossie_metric,
    convert_ossie_metric,
)
from ossie_hex.util.parse_sql import SQLGlotDialect, parse_one
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> Iterator[ExportContext]:
    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    with ctx.semantic_model_scope("model"):
        ctx.hex_ids.set_for_dataset("bar", "bar")
        ctx.hex_ids.set_for_field("bar", "baz", "baz")
        ctx.hex_ids.set_for_metric("foo", "foo")
        analysis = MetricAnalysis("foo", parse_one("bar.baz"), None, ("bar",))
        ctx.analysis.set_for_metric(analysis)
        assignment = MetricAssignment("foo", "bar", None)
        ctx.assignment.set_for_metric(assignment)
        yield ctx


def test_preserve_name(ctx: ExportContext) -> None:
    name = "foo"
    foo = Quick.metric(name, "Integer", [("ANSI_SQL", "bar.baz")])

    result = convert_ossie_metric(foo, ctx=ctx)

    assert result is not None
    hex_measure, _ = result
    assert hex_measure.id == name
    assert not ctx.problems


def test_preserve_description(ctx: ExportContext) -> None:
    description = "Foo bar baz"
    foo = Quick.metric(
        "foo", "Integer", [("ANSI_SQL", "bar.baz")], description=description
    )

    result = convert_ossie_metric(foo, ctx=ctx)

    assert result is not None
    hex_measure, _ = result
    assert hex_measure.description == description
    assert not ctx.problems


def test_defaults_to_number_datatype(ctx: ExportContext) -> None:
    datatype = None
    foo = Quick.metric("foo", datatype, [("ANSI_SQL", "bar.baz")])

    result = convert_ossie_metric(foo, ctx=ctx)

    assert result is not None
    hex_measure, _ = result
    assert hex_measure.type == HexDataType.NUMBER
    assert not ctx.problems


def test_warns_about_ai_context(ctx: ExportContext) -> None:
    ai_context = OssieAIContextObject(synonyms=("bar", "baz"))
    foo = Quick.metric(
        "foo", "Integer", [("ANSI_SQL", "bar.baz")], ai_context=ai_context
    )

    result = convert_ossie_metric(foo, ctx=ctx)

    assert result is not None
    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_custom_extensions(ctx: ExportContext) -> None:
    custom_extension = OssieCustomExtension(vendor_name="foo", data="bar")
    foo = Quick.metric(
        "foo",
        "Integer",
        [("ANSI_SQL", "bar.baz")],
        custom_extensions=[custom_extension],
    )

    result = convert_ossie_metric(foo, ctx=ctx)

    assert result is not None
    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_analyzes_expression_with_no_references(ctx: ExportContext) -> None:
    foo = Quick.metric("foo", "Integer", [("ANSI_SQL", "COUNT(1)")])

    analysis = analyze_ossie_metric(foo, ctx=ctx)

    assert analysis is not None
    assert analysis.expr.sql() == snapshot("COUNT(1)")
    assert analysis.dialect is None
    assert analysis.dataset_names == ()
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Referencing no datasets is not supported."
    )


def test_analyzes_expression_with_one_reference(ctx: ExportContext) -> None:
    foo = Quick.metric("foo", "Integer", [("ANSI_SQL", "bar.baz")])

    analysis = analyze_ossie_metric(foo, ctx=ctx)

    assert analysis is not None
    assert analysis.expr.sql() == snapshot("bar.baz")
    assert analysis.dialect is None
    assert analysis.dataset_names == ("bar",)
    assert not ctx.problems


def test_analyzes_expression_with_two_references(ctx: ExportContext) -> None:
    foo = Quick.metric("foo", "Integer", [("ANSI_SQL", "bar.baz + baz.qux")])

    ctx.hex_ids.set_for_dataset("baz", "baz")
    ctx.hex_ids.set_for_field("baz", "qux", "qux")

    analysis = analyze_ossie_metric(foo, ctx=ctx)

    assert analysis is not None
    assert analysis.expr.sql() == snapshot("bar.baz + baz.qux")
    assert analysis.dialect is None
    assert analysis.dataset_names == ("bar", "baz")
    assert not ctx.problems


def test_analyzes_expression_with_many_references(ctx: ExportContext) -> None:
    foo = Quick.metric(
        "foo", "Integer", [("ANSI_SQL", "bar.baz + baz.qux + qux.corge")]
    )

    ctx.hex_ids.set_for_dataset("baz", "baz")
    ctx.hex_ids.set_for_field("baz", "qux", "qux")
    ctx.hex_ids.set_for_dataset("qux", "qux")
    ctx.hex_ids.set_for_field("qux", "corge", "corge")

    analysis = analyze_ossie_metric(foo, ctx=ctx)

    assert analysis is not None
    assert analysis.expr.sql() == snapshot("bar.baz + baz.qux + qux.corge")
    assert analysis.dialect is None
    assert analysis.dataset_names == ("bar", "baz", "qux")
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Referencing more than two datasets is not supported. Found 3: bar, baz, qux."
    )


def test_compiles_local_field_reference(ctx: ExportContext) -> None:
    foo = Quick.metric(
        "foo",
        "Integer",
        [("ANSI_SQL", "bar.baz")],
    )

    result = convert_ossie_metric(foo, ctx=ctx)

    assert result is not None
    hex_measure, hex_model_id = result
    assert hex_model_id == "bar"
    assert hex_measure.func_sql == snapshot("${baz}")
    assert not ctx.problems


def test_preserves_expression_dialect(ctx: ExportContext) -> None:
    ctx._set_dialects("BIGQUERY", "bigquery")
    foo = Quick.metric(
        "foo",
        "Integer",
        [("BIGQUERY", "COUNTIF(bar.baz)")],
    )

    analysis = MetricAnalysis(
        "foo", parse_one("COUNTIF(bar.baz)"), SQLGlotDialect.BIGQUERY, ("bar",)
    )
    ctx.analysis.set_for_metric(analysis)

    result = convert_ossie_metric(foo, ctx=ctx)

    assert result is not None
    hex_measure, hex_model_id = result
    assert hex_model_id == "bar"
    assert hex_measure.func_sql == snapshot("COUNTIF(${baz})")
    assert not ctx.problems


def test_reports_missing_source_model() -> None:
    foo = Quick.metric("foo", "Integer", [("ANSI_SQL", "bar.baz")])

    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    with ctx.semantic_model_scope("model"):
        ctx.hex_ids.set_for_metric("foo", "foo")
        analysis = MetricAnalysis("foo", parse_one("bar.baz"), None, ("bar",))
        ctx.analysis.set_for_metric(analysis)
        assignment = MetricAssignment("foo", "bar", None)
        ctx.assignment.set_for_metric(assignment)

        result = convert_ossie_metric(foo, ctx=ctx)

        assert result is None
        assert problems_snapshot(ctx.problems) == snapshot(
            "[ERROR] Source model not available: bar"
        )
