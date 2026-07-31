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

"""Tests for ossie_sql.functions.compliance_level()."""

from __future__ import annotations

import pytest

from ossie_sql.functions import (
    EXPERIMENTAL_FUNCTIONS,
    RECOMMENDED_FUNCTIONS,
    REQUIRED_FUNCTIONS,
    compliance_level,
)

REQUIRED_EXAMPLES = [
    "SUM",
    "COUNT",
    "AVG",
    "MEDIAN",
    "PERCENTILE_CONT",
    "YEAR",
    "DATEADD",
    "DATEDIFF",
    "EXTRACT",
    "DATE_PART",
    "CONCAT",
    "POSITION",
    "STARTSWITH",
    "ABS",
    "MOD",
    "IF",
    "IFF",
    "ZEROIFNULL",
    "ROW_NUMBER",
    "LAG",
    "CAST",
]

RECOMMENDED_EXAMPLES = [
    "APPROX_COUNT_DISTINCT",
    "APPROX_PERCENTILE",
    "REGEXP_EXTRACT",
    "SIN",
    "PI",
    "TRY_CAST",
]

EXPERIMENTAL_EXAMPLES = ["TO_CHAR"]


@pytest.mark.parametrize("name", REQUIRED_EXAMPLES)
def test_required_functions(name: str) -> None:
    assert compliance_level(name) == "REQUIRED"


@pytest.mark.parametrize("name", RECOMMENDED_EXAMPLES)
def test_recommended_functions(name: str) -> None:
    assert compliance_level(name) == "RECOMMENDED"


@pytest.mark.parametrize("name", EXPERIMENTAL_EXAMPLES)
def test_experimental_functions(name: str) -> None:
    assert compliance_level(name) == "EXPERIMENTAL"


@pytest.mark.parametrize("name", ["sum", "Sum", "sUM"])
def test_compliance_level_is_case_insensitive(name: str) -> None:
    assert compliance_level(name) == "REQUIRED"


@pytest.mark.parametrize(
    "name",
    [
        "SNOWFLAKE_VENDOR_FUNC",
        "TO_VARCHAR",
        "TO_NUMBER",
        "TO_BOOLEAN",
        "EXISTS_IN",
        "NOT_A_FUNCTION",
    ],
)
def test_unknown_functions_return_none(name: str) -> None:
    # These are either vendor extensions or names from an earlier draft that
    # the committed spec never defines -- compliance_level() reports them as
    # unknown (None) rather than raising, matching the spec's pass-through
    # philosophy for anything outside its own tables.
    assert compliance_level(name) is None


def test_tiers_are_disjoint() -> None:
    assert not (REQUIRED_FUNCTIONS & RECOMMENDED_FUNCTIONS)
    assert not (REQUIRED_FUNCTIONS & EXPERIMENTAL_FUNCTIONS)
    assert not (RECOMMENDED_FUNCTIONS & EXPERIMENTAL_FUNCTIONS)


def test_all_names_are_upper_case() -> None:
    for name in REQUIRED_FUNCTIONS | RECOMMENDED_FUNCTIONS | EXPERIMENTAL_FUNCTIONS:
        assert name == name.upper()
