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
from ossie_hex.ossie_to_hex.convert_ossie_relationship import convert_ossie_relationship
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> Iterator[ExportContext]:
    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    with ctx.semantic_model_scope("model"):
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
