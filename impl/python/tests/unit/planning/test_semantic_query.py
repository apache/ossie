"""Unit tests for :mod:`osi.planning.semantic_query`.

Covers every branch of :class:`SemanticQuery.__post_init__` plus the
:class:`Reference` / :class:`OrderBy` shape. Error codes asserted here:
``E1002`` (missing required field), ``E1004`` (type mismatch).
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.errors import ErrorCode, OSIParseError
from osi.planning.semantic_query import OrderBy, Reference, SemanticQuery, SortDirection

# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------


class TestReference:
    def test_qualified_reference_renders_dataset_dot_name(self) -> None:
        ref = Reference(
            dataset=normalize_identifier("orders"),
            name=normalize_identifier("amount"),
        )
        assert ref.is_qualified
        assert str(ref) == "orders.amount"

    def test_bare_reference_has_no_dataset(self) -> None:
        ref = Reference(dataset=None, name=normalize_identifier("avg_order_value"))
        assert not ref.is_qualified
        assert str(ref) == "avg_order_value"


# ---------------------------------------------------------------------------
# OrderBy
# ---------------------------------------------------------------------------


class TestOrderBy:
    def test_default_direction_is_ascending(self) -> None:
        ob = OrderBy(
            target=Reference(dataset=None, name=normalize_identifier("revenue"))
        )
        assert ob.direction is SortDirection.ASC

    def test_explicit_desc(self) -> None:
        ob = OrderBy(
            target=Reference(dataset=None, name=normalize_identifier("revenue")),
            direction=SortDirection.DESC,
        )
        assert ob.direction is SortDirection.DESC


# ---------------------------------------------------------------------------
# SemanticQuery
# ---------------------------------------------------------------------------


def _ref(name: str) -> Reference:
    return Reference(dataset=None, name=normalize_identifier(name))


class TestSemanticQueryValidation:
    def test_empty_query_raises_E_EMPTY_AGGREGATION_QUERY(self) -> None:
        # S-2: per D-010 / D-011 the empty case has its own
        # error codes; the historical default is the aggregation
        # shape's E_EMPTY_AGGREGATION_QUERY.
        with pytest.raises(OSIParseError) as excinfo:
            SemanticQuery()
        assert excinfo.value.code is ErrorCode.E_EMPTY_AGGREGATION_QUERY

    def test_dimension_only_query_is_valid(self) -> None:
        q = SemanticQuery(dimensions=(_ref("status"),))
        assert q.dimensions[0].name == "status"
        assert q.measures == ()

    def test_measure_only_query_is_valid(self) -> None:
        q = SemanticQuery(measures=(_ref("total_revenue"),))
        assert q.measures[0].name == "total_revenue"

    def test_negative_limit_rejected_E1004(self) -> None:
        with pytest.raises(OSIParseError) as excinfo:
            SemanticQuery(measures=(_ref("total"),), limit=-1)
        assert excinfo.value.code is ErrorCode.E1004_TYPE_MISMATCH

    def test_zero_limit_allowed(self) -> None:
        q = SemanticQuery(measures=(_ref("total"),), limit=0)
        assert q.limit == 0

    def test_no_limit_means_unlimited(self) -> None:
        q = SemanticQuery(measures=(_ref("total"),))
        assert q.limit is None

    def test_is_frozen(self) -> None:
        q = SemanticQuery(measures=(_ref("total"),))
        from dataclasses import FrozenInstanceError

        with pytest.raises((FrozenInstanceError, AttributeError)):
            q.limit = 10  # type: ignore[misc]
