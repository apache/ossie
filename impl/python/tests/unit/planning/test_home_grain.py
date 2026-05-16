"""D-003 + D-015 implicit home-grain aggregation rewrite.

Pins the parser-side AST → AST rewrite that turns a home-grain field
body that aggregates a finer-grained dataset into a correlated
subquery.

Three classes of test:

1. **Rewrite shape**: the rewritten ``FrozenSQL`` is structurally a
   correlated ``SELECT … WHERE foreign.fk = home.pk``.
2. **Equivalence (D-015)**: two semantically equivalent model
   formulations (the implicit form + the explicit correlated
   subquery) plan to the same generated SQL after the rewrite.
3. **Cleanliness**: fields that don't reference any foreign dataset
   are returned untouched; the rewrite is a no-op in the absence of
   cross-grain aggregates.
"""

from __future__ import annotations

import pytest
import sqlglot
from sqlglot import expressions as exp

from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.parsing.graph import build_graph
from osi.parsing.models import Dataset, Field, Relationship, SemanticModel
from osi.planning.home_grain import rewrite_field_for_home_grain


def _customers() -> Dataset:
    return Dataset(
        name="customers",
        source="customers",
        primary_key=["id"],
        fields=[
            Field(name="id", expression="id"),
            Field(name="region", expression="region"),
        ],
    )


def _orders() -> Dataset:
    return Dataset(
        name="orders",
        source="orders",
        primary_key=["id"],
        fields=[
            Field(name="id", expression="id"),
            Field(name="customer_id", expression="customer_id"),
            Field(name="status", expression="status"),
            Field(name="amount", expression="amount", role="fact"),
        ],
    )


def _model(extra_field: Field) -> tuple[SemanticModel, dict]:
    customers = _customers().model_copy(
        update={"fields": (*_customers().fields, extra_field)}
    )
    model = SemanticModel(
        name="m",
        datasets=[customers, _orders()],
        relationships=[
            Relationship.model_validate(
                {
                    "name": "orders_to_customer",
                    "from": "orders",
                    "to": "customers",
                    "from_columns": ["customer_id"],
                    "to_columns": ["id"],
                }
            )
        ],
    )
    by_name = {d.name: d for d in model.datasets}
    return model, by_name


class TestRewriteShape:
    """The rewrite produces a correlated subquery with the right shape."""

    def test_top_level_sum_wraps_in_correlated_subquery(self) -> None:
        field = Field(name="lifetime_value", expression="SUM(orders.amount)")
        model, by_name = _model(field)
        graph = build_graph(model)
        rewritten = rewrite_field_for_home_grain(
            field, home=model.datasets[0].name, graph=graph, datasets_by_name=by_name
        )
        assert isinstance(rewritten.expr, exp.Subquery)
        inner = rewritten.expr.this
        assert isinstance(inner, exp.Select)
        # Body still has the SUM
        sums = list(inner.find_all(exp.Sum))
        assert len(sums) == 1
        # FROM is the foreign dataset's physical source
        from_clause = inner.args["from"]
        assert isinstance(from_clause, exp.From)
        assert str(from_clause.this).lower() == "orders"
        # WHERE is the correlation predicate
        where = inner.args["where"]
        assert isinstance(where, exp.Where)
        assert isinstance(where.this, exp.EQ)
        eq = where.this
        assert eq.this.table == "orders"
        assert eq.this.name == "customer_id"
        assert eq.expression.table == "customers"
        assert eq.expression.name == "id"

    def test_nested_aggregate_in_boolean_expression_wrapped(self) -> None:
        field = Field(
            name="has_completed",
            expression=(
                "COUNT(CASE WHEN orders.status = 'completed' THEN orders.id END) > 0"
            ),
        )
        model, by_name = _model(field)
        graph = build_graph(model)
        rewritten = rewrite_field_for_home_grain(
            field, home=model.datasets[0].name, graph=graph, datasets_by_name=by_name
        )
        # Top is still a comparison; the COUNT was replaced with a Subquery.
        assert isinstance(rewritten.expr, exp.GT)
        left = rewritten.expr.this
        assert isinstance(left, exp.Subquery)
        # The right-hand side is still the literal 0.
        right = rewritten.expr.expression
        assert isinstance(right, exp.Literal)
        assert right.name == "0"


class TestEquivalence:
    """D-015: two equivalent formulations plan to identical SQL.

    The spec admits three execution shapes (correlated subquery,
    LATERAL, pre-agg CTE) so long as each produces the same scalar
    per home-grain row. We pin shape #1 (correlated subquery) and
    cross-check that the same observable behaviour holds for the
    handcrafted equivalent.
    """

    def test_rewrite_matches_handwritten_correlated_subquery(self) -> None:
        implicit_field = Field(name="ltv", expression="SUM(orders.amount)")
        model, by_name = _model(implicit_field)
        graph = build_graph(model)
        rewritten = rewrite_field_for_home_grain(
            implicit_field,
            home=model.datasets[0].name,
            graph=graph,
            datasets_by_name=by_name,
        )

        explicit_sql = (
            "(SELECT SUM(orders.amount) FROM orders "
            "WHERE orders.customer_id = customers.id)"
        )
        explicit = FrozenSQL.of(sqlglot.parse_one(explicit_sql))
        assert rewritten.canonical == explicit.canonical

    def test_count_distinct_pk_round_trips(self) -> None:
        implicit_field = Field(
            name="distinct_orders", expression="COUNT(DISTINCT orders.id)"
        )
        model, by_name = _model(implicit_field)
        graph = build_graph(model)
        rewritten = rewrite_field_for_home_grain(
            implicit_field,
            home=model.datasets[0].name,
            graph=graph,
            datasets_by_name=by_name,
        )
        explicit = FrozenSQL.of(
            sqlglot.parse_one(
                "(SELECT COUNT(DISTINCT orders.id) FROM orders "
                "WHERE orders.customer_id = customers.id)"
            )
        )
        assert rewritten.canonical == explicit.canonical

    def test_min_and_max_round_trip(self) -> None:
        for fn in ("MIN", "MAX"):
            implicit_field = Field(
                name=f"first_amount_{fn.lower()}",
                expression=f"{fn}(orders.amount)",
            )
            model, by_name = _model(implicit_field)
            graph = build_graph(model)
            rewritten = rewrite_field_for_home_grain(
                implicit_field,
                home=model.datasets[0].name,
                graph=graph,
                datasets_by_name=by_name,
            )
            explicit = FrozenSQL.of(
                sqlglot.parse_one(
                    f"(SELECT {fn}(orders.amount) FROM orders "
                    "WHERE orders.customer_id = customers.id)"
                )
            )
            assert rewritten.canonical == explicit.canonical


class TestNoOp:
    """Fields without cross-grain aggregates pass through untouched."""

    def test_bare_column_field_is_unchanged(self) -> None:
        field = Field(name="segment", expression="segment")
        model, by_name = _model(field)
        graph = build_graph(model)
        out = rewrite_field_for_home_grain(
            field, home=model.datasets[0].name, graph=graph, datasets_by_name=by_name
        )
        assert out is field.expression

    def test_aggregate_over_home_columns_only_is_unchanged(self) -> None:
        # SUM(amount) where amount is a home-grain column. Foundation
        # accepts this *only* in a Measures context; field-side it is
        # an unusual shape but the rewrite leaves it untouched (no
        # foreign reference to wrap).
        field = Field(
            name="amount_sum_local",
            expression="SUM(amount)",
        )
        # We need amount to live on the *home* dataset for this case.
        home = Dataset(
            name="home",
            source="home",
            primary_key=["id"],
            fields=[
                Field(name="id", expression="id"),
                Field(name="amount", expression="amount", role="fact"),
                field,
            ],
        )
        model = SemanticModel(name="m", datasets=[home])
        by_name = {d.name: d for d in model.datasets}
        graph = build_graph(model)
        out = rewrite_field_for_home_grain(
            field, home=home.name, graph=graph, datasets_by_name=by_name
        )
        assert out is field.expression


def test_normalised_identifier_used_for_correlation() -> None:
    """The rewrite is robust to identifier casing in the field expression."""
    field = Field(name="ltv", expression="SUM(Orders.Amount)")
    model, by_name = _model(field)
    graph = build_graph(model)
    rewritten = rewrite_field_for_home_grain(
        field,
        home=model.datasets[0].name,
        graph=graph,
        datasets_by_name=by_name,
    )
    assert isinstance(rewritten.expr, exp.Subquery)
    where = rewritten.expr.this.args["where"]
    eq = where.this
    # Foundation normalises identifiers case-insensitively (§3.1).
    assert eq.this.table.lower() == "orders"
    assert eq.expression.table.lower() == "customers"


@pytest.mark.parametrize("agg", ["SUM", "COUNT", "MIN", "MAX", "COUNT(DISTINCT"])
def test_every_supported_aggregate_rewrites(agg: str) -> None:
    """Smoke: every Foundation-supported aggregate is wrapped, not skipped."""
    expr = (
        "COUNT(DISTINCT orders.id)"
        if agg == "COUNT(DISTINCT"
        else f"{agg}(orders.amount)"
    )
    field = Field(name=f"f_{normalize_identifier(agg.split('(')[0])}", expression=expr)
    model, by_name = _model(field)
    graph = build_graph(model)
    rewritten = rewrite_field_for_home_grain(
        field, home=model.datasets[0].name, graph=graph, datasets_by_name=by_name
    )
    assert isinstance(rewritten.expr, exp.Subquery)
