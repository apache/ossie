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

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.load_ossie_expression import (
    load_ossie_field_expression,
    load_ossie_metric_expression,
)
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


def test_valid_field_expression(ctx: ExportContext) -> None:
    expr = Quick.expression([("ANSI_SQL", "id")])
    result = load_ossie_field_expression(expr, ctx=ctx)
    assert result is not None
    assert len(result.dialects) == 1
    assert not ctx.problems


def test_keeps_valid_dialects_when_mixed(ctx: ExportContext) -> None:
    expr = Quick.expression(
        [
            ("ANSI_SQL", "SELECT FROM"),
            ("SNOWFLAKE", "id"),
        ]
    )
    result = load_ossie_field_expression(expr, ctx=ctx)
    assert result is not None
    assert len(result.dialects) == 1
    assert result.dialects[0].dialect == "SNOWFLAKE"
    assert result.dialects[0].expression == "id"
    assert problems_snapshot(ctx.problems) == snapshot(
        """\
[ERROR] Unable to parse: Expected table name but got None. Line 1, Col: 11.
  SELECT \x1b[4mFROM\x1b[0m\
"""
    )


def test_rejects_expression_with_no_valid_dialects(ctx: ExportContext) -> None:
    expression = Quick.expression([("ANSI_SQL", "SELECT FROM")])
    result = load_ossie_field_expression(expression, ctx=ctx)
    assert result is None
    assert problems_snapshot(ctx.problems) == snapshot(
        """\
[ERROR] Unable to parse: Expected table name but got None. Line 1, Col: 11.
  SELECT \x1b[4mFROM\x1b[0m

[ERROR] Expression must have at least one valid dialect\
"""
    )


def test_valid_metric_expression(ctx: ExportContext) -> None:
    field_names = [("foo", "bar")]
    expression = Quick.expression([("ANSI_SQL", "foo.bar")])
    result = load_ossie_metric_expression(expression, field_names=field_names, ctx=ctx)
    assert result is not None
    assert len(result.dialects) == 1
    assert not ctx.problems


def test_rejects_metric_expression_with_unknown_field(ctx: ExportContext) -> None:
    field_names = [("other", "bar")]
    expression = Quick.expression([("ANSI_SQL", "foo.bar")])
    result = load_ossie_metric_expression(expression, field_names=field_names, ctx=ctx)
    assert result is None
    assert problems_snapshot(ctx.problems) == snapshot(
        """\
[ERROR] Field expression references field not in semantic model: foo.bar

[ERROR] Expression must have at least one valid dialect\
"""
    )
