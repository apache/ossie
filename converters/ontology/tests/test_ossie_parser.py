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

"""Unit tests for OssieParser and the spec -> OssieOntology conversion, driven by
the `examples/flights.yaml` ontology."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ossie_ontology.converter.ossie_to_spec.converter import (
    OssieToSpecConverter,
    _convert_ontology_concepts,
)
from ossie_ontology.model import (
    Concept,
    ConceptType,
    OntologyComponent,
    OssieOntology,
    Relationship,
    RelationshipMultiplicity,
)
from ossie_ontology.parser import OssieParser


# ----- Document-level metadata ------------------------------------------

def test_parse_returns_model_with_metadata(flights_model):
    assert flights_model.name == "Flights"
    assert flights_model.version == "0.2.0.dev0"
    assert flights_model.description == "Ontology of flights into and out of airports."


def test_parse_returns_populated_ontology(flights_model):
    ontology = flights_model.ontology
    # Built-in concepts (String, Integer, Decimal, ...) are always present on top
    # of the ones declared in the spec.
    assert len(ontology.concepts(exclude_builtin=True)) == 44
    assert len(ontology.concepts()) > len(ontology.concepts(exclude_builtin=True))
    assert len(ontology.relationships) == 58


# ----- Ontology-level requires ------------------------------------------

def test_ontology_level_requires(flights_model):
    requires = [str(r) for r in flights_model.ontology.requires]
    assert requires == ["COUNT[Airport] > 0", "COUNT[Carrier] > 0"]


# ----- Concept-level requires -------------------------------------------

@pytest.mark.parametrize(
    "concept_name, expected",
    [
        ("DegreesLatitude", ["DegreesLatitude <= 90", "DegreesLatitude >= -90"]),
        ("DegreesLongitude", ["DegreesLongitude <= 180", "DegreesLongitude >= -180"]),
        (
            "CancelationCode",
            ["CancelationCode == 'A' OR CancelationCode == 'B' OR CancelationCode == 'C' OR CancelationCode == 'D'"],
        ),
    ],
)
def test_concept_requires(flights_model, concept_name, expected):
    concept = flights_model.ontology.lookup_concept(concept_name)
    assert concept is not None
    assert [str(r) for r in concept.requires] == expected


# ----- Value-type inheritance -------------------------------------------

@pytest.mark.parametrize(
    "concept_name, parent_name",
    [
        ("NrFeet", "Decimal"),
        ("NrPounds", "Integer"),
        ("Capacity", "NrPounds"),
        ("CancelationCode", "String"),
        ("Delay", "NrMinutes"),
    ],
)
def test_value_type_extends(flights_model, concept_name, parent_name):
    concept = flights_model.ontology.lookup_concept(concept_name)
    assert concept is not None
    assert concept.type == ConceptType.VALUE_TYPE
    assert [p.name for p in concept.extends] == [parent_name]


# ----- Identifiers -------------------------------------------------------

def test_identify_by(flights_model):
    airport = flights_model.ontology.lookup_concept("Airport")
    assert airport is not None
    assert airport.type == ConceptType.ENTITY_TYPE
    assert list(airport.identify_by.keys()) == ["Airport.code"]


# ----- Relationship multiplicity ----------------------------------------

def test_relationship_multiplicity(flights_model):
    ontology = flights_model.ontology
    airport = ontology.lookup_concept("Airport")
    code_rel = ontology.lookup_concept_relationship(airport, "code")
    assert code_rel is not None
    assert code_rel.multiplicity == RelationshipMultiplicity.ONE_TO_ONE


# ----- Ontology mapping / semantic model --------------------------------

def test_ontology_mapping(flights_model):
    assert len(flights_model.ontology_mappings) == 1
    mapping = flights_model.ontology_mappings[0]
    semantic_model = mapping.semantic_model
    dataset_names = {d.name for d in semantic_model.datasets}
    assert {"AIRPORT", "FLIGHT", "CARRIER", "ROUTE"} <= dataset_names
    assert len(mapping.concept_mappings) == 11


# ----- load_data --------------------------------------------------------

def test_load_data_reads_yaml(tmp_path: Path):
    path = tmp_path / "spec.yaml"
    path.write_text("a: 1\nb:\n  - x\n  - y\n")
    assert OssieParser.load_data(path) == {"a": 1, "b": ["x", "y"]}


def test_load_data_reads_json(tmp_path: Path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"a": 1, "b": ["x", "y"]}))
    assert OssieParser.load_data(path) == {"a": 1, "b": ["x", "y"]}


def test_parse_of_flights_as_json(flights_path: Path, tmp_path: Path):
    # The parser selects JSON vs YAML from the file suffix; a .json rendering of
    # the same spec must produce an equivalent model.
    json_path = tmp_path / "flights.json"
    json_path.write_text(json.dumps(yaml.safe_load(flights_path.read_text())))
    model = OssieParser().parse(json_path)
    assert model.name == "Flights"
    assert len(model.ontology.concepts(exclude_builtin=True)) == 44


# ----- Error handling ---------------------------------------------------

def test_parse_rejects_directory(tmp_path: Path):
    with pytest.raises(ValueError, match="is not a file"):
        OssieParser().parse(tmp_path)


def test_parse_rejects_missing_file(tmp_path: Path):
    with pytest.raises(ValueError, match="is not a file"):
        OssieParser().parse(tmp_path / "does_not_exist.yaml")


def test_spec_requires_parse_first():
    parser = OssieParser()
    with pytest.raises(RuntimeError):
        parser.spec()


def test_parsers_do_not_share_formula_factories():
    a, b = OssieParser(), OssieParser()
    assert a._formula_factory is not b._formula_factory
    assert a._mapping_formula_factory is not b._mapping_formula_factory


# ----- Round-trip invariants --------------------------------------------

def _structure_sets(model: OssieOntology):
    ontology = model.ontology
    return (
        {c.name for c in ontology.concepts(exclude_builtin=True)},
        {r.full_name for r in ontology.relationships},
        {str(req) for req in ontology.requires},
    )


def test_roundtrip_preserves_structure(flights_path, tmp_path: Path):
    """Parsing -> spec -> YAML -> parsing preserves the ontology structure.

    Note: concept/relationship *emission order* is not guaranteed to be stable
    across a round-trip (it depends on the topological tie-breaking of the input
    order), so we compare the sets of concepts, relationships, and requires
    rather than the raw YAML.
    """
    model1 = OssieParser().parse(flights_path)
    yaml1 = OssieToSpecConverter.convert(model1).dump_yaml()

    roundtrip_path = tmp_path / "roundtrip.yaml"
    roundtrip_path.write_text(yaml1)
    model2 = OssieParser().parse(roundtrip_path)

    assert _structure_sets(model1) == _structure_sets(model2)


def test_dump_yaml_is_deterministic_for_fixed_input(flights_path):
    """The same input file always dumps to identical YAML (so the snapshot is
    stable across runs)."""
    yaml_a = OssieToSpecConverter.convert(OssieParser().parse(flights_path)).dump_yaml()
    yaml_b = OssieToSpecConverter.convert(OssieParser().parse(flights_path)).dump_yaml()
    assert yaml_a == yaml_b


# ----- concept dependency errors ----------------------------------------

def _write_spec(tmp_path: Path, concepts: list[dict]) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump({"version": "0.1.0", "name": "Demo", "ontology": concepts}))
    return path


def _concept(name: str, *, extends: list[str] | None = None) -> dict:
    concept: dict = {
        "concept": name,
        "type": "EntityType",
        "relationships": [{"name": f"{name.lower()}_id", "roles": [{"concept": "String"}]}],
        "identify_by": [f"{name.lower()}_id"],
    }
    if extends:
        concept["extends"] = extends
    return concept


def test_extends_an_undeclared_concept_names_it(tmp_path: Path):
    """The dependency sort runs before this check, so it must not claim a cycle.

    An `extends` target missing from the document is a dangling edge, not a
    loop, and the author needs to be told which name is missing.
    """
    path = _write_spec(tmp_path, [_concept("Gadget", extends=["Undeclared"])])

    with pytest.raises(ValueError, match="Subtype 'Undeclared' is not declared"):
        OssieParser().parse(path)


def test_extends_in_a_cycle_is_still_reported_as_a_cycle(tmp_path: Path):
    path = _write_spec(
        tmp_path, [_concept("A", extends=["B"]), _concept("B", extends=["A"])]
    )

    with pytest.raises(ValueError, match="contains a cycle"):
        OssieParser().parse(path)


def test_extends_a_builtin_is_not_a_dependency(tmp_path: Path):
    """Builtins are never declared in the document, and never sorted."""
    path = _write_spec(tmp_path, [_concept("Code", extends=["String"])])

    model = OssieParser().parse(path)

    concept = model.ontology.lookup_concept("Code")
    assert concept is not None
    assert [p.name for p in concept.extends] == ["String"]


# ----- grouping relationships under their concept -----------------------

def test_relationships_are_grouped_under_their_own_container():
    """Each concept emits exactly its own relationships, in declaration order.

    The conversion groups the ontology's relationships by container in one pass,
    so this pins the assignment: interleaved declarations, a concept that is not
    a component (dropped, along with the relationship it contains), and the
    order within each group.
    """
    ontology = OntologyComponent()
    alpha = Concept(name="Alpha", type=ConceptType.ENTITY_TYPE)
    beta = Concept(name="Beta", type=ConceptType.ENTITY_TYPE)
    hidden = Concept(name="Hidden", type=ConceptType.ENTITY_TYPE, is_component=False)
    for concept in (alpha, beta, hidden):
        ontology.add_concept(concept)
    for container, name in [
        (alpha, "a1"), (beta, "b1"), (alpha, "a2"), (hidden, "h1"), (beta, "b2"),
    ]:
        ontology.add_relationship(
            Relationship(name=name, container=container, relates=[(beta, None)])
        )

    components = _convert_ontology_concepts(ontology)

    assert {c.concept: [r.name for r in c.relationships] for c in components} == {
        "Alpha": ["a1", "a2"],
        "Beta": ["b1", "b2"],
    }
