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

"""Law §4.5 — Aggregate Idempotence at same grain.

For any state whose grain already matches ``target_grain`` and whose
aggregations are identity re-aggregations at that grain, ``aggregate``
returns a state that agrees on grain and columns.

Mutation target: ``src/osi/planning/algebra/operations.py::aggregate``.
"""

from __future__ import annotations

from hypothesis import given, settings
from strategies import aggregate_column, states

from ossie.algebra import CalculationState, aggregate
from ossie.common.identifiers import normalize_identifier


@given(state=states(min_facts=1, max_facts=3))
@settings(max_examples=200, deadline=None)
def test_same_grain_agg_preserves_grain(state: CalculationState) -> None:
    fact = next(c for c in state.columns if c.kind.value == "fact")
    out = aggregate(
        state,
        state.grain,
        [
            aggregate_column(
                normalize_identifier(f"total_{fact.name}"),
                over=fact.name,
            )
        ],
    )
    assert out.grain == state.grain
