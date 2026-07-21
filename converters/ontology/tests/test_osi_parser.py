"""Unit tests for OsiParser and the spec -> OsiOntology conversion, driven by
the `examples/flights.yaml` ontology."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from osi.converter.osi_to_spec.converter import OsiToSpecConverter
from osi.model import ConceptType, OsiOntology, RelationshipMultiplicity
from osi.parser import OsiParser


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
    assert OsiParser.load_data(path) == {"a": 1, "b": ["x", "y"]}


def test_load_data_reads_json(tmp_path: Path):
    path = tmp_path / "spec.json"
    path.write_text(json.dumps({"a": 1, "b": ["x", "y"]}))
    assert OsiParser.load_data(path) == {"a": 1, "b": ["x", "y"]}


def test_parse_of_flights_as_json(flights_path: Path, tmp_path: Path):
    # The parser selects JSON vs YAML from the file suffix; a .json rendering of
    # the same spec must produce an equivalent model.
    json_path = tmp_path / "flights.json"
    json_path.write_text(json.dumps(yaml.safe_load(flights_path.read_text())))
    model = OsiParser().parse(json_path)
    assert model.name == "Flights"
    assert len(model.ontology.concepts(exclude_builtin=True)) == 44


# ----- Error handling ---------------------------------------------------

def test_parse_rejects_directory(tmp_path: Path):
    with pytest.raises(ValueError, match="is not a file"):
        OsiParser().parse(tmp_path)


def test_parse_rejects_missing_file(tmp_path: Path):
    with pytest.raises(ValueError, match="is not a file"):
        OsiParser().parse(tmp_path / "does_not_exist.yaml")


def test_spec_requires_parse_first():
    parser = OsiParser()
    with pytest.raises(RuntimeError):
        parser.spec()


def test_parsers_do_not_share_formula_factories():
    a, b = OsiParser(), OsiParser()
    assert a._formula_factory is not b._formula_factory
    assert a._mapping_formula_factory is not b._mapping_formula_factory


# ----- Round-trip invariants --------------------------------------------

def _structure_sets(model: OsiOntology):
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
    model1 = OsiParser().parse(flights_path)
    yaml1 = OsiToSpecConverter.convert(model1).dump_yaml()

    roundtrip_path = tmp_path / "roundtrip.yaml"
    roundtrip_path.write_text(yaml1)
    model2 = OsiParser().parse(roundtrip_path)

    assert _structure_sets(model1) == _structure_sets(model2)


def test_dump_yaml_is_deterministic_for_fixed_input(flights_path):
    """The same input file always dumps to identical YAML (so the snapshot is
    stable across runs)."""
    yaml_a = OsiToSpecConverter.convert(OsiParser().parse(flights_path)).dump_yaml()
    yaml_b = OsiToSpecConverter.convert(OsiParser().parse(flights_path)).dump_yaml()
    assert yaml_a == yaml_b