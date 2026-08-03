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

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def minimal_hex_path() -> str:
    return str(FIXTURES / "minimal_hex")


@pytest.fixture
def named_joins_hex_path() -> str:
    return str(FIXTURES / "named_joins_hex")


@pytest.fixture
def query_hex_path() -> str:
    return str(FIXTURES / "query_hex")


@pytest.fixture
def formula_measure_hex_path() -> str:
    return str(FIXTURES / "formula_measure_hex")


@pytest.fixture
def calc_dimension_hex_path() -> str:
    return str(FIXTURES / "calc_dimension_hex")


@pytest.fixture(
    params=[
        pytest.param(str(FIXTURES / "minimal_hex"), id="minimal_hex"),
        pytest.param(str(FIXTURES / "named_joins_hex"), id="named_joins_hex"),
        pytest.param(str(FIXTURES / "query_hex"), id="query_hex"),
        pytest.param(str(FIXTURES / "formula_measure_hex"), id="formula_measure_hex"),
        pytest.param(str(FIXTURES / "calc_dimension_hex"), id="calc_dimension_hex"),
    ]
)
def hex_project_path(request: pytest.FixtureRequest) -> str:
    assert isinstance(request.param, str)
    return request.param


@pytest.fixture
def tpcds_ossie_yaml() -> str:
    path = REPO_ROOT / "examples" / "tpcds_semantic_model.yaml"
    return path.read_text(encoding="utf-8")
