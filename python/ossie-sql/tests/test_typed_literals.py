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

"""Typed-literal and CAST/TRY_CAST construction tests.

See the spec's "Date/Time Construction" and "Type Conversion Functions"
sections: typed literals (``DATE '...'``) and the equivalent ``CAST(...)``
form are declared interchangeable, so this asserts they parse to
structurally equal ASTs, not just that each round-trips individually.
"""

from __future__ import annotations

import pytest
import sqlglot

import ossie_sql  # noqa: F401


@pytest.mark.parametrize(
    "typed_literal,cast_form",
    [
        ("DATE '2024-01-15'", "CAST('2024-01-15' AS DATE)"),
        ("TIME '10:30:00'", "CAST('10:30:00' AS TIME)"),
        (
            "TIMESTAMP_NTZ '2024-01-15 10:30:00'",
            "CAST('2024-01-15 10:30:00' AS TIMESTAMP_NTZ)",
        ),
    ],
)
def test_typed_literal_equivalent_to_cast(typed_literal: str, cast_form: str) -> None:
    a = sqlglot.parse_one(typed_literal, read="ossie")
    b = sqlglot.parse_one(cast_form, read="ossie")
    assert a == b
    # Both spellings round-trip to the compact typed-literal form -- the
    # spec's primary documented construction syntax for these types.
    assert a.sql(dialect="ossie") == typed_literal
    assert b.sql(dialect="ossie") == typed_literal


def test_try_cast_returns_null_on_failure_syntax() -> None:
    parsed = sqlglot.parse_one("TRY_CAST(a AS INTEGER)", read="ossie")
    assert isinstance(parsed, sqlglot.exp.TryCast)
    # INTEGER / INT are spec-declared synonyms.
    assert parsed.sql(dialect="ossie") == "TRY_CAST(a AS INT)"


@pytest.mark.parametrize(
    "target_type",
    ["VARCHAR", "INTEGER", "DECIMAL", "FLOAT", "BOOLEAN", "DATE", "TIMESTAMP", "TIME"],
)
def test_cast_supports_spec_target_types(target_type: str) -> None:
    parsed = sqlglot.parse_one(f"CAST(a AS {target_type})", read="ossie")
    assert isinstance(parsed, sqlglot.exp.Cast)
