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
from ossie import (
    OssieAIContextObject,
    OssieCustomExtension,
    OssieSemanticModel,
)

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.convert_ossie_semantic_model import (
    convert_ossie_semantic_model,
)
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    return ctx


def test_preserves_name(ctx: ExportContext) -> None:
    name = "sales"
    semantic_model = OssieSemanticModel(name=name, datasets=[])

    result = convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert result.name == name
    assert problems_snapshot(ctx.problems) == snapshot("")


def test_warns_about_description(ctx: ExportContext) -> None:
    description = "Sales are important."
    semantic_model = OssieSemanticModel(
        name="sales", datasets=[], description=description
    )

    convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_ai_context(ctx: ExportContext) -> None:
    ai_context = OssieAIContextObject(synonyms=("bar", "baz"))
    semantic_model = OssieSemanticModel(
        name="sales", datasets=[], ai_context=ai_context
    )

    convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")


def test_warns_about_custom_extensions(ctx: ExportContext) -> None:
    custom_extension = OssieCustomExtension(vendor_name="foo", data="bar")
    semantic_model = OssieSemanticModel(
        name="sales", datasets=[], custom_extensions=[custom_extension]
    )

    convert_ossie_semantic_model(semantic_model, ctx=ctx)

    assert problems_snapshot(ctx.problems) == snapshot("[WARNING] Not supported")
