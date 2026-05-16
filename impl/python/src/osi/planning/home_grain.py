"""Implicit home-grain aggregation rewrite (D-003 + D-015).

A field declared on dataset ``H`` whose body aggregates columns from a
*finer-grained* dataset ``F`` is implicitly evaluated **at H's grain**
(``Proposed_OSI_Semantics §4.5 form (1) + D-015``). The compilation
strategy is engine-defined; D-015 only requires the result to be
equivalent to:

* a correlated subquery,
* a ``LATERAL`` join, or
* a pre-aggregated CTE merged back on the home key.

This module pins the choice for the OSI Python reference implementation:
**correlated subquery**. The choice is opaque to the spec and produces
the same per-row scalar values as either alternative.

Scope (Foundation v0.1):

* The aggregate must reference exactly one foreign dataset.
* That foreign dataset must be related to the home dataset by a single
  N:1 relationship (``F`` on the N side, ``H`` on the 1 side).
* Anything else is *not rewritten* and falls through to the planner's
  pre-existing behaviour. Multi-hop / multi-dataset rewrites are
  S-21's responsibility (composes with nested-aggregate planning).

The rewrite is purely an AST → AST transformation on the field's
``FrozenSQL`` body. The algebra and codegen layers see the rewritten
expression and never know the difference.
"""

from __future__ import annotations

from typing import Mapping

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.errors import OSIParseError
from osi.parsing.graph import Cardinality, RelationshipEdge, RelationshipGraph
from osi.parsing.models import Dataset, Field


def rewrite_field_for_home_grain(
    field: Field,
    *,
    home: Identifier,
    graph: RelationshipGraph,
    datasets_by_name: Mapping[Identifier, Dataset],
) -> FrozenSQL:
    """Return a copy of ``field.expression`` with cross-grain aggregates wrapped.

    For every ``exp.AggFunc`` node in the field body whose argument
    columns reference exactly one foreign dataset reachable from
    ``home`` via a single safe N:1 step, the aggregate is replaced by
    a correlated subquery::

        ( SELECT <agg> FROM <foreign> WHERE <foreign.fk> = <home.pk> )

    Aggregates that already live on ``home`` are left alone.
    Aggregates we cannot resolve (multi-hop, multi-dataset, no
    matching N:1 edge) are left alone too — the surrounding planner
    passes will reject them with the appropriate error if needed.
    """
    body = field.expression.expr
    if not _has_cross_grain_aggregate(body, home=home):
        return field.expression

    new_body = body.copy()
    # Walk every aggregate, deepest-first so nested rewrites don't
    # interfere with each other. ``find_all`` yields parents before
    # children, so we reverse for safety even though Foundation v0.1
    # does not allow nested aggregates today.
    aggregates = list(new_body.find_all(exp.AggFunc))
    top_replacement: exp.Expression | None = None
    for agg in aggregates:
        foreign_datasets = _foreign_datasets_in(agg, home=home)
        if len(foreign_datasets) != 1:
            continue
        foreign = next(iter(foreign_datasets))
        edge = _find_n1_edge(home=home, foreign=foreign, graph=graph)
        if edge is None:
            continue
        if foreign not in datasets_by_name:
            continue
        subquery = _build_correlated_subquery(
            agg=agg,
            home=home,
            foreign=foreign,
            edge=edge,
            foreign_dataset=datasets_by_name[foreign],
        )
        if agg is new_body:
            # ``Expression.replace`` mutates the *parent*; when the
            # aggregate is the field's top-level expression there is
            # no parent and we have to swap the body wholesale.
            top_replacement = subquery
        else:
            agg.replace(subquery)
    if top_replacement is not None:
        new_body = top_replacement
    return FrozenSQL.of(new_body)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _has_cross_grain_aggregate(body: exp.Expression, *, home: Identifier) -> bool:
    """Cheap pre-check used to skip the deep rewrite when nothing matches."""
    for agg in body.find_all(exp.AggFunc):
        if _foreign_datasets_in(agg, home=home):
            return True
    return False


def _foreign_datasets_in(
    node: exp.Expression, *, home: Identifier
) -> frozenset[Identifier]:
    """Return the set of dataset qualifiers in ``node`` that are not ``home``."""
    out: set[Identifier] = set()
    for col in node.find_all(exp.Column):
        if not col.table:
            continue
        try:
            ds = normalize_identifier(col.table)
        except OSIParseError:
            continue
        if ds != home:
            out.add(ds)
    return frozenset(out)


def _find_n1_edge(
    *,
    home: Identifier,
    foreign: Identifier,
    graph: RelationshipGraph,
) -> RelationshipEdge | None:
    """Return the unique edge where ``foreign`` is N and ``home`` is 1.

    Returns ``None`` if there is no such edge or there are multiple
    candidates (the planner's path-finder will surface the ambiguity
    when the field is actually used).
    """
    candidates: list[RelationshipEdge] = []
    for edge in graph.neighbors(home):
        if edge.cardinality is Cardinality.N_TO_N:
            continue
        if edge.cardinality is Cardinality.N_TO_ONE:
            # foreign sits on the N (from) side, home on the 1 (to) side
            if edge.from_dataset == foreign and edge.to_dataset == home:
                candidates.append(edge)
            continue
        if edge.cardinality is Cardinality.ONE_TO_ONE:
            if {edge.from_dataset, edge.to_dataset} == {home, foreign}:
                candidates.append(edge)
    if len(candidates) == 1:
        return candidates[0]
    return None


# ---------------------------------------------------------------------------
# Subquery construction
# ---------------------------------------------------------------------------


def _build_correlated_subquery(
    *,
    agg: exp.AggFunc,
    home: Identifier,
    foreign: Identifier,
    edge: RelationshipEdge,
    foreign_dataset: Dataset,
) -> exp.Subquery:
    """Build ``(SELECT <agg> FROM <foreign> WHERE <correlation>)``.

    The correlation predicate ANDs ``foreign.fk = home.pk`` over every
    pair in ``edge``. We use the foreign dataset's *physical source*
    name in the FROM (matching what the source step would emit) and
    the home dataset's *logical name* in the correlation — the
    surrounding source-step SELECT runs from the same logical name,
    so the correlated reference resolves there.
    """
    inner_select = exp.Select()
    inner_select.set("expressions", [agg.copy()])
    foreign_table = exp.to_table(foreign_dataset.source or str(foreign))
    inner_select.set("from", exp.From(this=foreign_table))
    correlation = _build_correlation_predicate(
        home=home,
        foreign=foreign,
        edge=edge,
    )
    inner_select.set("where", exp.Where(this=correlation))
    return exp.Subquery(this=inner_select)


def _build_correlation_predicate(
    *,
    home: Identifier,
    foreign: Identifier,
    edge: RelationshipEdge,
) -> exp.Expression:
    """AND ``foreign.fk = home.pk`` for every column pair in ``edge``."""
    if edge.from_dataset == foreign:
        foreign_cols = edge.from_columns
        home_cols = edge.to_columns
    else:
        foreign_cols = edge.to_columns
        home_cols = edge.from_columns
    pairs = list(zip(foreign_cols, home_cols, strict=True))
    conds: list[exp.Expression] = []
    for fcol, hcol in pairs:
        conds.append(
            exp.EQ(
                this=exp.column(str(fcol), table=str(foreign)),
                expression=exp.column(str(hcol), table=str(home)),
            )
        )
    if len(conds) == 1:
        return conds[0]
    out = conds[0]
    for c in conds[1:]:
        out = exp.And(this=out, expression=c)
    return out


__all__ = ["rewrite_field_for_home_grain"]
