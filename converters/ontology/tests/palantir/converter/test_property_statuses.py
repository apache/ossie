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

"""Which properties are declared, which are mapped, and which are dropped."""

from __future__ import annotations

from pathlib import Path
from typing import Any


from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.vendor.palantir.model import Status

from tests.palantir.converter.builders import dataset, object_type, prop

from tests.palantir.converter.helpers import (
    _convert,
    _relationship_names,
    _mapped_relationship_names,
)


def _widget_export() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """One object type with a property per status, and its backing dataset."""
    object_types = [
        object_type(
            "widget",
            "Widget",
            status="active",
            properties=[
                prop("ri.p.label", "label", status="active", column="label"),
                prop("ri.p.note", "note", status="experimental", column="note"),
                prop("ri.p.legacy", "legacy", status="deprecated", column="legacy"),
            ],
        )
    ]
    return object_types, [dataset("widget", ["widget_id", "label", "note", "legacy"])]

def test_experimental_property_is_declared_but_not_mapped(tmp_path: Path):
    """An experimental property is part of the ontology, but nothing populates it.

    The two policies differ on purpose: the relationship is worth declaring
    while the work is in progress, but pointing a mapping at a column that is
    still being reshaped is not.
    """
    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert "Widget.note" in _relationship_names(model)
    assert _mapped_relationship_names(model, "Widget") == {"Widget.label"}

def test_deprecated_property_is_not_converted_at_all(tmp_path: Path):
    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert "Widget.legacy" not in _relationship_names(model)

def test_mapping_property_statuses_can_be_widened_by_a_subclass(tmp_path: Path):
    class DraftConverter(PalantirToOssieConverter):
        MAPPING_PROPERTY_STATUSES = (
            PalantirToOssieConverter.MAPPING_PROPERTY_STATUSES | {Status.EXPERIMENTAL}
        )

    object_types, datasets = _widget_export()
    model = _convert(tmp_path, object_types, datasets=datasets, converter=DraftConverter)

    assert _mapped_relationship_names(model, "Widget") == {"Widget.label", "Widget.note"}

