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
from ossie_hex.ossie_to_hex.load_ossie_dialect_expression import (
    parse_ossie_dialect_expression,
    validate_ossie_field_dialect_expression,
    validate_ossie_metric_dialect_expression,
)
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


def test_valid_field_expression(ctx: ExportContext) -> None:
    expression = Quick.dialect_expression("ANSI_SQL", "id")
    result = validate_ossie_field_dialect_expression(expression, ctx=ctx)
    assert result is True
    assert not ctx.problems


def test_invalid_field_expression(ctx: ExportContext) -> None:
    expression = Quick.dialect_expression("ANSI_SQL", "SELECT FROM")
    result = validate_ossie_field_dialect_expression(expression, ctx=ctx)
    assert result is False
    assert problems_snapshot(ctx.problems) == snapshot(
        """\
[ERROR] Unable to parse: Expected table name but got None. Line 1, Col: 11.
  SELECT \x1b[4mFROM\x1b[0m\
"""
    )


def test_parses_valid_expression(ctx: ExportContext) -> None:
    expression = Quick.dialect_expression("ANSI_SQL", "id")
    result = parse_ossie_dialect_expression(expression, ctx=ctx)
    assert result is not None
    assert not ctx.problems


def test_valid_metric_expression(ctx: ExportContext) -> None:
    field_names = [("foo", "bar")]
    expression = Quick.dialect_expression("ANSI_SQL", "SUM(foo.bar)")
    result = validate_ossie_metric_dialect_expression(
        expression, field_names=field_names, ctx=ctx
    )
    assert result is True
    assert not ctx.problems


def test_warns_about_unknown_field_reference(ctx: ExportContext) -> None:
    field_names = [("other", "bar")]
    expression = Quick.dialect_expression("ANSI_SQL", "foo.bar")
    result = validate_ossie_metric_dialect_expression(
        expression, field_names=field_names, ctx=ctx
    )
    assert result is False
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Field expression references field not in semantic model: foo.bar"
    )


def test_invalid_metric_expression(ctx: ExportContext) -> None:
    expression = Quick.dialect_expression("ANSI_SQL", "SELECT FROM")
    result = validate_ossie_metric_dialect_expression(
        expression, field_names=[], ctx=ctx
    )
    assert result is False
    assert problems_snapshot(ctx.problems) == snapshot(
        """\
[ERROR] Unable to parse: Expected table name but got None. Line 1, Col: 11.
  SELECT \x1b[4mFROM\x1b[0m\
"""
    )
