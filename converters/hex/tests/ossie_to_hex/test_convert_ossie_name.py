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
from ossie_hex.ossie_to_hex.convert_ossie_name import convert_ossie_name
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


def test_preserves_valid_id(ctx: ExportContext) -> None:
    ossie_name = "foo"
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result == ossie_name
    assert not ctx.problems


def test_lowercases_and_strips_without_notice(ctx: ExportContext) -> None:
    ossie_name = "  Foo  "
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result == "foo"
    assert not ctx.problems


def test_replaces_invalid_characters(ctx: ExportContext) -> None:
    ossie_name = "foo-bar"
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result == "foo_bar"
    assert problems_snapshot(ctx.problems) == snapshot(
        "[INFO] Normalized identifier: 'foo-bar' -> 'foo_bar'."
    )


def test_prefixes_leading_digit(ctx: ExportContext) -> None:
    ossie_name = "123"
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result == "_123"
    assert problems_snapshot(ctx.problems) == snapshot(
        "[INFO] Normalized identifier: '123' -> '_123'."
    )


def test_pads_short_name(ctx: ExportContext) -> None:
    ossie_name = "a"
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result == "a_"
    assert problems_snapshot(ctx.problems) == snapshot(
        "[INFO] Normalized identifier: 'a' -> 'a_'."
    )


def test_truncates_long_name(ctx: ExportContext) -> None:
    ossie_name = "a" * 129
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result == "a" * 128
    assert problems_snapshot(ctx.problems) == (
        f"[INFO] Normalized identifier: '{'a' * 129}' -> '{'a' * 128}'."
    )


@pytest.mark.parametrize("ossie_name", ["", "   "])
def test_rejects_empty_identifier(ctx: ExportContext, ossie_name: str) -> None:
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result is None
    assert (
        problems_snapshot(ctx.problems)
        == f"[ERROR] Unable to convert identifier: '{ossie_name}'."
    )


@pytest.mark.parametrize("ossie_name", ["dataset", "model", "this", "self", "env"])
def test_rejects_reserved_id(ctx: ExportContext, ossie_name: str) -> None:
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result is None
    assert (
        problems_snapshot(ctx.problems)
        == f"[ERROR] Unable to convert identifier: '{ossie_name}'."
    )


def test_rejects_reserved_prefix(ctx: ExportContext) -> None:
    ossie_name = "__hex_foo"
    result = convert_ossie_name(ossie_name, ctx=ctx)
    assert result is None
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Unable to convert identifier: '__hex_foo'."
    )
