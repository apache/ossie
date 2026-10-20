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
from ossie import OssieDialect

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.load_ossie_expression import (
    load_ossie_field_expression,
    load_ossie_metric_expression,
)
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    ctx = ExportContext()
    ctx.set_ossie_dialect(OssieDialect.ANSI_SQL)
    return ctx


def test_only_validates_preferred_field_variant(ctx: ExportContext) -> None:
    expression = Quick.expression(
        [
            ("DAX", "SELECT FROM"),  # Invalid, unused.
            ("ANSI_SQL", "id"),  # Valid, preferred.
        ]
    )

    result = load_ossie_field_expression(expression, ctx=ctx)

    assert result is expression
    assert not ctx.problems


def test_reports_invalid_preferred_variant(ctx: ExportContext) -> None:
    expression = Quick.expression(
        [
            ("DAX", "SELECT FROM"),  # Invalid, unused.
            ("ANSI_SQL", "SELECT FROM"),  # Invalid, preferred.
            ("SNOWFLAKE", "id"),  # Valid, unused.
        ]
    )

    result = load_ossie_field_expression(expression, ctx=ctx)

    assert result is expression
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[ERROR] Unable to parse: Expected table name but got None. Line 1, Col: 11.
  SELECT \x1b[4mFROM\x1b[0m
Cause: ['dialects', 'ANSI_SQL', 'expression']\
""")


def test_only_validates_preferred_dialect(ctx: ExportContext) -> None:
    field_names = [("foo", "bar")]
    expression = Quick.expression(
        [
            ("DAX", "SUM(foo[missing])"),  # Invalid reference, unused.
            ("ANSI_SQL", "SUM(foo.bar)"),  # Valid, preferred.
        ]
    )

    result = load_ossie_metric_expression(expression, field_names=field_names, ctx=ctx)

    assert result is expression
    assert not ctx.problems


def test_reports_metric_expression_with_invalid_reference(ctx: ExportContext) -> None:
    field_names = [("foo", "bar")]
    expression = Quick.expression(
        [
            ("DAX", "SELECT FROM"),  # Invalid syntax, unused.
            ("ANSI_SQL", "SUM(foo.missing)"),  # Invalid reference, preferred.
            ("SNOWFLAKE", "SUM(foo.bar)"),  # Valid, unused.
        ]
    )

    result = load_ossie_metric_expression(expression, field_names=field_names, ctx=ctx)

    assert result is expression
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[ERROR] Field expression references field not in semantic model: foo.missing
Cause: ['dialects', 'ANSI_SQL']\
""")
