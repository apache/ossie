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

"""Shared fixtures for the Microsoft converter tests."""

import json
import warnings
from pathlib import Path

import pytest
import yaml

from ossie_microsoft import convert_semantic_model_to_ossie

FIXTURES = Path(__file__).parent / "fixtures"
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def bim():
    with open(FIXTURES / "sales_model.bim", encoding="utf-8-sig") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def osi(bim):
    # The fixture deliberately exercises every lossy path, so it warns by design.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return yaml.safe_load(convert_semantic_model_to_ossie(bim))


@pytest.fixture(scope="module")
def model(osi):
    return osi["semantic_model"][0]
