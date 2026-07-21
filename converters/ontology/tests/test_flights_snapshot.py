"""Snapshot tests for the flights ontology.

These lock in the converted structure and the round-tripped YAML so that any
change in parsing/conversion output shows up as a reviewable diff.

Regenerate the snapshots after an intentional change with:

    pytest tests/test_flights_snapshot.py --snapshot-update
"""

from __future__ import annotations

from osi.converter.osi_to_spec.converter import OsiToSpecConverter
from osi.model import OntologyComponent, OsiOntology


def _render_structure(model: OsiOntology) -> str:
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
    spec = OsiToSpecConverter.convert(flights_model)
    snapshot.assert_match(spec.dump_yaml(), "flights_roundtrip.yaml")