"""Layer 3 of the compiler pipeline.

Takes a :class:`~osi.planning.QueryPlan` + :class:`Dialect` and produces
a SQL string via SQLGlot AST composition. Never reads the
:class:`~osi.parsing.models.SemanticModel`.

See ``../../../ARCHITECTURE.md`` §4 for the full contract. All SQL
manipulation goes through ``sqlglot.exp.*`` — raw-string SQL is banned.
"""

from __future__ import annotations

from osi.planning.plan import QueryPlan

from .cte_optimizer import optimize_ctes
from .dialect import Dialect, render_sql
from .transpiler import plan_to_select


def compile_plan(plan: QueryPlan, *, dialect: Dialect) -> str:
    """Full pipeline: :class:`QueryPlan` → SQL text for ``dialect``.

    Equivalent to::

        ast = plan_to_select(plan)
        ast = optimize_ctes(ast)
        return render_sql(ast, dialect=dialect)

    Provided as a single entry point so goldens, diagnostics, and the
    CLI never have to assemble the steps by hand.
    """
    ast = plan_to_select(plan)
    ast = optimize_ctes(ast)
    return render_sql(ast, dialect=dialect)


__all__ = [
    "Dialect",
    "compile_plan",
    "optimize_ctes",
    "plan_to_select",
    "render_sql",
]
