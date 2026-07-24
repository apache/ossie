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

"""Law §4.6 — Filter Commutativity.

``filter(filter(s, p1), p2)`` yields a state structurally equivalent to
``filter(filter(s, p2), p1)``. The Foundation algebra represents filter
by a no-op on the state (predicates live on the plan step), so this
law reduces to "filter never changes the state shape, regardless of
order."

A DuckDB-executed row-set equivalence test is added alongside the
Phase 4 codegen harness.
"""

from __future__ import annotations

import sqlglot
from hypothesis import given, settings
from strategies import states

from ossie.algebra import CalculationState, filter_
from ossie.common.sql_expr import FrozenSQL

_p1 = FrozenSQL.of(sqlglot.parse_one("1 = 1"))
_p2 = FrozenSQL.of(sqlglot.parse_one("2 = 2"))


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_filter_order_does_not_change_state_shape(
    state: CalculationState,
) -> None:
    left = filter_(
        filter_(state, _p1, dependencies=frozenset()), _p2, dependencies=frozenset()
    )
    right = filter_(
        filter_(state, _p2, dependencies=frozenset()), _p1, dependencies=frozenset()
    )
    assert left == right
