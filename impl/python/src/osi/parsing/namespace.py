"""Name resolution over a :class:`SemanticModel`.

The :class:`Namespace` is a read-only index built once by
:func:`build_namespace` and consulted by the planner and diagnostics.
It never mutates and never parses SQL — it only knows what names exist
where.

Scopes per ``Proposed_OSI_Semantics.md §4.7``:

* **Global** — datasets, relationships, model-level metrics, named
  filters, parameters.
* **Dataset** — fields + table-scoped metrics inside a dataset.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Iterable, Mapping

from osi.common.identifiers import Identifier
from osi.errors import ErrorCode, OSIParseError
from osi.parsing.models import (
    Dataset,
    Field,
    Metric,
    NamedFilter,
    Parameter,
    SemanticModel,
)


def _freeze(mapping: dict[Identifier, object]) -> Mapping[Identifier, object]:
    """Return a read-only view over ``mapping``.

    Keeps :class:`Namespace` honest about its
    "constructed once, never mutated" contract. Pydantic frozen
    dataclasses freeze identity but not the contents of nested
    ``dict`` fields, so we wrap them.
    """
    return MappingProxyType(mapping)


@dataclass(frozen=True, slots=True)
class DatasetNamespace:
    """Fields + table-scoped metrics visible inside one dataset."""

    dataset: Identifier
    fields: Mapping[Identifier, Field]
    metrics: Mapping[Identifier, Metric]

    def get_field(self, name: Identifier) -> Field:
        """Look up a field by normalized name. Raises ``E2002``."""
        try:
            return self.fields[name]
        except KeyError as exc:
            raise OSIParseError(
                ErrorCode.E2002_NAME_NOT_FOUND,
                f"field {name!r} not found in dataset {self.dataset!r}",
                context={"dataset": self.dataset, "name": name},
            ) from exc


@dataclass(frozen=True, slots=True)
class Namespace:
    """Read-only global index over a ``SemanticModel``."""

    datasets: Mapping[Identifier, DatasetNamespace]
    metrics: Mapping[Identifier, Metric]
    filters: Mapping[Identifier, NamedFilter]
    parameters: Mapping[Identifier, Parameter]
    relationships: Mapping[Identifier, object]
    _by_short: Mapping[Identifier, tuple[Identifier, ...]] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def get_dataset(self, name: Identifier) -> DatasetNamespace:
        """Look up a dataset namespace. Raises ``E2002``."""
        try:
            return self.datasets[name]
        except KeyError as exc:
            raise OSIParseError(
                ErrorCode.E2002_NAME_NOT_FOUND,
                f"dataset {name!r} not declared",
                context={"name": name},
            ) from exc

    def resolve_qualified(
        self, dataset_name: Identifier, field_name: Identifier
    ) -> Field:
        """Resolve a qualified ``dataset.field`` reference."""
        return self.get_dataset(dataset_name).get_field(field_name)

    def resolve_bare(self, short_name: Identifier) -> Identifier:
        """Resolve a bare field name.

        Returns the *dataset name* that owns the field. Raises
        ``E2001_AMBIGUOUS_NAME`` if multiple datasets share the name,
        ``E2002_NAME_NOT_FOUND`` if none does.
        """
        owners = self._by_short.get(short_name, ())
        if not owners:
            raise OSIParseError(
                ErrorCode.E2002_NAME_NOT_FOUND,
                f"name {short_name!r} does not resolve to any field",
                context={"name": short_name},
            )
        if len(owners) > 1:
            raise OSIParseError(
                ErrorCode.E2001_AMBIGUOUS_NAME,
                (
                    f"bare name {short_name!r} is ambiguous — belongs to "
                    f"{sorted(owners)}"
                ),
                context={"name": short_name, "owners": sorted(owners)},
            )
        return owners[0]


def build_namespace(model: SemanticModel) -> Namespace:
    """Build a :class:`Namespace` from a validated model.

    Duplicates at any scope are already caught by pydantic; this
    function just indexes what's there. Both fields *and* table-scoped
    metrics are added to the bare-name index so resolving a bare
    measure (e.g. ``total_revenue``) works through the same code path
    as resolving a bare field.
    """
    datasets: dict[Identifier, DatasetNamespace] = {}
    by_short: dict[Identifier, list[Identifier]] = {}
    for ds in model.datasets:
        datasets[ds.name] = _build_dataset_namespace(ds)
        for f in ds.fields:
            by_short.setdefault(f.name, []).append(ds.name)
        for m in ds.metrics:
            by_short.setdefault(m.name, []).append(ds.name)
    _assert_global_unique(model.metrics, kind="metric")
    _assert_global_unique(model.filters, kind="filter")
    _assert_global_unique(model.parameters, kind="parameter")
    by_short_frozen: dict[Identifier, tuple[Identifier, ...]] = {
        name: tuple(owners) for name, owners in by_short.items()
    }
    return Namespace(
        datasets=MappingProxyType(datasets),
        metrics=MappingProxyType({m.name: m for m in model.metrics}),
        filters=MappingProxyType({f.name: f for f in model.filters}),
        parameters=MappingProxyType({p.name: p for p in model.parameters}),
        relationships=MappingProxyType({r.name: r for r in model.relationships}),
        _by_short=MappingProxyType(by_short_frozen),
    )


def _build_dataset_namespace(dataset: Dataset) -> DatasetNamespace:
    fields = {f.name: f for f in dataset.fields}
    metrics = {m.name: m for m in dataset.metrics}
    overlap = set(fields) & set(metrics)
    if overlap:
        raise OSIParseError(
            ErrorCode.E2003_DUPLICATE_NAME,
            (
                f"dataset {dataset.name!r}: names {sorted(overlap)} are "
                "used for both a field and a table-scoped metric"
            ),
            context={"dataset": dataset.name, "names": sorted(overlap)},
        )
    return DatasetNamespace(
        dataset=dataset.name,
        fields=MappingProxyType(fields),
        metrics=MappingProxyType(metrics),
    )


def _assert_global_unique(items: Iterable[object], *, kind: str) -> None:
    seen: set[Identifier] = set()
    for item in items:
        name: Identifier = item.name  # type: ignore[attr-defined]
        if name in seen:
            raise OSIParseError(
                ErrorCode.E2003_DUPLICATE_NAME,
                f"{kind} {name!r} declared twice at the model scope",
                context={"kind": kind, "name": name},
            )
        seen.add(name)


__all__ = ["DatasetNamespace", "Namespace", "build_namespace"]
