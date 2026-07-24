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

"""Law §4.8 — Projection Idempotence.

``project(project(s, c1), c2) ≡ project(s, c2)`` whenever ``c2 ⊆ c1``
and ``c2`` covers ``s.grain``.

Mutation target: ``src/osi/planning/algebra/operations.py::project``.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from strategies import states

from ossie.algebra import CalculationState, project


@given(state=states(min_dims=1, max_dims=3, min_facts=1, max_facts=3), data=st.data())
@settings(max_examples=200, deadline=None)
def test_project_is_idempotent(state: CalculationState, data) -> None:
    # c1 ⊇ c2 ⊇ grain so both projections succeed.
    all_names = [c.name for c in state.columns]
    grain_names = sorted(state.grain)
    extras = [n for n in all_names if n not in state.grain]
    # Draw a size for c2 from [len(grain), len(all_names)], then grow c1.
    size_c2 = data.draw(
        st.integers(min_value=len(grain_names), max_value=len(all_names))
    )
    c2_extras = data.draw(
        st.lists(
            st.sampled_from(extras) if extras else st.nothing(),
            max_size=max(0, size_c2 - len(grain_names)),
            unique=True,
        )
        if extras
        else st.just([])
    )
    c2 = grain_names + list(dict.fromkeys(c2_extras))
    # Draw c1 strictly containing c2.
    remaining = [n for n in extras if n not in c2_extras]
    c1_extras = data.draw(
        st.lists(
            st.sampled_from(remaining) if remaining else st.nothing(),
            max_size=len(remaining),
            unique=True,
        )
        if remaining
        else st.just([])
    )
    c1 = c2 + list(dict.fromkeys(c1_extras))

    via_two = project(project(state, c1), c2)
    direct = project(state, c2)
    assert via_two == direct
