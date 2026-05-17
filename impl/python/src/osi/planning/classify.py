"""Classify ``where`` and ``having`` predicates.

The planner splits each boolean expression into top-level conjuncts and
assigns each conjunct to one of three buckets:

* **row-level** — ordinary boolean over fields; compiles to ``WHERE``
  on the measure group's pre-aggregated state.
* **semi-join** — ``EXISTS_IN`` / ``NOT EXISTS_IN`` function calls.
  This shape is **deferred in Foundation v0.1** (§10 / D-017) and is
  only accepted when the caller opts in via
  :attr:`FoundationFlags.experimental_exists_in`. With the flag on,
  the predicate compiles to :func:`osi.planning.algebra.filtering_join`;
  with the flag off, it is rejected with
  :attr:`ErrorCode.E_DEFERRED_KEY_REJECTED`.
* **post-aggregate (having)** — conjuncts that reference measures;
  compiles to ``HAVING`` on the final merged state. In the Foundation
  a conjunct is post-aggregate *iff* it comes from the ``having`` slot
  (§5.3 "Having vs Where" — no cross-mixing).

Column-dataset attribution is best-effort — if a bare column reference
is ambiguous, ``E2001_AMBIGUOUS_NAME`` surfaces from
:mod:`osi.planning.resolve`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier, normalize_identifier
from osi.common.sql_expr import FrozenSQL
from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIError, OSIParseError, OSIPlanningError
from osi.parsing.namespace import Namespace
from osi.planning.algebra.operations import FilterMode

# ---------------------------------------------------------------------------
# Typed predicates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RowLevelPredicate:
    """A conjunct that reads one or more fields, no semi-joins."""

    expression: FrozenSQL
    datasets: frozenset[Identifier]
    columns: frozenset[Identifier]


@dataclass(frozen=True, slots=True)
class SemiJoinKeyPair:
    """One ``(outer_col, rhs_dataset.rhs_field)`` pair from an EXISTS_IN."""

    outer_column: Identifier
    outer_dataset: Identifier | None
    rhs_dataset: Identifier
    rhs_column: Identifier


@dataclass(frozen=True, slots=True)
class SemiJoinPredicate:
    """A top-level ``EXISTS_IN`` / ``NOT EXISTS_IN`` call."""

    pairs: tuple[SemiJoinKeyPair, ...]
    mode: FilterMode


@dataclass(frozen=True, slots=True)
class PostAggregatePredicate:
    """A ``having``-side conjunct that reads measures."""

    expression: FrozenSQL
    measures: frozenset[Identifier]


@dataclass(frozen=True, slots=True)
class ClassifiedWhere:
    """Classification of the ``where`` predicate's top-level conjuncts."""

    row_level: tuple[RowLevelPredicate, ...]
    semi_joins: tuple[SemiJoinPredicate, ...]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_where(
    predicate: FrozenSQL | None,
    namespace: Namespace,
    *,
    flags: FoundationFlags | None = None,
) -> ClassifiedWhere:
    """Split a ``where`` clause into row-level + semi-join conjuncts.

    Foundation v0.1 (D-005 / D-012a) routes by *resolved expression
    shape*. ``WHERE`` is the row-level slot, so:

    * an aggregate function (``SUM``, ``COUNT``, …) anywhere in the
      tree raises :attr:`ErrorCode.E_AGGREGATE_IN_WHERE`;
    * a measure reference (the resolved form of a declared metric
      is an aggregate) raises the same code;
    * a single conjunct that mixes a bare column with an aggregate
      raises :attr:`ErrorCode.E_MIXED_PREDICATE_LEVEL` so the user
      sees the routing error before the placement error.

    ``EXISTS_IN`` / ``NOT EXISTS_IN`` are recognised as semi-join
    conjuncts but are **deferred in Foundation v0.1** (§10 / D-017):
    the conjunct is rejected with
    :attr:`ErrorCode.E_DEFERRED_KEY_REJECTED` unless the caller
    passed ``flags=FoundationFlags(experimental_exists_in=True)``.
    The flag is named ``experimental_*`` rather than ``allow_*``
    because, unlike the other deferred constructs, ``EXISTS_IN`` does
    not yet have a published OSI proposal — it lives behind the flag
    as an opt-in surface for callers willing to take the portability
    hit.
    """
    if flags is None:
        flags = FoundationFlags()
    if predicate is None:
        return ClassifiedWhere(row_level=(), semi_joins=())
    measure_names = _collect_measure_names(namespace)
    _reject_window_in_where(predicate.expr)
    _reject_mixed_predicate_level(predicate.expr, measure_names, where="where")
    conjuncts = _split_conjuncts(predicate.expr)
    row_level: list[RowLevelPredicate] = []
    semi_joins: list[SemiJoinPredicate] = []
    for node in conjuncts:
        sj = _try_semi_join(node, flags=flags)
        if sj is not None:
            semi_joins.append(sj)
            continue
        _reject_aggregate_in_where(node, measure_names)
        row_level.append(_classify_row_level(node, namespace))
    return ClassifiedWhere(
        row_level=tuple(row_level),
        semi_joins=tuple(semi_joins),
    )


def _reject_window_in_where(node: exp.Expression) -> None:
    """D-028: window functions are forbidden in ``WHERE``.

    SQL standardly forbids them too, but the Foundation surfaces a
    named code so the user gets actionable advice (move the predicate
    to ``Having`` after wrapping the window in a metric, or use a
    ``QUALIFY``-style outer-Where).

    Per Appendix C / Appendix B D-028 (`Proposed_OSI_Semantics.md`
    §6.10.1): "A window function in `Where` MUST raise
    `E_WINDOW_IN_WHERE`." D-030 governs the *fan-out* failure mode
    (``E_WINDOW_OVER_FANOUT_REWRITE``), not where-clause placement.
    """
    from osi.common.windows import contains_window

    if contains_window(node):
        raise OSIPlanningError(
            ErrorCode.E_WINDOW_IN_WHERE,
            (
                "Where predicate contains a window function; windows "
                "are only allowed in Measures, Fields, Order By, and "
                "Having (D-028). Move the predicate to Having or wrap "
                "the window in a metric first."
            ),
            context={"predicate": node.sql()},
        )


def _reject_mixed_predicate_level(
    node: exp.Expression,
    measure_names: frozenset[Identifier],
    *,
    where: str,
) -> None:
    """Reject the whole-predicate shape mix BEFORE per-conjunct routing.

    D-012c says a boolean predicate whose top-level tree mixes
    aggregate halves and row-level halves is rejected as a single
    mixed-shape error rather than as N per-conjunct placement
    errors. Catching it here keeps the diagnostic readable.
    """
    has_agg = _contains_aggregate(node) or (
        _first_measure_reference(node, measure_names) is not None
    )
    has_row = _contains_non_aggregate_column(node, measure_names)
    if has_agg and has_row:
        raise OSIPlanningError(
            ErrorCode.E_MIXED_PREDICATE_LEVEL,
            (
                "boolean predicate mixes row-level and aggregate halves; "
                "split into separate Where (row-level) and Having "
                "(aggregate) clauses. See Proposed_OSI_Semantics.md "
                "D-012c."
            ),
            context={"expression": node.sql(), "where": where},
        )


def _collect_measure_names(namespace: Namespace) -> frozenset[Identifier]:
    """Return every identifier that names a declared measure.

    The Foundation scopes metrics two ways: model-scoped (visible by
    bare name everywhere) and table-scoped (visible by bare name when
    unambiguous, or under a ``dataset.metric`` qualifier). Both forms
    must be rejected from ``WHERE``.
    """
    names: set[Identifier] = set(namespace.metrics.keys())
    for ds_ns in namespace.datasets.values():
        names.update(ds_ns.metrics.keys())
    return frozenset(names)


def _reject_aggregate_in_where(
    node: exp.Expression, measure_names: frozenset[Identifier]
) -> None:
    """Reject aggregate-shape conjuncts in a ``WHERE`` clause.

    Two surfaces produce a "this conjunct is an aggregate" verdict:

    1. A SQL aggregate function call (``SUM``, ``COUNT``, ``AVG``,
       …) appears in the AST.
    2. A column reference whose name matches a declared measure;
       resolving the metric would yield an aggregate expression.

    Either one alone ⇒ ``E_AGGREGATE_IN_WHERE``. If the conjunct
    *also* contains a row-level column reference outside the
    aggregate, the user has stitched two shapes into one boolean —
    that is ``E_MIXED_PREDICATE_LEVEL`` (D-012c) and takes
    precedence so the diagnostic points at the right fix.
    """
    has_agg = _contains_aggregate(node)
    measure_hit = _first_measure_reference(node, measure_names)
    if not has_agg and measure_hit is None:
        return
    has_row = _contains_non_aggregate_column(node, measure_names)
    if has_row:
        raise OSIPlanningError(
            ErrorCode.E_MIXED_PREDICATE_LEVEL,
            (
                "boolean predicate mixes row-level and aggregate halves; "
                "split into separate Where (row-level) and Having "
                "(aggregate) clauses. See Proposed_OSI_Semantics.md D-012c."
            ),
            context={"expression": node.sql(), "where": "where"},
        )
    if measure_hit is not None:
        qualifier = f"{measure_hit[1]}." if measure_hit[1] else ""
        raise OSIPlanningError(
            ErrorCode.E_AGGREGATE_IN_WHERE,
            (
                f"WHERE clause references measure {qualifier}{measure_hit[0]!r}; "
                "measures are aggregates — move this predicate to Having. "
                "See Proposed_OSI_Semantics.md D-012a."
            ),
            context={
                "measure": measure_hit[0],
                "expression": node.sql(),
                "suggestion": "having",
            },
        )
    raise OSIPlanningError(
        ErrorCode.E_AGGREGATE_IN_WHERE,
        (
            "WHERE clause contains an aggregate function; aggregates "
            "evaluate post-GROUP BY — move this predicate to Having. "
            "See Proposed_OSI_Semantics.md D-012a."
        ),
        context={"expression": node.sql(), "suggestion": "having"},
    )


def _contains_aggregate(node: exp.Expression) -> bool:
    """Return True iff ``node`` (or any descendant) is a SQL aggregate call."""
    return any(isinstance(_unwrap_walk(n), exp.AggFunc) for n in node.walk())


def _unwrap_walk(item: object) -> exp.Expression:
    if isinstance(item, exp.Expression):
        return item
    if isinstance(item, tuple) and item and isinstance(item[0], exp.Expression):
        return item[0]
    return exp.Expression()


def _first_measure_reference(
    node: exp.Expression, measure_names: frozenset[Identifier]
) -> tuple[Identifier, str | None] | None:
    if not measure_names:
        return None
    for col in node.find_all(exp.Column):
        try:
            name = normalize_identifier(col.name)
        except OSIParseError:
            continue
        if name in measure_names:
            return (name, col.table or None)
    return None


def _contains_non_aggregate_column(
    node: exp.Expression, measure_names: frozenset[Identifier]
) -> bool:
    """Return True iff ``node`` references a column **outside** every aggregate.

    Used to detect the mixed-level shape (D-012c). A column reference
    that lives inside an aggregate function (e.g. ``orders.amount``
    inside ``SUM(orders.amount)``) does NOT count — the aggregate
    consumes that reference. A bare column reference at the same
    level as the aggregate (e.g. ``customers.region`` next to
    ``SUM(...)``) DOES count.
    """
    for col in node.find_all(exp.Column):
        if _is_inside_aggregate(col):
            continue
        try:
            name = normalize_identifier(col.name)
        except OSIParseError:
            continue
        if name in measure_names:
            # Measure-named column is itself an aggregate; not a
            # row-level reference for the purposes of mixed-level
            # detection.
            continue
        return True
    return False


def _is_inside_aggregate(node: exp.Expression) -> bool:
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.AggFunc):
            return True
        parent = parent.parent
    return False


def classify_having(
    predicate: FrozenSQL | None,
    measure_names: Iterable[Identifier],
) -> tuple[PostAggregatePredicate, ...]:
    """Split a ``having`` clause into post-aggregate conjuncts.

    Foundation v0.1 (D-005 / D-012b / D-012c) routes by *resolved
    expression shape*. ``HAVING`` is the aggregate-shape slot, so:

    * A purely row-level conjunct (no aggregate function and no
      measure reference) ⇒ :attr:`ErrorCode.E_NON_AGGREGATE_IN_HAVING`.
    * A conjunct that mixes a row-level column with an aggregate
      ⇒ :attr:`ErrorCode.E_MIXED_PREDICATE_LEVEL` (D-012c) — wins
      over the placement error so the diagnostic points at the
      right fix.
    """
    if predicate is None:
        return ()
    measures = frozenset(measure_names)
    _reject_mixed_predicate_level(predicate.expr, measures, where="having")
    conjuncts = _split_conjuncts(predicate.expr)
    out: list[PostAggregatePredicate] = []
    for node in conjuncts:
        has_agg = _contains_aggregate(node)
        refs = _bare_column_refs(node)
        touched = refs & measures
        is_aggregate_shape = has_agg or bool(touched)
        if is_aggregate_shape:
            out.append(
                PostAggregatePredicate(
                    expression=FrozenSQL.of(node.copy()),
                    measures=touched,
                )
            )
            continue
        raise OSIPlanningError(
            ErrorCode.E_NON_AGGREGATE_IN_HAVING,
            (
                "Having conjunct is purely row-level (no aggregate); "
                "push it down to Where. See Proposed_OSI_Semantics.md "
                "D-012b."
            ),
            context={"expression": node.sql(), "suggestion": "where"},
        )
    return tuple(out)


# ---------------------------------------------------------------------------
# Semi-join recognition
# ---------------------------------------------------------------------------


def _try_semi_join(
    node: exp.Expression, *, flags: FoundationFlags
) -> SemiJoinPredicate | None:
    """Recognise an ``EXISTS_IN`` / ``NOT EXISTS_IN`` conjunct.

    Returns ``None`` if ``node`` is not a semi-join shape (so the
    caller can fall through to row-level / aggregate handling).

    If ``node`` IS a semi-join, the flag in ``FoundationFlags``
    decides whether to admit it. Foundation v0.1 §10 / D-017 lists
    semi-join filtering as deferred, so the default (flag off) is to
    raise ``E_DEFERRED_KEY_REJECTED`` with a hint that the caller can
    opt in via ``FoundationFlags(experimental_exists_in=True)``. The
    flag intentionally uses the ``experimental_`` prefix because the
    semi-join surface does not yet have a published OSI proposal.
    """
    inner, negated = _unwrap_not(node)
    if not isinstance(inner, exp.Anonymous):
        return None
    if (inner.this or "").upper() != "EXISTS_IN":
        return None
    if not flags.experimental_exists_in:
        raise OSIPlanningError(
            ErrorCode.E_DEFERRED_KEY_REJECTED,
            (
                "EXISTS_IN / NOT EXISTS_IN semi-join filtering is "
                "deferred in Foundation v0.1 (§10 / D-017). To "
                "experiment with this engine's semi-join path, "
                "parse the model with "
                "FoundationFlags(experimental_exists_in=True) — note "
                "that this is an experimental surface, not a "
                "Foundation-conformant one."
            ),
            context={
                "predicate": node.sql(),
                "deferred_feature": "exists_in_semi_join",
                "flag": "experimental_exists_in",
            },
        )
    raw_args: Sequence[exp.Expression] = tuple(inner.expressions)
    if len(raw_args) < 2 or len(raw_args) % 2 != 0:
        raise OSIPlanningError(
            ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT,
            "EXISTS_IN requires an even number of arguments "
            "(pairs of outer_col, rhs.col)",
            context={"arg_count": len(raw_args)},
        )
    pairs: list[SemiJoinKeyPair] = []
    for idx in range(0, len(raw_args), 2):
        outer = raw_args[idx]
        rhs = raw_args[idx + 1]
        pairs.append(_build_semi_join_pair(outer=outer, rhs=rhs))
    mode = FilterMode.ANTI if negated else FilterMode.SEMI
    return SemiJoinPredicate(pairs=tuple(pairs), mode=mode)


def _build_semi_join_pair(
    *, outer: exp.Expression, rhs: exp.Expression
) -> SemiJoinKeyPair:
    outer_col = _extract_column(outer)
    rhs_col = _extract_column(rhs)
    if rhs_col.dataset is None:
        raise OSIPlanningError(
            ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT,
            "EXISTS_IN right-hand column must be qualified (dataset.field)",
            context={"rhs": rhs.sql()},
        )
    return SemiJoinKeyPair(
        outer_column=outer_col.name,
        outer_dataset=outer_col.dataset,
        rhs_dataset=rhs_col.dataset,
        rhs_column=rhs_col.name,
    )


@dataclass(frozen=True, slots=True)
class _ColRef:
    dataset: Identifier | None
    name: Identifier


def _extract_column(node: exp.Expression) -> _ColRef:
    if not isinstance(node, exp.Column):
        raise OSIPlanningError(
            ErrorCode.E1208_UNSUPPORTED_SQL_CONSTRUCT,
            "EXISTS_IN arguments must be bare / qualified column references",
            context={"node": node.sql()},
        )
    dataset = node.table or None
    # Narrow exception handlers: ``normalize_identifier`` raises only
    # :class:`OSIError` on bad shapes (see
    # :mod:`osi.common.identifiers`). Catching a wider category here
    # would mask real bugs and break the "every failure carries a
    # code" contract by swallowing unrelated runtime errors.
    try:
        name = normalize_identifier(node.name)
    except OSIError as exc:
        raise OSIPlanningError(
            ErrorCode.E1005_IDENTIFIER_INVALID,
            f"invalid identifier in filter: {node.name!r}",
            context={"name": node.name},
        ) from exc
    try:
        ds_id = normalize_identifier(dataset) if dataset else None
    except OSIError as exc:
        raise OSIPlanningError(
            ErrorCode.E1005_IDENTIFIER_INVALID,
            f"invalid dataset in filter: {dataset!r}",
            context={"dataset": dataset},
        ) from exc
    return _ColRef(dataset=ds_id, name=name)


def _unwrap_not(node: exp.Expression) -> tuple[exp.Expression, bool]:
    if isinstance(node, exp.Not):
        inner = node.this
        return (inner, True)
    return (node, False)


# ---------------------------------------------------------------------------
# Row-level classification
# ---------------------------------------------------------------------------


def _classify_row_level(
    node: exp.Expression, namespace: Namespace
) -> RowLevelPredicate:
    columns: set[Identifier] = set()
    datasets: set[Identifier] = set()
    for col in node.find_all(exp.Column):
        try:
            name = normalize_identifier(col.name)
        except Exception as exc:
            raise OSIPlanningError(
                ErrorCode.E1005_IDENTIFIER_INVALID,
                f"invalid identifier {col.name!r} in filter",
                context={"name": col.name},
            ) from exc
        columns.add(name)
        if col.table:
            try:
                datasets.add(normalize_identifier(col.table))
            except Exception as exc:
                raise OSIPlanningError(
                    ErrorCode.E1005_IDENTIFIER_INVALID,
                    f"invalid dataset {col.table!r} in filter",
                    context={"dataset": col.table},
                ) from exc
        else:
            datasets.add(namespace.resolve_bare(name))
    return RowLevelPredicate(
        expression=FrozenSQL.of(node.copy()),
        datasets=frozenset(datasets),
        columns=frozenset(columns),
    )


# ---------------------------------------------------------------------------
# Boolean helpers
# ---------------------------------------------------------------------------


def _split_conjuncts(node: exp.Expression) -> tuple[exp.Expression, ...]:
    if isinstance(node, exp.And):
        return _split_conjuncts(node.left) + _split_conjuncts(node.right)
    if isinstance(node, exp.Paren):
        return _split_conjuncts(node.this)
    return (node,)


def _bare_column_refs(node: exp.Expression) -> frozenset[Identifier]:
    """Collect the set of names referenced by ``node``.

    *Both* bare and qualified references are returned by their short
    name — a measure used as ``orders.total_revenue`` should still
    show up under ``total_revenue`` so callers can match against
    declared measures uniformly.

    Any column whose name is not a valid OSI identifier raises
    :class:`OSIPlanningError` ``E1005_IDENTIFIER_INVALID``. Silently
    swallowing the error here once let bad inputs sneak through and
    produce confusing downstream failures; surfacing the parse error
    at the place we actually inspected the column is the diagnostic
    contract documented in ``ARCHITECTURE.md §5``.
    """
    out: set[Identifier] = set()
    for col in node.find_all(exp.Column):
        try:
            out.add(normalize_identifier(col.name))
        except OSIParseError as exc:
            raise OSIPlanningError(
                ErrorCode.E1005_IDENTIFIER_INVALID,
                f"invalid identifier in predicate: {col.name!r}",
                context={"name": col.name, "expression": node.sql()},
            ) from exc
    return frozenset(out)


__all__ = [
    "ClassifiedWhere",
    "PostAggregatePredicate",
    "RowLevelPredicate",
    "SemiJoinKeyPair",
    "SemiJoinPredicate",
    "classify_having",
    "classify_where",
]
