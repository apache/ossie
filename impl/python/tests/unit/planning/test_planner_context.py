"""Unit tests for :mod:`osi.planning.planner_context`."""

from __future__ import annotations

import pytest

from tests.unit.planning.fixtures import orders_context


def test_context_bundles_model_namespace_graph() -> None:
    ctx = orders_context()
    assert ctx.model.name.lower() == "demo"
    assert "orders" in ctx.namespace.datasets
    assert len(ctx.graph.edges) == 2


def test_context_is_frozen() -> None:
    ctx = orders_context()
    from dataclasses import FrozenInstanceError

    with pytest.raises((FrozenInstanceError, AttributeError)):
        ctx.model = None  # type: ignore[misc]
