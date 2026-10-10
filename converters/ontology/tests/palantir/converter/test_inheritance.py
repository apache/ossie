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

"""When a primary-key-to-primary-key relation is read as a subtype edge."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.vendor.palantir.model import Status
from ossie_ontology.model import OssieOntology

from tests.palantir.converter.builders import (
    dataset,
    many_to_one,
    object_type,
    prop,
)

from tests.palantir.converter.helpers import (
    _convert,
    _relationship_names,
    _identifier_columns,
    _linked_export,
    _supertype_names,
)


def _subtype_export(
    *, ot_status: str, relation_status: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Gadget as a subtype of Widget, at the given statuses."""
    object_types = [
        object_type("widget", "Widget", status=ot_status),
        object_type("gadget", "Gadget", status=ot_status),
    ]
    relations = [
        many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status=relation_status,
        )
    ]
    return object_types, relations

def _supertype_names(model: OssieOntology, concept_name: str) -> list[str]:
    concept = model.ontology.lookup_concept(concept_name)
    assert concept is not None, f"concept '{concept_name}' was not converted"
    return [parent.name for parent in concept.extends]

def test_primary_key_to_primary_key_relation_becomes_inheritance(tmp_path: Path):
    object_types, relations = _subtype_export(ot_status="active", relation_status="active")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == ["Widget"]
    # And only as inheritance: the relation is consumed, not also emitted as a
    # reference from the supertype to the subtype.
    assert "Widget.gadget_widget" not in _relationship_names(model)

def test_cycle_broken_subtype_relation_stays_an_ordinary_relationship(tmp_path: Path):
    """The other half of the same rule: only inheritance that was actually
    applied suppresses the relation.

    Two object types each declared a subtype of the other cannot both extend
    the other, so the topological sort drops one edge. That relation is not
    carried by any `extends`, so it has to come through as a reference — the
    skip in `_convert_relationships` is keyed on what was consumed, not on
    everything `_subtype_relations()` matched.
    """
    object_types = [
        object_type("widget", "Widget", status="active"),
        object_type("gadget", "Gadget", status="active"),
    ]
    relations = [
        many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status="active",
        ),
        many_to_one(
            "widget_gadget",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.gadget_id"},
            status="active",
        ),
    ]
    with pytest.warns(UserWarning, match="Cycle detected"):
        model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == ["Widget"]
    assert _supertype_names(model, "Widget") == []
    names = _relationship_names(model)
    assert "Widget.gadget_widget" not in names  # consumed as the extends above
    assert "Gadget.widget_gadget" in names  # the dropped edge, as a reference

def test_relation_that_does_not_map_primary_keys_is_not_inheritance(tmp_path: Path):
    """The structural half of the rule: a plain foreign key stays a reference."""
    object_types, relations = _linked_export(ot_status="active", relation_status="active")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == []
    assert "Gadget.gadget_widget" in _relationship_names(model)

def test_partially_mapped_composite_key_is_not_inheritance(tmp_path: Path):
    """Inheritance takes the parent's key whole, not in part.

    Widget is identified by (widget_id, wb) and the relation maps only
    widget_id, so a Gadget cannot be identified as a Widget — that is an
    ordinary foreign key. Reading it as inheritance instead reached
    `_convert_mappings`, which resolves every parent primary key through the
    property map, and died there on the unmapped `wb`.
    """
    object_types = [
        object_type(
            "widget",
            "Widget",
            status="active",
            properties=[prop("ri.p.wb", "wb")],
            primary_keys=["widget_id", "wb"],
        ),
        object_type("gadget", "Gadget", status="active"),
    ]
    relations = [
        many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status="active",
        )
    ]
    datasets = [dataset("widget", ["widget_id", "wb"]), dataset("gadget", ["gadget_id"])]

    model = _convert(tmp_path, object_types, relations=relations, datasets=datasets)

    assert _supertype_names(model, "Gadget") == []
    assert "Widget.gadget_widget" in _relationship_names(model)
    # Gadget keeps its own identity, and its mapping is built rather than crashing.
    assert _identifier_columns(model, "Gadget") == ["gadget_id"]

def test_supertype_excluded_by_the_object_type_policy_is_reported(tmp_path: Path):
    """The two status policies are independent, so they can disagree.

    A DEPRECATED parent joined to an ACTIVE child by an ACTIVE PK-to-PK relation
    passes the subtype-relation gate and fails the object-type gate under the
    defaults, so there is no parent concept to extend. That has to say which
    policies disagree — as an error, not an assert, so `-O` fails here too
    instead of building `Concept(extends=[None])` and dying later on a `None`.
    """
    object_types = [
        object_type("widget", "Widget", status="deprecated"),
        object_type("gadget", "Gadget", status="active"),
    ]
    relations = [
        many_to_one(
            "gadget_widget",
            one="gadget",
            many="widget",
            key_map={"ri.p.gadget_id": "ri.p.widget_id"},
            status="active",
        )
    ]

    with pytest.raises(ValueError, match="subtype of 'Widget'"):
        _convert(tmp_path, object_types, relations=relations)

def test_deprecated_subtype_relation_is_not_read_as_inheritance(tmp_path: Path):
    object_types, relations = _subtype_export(ot_status="active", relation_status="deprecated")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == []

def test_experimental_subtype_relation_between_active_object_types_is_read(tmp_path: Path):
    """Same endpoint rule as any other experimental relation."""
    object_types, relations = _subtype_export(ot_status="active", relation_status="experimental")
    model = _convert(tmp_path, object_types, relations=relations)

    assert _supertype_names(model, "Gadget") == ["Widget"]

def test_subtype_statuses_follow_the_converter_policy(tmp_path: Path):
    """Inheritance keeps step with what the converter admits as a concept.

    Widening object types alone leaves an all-experimental hierarchy flat, for
    the same reason it leaves experimental relations behind: the endpoints are
    checked separately.
    """

    class WidenedObjectTypes(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    class WidenedEndpoints(WidenedObjectTypes):
        RELATION_ENDPOINT_STATUSES = (
            PalantirToOssieConverter.RELATION_ENDPOINT_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, relations = _subtype_export(
        ot_status="experimental", relation_status="experimental"
    )

    partial = _convert(tmp_path, object_types, relations=relations, converter=WidenedObjectTypes)
    assert _supertype_names(partial, "Gadget") == []

    full = _convert(tmp_path, object_types, relations=relations, converter=WidenedEndpoints)
    assert _supertype_names(full, "Gadget") == ["Widget"]

def test_subtype_relation_statuses_can_be_widened_on_their_own(tmp_path: Path):
    """The narrower default is a policy, not a fact about the export."""

    class IntermediarySubtypes(PalantirToOssieConverter):
        SUBTYPE_RELATION_STATUSES = (
            PalantirToOssieConverter.SUBTYPE_RELATION_STATUSES | {Status.INTERMEDIARY}
        )

    object_types, relations = _subtype_export(ot_status="active", relation_status="intermediary")

    assert _supertype_names(_convert(tmp_path, object_types, relations=relations), "Gadget") == []

    widened = _convert(
        tmp_path, object_types, relations=relations, converter=IntermediarySubtypes
    )
    assert _supertype_names(widened, "Gadget") == ["Widget"]

