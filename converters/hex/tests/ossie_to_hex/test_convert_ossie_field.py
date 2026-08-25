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
from ossie import (
    OssieAIContextObject,
    OssieCustomExtension,
    OssieDimension,
)

from ossie_hex.hex import HexDataType
from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.convert_ossie_field import convert_ossie_field
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> Iterator[ExportContext]:
    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    with ctx.semantic_model_scope("model"):
        unique_field_names = set()
        with ctx.fields_scope(unique_field_names=unique_field_names):
            yield ctx


def test_preserve_name(ctx: ExportContext) -> None:
    name = "foo"
    foo = Quick.field(name, "String", [("ANSI_SQL", "foo")])
    result = convert_ossie_field(foo, ctx=ctx)
    assert result is not None
    assert result.id == name
    assert not ctx.problems


def test_preserve_label_description(ctx: ExportContext) -> None:
    label = "Foo"
    description = "Foo bar baz"
    foo = Quick.field(
        "foo",
        "String",
        [("ANSI_SQL", "foo")],
        label=label,
        description=description,
    )
    result = convert_ossie_field(foo, ctx=ctx)
    assert result is not None
    assert result.name == label
    assert result.description == description
    assert not ctx.problems


def test_defaults_to_string_datatype(ctx: ExportContext) -> None:
    datatype = None
    foo = Quick.field("foo", datatype, [("ANSI_SQL", "foo")])
    result = convert_ossie_field(foo, ctx=ctx)
    assert result is not None
    assert result.type == HexDataType.STRING
    assert not ctx.problems


def test_does_not_warn_about_dimension_is_time_with_temporal(
    ctx: ExportContext,
) -> None:
    datatype = "Date"
    dimension = OssieDimension(is_time=True)
    foo = Quick.field("foo", datatype, [("ANSI_SQL", "foo")], dimension=dimension)
    convert_ossie_field(foo, ctx=ctx)
    assert not ctx.problems


def test_warns_about_dimension_is_time_when_not_temporal(ctx: ExportContext) -> None:
    datatype = "Integer"
    dimension = OssieDimension(is_time=True)
    foo = Quick.field("foo", datatype, [("ANSI_SQL", "foo")], dimension=dimension)
    convert_ossie_field(foo, ctx=ctx)
    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_ai_context(ctx: ExportContext) -> None:
    ai_context = OssieAIContextObject(synonyms=("bar", "baz"))
    foo = Quick.field("foo", "String", [("ANSI_SQL", "foo")], ai_context=ai_context)
    convert_ossie_field(foo, ctx=ctx)
    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_custom_extensions(ctx: ExportContext) -> None:
    custom_extension = OssieCustomExtension(vendor_name="foo", data="bar")
    foo = Quick.field(
        "foo",
        "String",
        [("ANSI_SQL", "foo")],
        custom_extensions=[custom_extension],
    )
    convert_ossie_field(foo, ctx=ctx)
    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_converts_expression(ctx: ExportContext) -> None:
    foo = Quick.field("foo", "String", [("ANSI_SQL", "foo")])
    result = convert_ossie_field(foo, ctx=ctx)
    assert result is not None
    assert result.expr_sql == snapshot("foo")
    assert not ctx.problems
