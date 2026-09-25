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

"""M:M and intermediary relations, and the derivations they produce."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pytest

from ossie_ontology.model import OssieOntology

from tests.palantir.converter.builders import (
    intermediary,
    many_to_many,
    many_to_one,
    object_type,
    prop,
)

from tests.palantir.converter.helpers import _convert, _relationship_names, _supertype_names


def _tagging_export(*, widget_properties: Iterable[dict[str, Any]] = ()):
    """Widget tagged with Tag through the WTag join object type.

    The two M:1 links run from WTag, which holds a foreign key to each side —
    the ordinary join-table shape, and non-primary-key columns, so neither link
    is read as inheritance.
    """
    object_types = [
        object_type("widget", "Widget", status="active", properties=list(widget_properties)),
        object_type("tag", "Tag", status="active"),
        object_type(
            "wtag",
            "WTag",
            status="active",
            properties=[
                prop("ri.p.wtag_widget", "wtag_widget"),
                prop("ri.p.wtag_tag", "wtag_tag"),
            ],
        ),
    ]
    relations = [
        many_to_one(
            "a_link", one="widget", many="wtag",
            key_map={"ri.p.widget_id": "ri.p.wtag_widget"}, status="active",
        ),
        many_to_one(
            "b_link", one="tag", many="wtag",
            key_map={"ri.p.tag_id": "ri.p.wtag_tag"}, status="active",
        ),
        intermediary(
            "tagged", a="widget", b="tag", via="wtag",
            link_a="a_link", link_b="b_link", status="active",
        ),
    ]
    return object_types, relations

def _derived_by_expressions(model: OssieOntology, full_name: str) -> list[str]:
    [relationship] = [r for r in model.ontology.relationships if r.full_name == full_name]
    return [f.raw_expr for f in relationship.derived_by]

def test_many_to_many_relation_becomes_a_relationship_on_role_a(tmp_path: Path):
    object_types = [
        object_type("widget", "Widget", status="active"),
        object_type("tag", "Tag", status="active"),
    ]
    relations = [many_to_many("tags", a="widget", b="tag", status="active")]

    model = _convert(tmp_path, object_types, relations=relations)

    assert "Widget.tags" in _relationship_names(model)

def test_intermediary_relation_is_derived_from_its_two_links(tmp_path: Path):
    """The relationship lands on role A and is derived by joining both links."""
    object_types, relations = _tagging_export()

    model = _convert(tmp_path, object_types, relations=relations)

    names = _relationship_names(model)
    assert "Widget.tagged" in names
    assert {"WTag.a_link", "WTag.b_link"} <= names
    assert _supertype_names(model, "WTag") == []
    assert _derived_by_expressions(model, "Widget.tagged") == [
        "WTag.a_link(Widget) AND WTag.b_link(Tag)"
    ]

def test_many_to_many_link_colliding_with_a_property_name_is_skipped(tmp_path: Path):
    """Same guard as the M:1 case, on the other relation kind."""
    object_types = [
        object_type(
            "widget", "Widget", status="active",
            properties=[prop("ri.p.tags", "tags", column="tags")],
        ),
        object_type("tag", "Tag", status="active"),
    ]
    relations = [many_to_many("tags", a="widget", b="tag", status="active")]

    with pytest.warns(UserWarning, match="Relation 'Widget.tags' collides"):
        model = _convert(tmp_path, object_types, relations=relations)

    assert _relationship_names(model) == {"Widget.widget_id", "Widget.tags", "Tag.tag_id"}

def test_intermediary_link_colliding_with_a_property_name_is_skipped(tmp_path: Path):
    """Skipping leaves no half-built relationship: the join formula goes too."""
    object_types, relations = _tagging_export(
        widget_properties=[prop("ri.p.tagged", "tagged", column="tagged")]
    )

    with pytest.warns(UserWarning, match="Relation 'Widget.tagged' collides"):
        model = _convert(tmp_path, object_types, relations=relations)

    # The surviving 'Widget.tagged' is the property, so it carries no join.
    assert _derived_by_expressions(model, "Widget.tagged") == []
    assert {"WTag.a_link", "WTag.b_link"} <= _relationship_names(model)

