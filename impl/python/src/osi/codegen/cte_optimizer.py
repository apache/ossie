"""Post-build AST transforms for generated SQL.

The transpiler emits one CTE per :class:`PlanStep`. Some of those CTEs
are trivially inline-able (pass-through ``PROJECT`` s, single-use chains
with no grain-changing operation in between). This module applies two
conservative transforms; their contract is that the result is always
relationally equivalent to the input.

**Pass-through CTE inlining** — if the final (root) CTE is a bare
``SELECT col1, col2, … FROM step_M`` with no WHERE / GROUP BY / HAVING /
JOIN, the outer ``SELECT`` can read from ``step_M`` directly, making the
final PROJECT CTE unnecessary. This is the most common single
readability gain across every plan shape.

**Dead CTE removal** — if the transpiler ever produces a CTE that is
not referenced from any downstream step (possible as planner invariants
evolve), drop it. Reachability is computed as a BFS through *live* CTEs:
the seed set is the step CTEs referenced from the outer ``SELECT`` only,
and the transitive closure follows references *only inside CTEs already
proven live*. A previous implementation walked every table in the
entire AST, which would mark a CTE referenced by a dead CTE as live and
defeat the purpose of the pass.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from osi.planning.prefixes import is_step_alias


def _inline_trivial_final_cte(select: exp.Select) -> exp.Select:
    """Inline a trivially pass-through root CTE into the outer SELECT.

    Eliminates the pattern::

        step_N AS (SELECT step_M.a, step_M.b FROM step_M),
        SELECT step_N.a, step_N.b FROM step_N ORDER BY …

    replacing it with::

        SELECT step_M.a, step_M.b FROM step_M ORDER BY …

    Only applied when the root CTE passes every qualification: bare
    (table-qualified) column references, a single ``FROM``, and no
    ``WHERE`` / ``GROUP BY`` / ``HAVING`` / ``JOIN``.
    """
    with_clause = select.args.get("with")
    if with_clause is None:
        return select

    # Identify which CTE alias the outer SELECT reads from.
    outer_from = select.args.get("from")
    if outer_from is None:
        return select
    from_table = outer_from.this
    if not isinstance(from_table, exp.Table):
        return select
    root_alias = from_table.name
    if not root_alias or not is_step_alias(root_alias):
        return select

    # Look up the root CTE in the WITH clause.
    by_alias: dict[str, exp.CTE] = {
        _cte_name(c): c for c in with_clause.expressions if _cte_name(c)
    }
    root_cte = by_alias.get(root_alias)
    if root_cte is None:
        return select

    # Check that the root CTE body is a trivial pass-through.
    body = root_cte.this
    if not isinstance(body, exp.Select):
        return select
    if any(body.args.get(k) for k in ("where", "group", "having")):
        return select
    if body.args.get("joins"):
        return select
    inner_from = body.args.get("from")
    if inner_from is None:
        return select
    inner_table = inner_from.this
    if not isinstance(inner_table, exp.Table) or not is_step_alias(inner_table.name):
        return select
    inner_alias = inner_table.name
    # Every projection must be a bare column reference — no aliases, no
    # computed expressions.  ``exp.Column`` covers both bare and
    # table-qualified column references.
    if not body.expressions or any(
        not isinstance(p, exp.Column) for p in body.expressions
    ):
        return select

    # Safe to inline: rewrite outer SELECT column references and FROM.
    # Temporarily detach the WITH clause so find_all only visits the
    # outer SELECT body (not CTE bodies).
    select.set("with", None)
    try:
        for col in select.find_all(exp.Column):
            if col.table == root_alias:
                col.set("table", exp.to_identifier(inner_alias))
    finally:
        select.set("with", with_clause)

    select.set("from", exp.From(this=exp.to_table(inner_alias)))

    # Drop the now-inlined CTE from the WITH clause.
    kept = [c for c in with_clause.expressions if _cte_name(c) != root_alias]
    select.set("with", exp.With(expressions=kept) if kept else None)
    return select


def optimize_ctes(select: exp.Select) -> exp.Select:
    """Apply conservative CTE cleanup to ``select`` and return it.

    Idempotent; safe to call twice. Preserves CTE ordering.
    Applies :func:`_inline_trivial_final_cte` first, then dead-CTE
    removal, so that inlining can expose additional dead CTEs.
    """
    select = _inline_trivial_final_cte(select)

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

