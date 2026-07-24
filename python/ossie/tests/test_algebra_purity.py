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

"""Law §4.2 — Purity.

Every operator is pure: no I/O, no clocks, no randomness, no mutation of
inputs. Calling the same operator twice with the same arguments returns
equal results, and the input state is unchanged.

Mutation target: whole ``src/osi/planning/algebra/`` package.
"""

from __future__ import annotations

from copy import deepcopy

from hypothesis import given, settings
from strategies import (
    aggregate_column,
    dimension_column,
    fact_column,
    states,
)

from ossie.algebra import CalculationState, aggregate, project, source
from ossie.common.identifiers import normalize_identifier


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_project_does_not_mutate_state(state: CalculationState) -> None:
    before = deepcopy(state)
    _ = project(state, [c.name for c in state.columns])
    assert state == before


@given(state=states())
@settings(max_examples=200, deadline=None)
def test_project_is_deterministic(state: CalculationState) -> None:
    names = [c.name for c in state.columns]
    a = project(state, names)
    b = project(state, names)
    assert a == b
    assert a is not b or a == b


@given(state=states(min_facts=1, max_facts=3))
@settings(max_examples=100, deadline=None)
def test_aggregate_is_deterministic(state: CalculationState) -> None:
    target = state.grain
    fact = next(c for c in state.columns if c.kind.value == "fact")
    agg = aggregate_column(
        normalize_identifier("total_repeat"),
        over=fact.name,
    )
    a = aggregate(state, target, [agg])
    b = aggregate(state, target, [agg])
    assert a == b


def test_source_with_equal_args_is_equal() -> None:
    # Concrete case — property generator cannot compare because it
    # already returns a built state, but we can double-build here.
    pk = frozenset({normalize_identifier("a")})
    a = source(
        primary_key=pk,
        dimension_columns=[dimension_column(normalize_identifier("a"))],
        fact_columns=[fact_column(normalize_identifier("x"))],
    )
    b = source(
        primary_key=pk,
        dimension_columns=[dimension_column(normalize_identifier("a"))],
        fact_columns=[fact_column(normalize_identifier("x"))],
    )
    assert a == b
    # Strong structural equality — frozen dataclasses hash/compare by value.
    assert hash(a.grain) == hash(b.grain)
