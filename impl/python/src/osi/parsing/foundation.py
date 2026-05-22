"""Foundation-strictness checks gated by :class:`FoundationFlags`.

`Proposed_OSI_Semantics.md` §10 / D-003 / D-027 enumerate constructs the
Foundation explicitly defers. Each is recognised by pydantic (so the
spec-side YAML shape stays familiar) but rejected by this module unless
the caller opts in via :class:`~osi.config.FoundationFlags`.

Three flag-gated checks live here, mirroring the three flags:

* **D-003 — aggregates in fields**: a field's ``expression`` must not
  contain any aggregate function. Window functions remain allowed
  (they are not aggregates in the spec sense; the parser already
  routes them through :func:`osi.parsing.deferred._check_window_rules`).
  Violation ⇒ ``E_AGGREGATE_IN_FIELD``.

* **§4.5 — per-dataset metric blocks**: a dataset's ``metrics:`` block
  is deferred — every metric must live in the top-level ``metrics:``
  section. Violation ⇒ ``E_DEFERRED_KEY_REJECTED`` with the deferred
  field reported as ``"metrics"``.

* **D-027 — nested aggregation in metrics**: a metric expression must
  not nest an aggregate inside another aggregate. The Foundation's
  single-step interpretation gives identical numbers for distributive
  aggregates; non-distributive nested aggregates wait for §10's
  grain-aware-functions proposal. Violation ⇒
  ``E_NESTED_AGGREGATION_DEFERRED``.

The order of checks matters — the dataset-scoped-metric check fires
first so a model that uses both a dataset-scoped metric and an
aggregate-bodied field gets the more familiar deferred-key surface
before the aggregate-in-field rejection.

A fourth, unconditional check enforces that each dataset's
inter-field dependency graph is a DAG (``E_FIELD_DEPENDENCY_CYCLE``).
This is structural — not opt-in — because a cycle cannot be lowered
to the planner's staged-CTE shape on any dialect; see
:func:`osi.planning.steps.source_step`.
"""

from __future__ import annotations

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier
from osi.common.sql_expr import FrozenSQL
from osi.config import FoundationFlags
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.field_deps import field_inter_field_dependencies
from osi.parsing.models import Dataset, Field, Metric, SemanticModel


def check_foundation_strictness(model: SemanticModel, flags: FoundationFlags) -> None:
    """Reject deferred constructs not enabled in ``flags``.

    Runs after pydantic + cross-reference validation; receives the
    fully-built :class:`SemanticModel` so it can inspect already-parsed
    expression ASTs.
    """
    _check_dataset_scoped_metrics(model, flags)
    _check_aggregate_in_fields(model, flags)
    _check_nested_aggregation_in_metrics(model, flags)
    _check_field_dependency_cycles(model)


# ---------------------------------------------------------------------------
# Per-dataset metrics block (§4.5 deferral)
# ---------------------------------------------------------------------------


def _check_dataset_scoped_metrics(model: SemanticModel, flags: FoundationFlags) -> None:
    if flags.allow_dataset_scoped_metrics:
        return
    for dataset in model.datasets:
        if not dataset.metrics:
            continue
        offender = dataset.metrics[0]
        raise OSIParseError(
            ErrorCode.E_DEFERRED_KEY_REJECTED,
            (
                f"dataset {dataset.name!r}: per-dataset 'metrics:' "
                "blocks are deferred from Foundation v0.1 "
                "(Proposed_OSI_Semantics.md §4.5). Move metric "
                f"{offender.name!r} to the top-level 'metrics:' section "
                "and qualify the body with the dataset name (e.g. "
                f"'{dataset.name}.{offender.name} = SUM(amount)' becomes "
                f"top-level '{offender.name} = "
                f"SUM({dataset.name}.amount)'). To opt back into the "
                "legacy behaviour set "
                "'FoundationFlags(allow_dataset_scoped_metrics=True)'."
            ),
            context={
                "location": f"dataset {dataset.name!r}",
                "field": "metrics",
                "first_metric": str(offender.name),
                "flag": "allow_dataset_scoped_metrics",
            },
        )


# ---------------------------------------------------------------------------
# Aggregate-bodied fields (D-003)
# ---------------------------------------------------------------------------


def _check_aggregate_in_fields(model: SemanticModel, flags: FoundationFlags) -> None:
    if flags.allow_aggregate_in_field:
        return
    for dataset in model.datasets:
        for field in dataset.fields:
            _reject_field_aggregate(field=field, dataset_name=str(dataset.name))


def _reject_field_aggregate(*, field: Field, dataset_name: str) -> None:
    agg = _first_aggregate(field.expression)
    if agg is None:
        return
    function_name = type(agg).__name__.upper()
    raise OSIParseError(
        ErrorCode.E_AGGREGATE_IN_FIELD,
        (
            f"field {dataset_name}.{field.name!r}: aggregate function "
            f"{function_name!r} appears in a field expression. "
            "Foundation v0.1 §4.3 / D-003 requires every aggregate to "
            "live in a model-scoped metric (top-level 'metrics:' "
            "section, referenced by bare name). Window functions remain "
            "allowed in field expressions; only aggregate functions "
            "are rejected. To opt back into the legacy implicit "
            "home-grain rewrite set "
            "'FoundationFlags(allow_aggregate_in_field=True)'."
        ),
        context={
            "dataset": dataset_name,
            "field": str(field.name),
            "aggregate": function_name,
            "flag": "allow_aggregate_in_field",
        },
    )


def _first_aggregate(expression: FrozenSQL) -> exp.AggFunc | None:
    """Return the first :class:`exp.AggFunc` node in ``expression``.

    Only true aggregate functions count. Window expressions (an
    aggregate wrapped in ``OVER (...)``) are not flagged here — sqlglot
    builds windowed aggregates as an :class:`exp.Window` whose
    ``this`` happens to be an :class:`exp.AggFunc`, but the spec
    classifies windowed aggregates as window functions, which §4.3.1
    explicitly permits in field expressions.
    """
    for node in expression.expr.walk():
        ast = _unwrap_walk(node)
        if not isinstance(ast, exp.AggFunc):
            continue
        if _is_window_argument(ast):
            continue
        return ast
    return None


def _is_window_argument(agg: exp.AggFunc) -> bool:
    """Return True iff ``agg`` is the aggregate inside a ``Window`` node.

    A windowed aggregate (``SUM(amount) OVER (...)``) is an
    :class:`exp.Window` whose ``this`` is the underlying
    :class:`exp.AggFunc`. We don't want to reject those — §4.3.1
    explicitly permits window functions in field expressions.
    """
    parent = agg.parent
    return isinstance(parent, exp.Window) and parent.this is agg


# ---------------------------------------------------------------------------
# Nested aggregation in metrics (D-027)
# ---------------------------------------------------------------------------


def _check_nested_aggregation_in_metrics(
    model: SemanticModel, flags: FoundationFlags
) -> None:
    if flags.allow_nested_aggregation:
        return
    for metric in model.metrics:
        _reject_nested(metric=metric, scope="model")
    for dataset in model.datasets:
        for metric in dataset.metrics:
            _reject_nested(metric=metric, scope=str(dataset.name))


def _reject_nested(*, metric: Metric, scope: str) -> None:
    nested = _first_nested_aggregate(metric.expression)
    if nested is None:
        return
    outer, inner = nested
    raise OSIParseError(
        ErrorCode.E_NESTED_AGGREGATION_DEFERRED,
        (
            f"metric {scope}.{metric.name!r}: nested aggregation "
            f"({type(outer).__name__.upper()} of "
            f"{type(inner).__name__.upper()}) is deferred from "
            "Foundation v0.1 (Proposed_OSI_Semantics.md §4.5 / D-027). "
            "For distributive aggregates the single-step form gives "
            "identical numbers — write 'SUM(orders.amount)' instead "
            "of 'SUM(SUM(orders.amount))'. The non-distributive "
            "per-home-row interpretation waits for §10's grain-aware "
            "functions. To opt back into the legacy two-step planner "
            "set 'FoundationFlags(allow_nested_aggregation=True)'."
        ),
        context={
            "metric": str(metric.name),
            "scope": scope,
            "outer": type(outer).__name__.upper(),
            "inner": type(inner).__name__.upper(),
            "flag": "allow_nested_aggregation",
        },
    )


def _first_nested_aggregate(
    expression: FrozenSQL,
) -> tuple[exp.AggFunc, exp.AggFunc] | None:
    """Return the first ``(outer, inner)`` aggregate-of-aggregate pair.

    A windowed aggregate (``SUM(amount) OVER (...)``) is not counted
    as the outer; window-function bodies that themselves contain
    aggregates fall under the window-rules screen in
    :mod:`osi.parsing.deferred`, not the nested-aggregation rule.
    """
    for node in expression.expr.walk():
        outer = _unwrap_walk(node)
        if not isinstance(outer, exp.AggFunc):
            continue
        if _is_window_argument(outer):
            continue
        for child in outer.this.walk() if outer.this is not None else ():
            inner = _unwrap_walk(child)
            if inner is outer:
                continue
            if isinstance(inner, exp.AggFunc) and not _is_window_argument(inner):
                return outer, inner
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _unwrap_walk(item: object) -> exp.Expression:
    """Normalize SQLGlot ``walk()`` items across versions.

    Newer releases yield bare expressions; older releases yield
    ``(node, parent, key)`` tuples. We collapse both shapes so the
    callers above can be written uniformly.
    """
    if isinstance(item, exp.Expression):
        return item
    if isinstance(item, tuple) and item and isinstance(item[0], exp.Expression):
        return item[0]
    return exp.Expression()


# ---------------------------------------------------------------------------
# Field dependency cycles (structural — not flag-gated)
# ---------------------------------------------------------------------------


def _check_field_dependency_cycles(model: SemanticModel) -> None:
    """Reject any dataset whose inter-field dependency graph has a cycle.

    A field's expression may reference other fields on the same
    dataset by bare name (qualified references resolve through the
    relationship graph and are handled elsewhere). The planner lowers
    those references into a topologically ordered chain of
    ``ADD_COLUMNS`` CTE stages so the emitted SQL is portable; a
    cycle cannot be lowered and would force the planner to rely on
    lateral aliasing within a single ``SELECT`` (which Snowflake,
    PostgreSQL, and SQLite reject).

    Self-references (``expression = name`` on a field whose name
    matches its own identifier) are *not* cycles: they are the
    canonical identity-projection shape and resolve to the physical
    column at the SOURCE step.
    """
    for dataset in model.datasets:
        cycle = _find_field_cycle(dataset)
        if cycle is None:
            continue
        cycle_repr = " → ".join(str(name) for name in cycle)
        offender = cycle[0]
        raise OSIParseError(
            ErrorCode.E_FIELD_DEPENDENCY_CYCLE,
            (
                f"dataset {dataset.name!r}: inter-field dependency "
                f"cycle {cycle_repr!r}. Foundation v0.1 §4.3 requires "
                "each dataset's field dependency graph to be a DAG so "
                "the planner can lower derived fields into a sequence "
                "of ADD_COLUMNS stages compiled as portable SQL. "
                "Break the cycle by promoting the shared "
                "sub-expression to a single field that the others "
                "depend on, or by inlining one of the bodies."
            ),
            context={
                "dataset": str(dataset.name),
                "field": str(offender),
                "cycle": [str(name) for name in cycle],
            },
        )


def _find_field_cycle(dataset: Dataset) -> tuple[Identifier, ...] | None:
    """DFS the field dependency graph and return the first cycle found.

    Returns ``None`` when the graph is acyclic. The returned tuple
    starts and ends at the same identifier so callers can render it
    directly (``a → b → a``).
    """
    field_names = {field.name for field in dataset.fields}
    deps_by_field: dict[Identifier, frozenset[Identifier]] = {
        field.name: field_inter_field_dependencies(field, field_names)
        for field in dataset.fields
    }
    state: dict[Identifier, int] = {name: 0 for name in deps_by_field}
    stack: list[Identifier] = []
    for start in deps_by_field:
        if state[start] != 0:
            continue
        cycle = _dfs_cycle(start, deps_by_field, state, stack)
        if cycle is not None:
            return cycle
    return None


def _dfs_cycle(
    node: Identifier,
    deps: dict[Identifier, frozenset[Identifier]],
    state: dict[Identifier, int],
    stack: list[Identifier],
) -> tuple[Identifier, ...] | None:
    """Run an iterative DFS and return the first back-edge cycle, if any.

    ``state`` carries the standard three-color marking
    (0 = white / unvisited, 1 = gray / on the stack, 2 = black /
    finished). When we follow an edge into a gray node we extract the
    cycle by slicing ``stack`` from that node to the current end.
    """
    state[node] = 1
    stack.append(node)
    for child in sorted(deps[node]):
        if state[child] == 0:
            cycle = _dfs_cycle(child, deps, state, stack)
            if cycle is not None:
                return cycle
        elif state[child] == 1:
            start_index = stack.index(child)
            return tuple(stack[start_index:]) + (child,)
    state[node] = 2
    stack.pop()
    return None


__all__ = ["check_foundation_strictness"]
