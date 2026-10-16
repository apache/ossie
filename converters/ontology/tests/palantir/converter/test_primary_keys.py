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

"""Matching an export's primary keys against the columns a dataset declares."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pytest


from tests.palantir.converter.builders import (
    dataset,
    object_type,
    ot_rid,
    prop,
)

from tests.palantir.converter.helpers import _convert, _identifier_columns


def _location_export(
    *, pk_column: str, dataset_columns: Iterable[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """A Location whose primary key names *pk_column* in its backing dataset."""
    object_types = [
        {
            "rid": ot_rid("location"),
            "id": "location",
            "displayName": "Location",
            "properties": [
                prop("ri.p.locno", "locno", pk_columns={ot_rid("location"): pk_column})
            ],
            "primaryKeys": ["locno"],
        }
    ]
    return object_types, [dataset("location", dataset_columns)]

def test_composite_identifier_column_order_is_stable(tmp_path: Path):
    """`primary_keys()` is a set of identity-hashed properties, so iterating it
    yields a different order per run — which would reach the emitted YAML
    through referent_mappings and make any diff of it noisy.

    Converting the same export repeatedly in one process is what exposes this:
    a single conversion always looks fine.
    """
    object_types = [
        object_type(
            "thing",
            "Thing",
            status="active",
            properties=[prop("ri.p.bee", "bee"), prop("ri.p.aye", "aye")],
            primary_keys=["thing_id", "bee", "aye"],
        )
    ]
    datasets = [dataset("thing", ["thing_id", "bee", "aye"])]

    orderings = {
        tuple(_identifier_columns(_convert(tmp_path, object_types, datasets=datasets), "Thing"))
        for _ in range(20)
    }

    assert orderings == {("aye", "bee", "thing_id")}

def test_primary_key_column_is_matched_case_insensitively(tmp_path: Path):
    """`primaryKeyMapping` names the warehouse column, the dataset the API spelling."""
    object_types, datasets = _location_export(pk_column="LOCNO", dataset_columns=["locno"])
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert _identifier_columns(model, "Location") == ["locno"]

def test_exact_primary_key_column_match_wins(tmp_path: Path):
    """A case-sensitive source that carries both spellings keeps its own."""
    object_types, datasets = _location_export(
        pk_column="LOCNO", dataset_columns=["locno", "LOCNO"]
    )
    model = _convert(tmp_path, object_types, datasets=datasets)

    assert _identifier_columns(model, "Location") == ["LOCNO"]

def test_primary_key_column_absent_from_dataset_warns(tmp_path: Path):
    """A column that is missing outright is still reported, not folded away."""
    object_types, datasets = _location_export(pk_column="MISSING", dataset_columns=["locno"])

    with pytest.warns(UserWarning, match="does not contain a field named 'MISSING'"):
        model = _convert(tmp_path, object_types, datasets=datasets)

    # Nothing identifies the concept, so the mapping is dropped along with it.
    assert not [
        cm for om in model.ontology_mappings for cm in om.concept_mappings
    ]

