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
from ossie import OssieDataType, OssieDialect

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.load_ossie_metric import load_ossie_metric
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    ctx = ExportContext()
    ctx.set_ossie_dialect(OssieDialect.ANSI_SQL)
    return ctx


def test_returns_metric_with_valid_expression(ctx: ExportContext) -> None:
    field_names = []
    foo = Quick.metric(
        "foo",
        "Integer",
        [("ANSI_SQL", "SUM(1)")],
    )
    result = load_ossie_metric(foo, field_names=field_names, ctx=ctx)
    assert result == foo
    assert not ctx.problems


def test_preserves_metric_with_invalid_expression(ctx: ExportContext) -> None:
    field_names = []
    foo = Quick.metric(
        "foo",
        "Integer",
        [("ANSI_SQL", "SELECT FROM")],
    )
    result = load_ossie_metric(foo, field_names=field_names, ctx=ctx)
    assert result == foo
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[ERROR] Unable to parse: Expected table name but got None. Line 1, Col: 11.
  SELECT \x1b[4mFROM\x1b[0m
Cause: ['foo', 'expression', 'dialects', 'ANSI_SQL', 'expression']\
""")


def test_preserves_invalid_selected_expression_with_valid_alternative(
    ctx: ExportContext,
) -> None:
    field_names = [("orders", "amount")]
    foo = Quick.metric(
        "foo",
        "Integer",
        [
            ("ANSI_SQL", "SELECT FROM"),
            ("SNOWFLAKE", "SUM(1)"),
        ],
    )
    result = load_ossie_metric(foo, field_names=field_names, ctx=ctx)
    assert result == foo
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[ERROR] Unable to parse: Expected table name but got None. Line 1, Col: 11.
  SELECT \x1b[4mFROM\x1b[0m
Cause: ['foo', 'expression', 'dialects', 'ANSI_SQL', 'expression']\
""")


def test_returns_metric_with_valid_reference(ctx: ExportContext) -> None:
    field_names = [("orders", "amount")]
    foo = Quick.metric(
        "foo",
        "Integer",
        [("ANSI_SQL", "orders.amount")],
    )
    result = load_ossie_metric(foo, field_names=field_names, ctx=ctx)
    assert result == foo
    assert not ctx.problems


def test_preserves_invalid_selected_reference_with_valid_alternative(
    ctx: ExportContext,
) -> None:
    field_names = [("orders", "amount")]
    foo = Quick.metric(
        "foo",
        "Integer",
        [
            ("ANSI_SQL", "foo.amount"),
            ("SNOWFLAKE", "orders.amount"),
        ],
    )
    result = load_ossie_metric(foo, field_names=field_names, ctx=ctx)
    assert result == foo
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[ERROR] Field expression references field not in semantic model: foo.amount
Cause: ['foo', 'expression', 'dialects', 'ANSI_SQL']\
""")


def test_preserves_metric_with_invalid_reference(ctx: ExportContext) -> None:
    field_names = [("orders", "amount")]
    foo = Quick.metric(
        "foo",
        "Integer",
        [("ANSI_SQL", "foo.amount")],
    )
    result = load_ossie_metric(foo, field_names=field_names, ctx=ctx)
    assert result == foo
    assert problems_snapshot(ctx.problems, include_causes=True) == snapshot("""\
[ERROR] Field expression references field not in semantic model: foo.amount
Cause: ['foo', 'expression', 'dialects', 'ANSI_SQL']\
""")


def test_defaults_to_number_datatype(ctx: ExportContext) -> None:
    field_names = []
    foo = Quick.metric(
        "foo",
        None,
        [("ANSI_SQL", "SUM(1)")],
    )
    result = load_ossie_metric(foo, field_names=field_names, ctx=ctx)
    assert result is not None
    assert result.datatype == OssieDataType.DECIMAL
    assert problems_snapshot(ctx.problems) == snapshot(
        "[WARNING] Missing. Hex requires a datatype. Using default 'Decimal'."
    )
