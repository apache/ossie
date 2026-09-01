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

from ossie_thoughtspot import identifiers


@pytest.mark.parametrize("display,expected", [
    ("Order Date", "order_date"),
    ("Order-Date", "order_date"),
    ("Total Sales (AUD)", "total_sales_aud"),
    ("  Leading and trailing  ", "leading_and_trailing"),
    ("Multiple   spaces", "multiple_spaces"),
    ("Already_snake", "already_snake"),
    ("2024 Revenue", "n_2024_revenue"),
])
def test_normalise(display, expected):
    assert identifiers.normalise(display) == expected


def test_normalise_rejects_a_name_that_normalises_to_nothing():
    with pytest.raises(ValueError, match="normalises to an empty identifier"):
        identifiers.normalise("!!!")


def test_allocator_resolves_a_collision_with_a_numeric_suffix():
    # ID2: two distinct display names folding onto one identifier.
    alloc = identifiers.Allocator()
    assert alloc.allocate("Order Date") == "order_date"
    assert alloc.allocate("Order-Date") == "order_date_2"
    assert alloc.allocate("Order.Date") == "order_date_3"


def test_allocator_folds_case_when_detecting_collisions():
    # ID2: Ossie resolves regular identifiers case-insensitively, so a case-only
    # difference is ambiguous even though validate.py would accept it.
    alloc = identifiers.Allocator()
    assert alloc.allocate("Region") == "region"
    assert alloc.allocate("REGION") == "region_2"


def test_split_and_format_column_refs_round_trip():
    # ID3.
    assert identifiers.split_column_ref("[ORDERS::Order Date]") == ("ORDERS", "Order Date")
    assert identifiers.format_column_ref("ORDERS", "Order Date") == "[ORDERS::Order Date]"


def test_split_column_ref_rejects_a_malformed_reference():
    with pytest.raises(ValueError, match="not a ThoughtSpot column reference"):
        identifiers.split_column_ref("ORDERS::Order Date")
