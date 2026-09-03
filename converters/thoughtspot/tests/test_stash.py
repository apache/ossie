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


def test_restore_returns_the_stashed_value_with_no_witness_key():
    # X5, degraded (stash-if-present) shape — the most common form in
    # practice: no witness_key, so a present key always wins regardless of
    # `witness`. Correct only for values nothing downstream can edit.
    payload = {"some_key": "stashed_value"}
    assert stash.restore(payload, "some_key", "DERIVED") == "stashed_value"


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


class TestFindForbiddenKeyIsTheSingleChokePoint:
    """write_stash is the one function every stashed payload passes through,
    so the identity guard has to live there rather than at each caller --
    otherwise a caller that copies a whole sub-object verbatim (a model's
    parameters[], filters[], ...) rather than rebuilding it field by field
    can carry a forbidden key arbitrarily deep with nothing to catch it.

    Each payload shape below mirrors a real model-scope stash field this
    package copies wholesale: a nested identity key inside any of them must
    be caught the same way. The point of the last case is that this list
    does not have to be exhaustive for the guard to work -- an entirely
    unrelated, previously unseen key name is caught too, because the guard
    scans by shape (any key named guid/obj_id/fqn) rather than by an
    enumeration of known field names."""

    SHAPES = {
        "parameters": [{"name": "P", "default_value": {"obj_id": "p-1"}}],
        "filters": [{"column": "Region", "values": ["US", {"nested": {"fqn": "f-1"}}]}],
        "column_groups": [{"name": "Sales", "meta": {"guid": "g-1"}}],
        "lesson_plans": [{"lesson_id": 0, "extra": {"obj_id": "l-1"}}],
        "action_object_associations": [{"action_name": "A", "context": {"fqn": "a-1"}}],
        "constraints": {"rolling": {"window": {"guid": "c-1"}}},
        "model_joins_with": [{"name": "j", "destination": {"fqn": "j-1"}}],
        # A field name this module has never heard of -- the fail-closed
        # property itself: the guard must not depend on a list of known
        # model-scope keys to check.
        "a_future_property_nobody_has_named_yet": {"deeply": {"nested": {"obj_id": "u-1"}}},
    }

    @pytest.mark.parametrize("key,value", SHAPES.items(), ids=SHAPES.keys())
    def test_a_nested_identity_key_is_caught_regardless_of_which_field_carries_it(self, key, value):
        with pytest.raises(ConversionError):
            stash.write_stash({}, {key: value})

    def test_find_forbidden_key_names_the_key_it_found(self):
        assert stash.find_forbidden_key({"a": {"b": [{"obj_id": "x"}]}}) == "obj_id"

    def test_find_forbidden_key_returns_none_for_a_clean_payload(self):
        assert stash.find_forbidden_key({"a": {"b": ["ordinary", "values"]}}) is None

    def test_find_forbidden_key_accepts_a_wider_vocabulary_than_the_default(self):
        # tml_to_ossie.py's column-properties path checks a wider identity
        # vocabulary than X8's own three names (this package's own
        # dataset_id/custom_file_guid additions) -- find_forbidden_key has to
        # support that without stash.py hard-coding a second, wider set.
        wider = frozenset({"custom_file_guid"})
        assert stash.find_forbidden_key({"geo_config": {"custom_file_guid": "m-1"}}, wider) == "custom_file_guid"
        assert stash.find_forbidden_key({"geo_config": {"custom_file_guid": "m-1"}}) is None


class TestReadStashShapeVersion:
    def test_an_unrecognised_shape_version_raises_naming_the_object_and_version(self):
        # X3: a future payload shape must never be partially read as today's.
        obj = {"name": "orders", "custom_extensions": [
            {"vendor_name": VENDOR_KEY, "data": json.dumps({"_v": 999, "alias": "X"})}
        ]}
        with pytest.raises(ConversionError, match="orders") as excinfo:
            stash.read_stash(obj)
        assert "999" in str(excinfo.value)

    def test_a_missing_shape_version_raises_too(self):
        obj = {"name": "orders", "custom_extensions": [
            {"vendor_name": VENDOR_KEY, "data": json.dumps({"alias": "X"})}
        ]}
        with pytest.raises(ConversionError, match="orders"):
            stash.read_stash(obj)

    def test_the_current_shape_version_reads_normally(self):
        obj = {"name": "orders", "custom_extensions": [
            {"vendor_name": VENDOR_KEY, "data": json.dumps({"_v": STASH_VERSION, "alias": "X"})}
        ]}
        assert stash.read_stash(obj) == {"_v": STASH_VERSION, "alias": "X"}
