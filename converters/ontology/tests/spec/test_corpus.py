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

"""The corpus, pinned: every spec under fixtures/specs/, rendered two ways.

Two views of each ontology, both independent of any vendor:

  * the *structure* — concepts, identifiers, relationships and their
    multiplicities, rendered as sorted text, so a parsing or conversion change
    reads as a reviewable diff rather than a dump.
  * the *round trip* — model back out to spec YAML through `OssieToSpecConverter`,
    which is what catches a field that survives parsing but is dropped on the
    way out.

These are the breadth suite — they run over the whole corpus rather than
demonstrating one behaviour, which is what `test_parser.py` beside them does.

Regenerate after an intentional change with:

    pytest tests/spec/test_corpus.py --snapshot-update
"""

from __future__ import annotations

import pytest

from ossie_ontology.converter.ossie_to_spec.converter import OssieToSpecConverter
from ossie_ontology.model import OntologyComponent, OssieOntology

from tests.conftest import SPEC_NAMES


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


@pytest.mark.parametrize("spec_model", SPEC_NAMES, indirect=True, ids=SPEC_NAMES)
def test_structure_snapshot(spec_model: OssieOntology, snapshot):
    snapshot.assert_match(_render_structure(spec_model), "structure.txt")


@pytest.mark.parametrize("spec_model", SPEC_NAMES, indirect=True, ids=SPEC_NAMES)
def test_roundtrip_yaml_snapshot(spec_model: OssieOntology, snapshot):
    spec = OssieToSpecConverter.convert(spec_model)
    snapshot.assert_match(spec.dump_yaml(), "roundtrip.yaml")