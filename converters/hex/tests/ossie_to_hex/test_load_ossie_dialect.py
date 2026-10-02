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
from ossie_hex.ossie_to_hex.load_ossie_dialect import load_ossie_dialect
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


@pytest.mark.parametrize("ossie_dialect", list(OssieDialect))
def test_preserves_enum(ctx: ExportContext, ossie_dialect: OssieDialect) -> None:
    result = load_ossie_dialect(ossie_dialect, ctx=ctx)
    assert result is ossie_dialect
    assert not ctx.problems


@pytest.mark.parametrize("ossie_dialect", list(OssieDialect))
def test_parses_valid_string(ctx: ExportContext, ossie_dialect: OssieDialect) -> None:
    result = load_ossie_dialect(ossie_dialect.value, ctx=ctx)
    assert result is ossie_dialect
    assert not ctx.problems


@pytest.mark.parametrize("ossie_dialect", list(OssieDialect))
def test_parses_lowercase_string(
    ctx: ExportContext, ossie_dialect: OssieDialect
) -> None:
    result = load_ossie_dialect(ossie_dialect.value.lower(), ctx=ctx)
    assert result is ossie_dialect
    assert not ctx.problems


def test_warns_and_defaults_for_invalid_string(ctx: ExportContext) -> None:
    ossie_dialect = "not_a_dialect"
    result = load_ossie_dialect(ossie_dialect, ctx=ctx)
    assert result is OssieDialect.ANSI_SQL
    assert problems_snapshot(ctx.problems) == snapshot(
        "[WARNING] Invalid Ossie dialect: not_a_dialect. Using ANSI_SQL instead."
    )


def test_defaults_when_missing(ctx: ExportContext) -> None:
    result = load_ossie_dialect(None, ctx=ctx)
    assert result is OssieDialect.ANSI_SQL
    assert problems_snapshot(ctx.problems) == snapshot(
        "[INFO] No Ossie dialect specified; using ANSI_SQL"
    )
