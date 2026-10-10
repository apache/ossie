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

"""Tests for Role identity.

The hard case is a self-relationship whose roles carry no names — legal, since a
role name is only required to disambiguate. Its two roles then agree on
relationship, player and name, so position is the only thing telling them apart:
without it they compare equal, `sibling` hands back the role it was asked from,
and both collapse into one entry of any set keyed by Role.
"""

from __future__ import annotations

import pytest

from ossie_ontology.model import Concept, ConceptType, Relationship


@pytest.fixture
def person() -> Concept:
    return Concept(name="Person", type=ConceptType.ENTITY_TYPE)


def test_unnamed_roles_of_a_self_relationship_are_distinct(person: Concept):
    knows = Relationship(name="knows", container=person, relates=[(person, None)])
    first, second = knows.roles

    assert first != second
    assert (first.idx, second.idx) == (0, 1)
    assert len({first, second}) == 2


def test_sibling_of_each_role_is_the_other_end(person: Concept):
    """Traversal has to be able to cross the relationship from either side."""
    knows = Relationship(name="knows", container=person, relates=[(person, None)])
    first, second = knows.roles

    assert first.sibling is second
    assert second.sibling is first


def test_named_roles_of_a_self_relationship_are_distinct(person: Concept):
    """The case a role name was there to disambiguate keeps working."""
    knows = Relationship(name="knows", container=person, relates=[(person, "snd")])
    first, second = knows.roles

    assert first != second
    assert len({first, second}) == 2
    assert first.sibling is second
    assert second.sibling is first


def test_roles_of_different_relationships_are_not_equal(person: Concept):
    knows = Relationship(name="knows", container=person, relates=[(person, None)])
    manages = Relationship(name="manages", container=person, relates=[(person, None)])

    assert knows.role(0) != manages.role(0)
    assert len({knows.role(0), manages.role(0)}) == 2


def test_a_role_equals_itself(person: Concept):
    other = Concept(name="Company", type=ConceptType.ENTITY_TYPE)
    works_at = Relationship(name="works_at", container=person, relates=[(other, None)])

    assert works_at.role(0) == works_at.role(0)
    assert len({works_at.role(0), works_at.role(0)}) == 1


def test_role_lookup_by_concept_cannot_disambiguate_a_self_relationship(person: Concept):
    """Documenting a limit rather than a fix: both roles have the same player, so
    a concept alone names no single one and the first is returned. Callers that
    need a specific end index it, or give the role a name."""
    knows = Relationship(name="knows", container=person, relates=[(person, None)])

    assert knows.role(person) is knows.role(0)
    assert knows.role(1).idx == 1
