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

"""Snapshot tests for the flights ontology.

These lock in the converted structure and the round-tripped YAML so that any
change in parsing/conversion output shows up as a reviewable diff.

Regenerate the snapshots after an intentional change with:

    pytest tests/test_flights_snapshot.py --snapshot-update
"""

from __future__ import annotations

from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter
from ossie_ontology.model import OntologyComponent, OssieOntology


def _render_structure(model: OssieOntology) -> str:
    """Render a compact, deterministic text summary of the ontology structure."""
    ontology: OntologyComponent = model.ontology
    lines: list[str] = [
        f"name: {model.name}",
        f"version: {model.version}",
        f"description: {model.description}",
        "",
        "ontology requires:",
    ]
    for req in ontology.requires:
        lines.append(f"  - {req}")

    lines.append("")
    lines.append("concepts:")
    for concept in sorted(ontology.concepts(exclude_builtin=True), key=lambda c: c.name):
        type_name = concept.type.name if concept.type else "None"
        lines.append(f"  {concept.name} ({type_name})")
        if concept.extends:
            lines.append(f"    extends: {', '.join(p.name for p in concept.extends)}")
        if concept.identify_by:
            lines.append(f"    identify_by: {', '.join(sorted(concept.identify_by))}")
        for req in concept.requires:
            lines.append(f"    requires: {req}")

    lines.append("")
    lines.append("relationships:")
    for rel in sorted(ontology.relationships, key=lambda r: r.full_name):
        mult = rel.multiplicity.name if rel.multiplicity else "None"
        signature = " -> ".join(c.name for c in rel.signature)
        lines.append(f"  {rel.full_name} [{mult}]: {signature}")

    return "\n".join(lines) + "\n"


def test_flights_structure_snapshot(flights_model, snapshot):
    snapshot.assert_match(_render_structure(flights_model), "flights_structure.txt")


def test_flights_roundtrip_yaml_snapshot(flights_model, snapshot):
    spec = OssieToSpecConverter.convert(flights_model)
    snapshot.assert_match(spec.dump_yaml(), "flights_roundtrip.yaml")