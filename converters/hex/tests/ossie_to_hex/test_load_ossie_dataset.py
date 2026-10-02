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
from ossie import OssieDataset

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.load_ossie_dataset import load_ossie_dataset
from tests.ossie_to_hex.utils import Quick


@pytest.fixture
def ctx() -> ExportContext:
    return ExportContext()


@pytest.fixture
def foo() -> OssieDataset:
    return Quick.dataset(
        name="foo",
        source="public.foo",
        fields=[("id", "String", [("ANSI_SQL", "id")])],
    )


def test_keeps_valid_fields(ctx: ExportContext) -> None:
    foo = Quick.dataset(
        name="foo",
        source="public.foo",
        fields=[("id", "String", [("ANSI_SQL", "id")])],
    )
    result = load_ossie_dataset(foo, ctx=ctx)
    assert result.name == foo.name
    assert result.fields is not None
    assert len(result.fields) == 1
    assert result.fields[0].name == "id"
    assert not ctx.problems


def test_removes_invalid_fields(ctx: ExportContext) -> None:

    foo = Quick.dataset(
        name="foo",
        source="public.foo",
        fields=[
            ("good", "String", [("ANSI_SQL", "id")]),
            ("bad", "String", [("ANSI_SQL", "SELECT FROM")]),
        ],
    )
    result = load_ossie_dataset(foo, ctx=ctx)
    assert result.fields is not None
    assert len(result.fields) == 1
    assert result.fields[0].name == "good"
    assert ctx.problems
