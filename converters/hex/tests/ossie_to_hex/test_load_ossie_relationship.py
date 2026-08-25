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
from ossie_hex.ossie_to_hex.load_ossie_relationship import validate_ossie_relationship
from tests.ossie_to_hex.utils import Quick
from tests.utils import problems_snapshot


@pytest.fixture
def ctx() -> ExportContext:
    ctx = ExportContext()
    ctx._set_dialects("ANSI_SQL", "duckdb")
    return ctx


def test_warns_about_invalid_from(ctx: ExportContext) -> None:
    from_dataset = "bar"
    foo = Quick.relationship("foo", from_dataset, "baz", ["qux"], ["qoz"])
    dataset_names = set[str](["baz"])
    result = validate_ossie_relationship(foo, dataset_names=dataset_names, ctx=ctx)
    assert result is False
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Could not resolve dataset name: 'bar'."
    )


def test_warns_about_invalid_to(ctx: ExportContext) -> None:
    to_dataset = "baz"
    foo = Quick.relationship("foo", "bar", to_dataset, ["qux"], ["qoz"])
    dataset_names = set[str](["bar"])
    result = validate_ossie_relationship(foo, dataset_names=dataset_names, ctx=ctx)
    assert result is False
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] Could not resolve dataset name: 'baz'."
    )


def test_warns_about_mismatched_columns(ctx: ExportContext) -> None:
    from_columns = ["qux", "quux"]
    to_columns = ["qoz"]
    foo = Quick.relationship("foo", "bar", "baz", from_columns, to_columns)
    dataset_names = set[str](["bar", "baz"])
    result = validate_ossie_relationship(foo, dataset_names=dataset_names, ctx=ctx)
    assert result is False
    assert problems_snapshot(ctx.problems) == snapshot(
        "[ERROR] from_columns and to_columns must have equal length: 2 != 1"
    )
