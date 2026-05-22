"""Unit tests for :mod:`osi.parsing.namespace`."""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.models import (
    Dataset,
    Field,
    Metric,
    NamedFilter,
    Parameter,
    SemanticModel,
)
from osi.parsing.namespace import build_namespace


def _orders() -> Dataset:
    return Dataset(
        name="orders",
        source="sales.orders",
        primary_key=["id"],
        fields=[
            Field(name="id", expression="id"),
            Field(name="amount", expression="amount", role="fact"),
        ],
        metrics=[Metric(name="revenue", expression="SUM(amount)")],
    )


def _customers() -> Dataset:
    return Dataset(
        name="customers",
        source="sales.customers",
        primary_key=["id"],
        fields=[
            Field(name="id", expression="id"),
            Field(name="email", expression="email"),
        ],
    )


class TestBuildNamespace:
    def test_datasets_indexed(self) -> None:
        model = SemanticModel(name="m", datasets=[_orders(), _customers()])
        ns = build_namespace(model)
        assert set(ns.datasets) == {
            normalize_identifier("orders"),
            normalize_identifier("customers"),
        }

    def test_global_metrics_filters_parameters_indexed(self) -> None:
        model = SemanticModel(
            name="m",
            datasets=[_orders()],
            metrics=[Metric(name="g_rev", expression="SUM(orders.amount)")],
            filters=[NamedFilter(name="done", expression="status = 'done'")],
            parameters=[Parameter(name="min_amount", data_type="NUMBER", default=0)],
        )
        ns = build_namespace(model)
        assert normalize_identifier("g_rev") in ns.metrics
        assert normalize_identifier("done") in ns.filters
        assert normalize_identifier("min_amount") in ns.parameters


class TestResolveQualified:
    def test_happy_path(self) -> None:
        model = SemanticModel(name="m", datasets=[_orders(), _customers()])
        ns = build_namespace(model)
        field = ns.resolve_qualified(
            normalize_identifier("orders"), normalize_identifier("amount")
        )
        assert field.name == normalize_identifier("amount")

    def test_unknown_dataset_E2002(self) -> None:
        model = SemanticModel(name="m", datasets=[_orders()])
        ns = build_namespace(model)
        with pytest.raises(OSIParseError) as exc:
            ns.resolve_qualified(
                normalize_identifier("missing"), normalize_identifier("amount")
            )
        assert exc.value.code is ErrorCode.E2002_NAME_NOT_FOUND

    def test_unknown_field_E2002(self) -> None:
        model = SemanticModel(name="m", datasets=[_orders()])
        ns = build_namespace(model)
        with pytest.raises(OSIParseError) as exc:
            ns.resolve_qualified(
                normalize_identifier("orders"), normalize_identifier("missing")
            )
        assert exc.value.code is ErrorCode.E2002_NAME_NOT_FOUND


class TestResolveBare:
    def test_unique_bare_name(self) -> None:
        model = SemanticModel(name="m", datasets=[_orders(), _customers()])
        ns = build_namespace(model)
        owner = ns.resolve_bare(normalize_identifier("amount"))
        assert owner == normalize_identifier("orders")

    def test_ambiguous_bare_name_E2001(self) -> None:
        # both datasets declare an `id` column
        model = SemanticModel(name="m", datasets=[_orders(), _customers()])
        ns = build_namespace(model)
        with pytest.raises(OSIParseError) as exc:
            ns.resolve_bare(normalize_identifier("id"))
        assert exc.value.code is ErrorCode.E2001_AMBIGUOUS_NAME

    def test_unknown_bare_name_E2002(self) -> None:
        model = SemanticModel(name="m", datasets=[_orders()])
        ns = build_namespace(model)
        with pytest.raises(OSIParseError) as exc:
            ns.resolve_bare(normalize_identifier("nope"))
        assert exc.value.code is ErrorCode.E2002_NAME_NOT_FOUND
