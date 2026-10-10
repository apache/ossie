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

"""OntologyReasoner, on its own.

Nothing here needs the optional `relationalai` extra: the reasoner reads the
Ossie model and answers questions about it. The converter-side consequences of
those answers are asserted in tests/relationalai/test_converter.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ossie_ontology.model import Concept, ConceptType, OssieOntology
from ossie_ontology.parser import OssieParser
from ossie_ontology.reasoner import OntologyReasoner


@pytest.fixture
def reasoner_for(specs_dir: Path):
    """Parse a fixture spec and return it with a reasoner registered on it."""

    def build(spec: str) -> tuple[OssieOntology, OntologyReasoner]:
        ontology = OssieParser().parse(specs_dir / f"{spec}.yaml")
        reasoner = OntologyReasoner()
        ontology.ontology.register(reasoner)
        return ontology, reasoner

    return build


def names(scheme) -> tuple[str, ...] | None:
    return None if scheme is None else tuple(rel.full_name for rel in scheme)


# ---------------------------------------------------------------------------
# ref_scheme
# ---------------------------------------------------------------------------

def test_ref_scheme_returns_a_concepts_own_identifiers(reasoner_for):
    """The declared `identify_by`, in declaration order."""
    ontology, reasoner = reasoner_for("retail")
    employee = ontology.ontology.lookup_concept("Employee")

    assert names(reasoner.ref_scheme(employee)) == ("Employee.nr",)


def test_ref_scheme_returns_every_relationship_of_a_composite_identifier(reasoner_for):
    """A composite scheme comes back whole, not just its first element.

    Both converter call sites branch on `len(scheme) == 1` and refuse the
    composite case with a diagnostic, so a scheme that silently truncated to one
    relationship would turn a clear error into a wrong entity key.
    """
    ontology, reasoner = reasoner_for("flights")
    runway = ontology.ontology.lookup_concept("Runway")

    assert names(reasoner.ref_scheme(runway)) == ("Runway.designator", "Runway.airport")


def test_ref_scheme_inherits_from_the_supertype(reasoner_for):
    """A subtype with no identifier of its own answers with its parent's.

    `shallow=True` is the way to ask for only what the concept itself declares,
    and is the difference between "this concept has no identifier" and "this
    concept declares no identifier" — which are not the same question.
    """
    ontology, reasoner = reasoner_for("retail")
    contractor = ontology.ontology.lookup_concept("Contractor")

    assert names(reasoner.ref_scheme(contractor)) == ("Employee.nr",)
    assert reasoner.ref_scheme(contractor, shallow=True) is None


def test_ref_scheme_is_none_when_nothing_identifies_the_concept(reasoner_for):
    """None, not an empty tuple.

    Both callers lean on the falsiness rather than the type — the formula
    emitter writes `ref_scheme(...) or ()` — but the converter reports
    "has no reference scheme" off the same value, so the empty case has to stay
    falsy whichever way it is spelled.
    """
    ontology, reasoner = reasoner_for("retail")
    builtin = ontology.ontology.lookup_concept("String")

    assert reasoner.ref_scheme(builtin) is None


# ---------------------------------------------------------------------------
# in_subtype_closure
# ---------------------------------------------------------------------------

def linear_hierarchy() -> dict[str, Concept]:
    """C extends B extends A — two hops, so the answer is transitive, not direct."""
    a = Concept(name="A", type=ConceptType.ENTITY_TYPE)
    b = Concept(name="B", type=ConceptType.ENTITY_TYPE, extends=[a])
    c = Concept(name="C", type=ConceptType.ENTITY_TYPE, extends=[b])
    return {"A": a, "B": b, "C": c}


@pytest.mark.parametrize("arrival", [["A", "B", "C"], ["C", "B", "A"]])
def test_subtype_closure_does_not_depend_on_registration_order(arrival: list[str]):
    """Registration order must not change the answer.

    It used to. The closure was indexed as concepts arrived, and building it
    that way only sees a parent's own supertypes if the parent was registered
    first — so registering C before A lost the transitive C -> A edge and the
    predicate answered False. `spec_to_ossie` topologically sorts its concepts,
    which hid this; `palantir_to_ossie` breaks cycles and any caller may build
    an OntologyComponent by hand, so neither is protected by that accident.
    """
    concepts = linear_hierarchy()
    reasoner = OntologyReasoner()
    for name in arrival:
        reasoner.new_concept(concepts[name])

    assert reasoner.in_subtype_closure(concepts["C"], concepts["A"])
    assert reasoner.in_subtype_closure(concepts["B"], concepts["A"])
    # Strict: a concept is not its own subtype, and the relation has a direction.
    assert not reasoner.in_subtype_closure(concepts["A"], concepts["A"])
    assert not reasoner.in_subtype_closure(concepts["A"], concepts["C"])


# ---------------------------------------------------------------------------
# Malformed input: cyclic `extends`
# ---------------------------------------------------------------------------

def test_a_cycle_in_extends_terminates_instead_of_recursing():
    """Both walks stop on a cycle rather than exhausting the stack.

    `spec_to_ossie` rejects cycles and `palantir_to_ossie` breaks them, so no
    converter here can produce one — but `Concept.extend()` is public and this
    is a library, so the failure mode for a hand-built model should be an
    answer, not a thousand-frame traceback. Both queries treat an `extends`
    edge that leads nowhere new as contributing nothing, which is the only
    reading a cycle supports.

    Ingestion is covered too: `new_concept` asks `Concept.is_primitive`, which
    walks `extends` on the model side and used to recurse away on a cycle
    before the reasoner was asked anything at all.
    """
    a = Concept(name="A", type=ConceptType.ENTITY_TYPE)
    b = Concept(name="B", type=ConceptType.ENTITY_TYPE, extends=[a])
    a.extend(b)  # A extends B extends A
    unrelated = Concept(name="Unrelated", type=ConceptType.ENTITY_TYPE)

    reasoner = OntologyReasoner()
    reasoner.new_concept(a)
    reasoner.new_concept(b)

    assert reasoner.in_subtype_closure(a, unrelated) is False
    assert OntologyReasoner.ref_scheme(a) is None
    # The cycle itself is still a subtype edge, and is reported as one.
    assert reasoner.in_subtype_closure(a, b)
    assert reasoner.in_subtype_closure(b, a)


def test_a_long_extends_chain_does_not_exhaust_the_stack():
    """Depth is bounded by the model, not by Python's recursion limit.

    `in_subtype_closure` walks iteratively for this reason; a chain deeper than
    the interpreter's limit is pathological but it should not be the thing that
    decides whether the query works.
    """
    chain = [Concept(name="C0", type=ConceptType.ENTITY_TYPE)]
    for i in range(1, 2000):
        chain.append(Concept(name=f"C{i}", type=ConceptType.ENTITY_TYPE, extends=[chain[-1]]))

    reasoner = OntologyReasoner()
    assert reasoner.in_subtype_closure(chain[-1], chain[0])
    assert not reasoner.in_subtype_closure(chain[0], chain[-1])
