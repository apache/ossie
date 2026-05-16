"""Post-build AST transforms for generated SQL.

The transpiler emits one CTE per :class:`PlanStep`. Some of those CTEs
are trivially inline-able (pass-through ``PROJECT`` s, single-use chains
with no grain-changing operation in between). The optimizer is
deliberately *conservative*: its contract with goldens is that it can
*only* produce a plan that yields the same relational result set.

The Foundation ships with a single safe transform: **dead CTE removal**
— if the transpiler ever produces a CTE that isn't referenced from any
downstream step (possible as planner invariants evolve), drop it. Row-
preserving inlining is left as a follow-up behind a feature flag to
keep goldens stable while the rest of Phase 4 solidifies.

Reachability is computed as a BFS through *live* CTEs: the seed set is
the step CTEs referenced from the outer ``SELECT`` only, and the
transitive closure follows references *only inside CTEs already proven
live*. A previous implementation walked every table in the entire AST,
which would mark a CTE referenced by a dead CTE as live and defeat the
purpose of the pass.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from osi.planning.prefixes import is_step_alias


def optimize_ctes(select: exp.Select) -> exp.Select:
    """Apply conservative CTE cleanup to ``select`` and return it.

    Idempotent; safe to call twice. Preserves CTE ordering.
    """
    with_clause = select.args.get("with")
    if with_clause is None:
        return select

    by_alias: dict[str, exp.CTE] = {
        _cte_name(cte): cte for cte in with_clause.expressions if _cte_name(cte)
    }

    # Seed referenced set from the outer SELECT *only* — step CTEs that
    # nothing downstream of the WITH clause uses are dead by definition.
    referenced: set[str] = set()
    for table in _outer_table_refs(select):
        if table.name and is_step_alias(table.name):
            referenced.add(table.name)

    # BFS through live CTEs: only follow references inside CTEs already
    # in ``referenced``. This avoids the trap of letting a dead CTE
    # keep its own dependencies alive.
    frontier = list(referenced)
    while frontier:
        current = frontier.pop()
        cte = by_alias.get(current)
        if cte is None:
            continue
        for tbl in cte.this.find_all(exp.Table):
            if tbl.name and is_step_alias(tbl.name) and tbl.name not in referenced:
                referenced.add(tbl.name)
                frontier.append(tbl.name)

    kept: list[exp.CTE] = [
        c for c in with_clause.expressions if _cte_name(c) in referenced
    ]
    if len(kept) == len(with_clause.expressions):
        return select
    if not kept:
        select.set("with", None)
    else:
        select.set("with", exp.With(expressions=kept))
    return select


def _outer_table_refs(select: exp.Select) -> list[exp.Table]:
    """Return tables referenced from the outer ``SELECT`` (not from CTE bodies).

    Equivalent to ``select.find_all(exp.Table)`` minus everything reachable
    through ``with_clause.expressions``. Implemented by temporarily
    detaching the WITH clause to keep the recursion simple.
    """
    with_clause = select.args.get("with")
    select.set("with", None)
    try:
        return list(select.find_all(exp.Table))
    finally:
        select.set("with", with_clause)


def _cte_name(cte: exp.CTE) -> str:
    alias = cte.args.get("alias")
    if alias is None:
        return ""
    return str(alias.name)


__all__ = ["optimize_ctes"]
