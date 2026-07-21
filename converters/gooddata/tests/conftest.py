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

"""Shared pytest fixtures for Apache Ossie GoodData converter tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ossie_gooddata.models import GdDeclarativeModel, gd_model_from_dict

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def gooddata_tpcds_dict() -> dict:
    """Load the GoodData TPC-DS declarative model JSON fixture."""
    with open(FIXTURES_DIR / "gooddata_tpcds.json") as f:
        return json.load(f)


@pytest.fixture()
def gooddata_tpcds_model(gooddata_tpcds_dict: dict) -> GdDeclarativeModel:
    """Parse the GoodData TPC-DS fixture into typed dataclasses."""
    return gd_model_from_dict(gooddata_tpcds_dict)


@pytest.fixture()
def osi_tpcds_dict() -> dict:
    """Load the Ossie TPC-DS YAML fixture."""
    with open(FIXTURES_DIR / "osi_tpcds.yaml") as f:
        return yaml.safe_load(f)
