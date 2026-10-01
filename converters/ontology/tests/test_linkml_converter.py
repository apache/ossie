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

"""Tests for the Ossie <-> LinkML converters.

The translation itself lives in `linkml_scala` (the `neverblink-linkml`
package), which has its own test suite: 400+ tests, including unit tests,
integration with Ossie JSON Schema and validator, round-trip tests, and
expression parser fuzzing.

The tests here cover the integration: that the wiring works correctly and
that a round-trip through LinkML keeps the ontology intact."""

from __future__ import annotations

import json
import re

import linkml_scala
import pytest
import yaml

from ossie_ontology.converter.linkml_to_ossie.converter import LinkmlToOssieConverter
from ossie_ontology.converter.ossie_to_linkml.converter import OssieToLinkmlConverter
from ossie_ontology.converter.spec_to_ossie.converter import SpecToOssieConverter
from ossie_ontology.model import OssieOntology
from ossie_ontology.spec import OssieSpec


@pytest.fixture
def flights_linkml(flights_model: OssieOntology) -> str:
    return OssieToLinkmlConverter.convert(flights_model)


# ----- Ossie -> LinkML ---------------------------------------------------

def test_schema_loads_without_errors(flights_linkml: str):
    with linkml_scala.load_string(flights_linkml) as schema:
        # A schema can load and still have errors against it, so check the
        # report rather than just that the load returned.
        assert schema.issues(linkml_scala.ERROR) == []
        assert schema.issues(linkml_scala.FATAL) == []


def test_schema_carries_concepts(flights_linkml: str):
    schema = yaml.safe_load(flights_linkml)
    assert schema["name"] == "Flights"
    assert schema["description"] == "Ontology of flights into and out of airports."
    # Entity types become classes, value types become types.
    assert {"Airport", "Flight", "Carrier"} <= set(schema["classes"])
    assert {"CancelationCode", "DegreesLatitude"} <= set(schema["types"])
    # Relationships under a concept become that class's attributes.
    assert "code" in schema["classes"]["Airport"]["attributes"]


def test_schema_id_defaults_and_can_be_overridden(flights_model: OssieOntology):
    # An Ossie ontology has no id of its own, so one is made up from its name.
    assert yaml.safe_load(OssieToLinkmlConverter.convert(flights_model))["id"] == "https://example.org/flights"

    given = "https://ossie.apache.org/flights"
    assert yaml.safe_load(OssieToLinkmlConverter.convert(flights_model, schema_id=given))["id"] == given


def test_json_output_format(flights_model: OssieOntology):
    schema = json.loads(OssieToLinkmlConverter.convert(flights_model, output_format="json"))
    assert schema["name"] == "Flights"


def test_convert_spec_matches_convert(flights_model: OssieOntology, flights_path):
    """Going straight from the spec DTO describes the same schema as going via
    the runtime model.

    Compared as parsed YAML, not as text: building the runtime model sorts
    concepts topologically, so the two agree on content but not on the order
    classes and types are emitted in.
    """
    spec = OssieSpec.load_yaml(flights_path.read_text(encoding="utf-8"))
    assert yaml.safe_load(OssieToLinkmlConverter.convert_spec(spec)) == yaml.safe_load(
        OssieToLinkmlConverter.convert(flights_model)
    )


# ----- LinkML -> Ossie ---------------------------------------------------

def test_convert_text_returns_model(flights_linkml: str):
    model = LinkmlToOssieConverter().convert_text(flights_linkml)
    assert model.name == "Flights"
    assert model.version == "0.2.0.dev0"
    assert model.description == "Ontology of flights into and out of airports."


def test_convert_file_reads_from_disk(flights_linkml: str, tmp_path):
    path = tmp_path / "flights.linkml.yaml"
    path.write_text(flights_linkml, encoding="utf-8")
    assert LinkmlToOssieConverter().convert_file(path).name == "Flights"


def test_convert_to_spec_stops_at_the_dto(flights_linkml: str):
    with linkml_scala.load_string(flights_linkml) as schema:
        spec = LinkmlToOssieConverter().convert_to_spec(schema)
    assert isinstance(spec, OssieSpec)
    assert spec.name == "Flights"


def test_converters_do_not_share_formula_factories():
    a, b = LinkmlToOssieConverter(), LinkmlToOssieConverter()
    assert a._formula_factory is not b._formula_factory
    assert a._mapping_formula_factory is not b._mapping_formula_factory


# ----- Round-trip --------------------------------------------------------

def test_roundtrip_preserves_concepts_and_relationships(flights_model: OssieOntology, flights_linkml: str):
    back = LinkmlToOssieConverter().convert_text(flights_linkml)

    def names(model: OssieOntology) -> tuple[set[str], set[str]]:
        ontology = model.ontology
        return (
            {c.name for c in ontology.concepts(exclude_builtin=True)},
            {r.full_name for r in ontology.relationships},
        )

    assert names(back) == names(flights_model)


def test_roundtrip_preserves_concept_types_and_identifiers(flights_model: OssieOntology, flights_linkml: str):
    back = LinkmlToOssieConverter().convert_text(flights_linkml)
    for concept in flights_model.ontology.concepts(exclude_builtin=True):
        returned = back.ontology.lookup_concept(concept.name)
        assert returned is not None, concept.name
        assert returned.type == concept.type, concept.name
        assert set(returned.identify_by) == set(concept.identify_by), concept.name


def test_roundtrip_drops_ontology_mappings(flights_model: OssieOntology, flights_linkml: str):
    # Documents a known loss: LinkML describes types and their slots, so there
    # is nowhere to put datasets, join paths or metrics.
    assert flights_model.ontology_mappings != []
    assert LinkmlToOssieConverter().convert_text(flights_linkml).ontology_mappings == []


# ----- One full example, in both YAML formats ----------------------------

# The two documents below describe the same little ontology: one in Ossie's
# YAML, one in LinkML's. They convert into each other exactly:
#
#   Ossie                               LinkML
#   ----------------------------------  ------------------------------------
#   name, description                   name, description
#   (nothing)                           id, imports, default_range
#   prefixes                            prefixes
#   concept, type: ValueType            an entry under `types`
#   extends: [ Integer ]                typeof: integer
#   requires: [ NrPages >= 1 ]          minimum_value: 1
#   iri on a value type                 that type's uri
#   concept, type: EntityType           an entry under `classes`
#   iri on an entity type               class_uri
#   relationships                       that class's `attributes`
#   iri on a relationship               slot_uri
#   roles: [ { concept: Isbn } ]        range: Isbn
#   verbalizes                          title (the phrase, placeholders cut)
#   identify_by: [ isbn ]               identifier: true on that attribute
#   requires: [ Book.isbn ]             required: true on that attribute
#   multiplicity: OneToOne, ManyToOne   a single-valued attribute
#   no multiplicity                     multivalued: true
#   a camelCase relationship name       a snake_case slot, alias keeps the
#                                       original spelling (the slot_uri is
#                                       built from the snake_case name)
#   the order relationships are in      rank

EXAMPLE_OSSIE = """\
version: 0.2.0.dev0
name: Books
description: A tiny ontology of books and the people who wrote them.
prefixes:
  ex: https://example.org/books/
  xsd: http://www.w3.org/2001/XMLSchema#
ontology:
- concept: Author
  type: EntityType
  iri: ex:Author
  description: A person who wrote a book.
  identify_by:
  - name
  requires:
  - Author.name
  relationships:
  - name: name
    iri: ex:name
    roles:
    - concept: String
    verbalizes:
    - '{Author} is identified by {String}'
    multiplicity: OneToOne
- concept: Book
  type: EntityType
  iri: ex:Book
  identify_by:
  - isbn
  requires:
  - Book.isbn
  relationships:
  - name: isbn
    iri: ex:isbn
    roles:
    - concept: Isbn
    verbalizes:
    - '{Book} is identified by {Isbn}'
    multiplicity: OneToOne
  - name: pages
    iri: ex:pages
    roles:
    - concept: NrPages
    verbalizes:
    - '{Book} has pages- {NrPages}'
    multiplicity: ManyToOne
  - name: writtenBy
    iri: ex:written_by
    roles:
    - concept: Author
    verbalizes:
    - '{Book} is written by {Author}'
- concept: Isbn
  type: ValueType
  iri: xsd:string
  description: The identifier of a book.
  extends:
  - String
- concept: NrPages
  type: ValueType
  iri: xsd:integer
  extends:
  - Integer
  requires:
  - NrPages >= 1
"""

EXAMPLE_LINKML = """\
id: https://example.org/books
name: Books
description: A tiny ontology of books and the people who wrote them.
prefixes:
  linkml: https://w3id.org/linkml/
  ex: https://example.org/books/
  xsd: http://www.w3.org/2001/XMLSchema#
imports:
- linkml:types
default_range: string
classes:
  Book:
    class_uri: ex:Book
    attributes:
      isbn:
        slot_uri: ex:isbn
        title: is identified by
        identifier: true
        required: true
        rank: 1
        range: Isbn
      pages:
        slot_uri: ex:pages
        title: has pages-
        rank: 2
        range: NrPages
      written_by:
        slot_uri: ex:written_by
        title: is written by
        alias: writtenBy
        multivalued: true
        rank: 3
        range: Author
  Author:
    class_uri: ex:Author
    description: A person who wrote a book.
    attributes:
      name:
        slot_uri: ex:name
        title: is identified by
        identifier: true
        required: true
        rank: 1
        range: string
types:
  NrPages:
    uri: xsd:integer
    typeof: integer
    minimum_value: 1
  Isbn:
    uri: xsd:string
    description: The identifier of a book.
    typeof: string
"""


def test_full_example_ossie_to_linkml():
    """The Ossie document above converts to exactly the LinkML schema above."""
    model = SpecToOssieConverter().convert(OssieSpec.load_yaml(EXAMPLE_OSSIE))
    assert yaml.safe_load(OssieToLinkmlConverter.convert(model)) == yaml.safe_load(EXAMPLE_LINKML)


def test_full_example_linkml_to_ossie():
    """And back: the LinkML schema above converts to exactly the Ossie document."""
    with linkml_scala.load_string(EXAMPLE_LINKML) as schema:
        spec = LinkmlToOssieConverter().convert_to_spec(schema)
    assert yaml.safe_load(spec.dump_yaml()) == yaml.safe_load(EXAMPLE_OSSIE)


def test_iris_are_written_in_full_without_a_prefix_to_shorten_them():
    """The `ex:` and `xsd:` QNames above are a shortening, not a different IRI.

    Strip the two prefix declarations and the explicit URIs from the schema —
    leaving `linkml:`, which the import needs — and the same IRIs come back,
    written out in full, with no `prefixes` block to resolve them against.
    """
    stripped = EXAMPLE_LINKML
    for line in ("  ex: https://example.org/books/\n",
                 "  xsd: http://www.w3.org/2001/XMLSchema#\n"):
        assert line in stripped
        stripped = stripped.replace(line, "")
    stripped = re.sub(r"^ *(?:class_uri|slot_uri|uri): .*\n", "", stripped, flags=re.MULTILINE)

    with linkml_scala.load_string(stripped) as schema:
        spec = LinkmlToOssieConverter().convert_to_spec(schema)

    assert spec.prefixes == {}
    concepts = {c.concept: c for c in spec.ontology}
    # A class with no class_uri of its own is named by the schema id plus its name.
    assert concepts["Book"].iri == "https://example.org/books/Book"
    # A value type falls back to the XSD type it transitively extends.
    assert concepts["Isbn"].iri == "http://www.w3.org/2001/XMLSchema#string"
    # A slot URI is built from the snake_case slot name, not the alias.
    isbn, written_by = concepts["Book"].relationships[0], concepts["Book"].relationships[2]
    assert isbn.iri == "https://example.org/books/isbn"
    assert written_by.name == "writtenBy"
    assert written_by.iri == "https://example.org/books/written_by"
