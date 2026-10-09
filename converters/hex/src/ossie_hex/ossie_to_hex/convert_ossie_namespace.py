# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

"""Conversion of the Ossie namespace (i.e. unique identifiers within a semantic
model) to Hex identifiers requires coordination of concerns.

1. Initialize the namespace by converting identifiers and preparing inner-namespaces.
2. Decide where to place metrics and relationships (analysis + assignment).
3. Finalize the complete namespace by resolving members in their assignments.
4. Proceed with conversion of all members.

This module is responsible for the steps (1) and (3), while the rest is out-of-scope.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from ossie import OssieSemanticModel

from ..hex import HexEntityId
from ..util.problem import KeyPath
from .context import ExportContext
from .convert_ossie_name import convert_ossie_name


def initialize_ossie_namespace(
    ossie_semantic_model: OssieSemanticModel,
    *,
    ctx: ExportContext,
) -> None:
    """Initialize identifier mappings for Ossie semantic model members.

    Dataset IDs are allocated within the project. Member IDs (fields, metrics,
    and relationships) are provisional until placements are known. Call
    `finalize_ossie_namespace` after assignment and before converting members.
    Failed names stay in the source so their remaining properties can still be
    examined by later conversion steps.
    """
    with ctx.problem_scope("datasets"):
        dataset_names: list[str] = [d.name for d in ossie_semantic_model.datasets]
        dataset_ids = _convert_names(dataset_names, ctx=ctx)

    allocation_inputs: list[_Member] = []
    for name, hex_id in dataset_ids.items():
        allocation_inputs.append(
            _Member("datasets", name, hex_id, namespace_keys=(None,))
        )
    allocated = _allocate_ids(allocation_inputs, ctx=ctx)
    _store_ids(allocated, ctx=ctx)
    _report_renames(allocated, ctx=ctx)

    with ctx.problem_scope("datasets"):
        for ossie_dataset in ossie_semantic_model.datasets:
            with ctx.problem_scope(ossie_dataset.name, "fields"):
                field_names: list[str] = []
                for ossie_field in ossie_dataset.fields or []:
                    field_names.append(ossie_field.name)
                field_ids = _convert_names(field_names, ctx=ctx)
                for name, hex_id in field_ids.items():
                    ctx.hex_ids.set_for_field(ossie_dataset.name, name, hex_id)

    with ctx.problem_scope("metrics"):
        metric_names: list[str] = []
        for ossie_metric in ossie_semantic_model.metrics or []:
            metric_names.append(ossie_metric.name)
        metric_ids = _convert_names(metric_names, ctx=ctx)
        for name, hex_id in metric_ids.items():
            ctx.hex_ids.set_for_metric(name, hex_id)

    with ctx.problem_scope("relationships"):
        relationship_names: list[str] = []
        for ossie_relationship in ossie_semantic_model.relationships or []:
            relationship_names.append(ossie_relationship.name)
        relationship_ids = _convert_names(relationship_names, ctx=ctx)
        for name, hex_id in relationship_ids.items():
            ctx.hex_ids.set_for_relationship(name, hex_id)


def finalize_ossie_namespace(
    ossie_semantic_model: OssieSemanticModel,
    *,
    ctx: ExportContext,
) -> None:
    """Allocate member IDs in their assigned Hex models, leaving members intact."""
    allocated = _allocate_ids(
        _member_allocation_inputs(ossie_semantic_model, ctx=ctx), ctx=ctx
    )
    ctx.hex_ids.fields.clear()
    ctx.hex_ids.metrics.clear()
    ctx.hex_ids.relationships.clear()
    _store_ids(allocated, ctx=ctx)
    _report_renames(allocated, ctx=ctx)


@dataclass(frozen=True)
class _Member:
    """Identifying information and a candidate Hex ID for an Ossie model member.

    Source identity determines where to store the final ID and report problems.
    Allocation namespaces determine where that ID must be unique. Frozen
    instances can be dictionary keys and deduplicated during allocation.
    """

    group: Literal["datasets", "fields", "metrics", "relationships"]
    """The category or spec field which contains the member."""

    name: str
    """The original name of the member."""

    base_id: HexEntityId
    """The normalized Hex ID before adding any collision suffix."""

    namespace_keys: tuple[str | None, ...]
    """The allocation namespaces in which the final Hex ID must be unique.

    None identifies the project-wide dataset namespace; original dataset names
    identify member namespaces. Relationships may occupy multiple namespaces.
    An empty tuple indicates a member with no assigned output model.
    """

    dataset_name: str | None = None
    """The original owning dataset name for a field; None for other groups."""

    @property
    def path(self) -> KeyPath:
        """Return the source definition's path for problem reporting."""
        if self.group == "fields":
            assert self.dataset_name is not None
            return ["datasets", self.dataset_name, "fields", self.name]
        return [self.group, self.name]


def _convert_names(
    names: Iterable[str], *, ctx: ExportContext
) -> dict[str, HexEntityId]:
    """Normalize each original name once, reporting duplicates and retaining IDs."""
    result: dict[str, HexEntityId] = {}
    for name, count in Counter(names).items():
        with ctx.problem_scope(name):
            if count > 1:
                ctx.error(
                    f"Duplicate Ossie name: '{name}' ({count} definitions). "
                    "References cannot distinguish them; conversion will continue.",
                    code="duplicate-name",
                )
            if hex_id := convert_ossie_name(name, ctx=ctx):
                result[name] = hex_id
    return result


def _allocate_ids(
    allocation_inputs: list[_Member], *, ctx: ExportContext
) -> dict[_Member, HexEntityId]:
    """Assign IDs that are free in every namespace occupied by each definition.

    Reserve all input IDs before allocating suffixes, and prefer names already
    valid as Hex IDs. Track reserved and assigned IDs by allocation namespace,
    separately from source paths used for problem reporting. Suffix candidates
    avoid the union of occupied IDs across all placements of a definition.
    """
    reserved_ids_by_namespace: dict[str | None, set[HexEntityId]] = defaultdict(set)
    used_ids_by_namespace: dict[str | None, set[HexEntityId]] = defaultdict(set)
    for allocation_input in allocation_inputs:
        for namespace_key in allocation_input.namespace_keys:
            reserved_ids_by_namespace[namespace_key].add(allocation_input.base_id)
    result: dict[_Member, HexEntityId] = {}
    # Preserve valid source IDs first; ties are stable across input reorderings.
    # Exact duplicate names share one mapping; their error was already reported.
    for allocation_input in sorted(
        set(allocation_inputs),
        key=lambda n: (
            n.name != n.base_id,
            n.name,
            n.group,
            n.namespace_keys,
        ),
    ):
        with ctx.problem_scope(*allocation_input.path):
            hex_id = allocation_input.base_id
            collision = False
            for namespace_key in allocation_input.namespace_keys:
                if hex_id in used_ids_by_namespace[namespace_key]:
                    collision = True
                    break
            if collision:
                unavailable: set[str] = set()
                for namespace_key in allocation_input.namespace_keys:
                    unavailable.update(reserved_ids_by_namespace[namespace_key])
                    unavailable.update(used_ids_by_namespace[namespace_key])
                hex_id = None
                # N unavailable IDs can block at most N distinct, valid candidates.
                for number in range(2, len(unavailable) + 3):
                    suffix = f"_{number}"
                    candidate = allocation_input.base_id[: 128 - len(suffix)] + suffix
                    if candidate in unavailable:
                        continue
                    candidate_id = convert_ossie_name(candidate, ctx=ctx)
                    if candidate_id is not None and candidate_id not in unavailable:
                        hex_id = candidate_id
                        break
                if hex_id is None:
                    ctx.error(
                        f"Unable to allocate a unique Hex ID for '{allocation_input.name}' after "
                        f"{len(unavailable) + 1} suffix attempts. The definition was "
                        "omitted; dependent definitions may also be omitted.",
                        code="identifier-allocation-failed",
                    )
                    continue
            result[allocation_input] = hex_id
            for namespace_key in allocation_input.namespace_keys:
                used_ids_by_namespace[namespace_key].add(hex_id)
    return result


def _store_ids(ids: dict[_Member, HexEntityId], *, ctx: ExportContext) -> None:
    """Store allocated IDs under the original names used to resolve references."""
    for allocation_input, hex_id in ids.items():
        if allocation_input.group == "datasets":
            ctx.hex_ids.set_for_dataset(allocation_input.name, hex_id)
        elif allocation_input.group == "fields":
            assert allocation_input.dataset_name is not None
            ctx.hex_ids.set_for_field(
                allocation_input.dataset_name, allocation_input.name, hex_id
            )
        elif allocation_input.group == "metrics":
            ctx.hex_ids.set_for_metric(allocation_input.name, hex_id)
        else:
            ctx.hex_ids.set_for_relationship(allocation_input.name, hex_id)


def _report_renames(ids: dict[_Member, HexEntityId], *, ctx: ExportContext) -> None:
    """Report collision suffixes at the corresponding Ossie definition paths."""
    for allocation_input, hex_id in ids.items():
        with ctx.problem_scope(*allocation_input.path):
            if hex_id != allocation_input.base_id:
                ctx.warn(
                    f"Identifier collision: '{allocation_input.name}' normalizes to occupied Hex ID "
                    f"'{allocation_input.base_id}'. Using '{hex_id}' instead; the definition was preserved.",
                    code="identifier-collision",
                )


def _member_allocation_inputs(
    model: OssieSemanticModel, *, ctx: ExportContext
) -> list[_Member]:
    """Collect normalized members with the namespaces chosen by assignment.

    Fields occupy their owning dataset's namespace. Metrics occupy their source
    model's namespace, while relationships occupy every planned source model's
    namespace. Source paths remain based on the original Ossie definitions.
    """
    result: list[_Member] = []
    for dataset in model.datasets:
        for field in dataset.fields or []:
            hex_id = ctx.hex_ids.for_field(dataset.name, field.name)
            if hex_id is None:
                continue
            result.append(
                _Member(
                    "fields",
                    field.name,
                    hex_id,
                    namespace_keys=(dataset.name,),
                    dataset_name=dataset.name,
                )
            )
    for metric in model.metrics or []:
        if (hex_id := ctx.hex_ids.for_metric(metric.name)) is None:
            continue
        assignment = ctx.assignment.for_metric(metric.name)
        namespace_keys = (assignment.source,) if assignment is not None else ()
        result.append(
            _Member("metrics", metric.name, hex_id, namespace_keys=namespace_keys)
        )
    for relationship in model.relationships or []:
        if (hex_id := ctx.hex_ids.for_relationship(relationship.name)) is None:
            continue
        sources: set[str] = set()
        for placement in ctx.assignment.for_relationship(relationship.name):
            sources.add(placement.source)
        namespace_keys = tuple(sorted(sources))
        result.append(
            _Member(
                "relationships",
                relationship.name,
                hex_id,
                namespace_keys=namespace_keys,
            )
        )
    return result
