"""Phase 6 — performance baselines for the compile pipeline.

These benchmarks measure the *pure compile* cost (parse → plan →
codegen) against the TPC-DS Foundation model. They're the signal we use
to detect regressions: a commit that doubles a benchmark median is a
regression even if every unit test still passes.

``make bench`` runs this file via ``pytest -m benchmark``. The CI job
stores results in ``.benchmarks/`` and compares against the last run.
"""

from __future__ import annotations

import pytest
import sqlglot

from osi.codegen import Dialect, compile_plan
from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.parsing.graph import build_graph
from osi.parsing.namespace import build_namespace
from osi.parsing.parser import parse_semantic_model
from osi.planning import OrderBy, Reference, SemanticQuery, SortDirection, plan
from osi.planning.planner_context import PlannerContext
from tests.e2e.tpcds_fixtures import load_tpcds_context


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _sql(expr: str) -> FrozenSQL:
    return FrozenSQL.of(sqlglot.parse_one(expr))


@pytest.fixture(scope="module")
def context() -> PlannerContext:
    return load_tpcds_context()


@pytest.mark.benchmark(group="parse")
def test_benchmark__parse_tpcds_model(benchmark) -> None:
    """Baseline: parse + validate the TPC-DS Foundation model end-to-end."""
    from pathlib import Path

    model_path = (
        Path(__file__).resolve().parents[2] / "examples" / "models" / "tpcds_thin.yaml"
    )
    source = model_path.read_text()

    def _round() -> PlannerContext:
        result = parse_semantic_model(source)
        return PlannerContext(
            model=result.model,
            namespace=build_namespace(result.model),
            graph=build_graph(result.model),
        )

    ctx = benchmark(_round)
    assert ctx.model.name == "tpcds_thin"


@pytest.mark.benchmark(group="plan")
def test_benchmark__plan_multi_fact_merge(context, benchmark) -> None:
    """Plan cost for a two-fact merge across a shared dimension."""
    q = SemanticQuery(
        dimensions=(_ref("store", "s_state"),),
        measures=(
            _ref("store_sales", "total_sales"),
            _ref("store_returns", "total_returns"),
        ),
    )
    result = benchmark(plan, q, context)
    assert result.root is not None


@pytest.mark.benchmark(group="compile")
def test_benchmark__compile_simple_aggregate(context, benchmark) -> None:
    """Compile: plan + codegen for a single-table aggregate."""
    q = SemanticQuery(
        dimensions=(_ref("item", "i_category"),),
        measures=(_ref("store_sales", "total_sales"),),
    )

    def _round() -> str:
        return compile_plan(plan(q, context), dialect=Dialect.DUCKDB)

    sql = benchmark(_round)
    assert "GROUP BY" in sql


@pytest.mark.benchmark(group="compile")
def test_benchmark__compile_with_filter_and_order(context, benchmark) -> None:
    """Compile: plan + codegen for a filtered top-N aggregate."""
    q = SemanticQuery(
        dimensions=(_ref("item", "i_category"),),
        measures=(_ref("store_sales", "total_sales"),),
        where=_sql("store_sales.ss_quantity > 1"),
        order_by=(
            OrderBy(
                target=_ref("store_sales", "total_sales"),
                direction=SortDirection.DESC,
            ),
        ),
        limit=5,
    )

    def _round() -> str:
        return compile_plan(plan(q, context), dialect=Dialect.DUCKDB)

    sql = benchmark(_round)
    assert "WHERE" in sql and "ORDER BY" in sql


@pytest.mark.benchmark(group="compile")
def test_benchmark__compile_dual_enrichment(context, benchmark) -> None:
    """Compile: two-hop enrichment (item + customer)."""
    q = SemanticQuery(
        dimensions=(
            _ref("item", "i_category"),
            _ref("customer", "c_birth_country"),
        ),
        measures=(_ref("store_sales", "total_sales"),),
    )

    def _round() -> str:
        return compile_plan(plan(q, context), dialect=Dialect.DUCKDB)

    sql = benchmark(_round)
    assert sql.count("INNER JOIN") + sql.count("LEFT JOIN") >= 2
