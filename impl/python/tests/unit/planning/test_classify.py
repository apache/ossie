"""Unit tests for :mod:`osi.planning.classify`.

Splits ``where`` into row-level vs semi-join predicates and ``having``
into post-aggregate predicates. Error codes asserted here:
``E1005`` (identifier invalid), ``E1208`` (unsupported SQL construct),
``E3009`` (post-aggregate refers to pre-aggregate only),
``E_DEFERRED_KEY_REJECTED`` (semi-join in strict Foundation).

``EXISTS_IN`` / ``NOT EXISTS_IN`` is gated by
``FoundationFlags.experimental_exists_in``; the tests in
:class:`TestSemiJoins` flip the flag to exercise the experimental
admission path, and the strict-default tests in
:class:`TestSemiJoinsStrict` verify the Foundation-default rejection.
"""

from __future__ import annotations

import pytest

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL, parse_sql_expr
from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIPlanningError
from osi.planning.algebra.operations import FilterMode
from osi.planning.classify import SemiJoinPredicate, classify_having, classify_where
from tests.unit.planning.fixtures import orders_context


def _where(sql: str) -> FrozenSQL:
    return FrozenSQL.of(parse_sql_expr(sql))


EXISTS_FLAGS = FoundationFlags(experimental_exists_in=True)


# ---------------------------------------------------------------------------
# classify_where — row-level
# ---------------------------------------------------------------------------


class TestRowLevel:
    def test_none_predicate_returns_empty_classification(self) -> None:
        ns = orders_context().namespace
        out = classify_where(None, ns)
        assert out.row_level == ()
        assert out.semi_joins == ()

    def test_single_conjunct_becomes_one_row_level_predicate(self) -> None:
        ns = orders_context().namespace
        out = classify_where(_where("amount > 100"), ns)
        assert len(out.row_level) == 1
        assert out.semi_joins == ()
        pred = out.row_level[0]
        assert normalize_identifier("amount") in pred.columns

    def test_conjunction_splits_into_multiple_predicates(self) -> None:
        ns = orders_context().namespace
        out = classify_where(_where("amount > 100 AND status = 'open'"), ns)
        assert len(out.row_level) == 2
        assert out.semi_joins == ()

    def test_qualified_column_binds_to_dataset(self) -> None:
        ns = orders_context().namespace
        out = classify_where(_where("orders.amount > 100"), ns)
        assert normalize_identifier("orders") in out.row_level[0].datasets

    def test_bare_column_resolved_via_namespace(self) -> None:
        ns = orders_context().namespace
        out = classify_where(_where("refund_amount > 0"), ns)
        # Only ``returns`` declares ``refund_amount`` — so the bare name
        # binds there.
        assert normalize_identifier("returns") in out.row_level[0].datasets

    def test_parenthesised_conjuncts_still_split(self) -> None:
        ns = orders_context().namespace
        out = classify_where(_where("(amount > 100) AND (status = 'x')"), ns)
        assert len(out.row_level) == 2


# ---------------------------------------------------------------------------
# classify_where — semi-joins
# ---------------------------------------------------------------------------


class TestSemiJoinsStrict:
    """Foundation default (flag off): EXISTS_IN is rejected at classify."""

    def test_exists_in_rejected_with_deferred_code(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_where(_where("EXISTS_IN(customer_id, returns.customer_id)"), ns)
        assert excinfo.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED
        assert excinfo.value.context.get("flag") == "experimental_exists_in"

    def test_not_exists_in_rejected_with_deferred_code(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_where(
                _where("NOT EXISTS_IN(customer_id, returns.customer_id)"), ns
            )
        assert excinfo.value.code is ErrorCode.E_DEFERRED_KEY_REJECTED


class TestSemiJoins:
    """Experimental flag on: classify recognises the semi-join shape."""

    def test_exists_in_produces_semi_predicate(self) -> None:
        ns = orders_context().namespace
        out = classify_where(
            _where("EXISTS_IN(customer_id, returns.customer_id)"),
            ns,
            flags=EXISTS_FLAGS,
        )
        assert out.row_level == ()
        assert len(out.semi_joins) == 1
        sj = out.semi_joins[0]
        assert isinstance(sj, SemiJoinPredicate)
        assert sj.mode is FilterMode.SEMI
        assert sj.pairs[0].rhs_dataset == normalize_identifier("returns")

    def test_not_exists_in_produces_anti_predicate(self) -> None:
        ns = orders_context().namespace
        out = classify_where(
            _where("NOT EXISTS_IN(customer_id, returns.customer_id)"),
            ns,
            flags=EXISTS_FLAGS,
        )
        assert out.semi_joins[0].mode is FilterMode.ANTI

    def test_composite_key_exists_in(self) -> None:
        ns = orders_context().namespace
        out = classify_where(
            _where(
                "EXISTS_IN(order_id, returns.order_id, "
                "customer_id, returns.customer_id)"
            ),
            ns,
            flags=EXISTS_FLAGS,
        )
        sj = out.semi_joins[0]
        assert len(sj.pairs) == 2

    def test_odd_argument_count_rejected_E1208(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_where(_where("EXISTS_IN(order_id)"), ns, flags=EXISTS_FLAGS)
        assert excinfo.value.code is ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT

    def test_unqualified_rhs_rejected_E1208(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_where(
                _where("EXISTS_IN(customer_id, customer_id)"),
                ns,
                flags=EXISTS_FLAGS,
            )
        assert excinfo.value.code is ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT

    def test_mixed_row_level_and_semi_join(self) -> None:
        ns = orders_context().namespace
        out = classify_where(
            _where("amount > 0 AND EXISTS_IN(customer_id, returns.customer_id)"),
            ns,
            flags=EXISTS_FLAGS,
        )
        assert len(out.row_level) == 1
        assert len(out.semi_joins) == 1


# ---------------------------------------------------------------------------
# classify_having
# ---------------------------------------------------------------------------


class TestHaving:
    def test_none_having_returns_empty(self) -> None:
        out = classify_having(None, (normalize_identifier("total_revenue"),))
        assert out == ()

    def test_having_referencing_measure_accepted(self) -> None:
        out = classify_having(
            _where("total_revenue > 1000"),
            (normalize_identifier("total_revenue"),),
        )
        assert len(out) == 1
        assert normalize_identifier("total_revenue") in out[0].measures

    def test_having_without_measure_rejected_with_named_code(self) -> None:
        # S-3 / D-012b: pure row-level conjunct in HAVING raises the
        # named code rather than the legacy E3009.
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_having(
                _where("status = 'open'"),
                (normalize_identifier("total_revenue"),),
            )
        assert excinfo.value.code is ErrorCode.E_NON_AGGREGATE_IN_HAVING

    def test_having_splits_on_conjunction(self) -> None:
        out = classify_having(
            _where("total_revenue > 1 AND total_revenue < 100"),
            (normalize_identifier("total_revenue"),),
        )
        assert len(out) == 2


# ---------------------------------------------------------------------------
# classify_where — measure references belong in HAVING
# ---------------------------------------------------------------------------


class TestWhereRejectsAggregates:
    """``WHERE`` is the row-level slot; aggregates belong in ``HAVING``.

    Foundation v0.1 D-005 / D-012a routes by *resolved expression
    shape*: a measure reference (resolves to an aggregate) or a raw
    aggregate function in ``WHERE`` raises
    :attr:`ErrorCode.E_AGGREGATE_IN_WHERE`. A predicate whose tree
    mixes both shapes raises :attr:`ErrorCode.E_MIXED_PREDICATE_LEVEL`.
    """

    def test_bare_measure_in_where_rejected(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_where(_where("total_revenue > 1000"), ns)
        assert excinfo.value.code is ErrorCode.E_AGGREGATE_IN_WHERE

    def test_qualified_measure_in_where_rejected(self) -> None:
        """A qualified measure ref must not bypass the WHERE check.

        ``orders.total_revenue`` is the same measure under a qualifier;
        the rule must not be circumvented by qualification.
        """
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_where(_where("orders.total_revenue > 1000"), ns)
        assert excinfo.value.code is ErrorCode.E_AGGREGATE_IN_WHERE

    def test_measure_mixed_with_dimension_predicate_rejected(self) -> None:
        # Mixed-level predicate (D-012c) wins over per-conjunct
        # placement so the diagnostic points at the right fix.
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_where(
                _where("status = 'open' AND total_revenue > 1000"),
                ns,
            )
        assert excinfo.value.code is ErrorCode.E_MIXED_PREDICATE_LEVEL

    def test_raw_aggregate_in_where_rejected(self) -> None:
        ns = orders_context().namespace
        with pytest.raises(OSIPlanningError) as excinfo:
            classify_where(_where("SUM(orders.amount) > 100"), ns)
        assert excinfo.value.code is ErrorCode.E_AGGREGATE_IN_WHERE
