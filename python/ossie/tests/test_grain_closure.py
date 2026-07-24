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

"""Law §4.4 — Closure of Grain.

For any step sequence the resulting grain is a pure function of the
argument stream. The symbolic simulator in
:mod:`ossie.algebra.grain` and the concrete algebra agree.

Mutation target: ``src/osi/planning/algebra/grain.py``.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from strategies import aggregate_column, states

from ossie.algebra import CalculationState, aggregate, filter_, project
from ossie.algebra.grain import (
    AggregateStep,
    OperatorTag,
    SimpleStep,
    SourceStep,
    simulate_grain,
)
from ossie.common.identifiers import normalize_identifier


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_source_grain_matches_simulation(state: CalculationState) -> None:
    symbolic = simulate_grain((SourceStep(OperatorTag.SOURCE, state.grain),))
    assert symbolic == state.grain


@given(data=st.data(), state=states())
@settings(max_examples=200, deadline=None)
def test_aggregate_grain_matches_simulation(
    data: st.DataObject, state: CalculationState
) -> None:
    # Draw the aggregation target as a non-empty subset of the state's
    # grain — this trivially satisfies the aggregate precondition and
    # avoids filtering out most generated states via ``assume``.
    grain_list = sorted(state.grain)
    size = data.draw(st.integers(min_value=1, max_value=len(grain_list)))
    target = frozenset(data.draw(st.permutations(grain_list))[:size])
    fact_names = [c.name for c in state.columns if c.kind.value == "fact"]
    aggs = (
        [aggregate_column(_unused_name(state), over=fact_names[0])]
        if fact_names
        else []
    )
    concrete = aggregate(state, target, aggs)
    symbolic = simulate_grain(
        (
            SourceStep(OperatorTag.SOURCE, state.grain),
            AggregateStep(OperatorTag.AGGREGATE, target),
        )
    )
    assert concrete.grain == symbolic == target


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_project_preserves_grain(state: CalculationState) -> None:
    concrete = project(state, [c.name for c in state.columns])
    symbolic = simulate_grain(
        (
            SourceStep(OperatorTag.SOURCE, state.grain),
            SimpleStep(OperatorTag.PROJECT),
        )
    )
    assert concrete.grain == symbolic == state.grain


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_filter_preserves_grain(state: CalculationState) -> None:
    # Use a predicate with no dependencies — structural preservation
    # is what the law tests, not predicate validation.
    import sqlglot

    from ossie.common.sql_expr import FrozenSQL

    pred = FrozenSQL.of(sqlglot.parse_one("1 = 1"))
    concrete = filter_(state, pred, dependencies=frozenset())
    symbolic = simulate_grain(
        (
            SourceStep(OperatorTag.SOURCE, state.grain),
            SimpleStep(OperatorTag.FILTER),
        )
    )
    assert concrete.grain == symbolic == state.grain


def _unused_name(state: CalculationState) -> str:
    i = 0
    while True:
        candidate = normalize_identifier(f"agg_{i}")
        if candidate not in state.column_names:
            return candidate
        i += 1
