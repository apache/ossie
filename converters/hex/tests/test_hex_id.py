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

from ossie_hex.hex_types import normalize_to_hex_id
from ossie_hex.util.errors import ConversionError


def test_coerce_hex_id_prefixes_number_with_underscore() -> None:
    assert normalize_to_hex_id('"123 Orders"', "dataset", set()) == "_123_orders"


def test_coerce_hex_id_replaces_empty_result() -> None:
    assert normalize_to_hex_id('"!"', "dataset", set()) == "_1"


def test_normalize_to_hex_id_preserves_valid_id() -> None:
    assert normalize_to_hex_id("order_items", "dataset", set()) == "order_items"


def test_normalize_to_hex_id_rejects_collisions() -> None:
    with pytest.raises(ConversionError, match="collides"):
        normalize_to_hex_id("Orders", "dataset", {"orders"})


@pytest.mark.parametrize("name", ["", "   "])
def test_normalize_to_hex_id_rejects_a_blank_name(name: str) -> None:
    """A name with nothing in it is not the same as one that coerces to nothing.

    ``"!"`` has a character to work from and lands on a placeholder ID, but a
    blank name would have the converter invent the whole thing.
    """
    with pytest.raises(ConversionError, match="dataset has a blank name"):
        normalize_to_hex_id(name, "dataset", set())
