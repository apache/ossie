"""Unit tests for :mod:`osi.planning.resolve`.

Error codes covered:
``E2001`` (ambiguous bare name), ``E2002`` (not found),
``E1207`` (facts-metrics exclusivity), ``E1206`` (non-metric used as measure).
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIParseError, OSIPlanningError
from osi.planning.resolve import (
    ResolvedDimension,
    ResolvedFact,
    ResolvedMetric,
    resolve_dimension,
    resolve_measure,
    resolve_reference,
)
from osi.planning.semantic_query import Reference
from tests.unit.planning.fixtures import orders_context


def _ref(ds: str | None, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds) if ds else None,
        name=normalize_identifier(name),
    )


class TestResolveReference:
    def test_qualified_dimension_resolves(self) -> None:
        ns = orders_context().namespace
        got = resolve_reference(_ref("orders", "status"), ns)
        assert isinstance(got, ResolvedDimension)
        assert got.dataset == normalize_identifier("orders")

    def test_qualified_fact_resolves_to_ResolvedFact(self) -> None:
        ns = orders_context().namespace
        got = resolve_reference(_ref("orders", "amount"), ns)
        assert isinstance(got, ResolvedFact)

    def test_qualified_table_metric_resolves(self) -> None:
        ns = orders_context().namespace
        got = resolve_reference(_ref("orders", "total_revenue"), ns)
        assert isinstance(got, ResolvedMetric)
        assert got.dataset == normalize_identifier("orders")

    def test_qualified_unknown_name_E2002(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            resolve_reference(_ref("orders", "does_not_exist"), ns)
        assert excinfo.value.code is ErrorCode.E2002_NAME_NOT_FOUND

    def test_qualified_unknown_dataset_surfaces(self) -> None:
        ns = orders_context().namespace
        with pytest.raises((OSIPlanningError, OSIParseError)) as excinfo:
            resolve_reference(_ref("ghost", "status"), ns)
        assert excinfo.value.code is ErrorCode.E2002_NAME_NOT_FOUND

    def test_bare_unambiguous_dimension(self) -> None:
        ns = orders_context().namespace
        got = resolve_reference(_ref(None, "status"), ns)
        assert isinstance(got, ResolvedDimension)
        assert got.dataset == normalize_identifier("orders")

    def test_bare_ambiguous_E2001(self) -> None:
        ns = orders_context().namespace
        with pytest.raises((OSIPlanningError, OSIParseError)) as excinfo:
            # ``customer_id`` is declared on both orders and returns.
            resolve_reference(_ref(None, "customer_id"), ns)
        assert excinfo.value.code is ErrorCode.E2001_AMBIGUOUS_NAME


class TestResolveDimension:
    def test_metric_rejected_as_dimension_E1207(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            resolve_dimension(_ref("orders", "total_revenue"), ns)
        assert excinfo.value.code is ErrorCode.E1207_FACTS_METRICS_EXCLUSIVE

    def test_fact_rejected_as_dimension_E1207(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            resolve_dimension(_ref("orders", "amount"), ns)
        assert excinfo.value.code is ErrorCode.E1207_FACTS_METRICS_EXCLUSIVE


class TestResolveMeasure:
    def test_metric_accepted(self) -> None:
        ns = orders_context().namespace
        got = resolve_measure(_ref("orders", "total_revenue"), ns)
        assert isinstance(got, ResolvedMetric)

    def test_field_rejected_as_measure_E1206(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            resolve_measure(_ref("orders", "amount"), ns)
        assert excinfo.value.code is ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE
