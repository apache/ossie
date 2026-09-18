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

import pytest

from ossie_ontology.model import Concept, ConceptType, OntologyComponent, Relationship
from ossie_ontology.expr.formula.model import ConceptRefHandle, DotJoinHandle, RelationshipRefHandle
from ossie_ontology.expr.formula.validator import FormulaValidator
from ossie_ontology.reasoner import OntologyReasoner


class TestFormulaValidatorDotJoin:
    """FormulaValidator._validate_dot_join's container type check can't be reached through
    the OSI parser: a dotted relationship reference only resolves at parse time when the
    relationship is actually declared on the left-hand concept (or one of its ancestors),
    which already guarantees the check would pass. These tests call the validator directly
    with a hand-built AST to exercise the check in isolation."""

    # `_validate_dot_join` only consults the ontology on its VarHandle branch,
    # which these cases do not take — but passing None to a parameter typed
    # `OntologyComponent` says that badly. An empty component is just as inert
    # and keeps the call honest.
    def _reasoner_with(self, *concepts: Concept) -> OntologyReasoner:
        reasoner = OntologyReasoner()
        for concept in concepts:
            reasoner.new_concept(concept)
        return reasoner

    def test_dot_join_container_type_mismatch_raises(self):
        person = Concept(name="Person", type=ConceptType.ENTITY_TYPE)
        salary = Concept(name="Salary", type=ConceptType.VALUE_TYPE)
        company = Concept(name="Company", type=ConceptType.ENTITY_TYPE)  # unrelated to Person
        earns = Relationship(name="earns", container=person, relates=[(salary, None)])

        reasoner = self._reasoner_with(person, salary, company)
        dot_join = DotJoinHandle(ConceptRefHandle(company), RelationshipRefHandle(earns))

        with pytest.raises(
            ValueError,
            match=(
                r"Type mismatch for 'Company\.earns' in the formula 'Company\.earns'\. "
                r"Expected type in the relationship 'Person' got 'Company'"
            ),
        ):
            FormulaValidator._validate_dot_join(OntologyComponent(), reasoner, [dot_join], {}, "Company.earns")

    def test_dot_join_container_subtype_is_allowed(self):
        person = Concept(name="Person", type=ConceptType.ENTITY_TYPE)
        salary = Concept(name="Salary", type=ConceptType.VALUE_TYPE)
        employee = Concept(name="Employee", type=ConceptType.ENTITY_TYPE, extends=[person])
        earns = Relationship(name="earns", container=person, relates=[(salary, None)])

        reasoner = self._reasoner_with(person, salary, employee)
        dot_join = DotJoinHandle(ConceptRefHandle(employee), RelationshipRefHandle(earns))

        # Should not raise: Employee is a subtype of earns' container (Person).
        FormulaValidator._validate_dot_join(OntologyComponent(), reasoner, [dot_join], {}, "Employee.earns")