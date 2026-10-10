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

"""Tests for concept lookup on OntologyComponent.

Two operations, deliberately separate: `lookup_concept` answers whether a name
is taken and adds nothing, which is what callers testing for an existing concept
depend on; `ensure_builtin_concept` is the one that creates a builtin on demand,
since builtins are declared nowhere.
"""

from __future__ import annotations

from ossie_ontology.model import (
    BUILTIN_CONCEPTS,
    Concept,
    ConceptType,
    OntologyComponent,
)


def _concept_names(ontology: OntologyComponent) -> list[str]:
    return [c.name for c in ontology.concepts()]


def test_lookup_concept_finds_a_declared_concept():
    ontology = OntologyComponent()
    alpha = Concept(name="Alpha", type=ConceptType.ENTITY_TYPE)
    ontology.add_concept(alpha)

    assert ontology.lookup_concept("Alpha") is alpha


def test_lookup_concept_does_not_create_a_builtin():
    """The check `lookup_concept(name) is None` has to stay a question.

    A caller asking whether a name is free would otherwise take that name — and
    a concept named like a builtin would come back as the builtin it just made.
    """
    ontology = OntologyComponent()

    assert "Date" in BUILTIN_CONCEPTS
    assert ontology.lookup_concept("Date") is None
    assert _concept_names(ontology) == []


def test_lookup_concept_of_nothing_is_none():
    ontology = OntologyComponent()

    assert ontology.lookup_concept(None) is None
    assert ontology.lookup_concept("") is None


def test_ensure_builtin_concept_creates_it_once():
    ontology = OntologyComponent()

    first = ontology.ensure_builtin_concept("Date")
    assert first is not None
    assert first.is_builtin
    # A builtin is not emitted as a concept component of its own.
    assert not first.is_component

    assert ontology.ensure_builtin_concept("Date") is first
    assert _concept_names(ontology) == ["Date"]


def test_ensure_builtin_concept_returns_a_declared_concept_unchanged():
    ontology = OntologyComponent()
    alpha = Concept(name="Alpha", type=ConceptType.ENTITY_TYPE)
    ontology.add_concept(alpha)

    assert ontology.ensure_builtin_concept("Alpha") is alpha
    assert _concept_names(ontology) == ["Alpha"]


def test_ensure_builtin_concept_will_not_invent_a_non_builtin():
    ontology = OntologyComponent()

    assert ontology.ensure_builtin_concept("Widget") is None
    assert _concept_names(ontology) == []
