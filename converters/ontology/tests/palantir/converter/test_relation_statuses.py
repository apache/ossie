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

"""Which relations become relationships, and what blocks one."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.vendor.palantir.model import Status

from tests.palantir.converter.builders import (
    dataset,
    many_to_one,
    object_type,
    prop,
)

from tests.palantir.converter.helpers import (
    _convert,
    _concept_names,
    _relationship_names,
    _mapped_relationship_names,
)


def _linked_export(
    *, ot_status: str, relation_status: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Two object types and the M:1 relation between them, at the given statuses."""
    object_types = [
        object_type("widget", "Widget", status=ot_status),
        object_type(
            "gadget",
            "Gadget",
            status=ot_status,
            properties=[prop("ri.p.gadget_widget_id", "gadget_widget_id", column="widget_id")],
        ),
    ]
    relations = [
        many_to_one(
            "gadget_widget",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.gadget_widget_id"},
            status=relation_status,
        )
    ]
    return object_types, relations

def test_relation_statuses_gate_conversion(tmp_path: Path):
    object_types, relations = _linked_export(ot_status="active", relation_status="deprecated")
    model = _convert(tmp_path, object_types, relations=relations)

    assert "Gadget.gadget_widget" not in _relationship_names(model)

def test_experimental_relation_between_active_object_types_is_converted(tmp_path: Path):
    """Experimental relations ride in on their endpoints rather than their own status."""
    object_types, relations = _linked_export(ot_status="active", relation_status="experimental")
    model = _convert(tmp_path, object_types, relations=relations)

    assert "Gadget.gadget_widget" in _relationship_names(model)

def test_experimental_relation_needs_its_endpoints_admitted_too(tmp_path: Path):
    """Widening object types alone leaves their experimental relations behind.

    The endpoint check is separate from the object-type policy, so a converter
    that admits experimental object types still drops every experimental
    relation between them until it widens the endpoint statuses as well.
    """

    class WidenedObjectTypes(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    class WidenedEndpoints(WidenedObjectTypes):
        RELATION_ENDPOINT_STATUSES = (
            PalantirToOssieConverter.RELATION_ENDPOINT_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, relations = _linked_export(
        ot_status="experimental", relation_status="experimental"
    )

    partial = _convert(tmp_path, object_types, relations=relations, converter=WidenedObjectTypes)
    assert _concept_names(partial) == {"Widget", "Gadget"}
    assert "Gadget.gadget_widget" not in _relationship_names(partial)

    full = _convert(tmp_path, object_types, relations=relations, converter=WidenedEndpoints)
    assert "Gadget.gadget_widget" in _relationship_names(full)

def test_link_colliding_with_a_property_name_is_skipped(tmp_path: Path):
    """A shared name costs the link, not the conversion.

    Exports routinely name a link after the foreign-key column backing it, and
    both become relationships on the same concept — which the ontology rejects
    as a duplicate. The relation is dropped with a warning; the property, which
    is converted first, keeps the name.
    """
    object_types = [
        object_type("widget", "Widget", status="active"),
        object_type(
            "gadget",
            "Gadget",
            status="active",
            properties=[prop("ri.p.owner", "owner", column="owner")],
        ),
    ]
    relations = [
        many_to_one(
            "owner",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.owner"},
            status="active",
        )
    ]
    datasets = [dataset("widget", ["widget_id"]), dataset("gadget", ["gadget_id", "owner"])]

    with pytest.warns(UserWarning, match="Relation 'Gadget.owner' collides"):
        model = _convert(tmp_path, object_types, relations=relations, datasets=datasets)

    # One 'owner' relationship, and it is the property's — still mapped to its
    # own column, rather than the skipped link's walk into Widget.
    assert _relationship_names(model) == {"Widget.widget_id", "Gadget.gadget_id", "Gadget.owner"}
    assert _mapped_relationship_names(model, "Gadget") == {"Gadget.owner"}

