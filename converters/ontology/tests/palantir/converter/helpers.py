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

"""Converting a built export, and reading the result back.

Split out because every suite in this package needs them: `convert` runs an
export through the real parser and converter, and the rest answer questions
about the model that comes out.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


from ossie_ontology.converter.palantir_to_ossie.converter import PalantirToOssieConverter
from ossie_ontology.vendor.palantir.parser import PalantirParser
from ossie_ontology.model import ConceptMapping, DatasetField, OssieOntology

from tests.palantir.converter.builders import many_to_one, object_type, prop


def _convert(
    tmp_path: Path,
    object_types: Iterable[dict[str, Any]],
    *,
    relations: Iterable[dict[str, Any]] = (),
    datasets: Iterable[dict[str, Any]] = (),
    converter: type[PalantirToOssieConverter] = PalantirToOssieConverter,
) -> OssieOntology:
    """Write an extracted-folder export, parse it, and convert it.

    Both files are rewritten on every call, so a test that converts the same
    export under two different policies can call this twice.
    """
    export = tmp_path / "export"
    (export / "data_sets").mkdir(parents=True, exist_ok=True)
    (export / "ontology.json").write_text(
        json.dumps({"objectTypes": list(object_types), "relations": list(relations)})
    )
    (export / "data_sets" / "ds.json").write_text(json.dumps(list(datasets)))
    return converter().convert(PalantirParser().parse(export))

def _concept_names(model: OssieOntology) -> set[str]:
    return {c.name for c in model.ontology.concepts(exclude_builtin=True)}

def _relationship_names(model: OssieOntology) -> set[str]:
    return {r.full_name for r in model.ontology.relationships}

def _concept_mapping(model: OssieOntology, concept_name: str) -> ConceptMapping:
    mappings = [
        cm
        for om in model.ontology_mappings
        for cm in om.concept_mappings
        if cm.concept.name == concept_name
    ]
    assert len(mappings) == 1, f"expected one mapping for '{concept_name}', got {len(mappings)}"
    return mappings[0]

def _mapped_relationship_names(model: OssieOntology, concept_name: str) -> set[str]:
    """Names of the relationships a concept's mapping actually populates."""
    names = set()
    for lm in _concept_mapping(model, concept_name).link_mappings:
        for child in lm.children or []:
            assert child.relationship is not None
            names.add(child.relationship.full_name)
    return names

def _identifier_columns(model: OssieOntology, concept_name: str) -> list[str]:
    """The dataset fields a concept's mapping identifies its instances by."""
    [object_mapping] = _concept_mapping(model, concept_name).object_mappings
    columns = []
    for rm in object_mapping.referent_mappings or []:
        assert isinstance(rm.expression, DatasetField)
        columns.append(rm.expression.name)
    return columns

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

def _supertype_names(model: OssieOntology, concept_name: str) -> list[str]:
    concept = model.ontology.lookup_concept(concept_name)
    assert concept is not None, f"concept '{concept_name}' was not converted"
    return [parent.name for parent in concept.extends]

