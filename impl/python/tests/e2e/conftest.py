"""Shared DuckDB fixtures for Phase 4 E2E tests.

We build an in-memory DuckDB that mirrors the ``sales`` schema declared
in :mod:`tests.unit.planning.fixtures`. Every physical ``source:`` in
the semantic model has a matching DuckDB table populated with a small,
deterministic dataset — just enough rows to exercise joins, filters,
aggregates, and full-outer merges.

Keeping the fixture minimal is intentional: E2E tests assert on *row
sets* (layer 4 of the test pyramid), so the inputs stay legible in
failure messages.
"""

from __future__ import annotations

from collections.abc import Iterator

import duckdb
import pytest


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("CREATE SCHEMA IF NOT EXISTS sales")

    conn.execute("""
        CREATE TABLE sales.customers (
            id INTEGER,
            region VARCHAR,
            market_segment VARCHAR
        )
        """)
    conn.execute("""
        INSERT INTO sales.customers VALUES
            (1, 'NA',   'enterprise'),
            (2, 'NA',   'smb'),
            (3, 'EMEA', 'enterprise'),
            (4, 'APAC', 'smb')
        """)

    conn.execute("""
        CREATE TABLE sales.orders (
            order_id INTEGER,
            customer_id INTEGER,
            status VARCHAR,
            amount DOUBLE,
            discount DOUBLE
        )
        """)
    conn.execute("""
        INSERT INTO sales.orders VALUES
            (10, 1, 'paid',    100.0,  5.0),
            (11, 1, 'paid',    200.0, 10.0),
            (12, 2, 'paid',     50.0,  0.0),
            (13, 2, 'pending',  75.0,  0.0),
            (14, 3, 'paid',    300.0, 15.0),
            (15, 4, 'pending', 125.0,  0.0)
        """)

    conn.execute("""
        CREATE TABLE sales.returns (
            return_id INTEGER,
            customer_id INTEGER,
            order_id INTEGER,
            refund_amount DOUBLE
        )
        """)
    conn.execute("""
        INSERT INTO sales.returns VALUES
            (100, 1, 10, 20.0),
            (101, 3, 14, 50.0),
            (102, 4, 15, 10.0)
        """)


@pytest.fixture()
def duckdb_sales() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield an in-memory DuckDB seeded with the Foundation sales schema."""
    conn = duckdb.connect(":memory:")
    try:
        _seed(conn)
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def duckdb_tpcds() -> Iterator[duckdb.DuckDBPyConnection]:
    """Yield an in-memory DuckDB seeded with the TPC-DS Foundation schema."""
    from tests.e2e.tpcds_fixtures import seed_tpcds

    conn = duckdb.connect(":memory:")
    try:
        seed_tpcds(conn)
        yield conn
    finally:
        conn.close()
