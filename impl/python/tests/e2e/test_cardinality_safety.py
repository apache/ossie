"""Cardinality-safety tests — pinning behaviour when a 1:N edge is mismarked as N:N.

These tests exercise the planner's safety contract from
``Proposed_OSI_Semantics.md §6.1 / §6.5``: when a relationship's
join-key columns are *actually* unique on the to-side (the
relationship is genuinely 1:N or N:1) but the model author fails to
declare that uniqueness, cardinality inference yields the
conservative ``N:N``. The Foundation MUST then either:

1. produce a result that is *numerically identical* to the
   correctly-declared model (when a safe route applies — e.g.
   ``EXISTS_IN`` semi-join, which doesn't fan rows out), OR
2. refuse the query with the actionable
   ``E3012_MN_NO_SAFE_REWRITE`` (when no route applies) — never
   silently emit an inflated ``SUM`` over a fanned-out join.

Every test in this file constructs *the same data* but loads it
through two YAML models — one canonical ("the relationship is N:1")
and one mismarked ("the relationship is N:N because the to-side has
no PK or UK on the join column"). The tests cross-reference results
to pin the safety guarantee.

A note on PK requirement
------------------------
The OSI spec (``§4.2``) declares ``primary_key`` *optional*: missing
keys force conservative ``N:N`` inference but the model is still
valid. The Foundation algebra is stricter — :func:`osi.planning.algebra.operations.source`
requires a non-empty ``primary_key`` and raises
``E2007_MISSING_PRIMARY_KEY`` otherwise. ``test_pk_required_by_algebra``
documents this implementation constraint so any future relaxation
trips the test deliberately.
"""

from __future__ import annotations

import textwrap

import duckdb
import pytest
import sqlglot

from osi.codegen import Dialect, compile_plan
from osi.common.identifiers import normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIError
from osi.parsing.graph import Cardinality, build_graph
from osi.parsing.namespace import build_namespace
from osi.parsing.parser import parse_semantic_model
from osi.planning import Reference, SemanticQuery, plan
from osi.planning.planner_context import PlannerContext


def _ref(ds: str, name: str) -> Reference:
    return Reference(
        dataset=normalize_identifier(ds),
        name=normalize_identifier(name),
    )


def _ctx(model_yaml: str) -> PlannerContext:
    # The cardinality-safety fixtures use per-dataset ``metrics:``
    # blocks, deferred under the strict Foundation; opt back in via
    # the legacy-permissive flag set so the planner-side cardinality
    # contract stays exercised end-to-end.
    parsed = parse_semantic_model(model_yaml, flags=FoundationFlags.legacy_permissive())
    return PlannerContext(
        model=parsed.model,
        namespace=build_namespace(parsed.model),
        graph=build_graph(parsed.model),
    )


def _run(
    conn: duckdb.DuckDBPyConnection,
    query: SemanticQuery,
    context: PlannerContext,
) -> list[tuple]:
    qp = plan(query, context)
    sql = compile_plan(qp, dialect=Dialect.DUCKDB)
    return sorted(conn.execute(sql).fetchall())


# ---------------------------------------------------------------------------
# Shared seed: customers (1) ←→ orders (N)
# ---------------------------------------------------------------------------

_SEED: tuple[str, ...] = (
    "CREATE SCHEMA cs;",
    "CREATE TABLE cs.customers (id INTEGER, region VARCHAR);",
    """
    INSERT INTO cs.customers VALUES
        (1, 'NA'), (2, 'NA'), (3, 'EMEA'), (4, 'APAC');
    """,
    """
    CREATE TABLE cs.orders (
        order_id INTEGER, customer_id INTEGER, amount DOUBLE
    );
    """,
    # Two orders per NA customer, one each for EMEA / APAC. customer_id
    # references customers.id with a true many-to-one shape: every
    # customer_id maps to exactly one customer row.
    """
    INSERT INTO cs.orders VALUES
        (10, 1, 100.0), (11, 1, 200.0),
        (12, 2,  50.0), (13, 2,  75.0),
        (14, 3, 300.0),
        (15, 4, 125.0);
    """,
)


@pytest.fixture()
def duckdb_cs() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB seeded with the customers/orders schema above."""
    conn = duckdb.connect(":memory:")
    for stmt in _SEED:
        conn.execute(stmt)
    return conn


# ---------------------------------------------------------------------------
# Three modelling variants over the same physical data.
# ---------------------------------------------------------------------------

# Variant A — canonical: customers.primary_key=[id], so orders.customer_id
# matches a key on the to-side and the relationship is inferred as N:1.
_CANONICAL_N1_MODEL = textwrap.dedent("""\
    semantic_model:
      - name: cs_canonical
        dialect: ANSI_SQL
        datasets:
          - name: orders
            source: cs.orders
            primary_key: [order_id]
            fields:
              - {name: order_id,    expression: order_id,    role: dimension}
              - {name: customer_id, expression: customer_id, role: dimension}
              - {name: amount,      expression: amount,      role: fact}
            metrics:
              - {name: total_revenue, expression: SUM(amount)}
              - {name: order_count,   expression: COUNT(*)}
          - name: customers
            source: cs.customers
            primary_key: [id]
            fields:
              - {name: id,     expression: id,     role: dimension}
              - {name: region, expression: region, role: dimension}
        relationships:
          - {name: orders_to_customers, from: orders, to: customers,
             from_columns: [customer_id], to_columns: [id]}
    """)


# Variant B — *mismarked*: customers has a PK on a different (synthetic)
# column, so the relationship's to_columns=[id] do NOT match any key
# on the to-side. Cardinality inference yields N:N even though the data
# is 1:N. The Foundation must refuse a measure-traversing query with
# ``E3012`` rather than silently fan out the SUM.
_MISMARKED_NN_MODEL = textwrap.dedent("""\
    semantic_model:
      - name: cs_mismarked
        dialect: ANSI_SQL
        datasets:
          - name: orders
            source: cs.orders
            primary_key: [order_id]
            fields:
              - {name: order_id,    expression: order_id,    role: dimension}
              - {name: customer_id, expression: customer_id, role: dimension}
              - {name: amount,      expression: amount,      role: fact}
            metrics:
              - {name: total_revenue, expression: SUM(amount)}
              - {name: order_count,   expression: COUNT(*)}
          - name: customers
            # Composite PK that does NOT match the join-key alone.
            source: cs.customers
            primary_key: [id, region]
            fields:
              - {name: id,     expression: id,     role: dimension}
              - {name: region, expression: region, role: dimension}
        relationships:
          - {name: orders_to_customers, from: orders, to: customers,
             from_columns: [customer_id], to_columns: [id]}
    """)


# Variant C — same mismarked PK as B *plus* an explicit ``unique_keys``
# declaration on customers.id. Per ``§6.1`` cardinality inference
# accepts a UK match too, so the relationship is once again inferred
# as N:1 and the standard enrichment plan is restored. Demonstrates
# that the user has a recovery path without changing the PK.
_RECOVERED_VIA_UK_MODEL = textwrap.dedent("""\
    semantic_model:
      - name: cs_recovered
        dialect: ANSI_SQL
        datasets:
          - name: orders
            source: cs.orders
            primary_key: [order_id]
            fields:
              - {name: order_id,    expression: order_id,    role: dimension}
              - {name: customer_id, expression: customer_id, role: dimension}
              - {name: amount,      expression: amount,      role: fact}
            metrics:
              - {name: total_revenue, expression: SUM(amount)}
              - {name: order_count,   expression: COUNT(*)}
          - name: customers
            source: cs.customers
            primary_key: [id, region]
            unique_keys:
              - [id]
            fields:
              - {name: id,     expression: id,     role: dimension}
              - {name: region, expression: region, role: dimension}
        relationships:
          - {name: orders_to_customers, from: orders, to: customers,
             from_columns: [customer_id], to_columns: [id]}
    """)


# ---------------------------------------------------------------------------
# §1: PK is required by the algebra (implementation constraint, not spec).
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_pk_required_by_algebra() -> None:
    """Source operator rejects datasets with no PK with ``E2007``.

    The OSI spec (``Proposed_OSI_Semantics.md §4.2``) declares
    ``primary_key`` optional; missing keys "force the planner into
    conservative N:N cardinality" but do not invalidate the model.
    The Foundation algebra is stricter:
    :func:`osi.planning.algebra.operations.source` requires a
    non-empty ``primary_key``. This test pins that constraint so any
    future relaxation (e.g. an implicit-rowid grain) is a deliberate
    breaking change — not silent drift.
    """
    no_pk_model = textwrap.dedent("""\
        semantic_model:
          - name: nopk
            dialect: ANSI_SQL
            datasets:
              - name: orders
                source: cs.orders
                fields:
                  - {name: order_id,    expression: order_id,    role: dimension}
                  - {name: customer_id, expression: customer_id, role: dimension}
                  - {name: amount,      expression: amount,      role: fact}
                metrics:
                  - {name: total_revenue, expression: SUM(amount)}
        """)
    ctx = _ctx(no_pk_model)
    q = SemanticQuery(
        measures=(_ref("orders", "total_revenue"),),
    )
    with pytest.raises(OSIError) as excinfo:
        plan(q, ctx)
    assert excinfo.value.code is ErrorCode.E2007_MISSING_PRIMARY_KEY


# ---------------------------------------------------------------------------
# §2: cardinality inference matches the spec's §6.1 rules.
# ---------------------------------------------------------------------------


def test_canonical_pk_yields_n1_inference() -> None:
    """A PK on the to-side's join column yields ``N:1`` inference."""
    ctx = _ctx(_CANONICAL_N1_MODEL)
    edge = ctx.graph.edges[0]
    assert edge.cardinality is Cardinality.N_TO_ONE


def test_mismarked_pk_yields_nn_inference() -> None:
    """A PK that does NOT match the to-columns yields conservative ``N:N``.

    The customers dataset's PK is ``[id, region]``; the relationship's
    ``to_columns: [id]`` does not match any declared key on the to-side
    so per ``§6.1`` cardinality inference falls back to ``N:N`` —
    even though the data is genuinely 1:N.
    """
    ctx = _ctx(_MISMARKED_NN_MODEL)
    edge = ctx.graph.edges[0]
    assert edge.cardinality is Cardinality.N_TO_N


def test_unique_keys_recovers_n1_inference() -> None:
    """Explicit ``unique_keys: [[id]]`` restores ``N:1`` inference.

    Pins ``§6.1``: cardinality inference accepts either the PK *or*
    any declared unique key. Authors who can't move the PK can still
    surface the join-key uniqueness via ``unique_keys`` and recover
    the standard enrichment plan.
    """
    ctx = _ctx(_RECOVERED_VIA_UK_MODEL)
    edge = ctx.graph.edges[0]
    assert edge.cardinality is Cardinality.N_TO_ONE


# ---------------------------------------------------------------------------
# §3: behaviour under mismarked-N:N. The planner must REFUSE measure-
# traversing queries with the spec-correct error rather than emit a
# fanned-out SUM.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_canonical_enrichment_returns_correct_rows(duckdb_cs) -> None:
    """Baseline: SUM(amount) by customers.region against the canonical model.

    Establishes the numerical reference every other test compares
    against. NA: 100+200+50+75=425. EMEA: 300. APAC: 125.
    """
    ctx = _ctx(_CANONICAL_N1_MODEL)
    q = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    rows = _run(duckdb_cs, q, ctx)
    assert rows == [("APAC", 125.0), ("EMEA", 300.0), ("NA", 425.0)]


@pytest.mark.e2e
def test_mismarked_nn_refuses_enrichment_with_E3012(duckdb_cs) -> None:
    """Mismarked-N:N must raise ``E3012``, not silently fan rows out.

    ``Proposed_OSI_Semantics.md §6.5`` mandates the planner refuse
    every M:N traversal that has no bridge / stitch / EXISTS_IN
    route. The spec is explicit (``§6.5.3``): "An UNSAFE directive
    would only be needed to *bypass correctness* — to silently emit
    an inflated SUM over a fanned-out join. None of Tableau, Looker,
    or Power BI offers that, and OSI does not either."

    This is the central safety property: a conservatively-declared
    model NEVER produces a wrong number. It either plans a safe
    route or raises a typed error.
    """
    _ = duckdb_cs  # fixture pins the data shape; no SQL is executed here
    ctx = _ctx(_MISMARKED_NN_MODEL)
    q = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    with pytest.raises(OSIError) as excinfo:
        plan(q, ctx)
    assert excinfo.value.code is ErrorCode.E3012_MN_NO_SAFE_REWRITE
    # The error context surfaces the actionable resolution suggestions
    # the spec calls for in §6.5.
    msg = str(excinfo.value)
    assert "EXISTS_IN" in msg


@pytest.mark.e2e
def test_recovered_model_matches_canonical_results(duckdb_cs) -> None:
    """Adding ``unique_keys`` produces *byte-identical* results.

    Spec contract (``§4.2``): "``primary_key`` and ``unique_keys``
    drive cardinality inference (§6.1)." The algebra honours this
    symmetry — :func:`osi.planning.algebra.source` plumbs declared
    UKs into :class:`CalculationState.unique_keys`, and
    :func:`enrich` uses :meth:`CalculationState.is_unique_on` to
    discharge its fan-trap rule against the PK *or* any UK.

    Acceptance test for ``INFRA.md I-16``: a model that declares the
    join column via ``unique_keys`` (rather than as the PK) plans
    and runs identically to the canonical-PK model.
    """
    canonical_ctx = _ctx(_CANONICAL_N1_MODEL)
    recovered_ctx = _ctx(_RECOVERED_VIA_UK_MODEL)
    q = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(_ref("orders", "total_revenue"),),
    )
    canonical_rows = _run(duckdb_cs, q, canonical_ctx)
    recovered_rows = _run(duckdb_cs, q, recovered_ctx)
    assert canonical_rows == recovered_rows
    assert canonical_rows == [("APAC", 125.0), ("EMEA", 300.0), ("NA", 425.0)]


# ---------------------------------------------------------------------------
# §4: EXISTS_IN works regardless of cardinality. A semi-join filter
# never fans rows out, so the spec (``§7.4``) lets it traverse any
# edge — including one mismarked as N:N. The result must match the
# canonical model exactly.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_exists_in_filter_is_cardinality_independent(duckdb_cs) -> None:
    """``EXISTS_IN`` returns identical filter-results on both models.

    Per ``§6.5`` the *filter route* (``EXISTS_IN``) is one of the
    three safe M:N resolutions. Because semi-joins never add rows,
    the planner can apply this route even when the underlying
    relationship is N:N — and the answer is the same as on the
    correctly-declared N:1 model.

    Query: SUM(amount) over orders that have a *matching customer*.
    Every order in the seed has a matching customer, so the result
    equals the unfiltered total: 850.
    """
    where = FrozenSQL.of(
        sqlglot.parse_one("EXISTS_IN(orders.customer_id, customers.id)")
    )
    q = SemanticQuery(
        measures=(_ref("orders", "total_revenue"),),
        where=where,
    )
    canonical_rows = _run(duckdb_cs, q, _ctx(_CANONICAL_N1_MODEL))
    mismarked_rows = _run(duckdb_cs, q, _ctx(_MISMARKED_NN_MODEL))
    assert canonical_rows == mismarked_rows == [(850.0,)]


@pytest.mark.e2e
def test_not_exists_in_filter_is_cardinality_independent(duckdb_cs) -> None:
    """``NOT EXISTS_IN`` (anti-join) is also cardinality-independent.

    Insert one orphan order whose ``customer_id`` doesn't appear in
    customers, then assert that the anti-join returns its amount
    against both models. Pins that the ``§7.4`` ANTI ``filtering_join``
    fires regardless of how the model declares the relationship.
    """
    duckdb_cs.execute("INSERT INTO cs.orders VALUES (99, 999, 42.0);")
    where = FrozenSQL.of(
        sqlglot.parse_one("NOT EXISTS_IN(orders.customer_id, customers.id)")
    )
    q = SemanticQuery(
        measures=(_ref("orders", "total_revenue"),),
        where=where,
    )
    canonical_rows = _run(duckdb_cs, q, _ctx(_CANONICAL_N1_MODEL))
    mismarked_rows = _run(duckdb_cs, q, _ctx(_MISMARKED_NN_MODEL))
    assert canonical_rows == mismarked_rows == [(42.0,)]


# ---------------------------------------------------------------------------
# §5: chasm-trap stitch is cardinality-tolerant when both edges are
# correctly declared, but breaks down identically on both sides if
# either edge is mismarked. Pins the symmetric safety property.
# ---------------------------------------------------------------------------


_SHARED_DIM_SEED: tuple[str, ...] = _SEED + (
    """
    CREATE TABLE cs.returns (
        return_id INTEGER, customer_id INTEGER, refund_amount DOUBLE
    );
    """,
    """
    INSERT INTO cs.returns VALUES
        (200, 1,  5.0), (201, 3, 10.0);
    """,
)


def _stitch_model(canonical: bool) -> str:
    """Return a sales-style model where orders+returns stitch via customers.

    ``canonical=True`` declares ``customers.primary_key=[id]``;
    ``canonical=False`` mismarks it as ``[id, region]`` so both
    relationships fall back to ``N:N``.
    """
    pk = "[id]" if canonical else "[id, region]"
    return textwrap.dedent(f"""\
        semantic_model:
          - name: cs_stitch
            dialect: ANSI_SQL
            datasets:
              - name: orders
                source: cs.orders
                primary_key: [order_id]
                fields:
                  - {{name: order_id,    expression: order_id,    role: dimension}}
                  - {{name: customer_id, expression: customer_id, role: dimension}}
                  - {{name: amount,      expression: amount,      role: fact}}
                metrics:
                  - {{name: total_revenue, expression: SUM(amount)}}
              - name: customers
                source: cs.customers
                primary_key: {pk}
                fields:
                  - {{name: id,     expression: id,     role: dimension}}
                  - {{name: region, expression: region, role: dimension}}
              - name: returns
                source: cs.returns
                primary_key: [return_id]
                fields:
                  - {{name: return_id,     expression: return_id,     role: dimension}}
                  - {{name: customer_id,   expression: customer_id,   role: dimension}}
                  - {{name: refund_amount, expression: refund_amount, role: fact}}
                metrics:
                  - {{name: total_refunds, expression: SUM(refund_amount)}}
            relationships:
              - {{name: orders_to_customers, from: orders, to: customers,
                 from_columns: [customer_id], to_columns: [id]}}
              - {{name: returns_to_customers, from: returns, to: customers,
                 from_columns: [customer_id], to_columns: [id]}}
        """)


@pytest.fixture()
def duckdb_cs_with_returns() -> duckdb.DuckDBPyConnection:
    """In-memory DuckDB seeded with customers, orders *and* returns."""
    conn = duckdb.connect(":memory:")
    for stmt in _SHARED_DIM_SEED:
        conn.execute(stmt)
    return conn


@pytest.mark.e2e
def test_canonical_stitch_returns_correct_rows(duckdb_cs_with_returns) -> None:
    """Two-fact stitch on customers.region under correctly-declared N:1."""
    q = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    )
    rows = _run(duckdb_cs_with_returns, q, _ctx(_stitch_model(canonical=True)))
    assert rows == [
        ("APAC", 125.0, None),
        ("EMEA", 300.0, 10.0),
        ("NA", 425.0, 5.0),
    ]


@pytest.mark.e2e
def test_mismarked_stitch_refuses_with_E3012(duckdb_cs_with_returns) -> None:
    """Mismarking *either* edge poisons the stitch route too.

    Stitch (``§6.5.2``) is implemented by enriching each fact to the
    shared dim independently; if the enrichment edge is N:N the
    planner refuses (``E3012``), exactly as for a single-fact query.
    The contrapositive: the mismarked model never produces an
    inflated stitched SUM. Either edge being N:N is sufficient to
    trip the safety check.
    """
    _ = duckdb_cs_with_returns  # safety check raises before SQL execution
    ctx = _ctx(_stitch_model(canonical=False))
    q = SemanticQuery(
        dimensions=(_ref("customers", "region"),),
        measures=(
            _ref("orders", "total_revenue"),
            _ref("returns", "total_refunds"),
        ),
    )
    with pytest.raises(OSIError) as excinfo:
        plan(q, ctx)
    assert excinfo.value.code is ErrorCode.E3012_MN_NO_SAFE_REWRITE


# ---------------------------------------------------------------------------
# §6: dim-only queries see the same conservative behaviour. Pins that
# the safety property is *not* a measure-only artefact.
# ---------------------------------------------------------------------------


@pytest.mark.e2e
def test_dim_only_canonical_picks_orders_anchor(duckdb_cs) -> None:
    """Dim-only query on (orders.customer_id, customers.region) under N:1.

    Orders is the only safe anchor (it can N:1 enrich into customers);
    customers can't reach orders without a fan trap. Returns one row
    per *order* enriched with its region.
    """
    ctx = _ctx(_CANONICAL_N1_MODEL)
    q = SemanticQuery(
        dimensions=(
            _ref("orders", "order_id"),
            _ref("customers", "region"),
        ),
    )
    rows = _run(duckdb_cs, q, ctx)
    assert rows == [
        (10, "NA"),
        (11, "NA"),
        (12, "NA"),
        (13, "NA"),
        (14, "EMEA"),
        (15, "APAC"),
    ]


@pytest.mark.e2e
def test_dim_only_mismarked_refuses_with_E3012(duckdb_cs) -> None:
    """Mismarked-N:N dim-only also refuses with the spec-correct error.

    The dim-only group selector tries each referenced dataset as a
    safe anchor; both fail because the only edge is N:N. Bridge
    discovery then runs (``§6.5.1``); no third dataset exists, so
    the planner re-raises the underlying ``E3012``.
    """
    _ = duckdb_cs
    ctx = _ctx(_MISMARKED_NN_MODEL)
    q = SemanticQuery(
        dimensions=(
            _ref("orders", "order_id"),
            _ref("customers", "region"),
        ),
    )
    with pytest.raises(OSIError) as excinfo:
        plan(q, ctx)
    assert excinfo.value.code is ErrorCode.E3012_MN_NO_SAFE_REWRITE
