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
from ossie import OssieDataType

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.load_ossie_field import load_ossie_field
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


def test_returns_field_with_valid_expression(ctx: ExportContext) -> None:
    foo = Quick.field(
        "foo",
        "String",
        [("ANSI_SQL", "foo")],
    )
    result = load_ossie_field(foo, ctx=ctx)
    assert result is not None
    assert result.name == foo.name
    assert result.expression == foo.expression
    assert not ctx.problems


def test_returns_none_for_invalid_expression(ctx: ExportContext) -> None:
    foo = Quick.field(
        "foo",
        "String",
        [("ANSI_SQL", "SELECT FROM")],
    )
    result = load_ossie_field(foo, ctx=ctx)
    assert result is None
    assert ctx.problems


def test_defaults_to_string_datatype(ctx: ExportContext) -> None:
    foo = Quick.field(
        "foo",
        None,
        [("ANSI_SQL", "foo")],
    )
    result = load_ossie_field(foo, ctx=ctx)
    assert result is not None
    assert result.datatype == OssieDataType.STRING
    assert problems_snapshot(ctx.problems) == snapshot(
        "[WARNING] Missing. Hex requires a datatype. Using default 'String'."
    )
