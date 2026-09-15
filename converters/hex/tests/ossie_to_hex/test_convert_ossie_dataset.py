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

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.convert_ossie_dataset import convert_ossie_dataset
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> Iterator[ExportContext]:
    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    with ctx.semantic_model_scope("model"):
        ctx.hex_ids.set_for_dataset("foo", "foo")
        ctx.hex_ids.set_for_field("foo", "id", "id")
        yield ctx


def test_preserve_name(ctx: ExportContext) -> None:
    name = "foo"
    foo = Quick.dataset(
        name,
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.id == name
    assert not ctx.problems


def test_preserve_description(ctx: ExportContext) -> None:
    description = "Foo bar baz"
    foo = Quick.dataset(
        "foo",
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
        description=description,
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.description == description
    assert not ctx.problems


def test_detects_table_source(ctx: ExportContext) -> None:
    source = "public.foo"
    foo = Quick.dataset(
        "foo",
        source,
        [("id", "String", [("ANSI_SQL", "id")])],
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.base_sql_table == source
    assert result.base_sql_query is None
    assert not ctx.problems


def test_detects_query_source(ctx: ExportContext) -> None:
    source = "SELECT * FROM foo"
    foo = Quick.dataset(
        "foo",
        source,
        [("id", "String", [("ANSI_SQL", "id")])],
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.base_sql_table is None
    assert result.base_sql_query == source
    assert not ctx.problems


def test_warns_about_empty_source(ctx: ExportContext) -> None:
    source = "   "
    foo = Quick.dataset(
        "foo",
        source,
        [("id", "String", [("ANSI_SQL", "id")])],
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.base_sql_query == foo.source
    assert result.base_sql_table is None
    assert problems_snapshot(ctx.problems) == snapshot(
        "[WARNING] Dataset source is empty"
    )


def test_warns_about_empty_fields(ctx: ExportContext) -> None:
    fields = []
    foo = Quick.dataset(
        "foo",
        "public.foo",
        fields,
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.dimensions == []
    assert problems_snapshot(ctx.problems) == snapshot(
        "[WARNING] Dataset fields are empty"
    )


def test_single_primary_key_marks_field_unique(ctx: ExportContext) -> None:
    primary_key = ["id"]
    foo = Quick.dataset(
        "foo",
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
        primary_key=primary_key,
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert len(result.dimensions) == 1
    assert result.dimensions[0].id == "id"
    assert result.dimensions[0].unique is True
    assert not ctx.problems


def test_warns_about_composite_primary_key(ctx: ExportContext) -> None:
    primary_key = ["id", "created_at"]
    foo = Quick.dataset(
        "foo",
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
        primary_key=primary_key,
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.dimensions[0].unique is False
    assert problems_snapshot(ctx.problems) == snapshot(
        "[WARNING] Composite primary key is not supported: ['id', 'created_at']"
    )


def test_single_unique_key_marks_field_unique(ctx: ExportContext) -> None:
    unique_keys = [["id"]]
    foo = Quick.dataset(
        "foo",
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
        unique_keys=unique_keys,
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert len(result.dimensions) == 1
    assert result.dimensions[0].id == "id"
    assert result.dimensions[0].unique is True
    assert not ctx.problems


def test_warns_about_composite_unique_key(ctx: ExportContext) -> None:
    unique_keys = [["id", "status"]]
    foo = Quick.dataset(
        "foo",
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
        unique_keys=unique_keys,
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.dimensions[0].unique is False
    assert problems_snapshot(ctx.problems) == snapshot(
        "[WARNING] Composite unique key is not supported: ['id', 'status']"
    )


def test_warns_about_ai_context(ctx: ExportContext) -> None:
    ai_context = OssieAIContextObject(synonyms=("bar", "baz"))
    foo = Quick.dataset(
        "foo",
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
        ai_context=ai_context,
    )
    convert_ossie_dataset(foo, ctx=ctx)
    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_custom_extensions(ctx: ExportContext) -> None:
    custom_extension = OssieCustomExtension(vendor_name="foo", data="bar")
    foo = Quick.dataset(
        "foo",
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
        custom_extensions=[custom_extension],
    )
    convert_ossie_dataset(foo, ctx=ctx)
    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_empty_measures_relations(ctx: ExportContext) -> None:
    foo = Quick.dataset(
        "foo",
        "public.foo",
        [("id", "String", [("ANSI_SQL", "id")])],
    )
    result = convert_ossie_dataset(foo, ctx=ctx)
    assert result is not None
    assert result.measures == []
    assert result.relations == []
    assert not ctx.problems
