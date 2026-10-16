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

"""Document-level metadata on the semantic model a conversion generates."""

from __future__ import annotations

from pathlib import Path

from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter

from tests.palantir.converter.builders import dataset, object_type
from tests.palantir.converter.helpers import _convert


def test_generated_semantic_model_has_core_document_version(tmp_path: Path):
    model = _convert(
        tmp_path,
        [object_type("widget", "Widget", status="active")],
        datasets=[dataset("widget", ["widget_id"])],
    )

    document = OssieToSpecConverter.convert(model).dump_dict()

    assert document["version"] == "0.2.0.dev0"
    [mapping] = document["ontology_mappings"]
    assert mapping["semantic_model"]["version"] == "0.2.0.dev0"
    assert mapping["semantic_model"]["datasets"]
