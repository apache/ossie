"""Reference resolution against a :class:`~osi.parsing.namespace.Namespace`.

The planner receives :class:`~osi.planning.semantic_query.Reference`
values as strings-by-shape (``dataset.field`` or bare). This module
converts them into concrete :class:`ResolvedDimension`,
:class:`ResolvedFact`, or :class:`ResolvedMetric` records. Every
resolution failure raises :class:`~osi.errors.OSIPlanningError` (or
:class:`~osi.errors.OSIParseError` for SQL-surface shape violations)
with an :class:`~osi.errors.ErrorCode` drawn from the **parsing /
planning families**. The visible codes from this module today are:

* ``E2001_AMBIGUOUS_NAME`` — bare name resolves to multiple datasets.
* ``E2002_NAME_NOT_FOUND`` — bare or qualified name not in namespace.
* ``E1206_METRIC_IN_RAW_AGGREGATE`` — caller asked for a measure but
  the name resolves to a raw field aggregate.
* ``E1207_FACTS_METRICS_EXCLUSIVE`` — caller asked for a dimension
  but the name resolves to a fact or metric.
* ``E_INTERNAL_INVARIANT`` — qualified reference with no dataset
  (caller-side invariant violation).

The planner never catches these; they bubble to the top-level caller.

Scope (``Proposed_OSI_Semantics.md §4.7``):

1. Qualified ``dataset.field`` always resolves in that dataset's
   namespace.
2. Bare ``name`` searches:

   a. Global metrics (``model.metrics``).
   b. Named filters (resolved by callers — not here).
   c. Dataset-scoped fields, but only if the name is unambiguous across
      all datasets. Otherwise ``E2001_AMBIGUOUS_NAME``.

3. Table-scoped metrics (``dataset.metric``) are resolvable through
   qualified form.

The planner keeps everything it resolved on the returned record so
downstream stages (classify / joins / planner.plan) don't re-query the
namespace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from osi.common.identifiers import Identifier
from osi.errors import ErrorCode, OSIParseError, OSIPlanningError
from osi.parsing.models import Field, FieldRole, Metric
from osi.parsing.namespace import Namespace
from osi.planning.semantic_query import Reference


@dataclass(frozen=True, slots=True)
class ResolvedDimension:
    """A :class:`Reference` that names a dataset dimension/time-dim field."""

    dataset: Identifier
    field: Field


@dataclass(frozen=True, slots=True)
class ResolvedFact:
    """A :class:`Reference` that names a dataset fact field."""

    dataset: Identifier
    field: Field


@dataclass(frozen=True, slots=True)
class ResolvedMetric:
    """A :class:`Reference` that names a metric (table- or model-scoped)."""

    dataset: Identifier | None
    metric: Metric


ResolvedField = Union[ResolvedDimension, ResolvedFact]
ResolvedReference = Union[ResolvedField, ResolvedMetric]


def resolve_reference(ref: Reference, namespace: Namespace) -> ResolvedReference:
    """Resolve a :class:`Reference`. Raises ``E2001`` / ``E2002``."""
    if ref.is_qualified:
        return _resolve_qualified(
            dataset_name=_require_identifier(ref.dataset),
            name=ref.name,
            namespace=namespace,
        )
    return _resolve_bare(ref.name, namespace)


def resolve_dimension(ref: Reference, namespace: Namespace) -> ResolvedDimension:
    """Resolve a reference and assert it is a dimension.

    Raises :class:`OSIPlanningError` with ``E2002`` if the name does not
    exist and ``E1207_FACTS_METRICS_EXCLUSIVE`` if it resolves to a fact
    or metric.
    """
    resolved = resolve_reference(ref, namespace)
    if isinstance(resolved, ResolvedDimension):
        return resolved
    raise OSIPlanningError(
        ErrorCode.E1207_FACTS_METRICS_EXCLUSIVE,
        f"{ref} is not a dimension",
        context={"reference": str(ref)},
    )


def resolve_measure(ref: Reference, namespace: Namespace) -> ResolvedMetric:
    """Resolve a reference and assert it is a metric.

    The Foundation requires every measure to be a declared metric — raw
    aggregate SQL in the ``measures`` slot is not allowed (``E1206``).
    """
    resolved = resolve_reference(ref, namespace)
    if isinstance(resolved, ResolvedMetric):
        return resolved
    raise OSIPlanningError(
        ErrorCode.E1206_METRIC_IN_RAW_AGGREGATE,
        f"{ref} is not a declared metric",
        context={"reference": str(ref)},
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _resolve_qualified(
    *, dataset_name: Identifier, name: Identifier, namespace: Namespace
) -> ResolvedReference:
    try:
        ds = namespace.get_dataset(dataset_name)
    except OSIParseError as exc:
        raise OSIPlanningError(exc.code, str(exc), context=dict(exc.context)) from exc
    if name in ds.fields:
        return _dimension_or_fact(dataset=dataset_name, field=ds.fields[name])
    if name in ds.metrics:
        return ResolvedMetric(dataset=dataset_name, metric=ds.metrics[name])
    # S-22: Allow ``<dataset>.<model_metric>`` qualification for model-
    # level metrics. Windowed metrics in particular are scoped to a
    # single home dataset (every column reference resolves to one
    # dataset), and BI tools commonly write them as
    # ``orders.running_total``. The qualifier is allowed iff the
    # metric exists at the model level; the *home* of the metric is
    # validated downstream when the planner classifies the measure.
    if name in namespace.metrics:
        return ResolvedMetric(dataset=dataset_name, metric=namespace.metrics[name])
    raise OSIPlanningError(
        ErrorCode.E2002_NAME_NOT_FOUND,
        f"{dataset_name}.{name} does not name a field or metric",
        context={"dataset": dataset_name, "name": name},
    )


def _resolve_bare(name: Identifier, namespace: Namespace) -> ResolvedReference:
    if name in namespace.metrics:
        return ResolvedMetric(dataset=None, metric=namespace.metrics[name])
    try:
        owner = namespace.resolve_bare(name)
    except OSIParseError as exc:
        # The namespace raises OSIParseError with the specific E2001 /
        # E2002 code; re-raise as OSIPlanningError preserving the code.
        raise OSIPlanningError(
            exc.code,
            str(exc),
            context=dict(exc.context),
        ) from exc
    ds_ns = namespace.get_dataset(owner)
    # Dataset-scoped bare names may be either fields *or* table-scoped
    # metrics — both are indexed by the namespace's bare-name index.
    # Prefer fields (they're the common case); fall through to metrics
    # so a bare metric name such as ``total_revenue`` resolves to the
    # dataset's declared metric rather than raising a KeyError.
    if name in ds_ns.fields:
        return _dimension_or_fact(dataset=owner, field=ds_ns.fields[name])
    if name in ds_ns.metrics:
        return ResolvedMetric(dataset=owner, metric=ds_ns.metrics[name])
    raise OSIPlanningError(  # pragma: no cover — namespace.resolve_bare guards
        ErrorCode.E2002_NAME_NOT_FOUND,
        f"bare name {name!r} resolved to dataset {owner!r} but is neither a "
        "field nor a table-scoped metric there",
        context={"name": name, "dataset": owner},
    )


def _dimension_or_fact(*, dataset: Identifier, field: Field) -> ResolvedField:
    if field.role is FieldRole.FACT:
        return ResolvedFact(dataset=dataset, field=field)
    return ResolvedDimension(dataset=dataset, field=field)


def _require_identifier(value: Identifier | None) -> Identifier:
    """Force-unwrap an optional ``dataset`` for a qualified reference.

    ``Reference.is_qualified`` returns ``True`` only when ``dataset``
    is populated, so this should be unreachable. We still raise a
    typed :class:`OSIPlanningError` with
    :attr:`ErrorCode.E_INTERNAL_INVARIANT` rather than a bare
    ``AssertionError`` so the "every failure carries a code"
    invariant of :class:`OSIError` is preserved.
    """
    if value is None:
        raise OSIPlanningError(
            ErrorCode.E_INTERNAL_INVARIANT,
            (
                "qualified reference reached resolve._require_identifier "
                "with dataset=None; Reference.is_qualified should have "
                "guaranteed a non-None dataset"
            ),
            context={"caller": "osi.planning.resolve._require_identifier"},
        )
    return value


__all__ = [
    "ResolvedDimension",
    "ResolvedFact",
    "ResolvedField",
    "ResolvedMetric",
    "ResolvedReference",
    "resolve_dimension",
    "resolve_measure",
    "resolve_reference",
]
