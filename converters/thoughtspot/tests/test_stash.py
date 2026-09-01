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

import json

import pytest

from ossie_thoughtspot import stash
from ossie_thoughtspot.constants import STASH_VERSION, VENDOR_KEY
from ossie_thoughtspot.errors import ConversionError


def test_write_stash_serialises_data_as_a_json_string_not_an_object():
    # X2: ossie-schema.json types `data` as "string".
    obj = stash.write_stash({}, {"join_type": "LEFT_OUTER"})
    entry = obj["custom_extensions"][0]
    assert entry["vendor_name"] == VENDOR_KEY
    assert isinstance(entry["data"], str)
    assert json.loads(entry["data"])["join_type"] == "LEFT_OUTER"


def test_write_stash_stamps_the_shape_version():
    obj = stash.write_stash({}, {"k": "v"})
    assert json.loads(obj["custom_extensions"][0]["data"])["_v"] == STASH_VERSION


def test_write_stash_writes_nothing_for_an_empty_payload():
    # X6: a converted document stays clean where ThoughtSpot added nothing.
    assert stash.write_stash({}, {}) == {}


def test_write_stash_merges_into_the_existing_own_entry():
    # X1: one entry per object, merged — never a second THOUGHTSPOT entry.
    obj = stash.write_stash({}, {"a": 1})
    obj = stash.write_stash(obj, {"b": 2})
    own = [e for e in obj["custom_extensions"] if e["vendor_name"] == VENDOR_KEY]
    assert len(own) == 1
    assert json.loads(own[0]["data"])["a"] == 1
    assert json.loads(own[0]["data"])["b"] == 2


def test_foreign_vendor_entries_pass_through_untouched():
    # X7.
    obj = {"custom_extensions": [{"vendor_name": "DATABRICKS", "data": '{"x": 1}'}]}
    out = stash.write_stash(obj, {"a": 1})
    foreign = [e for e in out["custom_extensions"] if e["vendor_name"] == "DATABRICKS"]
    assert foreign == [{"vendor_name": "DATABRICKS", "data": '{"x": 1}'}]


def test_write_stash_refuses_identity_keys():
    # X8: a portable document must not carry instance-local identity.
    for key in ("guid", "obj_id", "fqn"):
        with pytest.raises(ConversionError, match=key):
            stash.write_stash({}, {key: "abc-123"})


def test_read_stash_raises_a_named_error_on_malformed_json():
    # X4: never a bare json traceback.
    obj = {"name": "orders", "custom_extensions": [{"vendor_name": VENDOR_KEY, "data": "{not json"}]}
    with pytest.raises(ConversionError, match="orders"):
        stash.read_stash(obj)


def test_read_stash_returns_empty_when_there_is_no_own_entry():
    assert stash.read_stash({"custom_extensions": [{"vendor_name": "OMNI", "data": "{}"}]}) == {}


def test_restore_prefers_the_stash_when_the_witness_still_agrees():
    # X5, positive case.
    payload = {"on_expression": "a = b", "ossie_expression": "a = b"}
    assert stash.restore(payload, "on_expression", "DERIVED",
                         witness="a = b", witness_key="ossie_expression") == "a = b"


def test_restore_rederives_when_the_witness_has_changed():
    # X5, the case a plain stash-if-present rule gets wrong: the user edited the
    # Ossie document, so the stashed copy is stale and must not win.
    payload = {"on_expression": "a = b", "ossie_expression": "a = b"}
    assert stash.restore(payload, "on_expression", "DERIVED",
                         witness="a = c", witness_key="ossie_expression") == "DERIVED"


def test_restore_falls_back_to_derived_when_the_key_is_absent():
    assert stash.restore({}, "on_expression", "DERIVED") == "DERIVED"
