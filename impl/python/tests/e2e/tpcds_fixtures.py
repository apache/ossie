"""TPC-DS Foundation fixtures for Phase 6 hardening.

We ship a hand-curated, miniature ``tpcds.*`` schema and seeded data
small enough to make per-query assertions tractable while still
exercising every Foundation shape (single-fact, multi-fact merge,
WHERE, ORDER BY, LIMIT). The model lives in
``examples/models/tpcds_thin.yaml``; this module owns the DuckDB seed.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from osi.parsing.graph import build_graph
from osi.parsing.namespace import build_namespace
from osi.parsing.parser import parse_semantic_model
from osi.planning.planner_context import PlannerContext

_MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "examples" / "models" / "tpcds_thin.yaml"
)


def load_tpcds_context() -> PlannerContext:
    """Parse ``tpcds_thin.yaml`` and build a fully-validated context."""
    result = parse_semantic_model(_MODEL_PATH.read_text())
    return PlannerContext(
        model=result.model,
        namespace=build_namespace(result.model),
        graph=build_graph(result.model),
    )


def seed_tpcds(conn: duckdb.DuckDBPyConnection) -> None:
    """Populate ``conn`` with a deterministic miniature TPC-DS dataset."""
    conn.execute("CREATE SCHEMA IF NOT EXISTS tpcds")

    conn.execute("""
        CREATE TABLE tpcds.item (
            i_item_sk   INTEGER,
            i_category  VARCHAR,
            i_class     VARCHAR,
            i_brand     VARCHAR
        )
        """)
    conn.execute("""
        INSERT INTO tpcds.item VALUES
            (1, 'Books',  'fiction',   'acme'),
            (2, 'Books',  'nonfic',    'acme'),
            (3, 'Music',  'pop',       'zen'),
            (4, 'Music',  'classical', 'zen'),
            (5, 'Sports', 'running',   'fit')
        """)

    conn.execute("""
        CREATE TABLE tpcds.customer (
            c_customer_sk         INTEGER,
            c_birth_country       VARCHAR,
            c_preferred_cust_flag VARCHAR
        )
        """)
    conn.execute("""
        INSERT INTO tpcds.customer VALUES
            (1, 'USA',    'Y'),
            (2, 'USA',    'N'),
            (3, 'CANADA', 'Y'),
            (4, 'MEXICO', 'N')
        """)

    conn.execute("""
        CREATE TABLE tpcds.store (
            s_store_sk INTEGER,
            s_state    VARCHAR,
            s_country  VARCHAR
        )
        """)
    conn.execute("""
        INSERT INTO tpcds.store VALUES
            (10, 'CA', 'USA'),
            (11, 'NY', 'USA'),
            (12, 'ON', 'CANADA')
        """)

    conn.execute("""
        CREATE TABLE tpcds.store_sales (
            ss_ticket_number    INTEGER,
            ss_item_sk          INTEGER,
            ss_customer_sk      INTEGER,
            ss_store_sk         INTEGER,
            ss_sold_date_sk     INTEGER,
            ss_quantity         INTEGER,
            ss_ext_sales_price  DOUBLE,
            ss_net_profit       DOUBLE
        )
        """)
    conn.execute("""
        INSERT INTO tpcds.store_sales VALUES
            (1001, 1, 1, 10, 20250101, 2, 20.0,  8.0),
            (1001, 3, 1, 10, 20250101, 1, 15.0,  5.0),
            (1002, 2, 2, 11, 20250102, 3, 30.0, 10.0),
            (1002, 5, 2, 11, 20250102, 1, 50.0, 20.0),
            (1003, 4, 3, 12, 20250103, 2, 40.0, 15.0),
            (1004, 1, 3, 12, 20250104, 1, 10.0,  4.0),
            (1005, 5, 4, 10, 20250105, 2, 100.0, 40.0)
        """)

    conn.execute("""
        CREATE TABLE tpcds.store_returns (
            sr_ticket_number INTEGER,
            sr_item_sk       INTEGER,
            sr_customer_sk   INTEGER,
            sr_store_sk      INTEGER,
            sr_return_amt    DOUBLE
        )
        """)
    conn.execute("""
        INSERT INTO tpcds.store_returns VALUES
            (1002, 5, 2, 11, 50.0),
            (1005, 5, 4, 10, 25.0)
        """)


__all__ = ["load_tpcds_context", "seed_tpcds"]
