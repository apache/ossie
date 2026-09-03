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

"""X5's enforcement point: every custom_extensions[THOUGHTSPOT] stash key
this converter reads on its Ossie -> TML direction has to declare, in
`constants.STASH_KEY_CLASSIFICATION`, whether it shadows a value this
converter could otherwise derive from the live Ossie document (and so needs
a witness and a currency check) or is information that exists nowhere else
(and so cannot go stale). Three keys were found reading the unsafe way
before this table existed — each found by generalising from the one before
it, never by a rule anyone consulted. This test is that rule, made
structural: a stash key read anywhere in ossie_to_thoughtspot.py that is
missing from the classification table fails here, so the question has to be
answered before the key is used, not left to memory.
"""
import re
from pathlib import Path

from ossie_thoughtspot import constants

_SRC = Path(__file__).resolve().parent.parent / "src" / "ossie_thoughtspot"

#: Every top-level custom_extensions[THOUGHTSPOT] key constant in
#: constants.py -- excludes witness-copy constants (a witness is not itself
#: a key this converter classifies; it is the currency check FOR one) and
#: the nested source_parts.{db,schema,db_table} sub-keys, which are never
#: read as standalone top-level payload keys.
_KEY_CONSTANT_RE = re.compile(r'^([A-Z][A-Z_0-9]*)\s*=\s*"', re.MULTILINE)


def _all_stash_key_constants() -> list[str]:
    text = (_SRC / "constants.py").read_text(encoding="utf-8")
    names = _KEY_CONSTANT_RE.findall(text)
    return [
        n for n in names
        if ("_STASH" in n or n == "STASH_TML_NAME")
        and not n.endswith("_WITNESS")
        and "SOURCE_PARTS_" not in n
    ]


def _keys_read_in_reverse_direction() -> set[str]:
    """Every stash-key VALUE (the payload string, e.g. "db_column_name" --
    the same shape STASH_KEY_CLASSIFICATION is keyed by) whose constant is
    imported into ossie_to_thoughtspot.py. The module only imports names it
    actually uses (nothing in this package imports a constant it never
    references), so import presence is a reliable proxy for "this key is
    read on the Ossie -> TML direction"."""
    text = (_SRC / "ossie_to_thoughtspot.py").read_text(encoding="utf-8")
    return {
        getattr(constants, name) for name in _all_stash_key_constants()
        if re.search(rf"\b{name}\b", text)
    }


def test_every_stash_key_constant_is_a_real_constants_attribute():
    # Guards the scan itself: a typo in _all_stash_key_constants' regex or
    # in this file would otherwise silently check nothing.
    for name in _all_stash_key_constants():
        assert hasattr(constants, name), name


def test_every_key_read_in_the_reverse_direction_is_classified():
    read_keys = _keys_read_in_reverse_direction()
    assert read_keys, "expected at least one stash key to be read"
    unclassified = sorted(read_keys - set(constants.STASH_KEY_CLASSIFICATION))
    assert unclassified == [], (
        f"stash key(s) read in ossie_to_thoughtspot.py with no entry in "
        f"STASH_KEY_CLASSIFICATION: {unclassified} -- classify each as "
        f"SHADOWS_DERIVABLE (needs a witness) or INFORMATION_ONLY before "
        f"reading it"
    )


def test_the_classification_table_names_no_key_that_does_not_exist():
    # The inverse check: every classified key must be a real constant's
    # value, so a renamed constant can't leave a stale string behind here.
    known_values = {getattr(constants, n) for n in _all_stash_key_constants()}
    unknown = sorted(k for k in constants.STASH_KEY_CLASSIFICATION if k not in known_values)
    assert unknown == []


def test_every_shadows_derivable_key_has_a_witness_constant_or_documented_self_check():
    """A SHADOWS_DERIVABLE key must be checkable for currency: either a
    `<NAME>_WITNESS` constant exists for it (the `stash.restore` path), or
    it is one of the keys documented as self-verifying (its own stashed
    value is reconstructed and compared against the live document directly,
    the same shape DATASET_STASH_SOURCE_PARTS and STASH_TML_NAME use).
    """
    self_verifying = {"DATASET_STASH_SOURCE_PARTS", "STASH_TML_NAME"}
    all_names = _all_stash_key_constants()
    name_by_value = {getattr(constants, n): n for n in all_names}
    for key, classification in constants.STASH_KEY_CLASSIFICATION.items():
        if classification is not constants.StashKeyClass.SHADOWS_DERIVABLE:
            continue
        name = name_by_value[key]
        if name in self_verifying:
            continue
        witness_name = f"{name}_WITNESS"
        assert hasattr(constants, witness_name), (
            f"{name} is classified SHADOWS_DERIVABLE but has no "
            f"{witness_name} constant and is not listed as self-verifying"
        )
