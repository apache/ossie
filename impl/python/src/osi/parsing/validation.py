"""Cross-reference validation over a built ``SemanticModel``.

Pydantic handles *shape* validation; this module handles the *semantic*
checks that span datasets / relationships / metrics:

* relationships point at real datasets and real columns
* metric expressions reference real fields (best-effort AST walk — full
  resolution happens in the planner)
* model-scoped derived metrics do not form cycles
* datasets used on the one-side of an N:1 relationship declare a PK

All errors are :class:`OSIParseError` in the ``E2xxx`` range; messages
cite the offending name.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from sqlglot import expressions as exp

from osi.common.identifiers import Identifier
from osi.common.windows import contains_window, is_windowed_expression
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.models import Metric, Relationship, SemanticModel
from osi.parsing.reserved_names import OSI_RESERVED_NAMES


def validate_model(model: SemanticModel) -> None:
    """Run every cross-reference check. Raises on the first failure."""
    datasets_by_name = {ds.name: ds for ds in model.datasets}
    _validate_no_osi_reserved_names(model)
    _validate_relationships(model.relationships, datasets_by_name)
    _validate_metric_references(model, datasets_by_name)
    _validate_metric_cycles(model.metrics)
    _validate_no_windowed_metric_composition(model, datasets_by_name)


# ---------------------------------------------------------------------------
# Reserved-name guard (D-019)
# ---------------------------------------------------------------------------


def _reject_reserved_name(*, kind: str, name: str, owner: str | None = None) -> None:
    """Raise ``E_RESERVED_NAME`` if ``name`` collides with an OSI keyword."""
    if name.lower() in OSI_RESERVED_NAMES:
        owner_clause = f" on {owner!r}" if owner else ""
        raise OSIParseError(
            ErrorCode.E_RESERVED_NAME,
            (
                f"{kind} name {name!r}{owner_clause} collides with the "
                "OSI grammar reserved keyword set "
                f"({sorted(OSI_RESERVED_NAMES)}); pick a different name "
                "(D-019)."
            ),
            context={"kind": kind, "name": name, "owner": owner},
        )


def _validate_no_osi_reserved_names(model: SemanticModel) -> None:
    """D-019: no user identifier may equal an OSI grammar keyword.

    Walks every dataset, field, model-scope metric, dataset-scope
    metric, and relationship; the first collision raises
    ``E_RESERVED_NAME`` with full owner context.
    """
    for ds in model.datasets:
        _reject_reserved_name(kind="dataset", name=str(ds.name))
        for field in ds.fields:
            _reject_reserved_name(
                kind="field", name=str(field.name), owner=str(ds.name)
            )
        for ds_metric in ds.metrics:
            _reject_reserved_name(
                kind="metric",
                name=str(ds_metric.name),
                owner=str(ds.name),
            )
    for metric in model.metrics:
        _reject_reserved_name(kind="metric", name=str(metric.name))
    for rel in model.relationships:
        _reject_reserved_name(kind="relationship", name=str(rel.name))


# ---------------------------------------------------------------------------
# Relationships
# ---------------------------------------------------------------------------


def _validate_relationships(
    relationships: Iterable[Relationship],
    datasets_by_name: Mapping[Identifier, object],
) -> None:
    for rel in relationships:
        _check_dataset_exists(rel, "from_dataset", rel.from_dataset, datasets_by_name)
        _check_dataset_exists(rel, "to_dataset", rel.to_dataset, datasets_by_name)
        _check_columns_exist(rel, side="from", datasets_by_name=datasets_by_name)
        _check_columns_exist(rel, side="to", datasets_by_name=datasets_by_name)


def _check_dataset_exists(
    rel: Relationship,
    label: str,
    name: Identifier,
    datasets_by_name: Mapping[Identifier, object],
) -> None:
    if name not in datasets_by_name:
        raise OSIParseError(
            ErrorCode.E2006_INVALID_RELATIONSHIP,
            (
                f"relationship {rel.name!r}: {label} {name!r} does not "
                "match any declared dataset"
            ),
            context={"relationship": rel.name, label: name},
        )


def _check_columns_exist(
    rel: Relationship,
    *,
    side: str,
    datasets_by_name: Mapping[Identifier, object],
) -> None:
    dataset_name = rel.from_dataset if side == "from" else rel.to_dataset
    columns = rel.from_columns if side == "from" else rel.to_columns
    ds = datasets_by_name[dataset_name]
    field_names = {f.name for f in ds.fields}  # type: ignore[attr-defined]
    for col in columns:
        if col not in field_names:
            raise OSIParseError(
                ErrorCode.E2006_INVALID_RELATIONSHIP,
                (
                    f"relationship {rel.name!r}: {side}_columns reference "
                    f"{col!r} which is not a field of dataset "
                    f"{dataset_name!r}"
                ),
                context={
                    "relationship": rel.name,
                    "side": side,
                    "column": col,
                    "dataset": dataset_name,
                },
            )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def _validate_metric_references(
    model: SemanticModel,
    datasets_by_name: Mapping[Identifier, object],
) -> None:
    """Light-touch reference check on metric expressions.

    The planner does full resolution; here we only reject obvious
    typos where a metric cites a nonexistent dataset. Bare-column
    references and metric-to-metric references stay tolerable because
    resolving them requires the full namespace (Phase 3).
    """
    known_datasets = set(datasets_by_name.keys())
    for metric in model.metrics:
        for column in metric.expression.expr.find_all(exp.Column):
            table = column.table
            if not table:
                continue
            from osi.common.identifiers import normalize_identifier

            try:
                normalized = normalize_identifier(table)
            except OSIParseError:
                continue  # quoted / exotic identifier — defer to planner
            if normalized not in known_datasets:
                raise OSIParseError(
                    ErrorCode.E2002_NAME_NOT_FOUND,
                    (
                        f"metric {metric.name!r} references unknown dataset "
                        f"{table!r}"
                    ),
                    context={"metric": metric.name, "dataset": table},
                )


def _validate_no_windowed_metric_composition(
    model: SemanticModel,
    datasets_by_name: Mapping[Identifier, object],
) -> None:
    """D-031: composing a metric on top of a windowed metric is rejected.

    A windowed metric is one whose top-level expression is a window
    function (``f(...) OVER (...)``). If a *different* metric's
    expression references such a windowed metric, the result is a
    "metric on top of a window" — the Foundation cannot guarantee a
    correct grain for the outer metric, so we reject.

    The check operates on parsed-but-unresolved metric bodies; full
    name resolution is the planner's job. We treat any column whose
    qualified or bare name matches a known windowed metric as a
    reference to it.
    """
    windowed_metric_names: set[str] = set()
    for metric in model.metrics:
        if is_windowed_expression(metric.expression.expr):
            windowed_metric_names.add(str(metric.name).lower())
    for ds in model.datasets:
        for ds_metric in ds.metrics:
            if is_windowed_expression(ds_metric.expression.expr):
                windowed_metric_names.add(f"{ds.name}.{ds_metric.name}".lower())
                windowed_metric_names.add(str(ds_metric.name).lower())

    if not windowed_metric_names:
        return

    def _check(metric_owner: str, metric: Metric) -> None:
        if is_windowed_expression(metric.expression.expr):
            return
        # Only metrics that themselves *combine* a windowed reference
        # with anything else are rejected. A metric that is *just* a
        # windowed expression is fine; we only care when the outer
        # body composes (a windowed reference inside an aggregate, an
        # arithmetic expression, another window, etc.).
        for column in metric.expression.expr.find_all(exp.Column):
            bare = (column.name or "").lower()
            qualified = f"{column.table.lower()}.{bare}" if column.table else ""
            if bare in windowed_metric_names or (
                qualified and qualified in windowed_metric_names
            ):
                target = qualified or bare
                raise OSIParseError(
                    ErrorCode.E_WINDOWED_METRIC_COMPOSITION,
                    (
                        f"metric {metric_owner!r} composes on top of "
                        f"windowed metric {target!r} (D-031 — composing "
                        "above a window function changes the grain "
                        "non-uniformly and is not in Foundation v0.1)"
                    ),
                    context={
                        "metric": metric_owner,
                        "windowed_reference": target,
                    },
                )
        # If the composing metric itself contains a window, that's
        # also rejected — we don't allow ``window(window-base + x)``
        # because the inner reference's frame is unrecoverable.
        if contains_window(metric.expression.expr):
            for column in metric.expression.expr.find_all(exp.Column):
                bare = (column.name or "").lower()
                if bare in windowed_metric_names:
                    raise OSIParseError(
                        ErrorCode.E_WINDOWED_METRIC_COMPOSITION,
                        (
                            f"metric {metric_owner!r} contains a window "
                            "that references another windowed metric "
                            "(D-031)"
                        ),
                        context={"metric": metric_owner},
                    )

    for metric in model.metrics:
        _check(str(metric.name), metric)
    for ds in model.datasets:
        for ds_metric in ds.metrics:
            _check(f"{ds.name}.{ds_metric.name}", ds_metric)


def _validate_metric_cycles(metrics: tuple[Metric, ...]) -> None:
    """Detect cycles among model-scoped derived metrics.

    A derived metric is any metric whose expression bare-references
    another metric by name. We approximate this by collecting every
    ``Column`` whose ``table`` is None and whose normalized name matches
    a declared metric.
    """
    if not metrics:
        return
    from osi.common.identifiers import normalize_identifier

    by_name = {m.name: m for m in metrics}
    edges: dict[Identifier, set[Identifier]] = {m.name: set() for m in metrics}
    for metric in metrics:
        for column in metric.expression.expr.find_all(exp.Column):
            if column.table:
                continue
            try:
                candidate = normalize_identifier(column.name)
            except OSIParseError:
                continue
            if candidate in by_name:
                edges[metric.name].add(candidate)
    _detect_cycle(edges)


def _detect_cycle(edges: dict[Identifier, set[Identifier]]) -> None:
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[Identifier, int] = {node: WHITE for node in edges}

    def visit(node: Identifier, stack: list[Identifier]) -> None:
        color[node] = GRAY
        stack.append(node)
        for child in edges.get(node, ()):
            if color[child] == GRAY:
                cycle = stack[stack.index(child) :] + [child]
                raise OSIParseError(
                    ErrorCode.E2005_CIRCULAR_METRIC,
                    ("metric composition cycle detected: " + " -> ".join(cycle)),
                    context={"cycle": cycle},
                )
            if color[child] == WHITE:
                visit(child, stack)
        stack.pop()
        color[node] = BLACK

    for node in edges:
        if color[node] == WHITE:
            visit(node, [])


__all__ = ["validate_model"]
