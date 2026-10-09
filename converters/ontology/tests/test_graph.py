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

"""Tests for the topological sorts.

An edge naming something outside `nodes` and a real cycle both used to stall
Kahn's algorithm, and both came back as "the graph contains a cycle". They are
different problems with different fixes, and only the caller knows what a
dangling endpoint of its own means — so the sorts ignore those edges and keep
the cycle report for cycles.
"""

from __future__ import annotations

import pytest

from ossie_ontology.common.graph import (
    is_acyclic_graph,
    topological_sort,
    topological_sort_break_cycles,
)


def _precedes(order: list[str], first: str, second: str) -> bool:
    return order.index(first) < order.index(second)


def test_topological_sort_orders_a_chain():
    order = topological_sort(["c", "b", "a"], [("a", "b"), ("b", "c")])

    assert sorted(order) == ["a", "b", "c"]
    assert _precedes(order, "a", "b") and _precedes(order, "b", "c")


def test_edge_to_an_unknown_node_is_ignored():
    """It orders nothing among the nodes given, so it constrains nothing."""
    order = topological_sort(["a", "b"], [("missing", "a"), ("a", "b")])

    assert sorted(order) == ["a", "b"]
    assert _precedes(order, "a", "b")


def test_edge_from_a_known_node_to_an_unknown_one_is_ignored():
    order = topological_sort(["a", "b"], [("a", "missing"), ("a", "b")])

    assert sorted(order) == ["a", "b"]


def test_cycle_still_raises():
    with pytest.raises(ValueError, match="contains a cycle"):
        topological_sort(["a", "b"], [("a", "b"), ("b", "a")])


def test_a_node_pointing_at_itself_is_a_cycle():
    with pytest.raises(ValueError, match="contains a cycle"):
        topological_sort(["a"], [("a", "a")])


def test_is_acyclic_graph_distinguishes_the_two():
    assert is_acyclic_graph(["a", "b"], [("missing", "a")])
    assert not is_acyclic_graph(["a", "b"], [("a", "b"), ("b", "a")])


def test_break_cycles_ignores_unknown_nodes_and_removes_nothing():
    """The cycle-breaking sort already skipped these; it stays that way."""
    order, removed = topological_sort_break_cycles(["a", "b"], [("missing", "a"), ("a", "b")])

    assert sorted(order) == ["a", "b"]
    assert removed == []


def test_break_cycles_removes_a_real_cycle_edge():
    with pytest.warns(UserWarning, match="Cycle detected"):
        order, removed = topological_sort_break_cycles(["a", "b"], [("a", "b"), ("b", "a")])

    assert sorted(order) == ["a", "b"]
    assert len(removed) == 1
