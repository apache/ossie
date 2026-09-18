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

"""Which object types a conversion admits, and how that policy is widened."""

from __future__ import annotations

from pathlib import Path

import pytest

from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.vendor.palantir.model import Status

from tests.palantir.converter.builders import dataset, object_type, prop

from tests.palantir.converter.helpers import _convert, _concept_names


def test_default_policy_admits_active_endorsed_and_intermediary(tmp_path: Path):
    model = _convert(tmp_path, [
        object_type("alpha", "Alpha", status="active"),
        object_type("bravo", "Bravo", status="endorsed"),
        object_type("charlie", "Charlie", status="intermediary"),
        object_type("delta", "Delta", status="experimental"),
        object_type("echo", "Echo", status="deprecated"),
    ])
    assert _concept_names(model) == {"Alpha", "Bravo", "Charlie"}

def test_object_type_named_like_a_builtin_is_reported(tmp_path: Path):
    """`date` pascal-cases to `Date`, which is a builtin value type.

    Letting it through gives the entity the builtin's name, and every DATE
    column in the export is then typed by this object type — so it is reported
    as the concept-name collision it is, naming the object type rather than
    surfacing later as a complaint about a missing 'date_id' property.
    """
    object_types = [
        object_type(
            "date",
            "date",
            status="active",
            properties=[prop("ri.p.label", "label", column="label")],
        )
    ]

    with pytest.raises(ValueError, match="which is a builtin value type"):
        _convert(tmp_path, object_types, datasets=[dataset("date", ["date_id", "label"])])

def test_object_type_statuses_can_be_widened_by_a_subclass(tmp_path: Path):
    """The extension point a draft-ontology converter is built on."""

    class DraftConverter(PalantirToOssieConverter):
        OBJECT_TYPE_STATUSES = PalantirToOssieConverter.OBJECT_TYPE_STATUSES | {Status.EXPERIMENTAL}

    object_types = [
        object_type("alpha", "Alpha", status="active"),
        object_type("delta", "Delta", status="experimental"),
    ]
    assert _concept_names(_convert(tmp_path, object_types)) == {"Alpha"}

    widened = _convert(tmp_path, object_types, converter=DraftConverter)
    assert _concept_names(widened) == {"Alpha", "Delta"}

