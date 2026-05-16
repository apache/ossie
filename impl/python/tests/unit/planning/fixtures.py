"""Shared Foundation semantic-model fixtures for the planner tests.

Constructing models through :mod:`osi.parsing` keeps the tests honest:
every fixture exercises the full parser + validator + namespace + graph
pipeline, so a regression in any of those surfaces immediately.

The helpers below return :class:`PlannerContext` handles ready to hand
to :func:`osi.planning.plan`.

Note on feature flags
---------------------
These fixtures predate the strict-Foundation deferral of per-dataset
``metrics:`` blocks, aggregate-bodied fields, and nested aggregation
(see :class:`osi.config.FoundationFlags` for the contract). They keep
the legacy YAML shape so the planner's existing handling of those
constructs stays test-covered behind the opt-in flags. Production
callers (the conformance adapter, the CLI, anything user-facing) use
:func:`osi.parsing.parser.parse_semantic_model` with the strict
defaults.
"""

from __future__ import annotations

import textwrap

from osi.config import FoundationFlags
from osi.parsing.graph import build_graph
from osi.parsing.namespace import build_namespace
from osi.parsing.parser import parse_semantic_model
from osi.planning.planner_context import PlannerContext

_ORDERS_MODEL = textwrap.dedent("""\
    semantic_model:
      - name: demo
        dialect: ANSI_SQL
        datasets:
          - name: orders
            source: sales.orders
            primary_key: [order_id]
            fields:
              - name: order_id
                expression: order_id
                role: dimension
              - name: customer_id
                expression: customer_id
                role: dimension
              - name: status
                expression: status
                role: dimension
              - name: amount
                expression: amount
                role: fact
              - name: discount
                expression: discount
                role: fact
            metrics:
              - name: total_revenue
                expression: SUM(amount)
              - name: order_count
                expression: COUNT(order_id)
              - name: distinct_customers
                expression: COUNT(DISTINCT customer_id)
              - name: max_amount
                expression: MAX(amount)
              - name: avg_discount
                expression: AVG(discount)
          - name: customers
            source: sales.customers
            primary_key: [id]
            fields:
              - name: id
                expression: id
                role: dimension
              - name: region
                expression: region
                role: dimension
              - name: segment
                expression: market_segment
                role: dimension
          - name: returns
            source: sales.returns
            primary_key: [return_id]
            fields:
              - name: return_id
                expression: return_id
                role: dimension
              - name: customer_id
                expression: customer_id
                role: dimension
              - name: order_id
                expression: order_id
                role: dimension
              - name: refund_amount
                expression: refund_amount
                role: fact
            metrics:
              - name: total_refunds
                expression: SUM(refund_amount)
        relationships:
          - name: orders_to_customers
            from: orders
            to: customers
            from_columns: [customer_id]
            to_columns: [id]
          - name: returns_to_customers
            from: returns
            to: customers
            from_columns: [customer_id]
            to_columns: [id]
        metrics:
          - name: avg_order_value
            expression: total_revenue / NULLIF(order_count, 0)
            description: Composite metric — exercises ADD_COLUMNS path.
    """)


_MN_MODEL = textwrap.dedent("""\
    semantic_model:
      - name: mn_model
        datasets:
          - name: grade_logs
            source: schools.grade_logs
            primary_key: [log_id]
            fields:
              - name: log_id
                expression: log_id
                role: dimension
              - name: course_title
                expression: course_title
                role: dimension
              - name: grade
                expression: grade
                role: fact
            metrics:
              - name: avg_grade
                expression: AVG(grade)
          - name: courses
            source: schools.courses
            primary_key: [course_id]
            fields:
              - name: course_id
                expression: course_id
                role: dimension
              - name: title
                expression: title
                role: dimension
              - name: subject
                expression: subject
                role: dimension
        relationships:
          - name: logs_to_courses
            from: grade_logs
            to: courses
            from_columns: [course_title]
            to_columns: [title]
    """)


def orders_context() -> PlannerContext:
    """Build a star-schema context around ``orders`` for planner tests.

    Includes a second fact dataset (``returns``) so multi-fact merge
    scenarios can be exercised. Every relationship is N:1. Uses the
    legacy-permissive flag set so the per-dataset ``metrics:`` blocks
    parse — see the module docstring.
    """
    result = parse_semantic_model(
        _ORDERS_MODEL, flags=FoundationFlags.legacy_permissive()
    )
    namespace = build_namespace(result.model)
    graph = build_graph(result.model)
    return PlannerContext(
        model=result.model,
        namespace=namespace,
        graph=graph,
    )


def mn_context() -> PlannerContext:
    """Build a model with a deliberate N:N edge for rejection tests."""
    result = parse_semantic_model(
        _MN_MODEL, flags=FoundationFlags.legacy_permissive()
    )
    namespace = build_namespace(result.model)
    graph = build_graph(result.model)
    return PlannerContext(
        model=result.model,
        namespace=namespace,
        graph=graph,
    )


__all__ = ["mn_context", "orders_context"]
