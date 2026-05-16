"""Unit tests for :func:`osi.diagnostics.describe`."""

from __future__ import annotations

from osi.diagnostics import describe, describe_json
from tests.unit.planning.fixtures import orders_context


def _model():
    return orders_context().model


def test_describe__mentions_every_dataset_by_name() -> None:
    text = describe(_model())
    for name in ("orders", "customers", "returns"):
        assert name in text, f"expected dataset {name!r} in description"


def test_describe__shows_sources() -> None:
    text = describe(_model())
    assert "sales.orders" in text
    assert "sales.customers" in text
    assert "sales.returns" in text


def test_describe__groups_fields_under_their_dataset() -> None:
    text = describe(_model())
    # ``amount`` lives on orders — it should appear *after* the orders
    # header and *before* the next dataset header.
    orders_idx = text.index("- orders")
    customers_idx = text.index("- customers")
    amount_idx = text.index("amount")
    assert orders_idx < amount_idx < customers_idx


def test_describe__lists_all_relationships() -> None:
    text = describe(_model())
    assert "orders_to_customers" in text
    assert "returns_to_customers" in text
    assert "→" in text


def test_describe_json__is_deterministic() -> None:
    model = _model()
    first = describe_json(model)
    second = describe_json(model)
    assert first == second
    assert first["name"] == "demo"
    assert [d["name"] for d in first["datasets"]] == [
        "orders",
        "customers",
        "returns",
    ]


def test_describe_json__dataset_metrics_present() -> None:
    view = describe_json(_model())
    orders = next(d for d in view["datasets"] if d["name"] == "orders")
    metric_names = {m["name"] for m in orders["metrics"]}
    assert "total_revenue" in metric_names
