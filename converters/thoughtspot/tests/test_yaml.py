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

from ossie_thoughtspot import _yaml

YAML11_BOOL_TOKENS = ["on", "On", "ON", "off", "Off", "yes", "Yes", "no", "No", "y", "n"]


@pytest.mark.parametrize("token", YAML11_BOOL_TOKENS)
def test_yaml11_bool_tokens_load_as_strings(token):
    # PyYAML implements YAML 1.1 and would return True/False for these.
    assert _yaml.load(f"value: {token}") == {"value": token}


@pytest.mark.parametrize("literal,expected", [("true", True), ("True", True), ("false", False)])
def test_real_booleans_still_load_as_booleans(literal, expected):
    assert _yaml.load(f"value: {literal}") == {"value": expected}


@pytest.mark.parametrize("token", YAML11_BOOL_TOKENS)
def test_yaml11_bool_tokens_are_quoted_on_dump(token):
    # Unquoted, a YAML 1.1 reader downstream would resolve these back to booleans.
    assert _yaml.load(_yaml.dump({"value": token})) == {"value": token}
    assert f"'{token}'" in _yaml.dump({"value": token})


def test_ordinary_strings_are_not_gratuitously_quoted():
    assert _yaml.dump({"value": "Region"}).strip() == "value: Region"


def test_round_trip_preserves_key_order():
    src = {"z": 1, "a": 2, "m": 3}
    assert list(_yaml.load(_yaml.dump(src))) == ["z", "a", "m"]
