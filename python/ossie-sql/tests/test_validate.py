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

"""Tests for ossie_sql.validate.validate_expression().

Covers the spec's "Not Supported in Expressions" table (SELECT/FROM/JOIN,
GROUP BY, WHERE, subqueries, CTEs, set operations, DDL/DML) plus the
pass-through-unknown-functions behavior from the "Dialect Extensions"
section.
"""

from __future__ import annotations

import pytest
import sqlglot

import ossie_sql  # noqa: F401
from ossie_sql.validate import UnsupportedConstructError, validate_expression

VALID_EXPRESSIONS = [
    "SUM(x)",
    "CASE WHEN a THEN 1 ELSE 0 END",
    "x IN (1, 2, 3)",
    "amount / SUM(amount) OVER () * 100",
    "x BETWEEN a AND b",
    # Vendor/unknown function name: passes through, per the spec's default.
    "SOME_VENDOR_SPECIFIC_FUNC(x, y)",
    "EXISTS_IN(x)",
]


@pytest.mark.parametrize("source", VALID_EXPRESSIONS)
def test_valid_expressions_pass(source: str) -> None:
    parsed = sqlglot.parse_one(source, read="ossie")
    validate_expression(parsed)  # must not raise


DISALLOWED_EXPRESSIONS = [
    "SELECT * FROM t",
    "x IN (SELECT id FROM t)",
    "WITH cte AS (SELECT 1) SELECT * FROM cte",
    "SELECT 1 UNION SELECT 2",
    "SELECT a FROM t1 JOIN t2 ON t1.id = t2.id",
]


@pytest.mark.parametrize("source", DISALLOWED_EXPRESSIONS)
def test_disallowed_constructs_raise(source: str) -> None:
    parsed = sqlglot.parse_one(source, read="ossie")
    with pytest.raises(UnsupportedConstructError):
        validate_expression(parsed)


def test_groups_frame_mode_rejected() -> None:
    parsed = sqlglot.parse_one(
        "SUM(x) OVER (ORDER BY d GROUPS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)",
        read="ossie",
    )
    with pytest.raises(UnsupportedConstructError):
        validate_expression(parsed)


def test_rows_and_range_frame_modes_accepted() -> None:
    for frame in [
        "ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
        "ROWS BETWEEN 6 PRECEDING AND CURRENT ROW",
        "RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW",
    ]:
        parsed = sqlglot.parse_one(f"SUM(x) OVER (ORDER BY d {frame})", read="ossie")
        validate_expression(parsed)  # must not raise
