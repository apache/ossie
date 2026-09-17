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

"""Which dataset's mapping a link is attached to when an object type has several."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ossie_ontology.model import DatasetField, OssieOntology

from tests.palantir.converter.builders import (
    dataset,
    many_to_one,
    object_type,
    prop,
)

from tests.palantir.converter.helpers import _convert


def _two_dataset_export(*, second_carries_primary_key: bool):
    """Gadget backed by two datasets, each holding the FK to Widget.

    With *second_carries_primary_key* false, nothing in the second dataset
    identifies a Gadget, so its ConceptMapping is dropped while the dataset —
    and its copy of the foreign-key column — remains.
    """
    object_types = [
        object_type("widget", "Widget", status="active"),
        object_type(
            "gadget",
            "Gadget",
            status="active",
            properties=[prop("ri.p.widget_ref", "widget_ref", column="widget_ref")],
            datasources=["ri.ds.a", "ri.ds.b"],
        ),
    ]
    relations = [
        many_to_one(
            "gadget_widget",
            one="widget",
            many="gadget",
            key_map={"ri.p.widget_id": "ri.p.widget_ref"},
            status="active",
        )
    ]
    second_columns = ["gadget_id", "widget_ref"] if second_carries_primary_key else ["widget_ref"]
    datasets = [
        {
            "mainDatasetId": "ri.ds.a",
            "datasetName": "ds_a",
            "datasetSchema": [{"name": c, "type": "STRING"} for c in ("gadget_id", "widget_ref")],
        },
        {
            "mainDatasetId": "ri.ds.b",
            "datasetName": "ds_b",
            "datasetSchema": [{"name": c, "type": "STRING"} for c in second_columns],
        },
        dataset("widget", ["widget_id"]),
    ]
    return object_types, relations, datasets

def _link_dataset_pairs(model: OssieOntology, concept_name: str) -> set[tuple[str, str]]:
    """For every attached link: (dataset identifying the mapping, dataset the
    link's own expression comes from). The two must always be the same one."""
    def dataset_name(expression: Any) -> str:
        assert isinstance(expression, DatasetField)
        assert expression.dataset is not None
        return expression.dataset.name

    pairs: set[tuple[str, str]] = set()
    for om in model.ontology_mappings:
        for cm in om.concept_mappings:
            if cm.concept.name != concept_name:
                continue
            [root] = cm.object_mappings
            identifying = {dataset_name(rm.expression) for rm in (root.referent_mappings or [])}
            for link_mapping in cm.link_mappings:
                for child in link_mapping.children or []:
                    for rm in child.object_mapping.referent_mappings or []:
                        pairs |= {(name, dataset_name(rm.expression)) for name in identifying}
    return pairs

def test_link_is_attached_to_the_mapping_for_its_owndataset(tmp_path: Path):
    """Two datasets, two mappings, and each link reads its own dataset."""
    object_types, relations, datasets = _two_dataset_export(second_carries_primary_key=True)

    model = _convert(tmp_path, object_types, relations=relations, datasets=datasets)

    assert _link_dataset_pairs(model, "Gadget") == {
        ("Gadget_ds_a", "Gadget_ds_a"),
        ("Gadget_ds_b", "Gadget_ds_b"),
    }

def test_link_is_not_attached_to_a_mapping_for_anotherdataset(tmp_path: Path):
    """A dataset whose mapping was dropped must not have its columns rehomed.

    The second dataset keeps the foreign-key column but loses its mapping, and
    the link built from that column has nowhere to go — attaching it to the
    first dataset's mapping would join two unrelated tables under one identity.
    """
    object_types, relations, datasets = _two_dataset_export(second_carries_primary_key=False)

    with pytest.warns(UserWarning, match="cannot attach link 'Gadget.gadget_widget'"):
        model = _convert(tmp_path, object_types, relations=relations, datasets=datasets)

    assert _link_dataset_pairs(model, "Gadget") == {("Gadget_ds_a", "Gadget_ds_a")}

