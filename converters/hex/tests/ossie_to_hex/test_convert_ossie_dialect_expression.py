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

from inline_snapshot import snapshot

from ossie_hex.ossie_to_hex.context import ExportContext
from ossie_hex.ossie_to_hex.convert_ossie_dialect_expression import (
    convert_ossie_dialect_expression,
)
from tests.ossie_to_hex.utils import Quick


def test_converts_without_resolver() -> None:
    dialect_expression = Quick.dialect_expression("ANSI_SQL", "amount")
    ctx = ExportContext()
    result = convert_ossie_dialect_expression(dialect_expression, resolve=None, ctx=ctx)
    assert result == snapshot("amount")
    assert not ctx.problems


def test_converts_with_no_references() -> None:
    dialect_expression = Quick.dialect_expression("ANSI_SQL", "SUM(1)")
    ctx = ExportContext()
    references: list[tuple[str, str]] = []

    def resolve(dataset_name: str, field_name: str) -> tuple[str | None, str]:
        references.append((dataset_name, field_name))
        return dataset_name, field_name

    result = convert_ossie_dialect_expression(
        dialect_expression,
        resolve=resolve,
        ctx=ctx,
    )

    assert result == snapshot("SUM(1)")
    assert references == []
    assert not ctx.problems


def test_converts_with_one_qualified_reference() -> None:
    dialect_expression = Quick.dialect_expression("ANSI_SQL", "SUM(orders.amount)")
    ctx = ExportContext()
    references: list[tuple[str, str]] = []

    def resolve(dataset_name: str, field_name: str) -> tuple[str | None, str]:
        references.append((dataset_name, field_name))
        return dataset_name, field_name

    result = convert_ossie_dialect_expression(
        dialect_expression,
        resolve=resolve,
        ctx=ctx,
    )

    assert result == snapshot("SUM(${orders.amount})")
    assert references == [("orders", "amount")]
    assert not ctx.problems


def test_converts_with_many_qualified_references() -> None:
    dialect_expression = Quick.dialect_expression(
        "ANSI_SQL",
        "SUM(orders.amount) + customers.id",
    )
    ctx = ExportContext()
    references: list[tuple[str, str]] = []

    def resolve(dataset_name: str, field_name: str) -> tuple[str | None, str]:
        references.append((dataset_name, field_name))
        return dataset_name, field_name

    result = convert_ossie_dialect_expression(
        dialect_expression,
        resolve=resolve,
        ctx=ctx,
    )

    assert result == snapshot("SUM(${orders.amount}) + ${customers.id}")
    assert references == [("orders", "amount"), ("customers", "id")]
    assert not ctx.problems
