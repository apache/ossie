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
from pydantic import ValidationError

from ossie_ontology.converter.ossie_to_spec.converter import (
    OssieToSpecConverter,
    _convert_ontology_concepts,
)
from ossie_ontology.model import (
    Concept,
    ConceptType,
    DataType,
    Formula,
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


def test_embedded_core_document_version_survives_roundtrip(flights_model, tmp_path):
    assert all(
        mapping.semantic_model.version == "0.2.0.dev0"
        for mapping in flights_model.ontology_mappings
    )

    exported = OssieToSpecConverter.convert(flights_model).dump_yaml()
    document = yaml.safe_load(exported)
    assert all(
        mapping["semantic_model"]["version"] == "0.2.0.dev0"
        for mapping in document["ontology_mappings"]
    )

    path = tmp_path / "roundtrip.yaml"
    path.write_text(exported)
    reparsed = OssieParser().parse(path)
    assert all(
        mapping.semantic_model.version == "0.2.0.dev0"
        for mapping in reparsed.ontology_mappings
    )


@pytest.mark.parametrize("version", ["missing", "0.1.0", "0.2.0", None, 2])
def test_parse_rejects_invalid_embedded_version(flights_path, tmp_path, version):
    document = yaml.safe_load(flights_path.read_text())
    model = document["ontology_mappings"][0]["semantic_model"]
    if version == "missing":
        del model["version"]
    else:
        model["version"] = version
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(document))

    with pytest.raises(ValidationError) as exc:
        OssieParser().parse(path)

    assert exc.value.errors()[0]["loc"] == ("ontology_mappings", 0, "semantic_model", "version")


@pytest.mark.parametrize("missing", [True, False], ids=["missing", "empty"])
def test_parse_rejects_missing_or_empty_datasets(flights_path, tmp_path, missing):
    document = yaml.safe_load(flights_path.read_text())
    model = document["ontology_mappings"][0]["semantic_model"]
    if missing:
        del model["datasets"]
    else:
        model["datasets"] = []
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(document))

    with pytest.raises(ValidationError) as exc:
        OssieParser().parse(path)

    assert exc.value.errors()[0]["loc"] == ("ontology_mappings", 0, "semantic_model", "datasets")


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


# ----- dataset field datatypes and the mappings that read them -----------

def _keyed_entity(name: str, key_type: str, *extra: dict) -> dict:
    return {
        "concept": name,
        "type": "EntityType",
        "identify_by": ["nr"],
        "relationships": [
            {
                "name": "nr",
                "roles": [{"concept": key_type, "name": "n"}],
                "verbalizes": [f"{{{name}}} has {{{key_type}:n}}"],
                "multiplicity": "OneToOne",
            },
            *extra,
        ],
    }


_KEY_TYPES = [
    {"concept": "StoreNr", "type": "ValueType", "extends": ["Integer"]},
    {"concept": "WarehouseNr", "type": "ValueType", "extends": ["Integer"]},
]


def _write_mapped_spec(
    tmp_path: Path,
    concepts: list[dict],
    concept_mappings: list[dict],
    datatype: str | None = None,
    metrics: list[dict] | None = None,
    untyped_columns: tuple[str, ...] = (),
) -> Path:
    """A spec whose one dataset `T` has a column `id` declared *datatype*, plus
    *untyped_columns* that declare none."""
    fields: list[dict] = []
    for name, field_datatype in [("id", datatype), *((name, None) for name in untyped_columns)]:
        field: dict = {"name": name, "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": name}]}}
        if field_datatype is not None:
            field["datatype"] = field_datatype
        fields.append(field)
    semantic_model: dict = {
        "version": "0.2.0.dev0",
        "name": "sm",
        "datasets": [{"name": "T", "source": "DB.S.T", "fields": fields}],
    }
    if metrics is not None:
        semantic_model["metrics"] = metrics
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump({
        "version": "0.2.0.dev0",
        "name": "Demo",
        "ontology": concepts,
        "ontology_mappings": [{"name": "m", "semantic_model": semantic_model, "concept_mappings": concept_mappings}],
    }))
    return path


def _key(concept: str, mapping: dict | None = None) -> dict:
    return {"concept": concept, "object_mappings": [mapping or {"expression": "T.id"}]}


def _field(model: OssieOntology, name: str = "id"):
    field = model.ontology_mappings[0].semantic_model.datasets[0].field(name)
    assert field is not None
    return field


def test_mappings_never_type_a_field(behaviours_dir: Path):
    """A field's type is what the spec declares for it, never what a mapping
    reads it as. `ref_scheme.yaml` declares no datatypes and keys Store from
    `STORES.storeNr` and `SALES.storeNr`; both stay untyped."""
    model = OssieParser().parse(behaviours_dir / "ref_scheme.yaml")

    assert {
        f"{dataset.name}.{field.name}": field.datatype
        for mapping in model.ontology_mappings
        for dataset in mapping.semantic_model.datasets
        for field in dataset.fields
    } == {
        "STORES.storeNr": None,
        "STORES.region": None,
        "SALES.saleNr": None,
        "SALES.storeNr": None,
        "SALES.storeRegion": None,
        "SALES.amount": None,
    }


def test_one_column_can_key_an_entity_both_ways(behaviours_dir: Path):
    """A referent mapping and a bare expression may both read one column.

    Each used to type the field — one as `StoreNr`, the other as `Store` — and
    the second failed with "A dataset field can only be bound to one ontology
    concept type". Mappings no longer type fields, so there is nothing to clash.
    """
    model = OssieParser().parse(behaviours_dir / "key_column_both_ways.yaml")

    sales = next(
        dataset
        for mapping in model.ontology_mappings
        for dataset in mapping.semantic_model.datasets
        if dataset.name == "SALES"
    )
    store_nr = next(f for f in sales.fields if f.name == "storeNr")
    assert store_nr.datatype is None


@pytest.mark.parametrize("datatype", [None, "Integer"])
def test_one_column_can_key_two_value_types(tmp_path: Path, datatype: str | None):
    """One column of site ids keys both Store (`StoreNr`) and Warehouse
    (`WarehouseNr`): fine whether the column is untyped or `Integer`, the
    builtin both key types extend."""
    path = _write_mapped_spec(
        tmp_path,
        [*_KEY_TYPES, _keyed_entity("Store", "StoreNr"), _keyed_entity("Warehouse", "WarehouseNr")],
        [_key("Store"), _key("Warehouse")],
        datatype=datatype,
    )

    field = _field(OssieParser().parse(path))
    assert (field.datatype.value if field.datatype else None) == datatype


def test_datatype_survives_a_round_trip(tmp_path: Path):
    """`datatype` on a field and on a metric is read, and written back out."""
    metric = {
        "name": "site_count",
        "expression": {"dialects": [{"dialect": "ANSI_SQL", "expression": "COUNT(T.id)"}]},
        "datatype": "Integer",
    }
    path = _write_mapped_spec(
        tmp_path, [*_KEY_TYPES, _keyed_entity("Store", "StoreNr")], [_key("Store")],
        datatype="Integer", metrics=[metric],
    )

    model = OssieParser().parse(path)
    assert _field(model).datatype is DataType.INTEGER
    assert model.ontology_mappings[0].semantic_model.metrics[0].datatype is DataType.INTEGER

    dumped = yaml.safe_load(OssieToSpecConverter.convert(model).dump_yaml())
    semantic_model = dumped["ontology_mappings"][0]["semantic_model"]
    assert semantic_model["datasets"][0]["fields"][0]["datatype"] == "Integer"
    assert semantic_model["metrics"][0]["datatype"] == "Integer"


@pytest.mark.parametrize(
    "datatype, builtin",
    [
        *((t, t.value) for t in DataType if t.value in ("String", "Integer", "Decimal", "Float", "Boolean", "Date", "DateTime")),
        (DataType.DATETIME_TZ, "DateTime"),
        (DataType.TIME, None),
        (DataType.OPAQUE, None),
    ],
)
def test_each_datatype_names_the_builtin_it_holds(datatype: DataType, builtin: str | None):
    """The one mapping validation, formula typing and pyrel schemas all read."""
    assert datatype.builtin_name == builtin


def test_an_unknown_datatype_is_rejected(tmp_path: Path):
    path = _write_mapped_spec(tmp_path, [*_KEY_TYPES, _keyed_entity("Store", "StoreNr")], [_key("Store")],
                              datatype="Long")

    with pytest.raises(ValidationError, match="datatype"):
        OssieParser().parse(path)


@pytest.mark.parametrize("datatype", ["String", "Date"])
def test_a_bare_key_must_match_the_column_datatype(tmp_path: Path, datatype: str):
    """Store is keyed by `StoreNr`, an `Integer`; a column declared otherwise
    cannot key it."""
    path = _write_mapped_spec(
        tmp_path, [*_KEY_TYPES, _keyed_entity("Store", "StoreNr")], [_key("Store")], datatype=datatype,
    )

    with pytest.raises(ValueError) as excinfo:
        OssieParser().parse(path)
    assert str(excinfo.value) == (
        f"Field 'T.id' is declared '{datatype}' but this mapping reads it as 'StoreNr', which "
        f"extends 'Integer'. A '{datatype}' column cannot hold 'Integer' values."
    )


@pytest.mark.parametrize("datatype", ["DateTime", "DateTimeTz"])
def test_a_timestamp_column_can_key_a_datetime_value_type(tmp_path: Path, datatype: str):
    """`DateTimeTz` is read as `DateTime`, as pyrel declares the column."""
    path = _write_mapped_spec(
        tmp_path,
        [{"concept": "Opened", "type": "ValueType", "extends": ["DateTime"]}, _keyed_entity("Store", "Opened")],
        [_key("Store")],
        datatype=datatype,
    )

    OssieParser().parse(path)


def test_a_referent_key_must_match_the_column_datatype(tmp_path: Path):
    """A referent mapping reads its column as the identifying role's player."""
    referent = {"referent_mappings": [{"relationship": "nr", "expression": "T.id"}]}
    path = _write_mapped_spec(
        tmp_path, [*_KEY_TYPES, _keyed_entity("Store", "StoreNr")], [_key("Store", referent)], datatype="String",
    )

    with pytest.raises(ValueError, match=(
        r"^Field 'T\.id' is declared 'String' but this mapping reads it as 'StoreNr', which extends "
        r"'Integer'\. A 'String' column cannot hold 'Integer' values\.$"
    )):
        OssieParser().parse(path)


def _store_with_code(code_mapping: dict, datatype: str) -> list:
    """Store keyed from the untyped `T.key`, its `code` (a `StoreNr`) read from
    `T.id` declared *datatype* through *code_mapping*."""
    code = {
        "name": "code",
        "roles": [{"concept": "StoreNr", "name": "c"}],
        "verbalizes": ["{Store} has {StoreNr:c}"],
        "multiplicity": "ManyToOne",
    }
    return [
        [*_KEY_TYPES, _keyed_entity("Store", "StoreNr", code)],
        [{
            "concept": "Store",
            "link_mappings": [{
                "object_mapping": {"expression": "T.key"},
                "children": [{"relationship": "code", "object_mapping": code_mapping}],
            }],
        }],
        datatype,
    ]


@pytest.mark.parametrize(
    "code_mapping, read_as",
    [
        ({"expression": "T.id"}, "StoreNr"),  # no concept: the role player
        ({"concept": "StoreNr", "expression": "T.id"}, "StoreNr"),  # cast to a custom value type
        ({"concept": "Integer", "expression": "T.id"}, "Integer"),  # a builtin, read as is
    ],
    ids=["no-concept", "custom-value-type", "builtin"],
)
def test_a_concept_does_not_exempt_the_column_from_its_datatype(tmp_path: Path, code_mapping: dict, read_as: str):
    """Naming the type does not make a `String` column hold `Integer`s: even
    the cast `StoreNr(T.id)` needs a column of StoreNr's base type."""
    concepts, mappings, datatype = _store_with_code(code_mapping, "String")
    path = _write_mapped_spec(tmp_path, concepts, mappings, datatype=datatype, untyped_columns=("key",))

    with pytest.raises(ValueError, match=rf"^Field 'T\.id' is declared 'String' but this mapping reads it as '{read_as}'"):
        OssieParser().parse(path)


@pytest.mark.parametrize(
    "code_mapping",
    [{"expression": "T.id"}, {"concept": "StoreNr", "expression": "T.id"}, {"concept": "Integer", "expression": "T.id"}],
    ids=["no-concept", "custom-value-type", "builtin"],
)
def test_a_concept_of_the_column_base_type_is_accepted(tmp_path: Path, code_mapping: dict):
    concepts, mappings, datatype = _store_with_code(code_mapping, "Integer")
    path = _write_mapped_spec(tmp_path, concepts, mappings, datatype=datatype, untyped_columns=("key",))

    OssieParser().parse(path)


def test_an_entity_concept_checks_the_column_against_its_key_type(tmp_path: Path):
    """`concept: Store` names the entity the column identifies, so the column
    is read as Store's key type, `StoreNr`, not as `Store`."""
    path = _write_mapped_spec(
        tmp_path, [*_KEY_TYPES, _keyed_entity("Store", "StoreNr")],
        [_key("Store", {"concept": "Store", "expression": "T.id"})], datatype="String",
    )

    with pytest.raises(ValueError, match=r"^Field 'T\.id' is declared 'String' but this mapping reads it as 'StoreNr'"):
        OssieParser().parse(path)


@pytest.mark.parametrize("datatype", [None, "Opaque"], ids=["no-datatype", "opaque"])
def test_a_column_without_a_portable_datatype_is_not_checked(tmp_path: Path, datatype: str | None):
    path = _write_mapped_spec(
        tmp_path, [*_KEY_TYPES, _keyed_entity("Store", "StoreNr")],
        [_key("Store", {"concept": "Store", "expression": "T.id"})], datatype=datatype,
    )

    OssieParser().parse(path)


# ----- value-type wrappers in mapping expressions ------------------------

_FLAGSHIP_NR = {"concept": "FlagshipNr", "type": "ValueType", "extends": ["StoreNr"]}


def _wrapped_key_spec(tmp_path: Path, expression: str, datatype: str | None = "Integer") -> Path:
    return _write_mapped_spec(
        tmp_path,
        [*_KEY_TYPES, _FLAGSHIP_NR, _keyed_entity("Store", "StoreNr")],
        [_key("Store", {"expression": expression})],
        datatype=datatype,
    )


@pytest.mark.parametrize(
    "expression, datatype",
    [
        ("StoreNr(T.id)", "Integer"),
        ("StoreNr(T.id)", None),  # nothing declared to check against
        ("FlagshipNr(T.id)", "Integer"),  # a subtype of the expected StoreNr
        ("StoreNr(T.id + 1)", "Integer"),  # any mapping expression may be wrapped
    ],
    ids=["qualified", "untyped-column", "subtype", "arithmetic"],
)
def test_a_value_type_wrapper_is_accepted(tmp_path: Path, expression: str, datatype: str | None):
    """`StoreNr(T.id)` reads the column as a `StoreNr`, so it can key Store."""
    model = OssieParser().parse(_wrapped_key_spec(tmp_path, expression, datatype))

    mapping = model.ontology_mappings[0].concept_mappings[0].object_mappings[0]
    assert isinstance(mapping.expression, Formula)
    assert mapping.expression.raw_expr == expression


def test_a_wrapped_column_must_have_the_wrapper_base_type(tmp_path: Path):
    """The wrapper is a cast, not a conversion: a `String` column wrapped in
    `StoreNr` still cannot hold `Integer`s."""
    with pytest.raises(ValueError, match=(
        r"^Field 'T\.id' is declared 'String' but this mapping reads it as 'StoreNr', which extends "
        r"'Integer'\. A 'String' column cannot hold 'Integer' values\.$"
    )):
        OssieParser().parse(_wrapped_key_spec(tmp_path, "StoreNr(T.id)", "String"))


def test_a_wrapper_must_be_the_type_the_mapping_reads(tmp_path: Path):
    """Store is keyed by `StoreNr`; a `WarehouseNr` is not one, base type or not."""
    with pytest.raises(ValueError, match=(
        r"^Mapping expression 'WarehouseNr\(T\.id\)' wraps its value as 'WarehouseNr', "
        r"but this mapping reads it as 'StoreNr'\.$"
    )):
        OssieParser().parse(_wrapped_key_spec(tmp_path, "WarehouseNr(T.id)"))


@pytest.mark.parametrize(
    "expression, message",
    [
        ("Store(T.id)", r"Only a declared value type can wrap a mapping expression, not 'Store'"),
        ("Integer(T.id)", r"Only a declared value type can wrap a mapping expression, not 'Integer'"),
        ("StoreNr(T.id, T.id)", r"The value type 'StoreNr' wraps exactly one expression, got 2"),
        ("StoreNr(T.id) + 1", r"A value-type wrapper must enclose the whole mapping expression"),
        ("StoreNr(StoreNr(T.id))", r"A value-type wrapper must enclose the whole mapping expression"),
    ],
    ids=["entity", "builtin", "two-arguments", "not-whole", "nested"],
)
def test_a_malformed_wrapper_is_rejected(tmp_path: Path, expression: str, message: str):
    with pytest.raises(ValueError, match=message):
        OssieParser().parse(_wrapped_key_spec(tmp_path, expression))


def test_is_primitive_terminates_on_a_cyclic_extends_chain():
    """The property answers rather than exhausting the stack.

    `is_primitive` follows a chain of single-parent `extends` looking for a
    builtin at the root. It used to recurse, so a hand-built cycle raised
    RecursionError from a property — and from inside `OntologyReasoner`, which
    asks it for every concept it ingests. No converter can produce a cycle
    (`spec_to_ossie` rejects them, `palantir_to_ossie` breaks them), but
    `Concept.extend()` is public API.
    """
    a = Concept(name="A", type=ConceptType.ENTITY_TYPE)
    b = Concept(name="B", type=ConceptType.ENTITY_TYPE, extends=[a])
    a.extend(b)  # A extends B extends A

    # No builtin anywhere on the cycle, so neither is primitive.
    assert a.is_primitive is False
    assert b.is_primitive is False


def test_is_primitive_still_follows_a_chain_to_its_builtin_root():
    """The guard must not break the case the property exists for."""
    ontology = OntologyComponent()
    string = ontology.ensure_builtin_concept("String")
    assert string is not None

    code = Concept(name="Code", type=ConceptType.VALUE_TYPE, extends=[string])
    sku = Concept(name="Sku", type=ConceptType.VALUE_TYPE, extends=[code])

    assert code.is_primitive is True
    assert sku.is_primitive is True
    # Two parents is not a chain, so not primitive — unchanged by the guard.
    assert Concept(name="Pair", type=ConceptType.VALUE_TYPE, extends=[code, sku]).is_primitive is False


# ----- Global identifiers -----------------------------------------------

_IRI_SPEC = {
    "version": "0.2.0.dev0",
    "name": "Demo",
    "prefixes": {"foaf": "http://xmlns.com/foaf/0.1/"},
    "ontology": [
        {
            "concept": "Person",
            "type": "EntityType",
            "iri": "foaf:Person",
            "identify_by": ["person_name"],
            "relationships": [
                {
                    "name": "person_name",
                    "iri": "foaf:name",
                    "roles": [{"concept": "String"}],
                    "verbalizes": ["{Person} is identified by {String}"],
                    "multiplicity": "OneToOne",
                }
            ],
        }
    ],
}


def test_iri_and_prefixes_survive_a_round_trip(tmp_path: Path):
    """A document that pins its concepts to an external vocabulary must come
    back out carrying the same pins.

    `iri` and `prefixes` are what tie an Ossie ontology to RDF/OWL vocabularies,
    so dropping them silently turns a mapped ontology into an unmapped one.
    """
    path = tmp_path / "spec.yaml"
    path.write_text(yaml.safe_dump(_IRI_SPEC))

    model = OssieParser().parse(path)

    assert model.prefixes == {"foaf": "http://xmlns.com/foaf/0.1/"}
    person = model.ontology.lookup_concept("Person")
    assert person is not None
    assert person.iri == "foaf:Person"
    person_name = model.ontology.lookup_concept_relationship(person, "person_name")
    assert person_name is not None
    assert person_name.iri == "foaf:name"

    roundtrip_path = tmp_path / "roundtrip.yaml"
    roundtrip_path.write_text(OssieToSpecConverter.convert(model).dump_yaml())
    reparsed = OssieParser().parse(roundtrip_path)

    assert reparsed.prefixes == model.prefixes
    reparsed_person = reparsed.ontology.lookup_concept("Person")
    assert reparsed_person is not None
    assert reparsed_person.iri == "foaf:Person"
    reparsed_name = reparsed.ontology.lookup_concept_relationship(reparsed_person, "person_name")
    assert reparsed_name is not None
    assert reparsed_name.iri == "foaf:name"


def test_a_document_without_iris_dumps_neither_field(tmp_path: Path):
    """Both fields are optional, so a document that declares neither must dump
    exactly as it did before they existed."""
    path = _write_spec(tmp_path, [_concept("Gadget")])

    dumped = OssieToSpecConverter.convert(OssieParser().parse(path)).dump_yaml()

    assert "iri:" not in dumped
    assert "prefixes:" not in dumped
