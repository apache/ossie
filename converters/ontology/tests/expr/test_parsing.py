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

"""End-to-end coverage for the formula parser, validator, and the two factories
that plug them into `OssieParser`.

`FormulaFactory` and `MappingFormulaFactory` in `ossie_ontology.model` keep a
formula's raw text and nothing else. Passing the parsing factories from
`ossie_ontology.expr.factory` to `OssieParser` swaps in the real thing: the
expression is lexed, parsed into an AST, and validated against the ontology (and,
for mapping expressions, against the semantic model's dataset columns). Every
case below goes through `OssieParser`, so it covers that whole path rather than
the validator alone — `test_formula_validator.py` handles the one check that
cannot be reached this way.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from ossie_ontology.model import Formula, OssieOntology
from ossie_ontology.parser import OssieParser



def parse(validation_dir: Path, fixture: str) -> OssieOntology:
    """Parse a fixture. `OssieParser` parses and validates formulas by default."""
    return OssieParser().parse(validation_dir / fixture)


# ---------------------------------------------------------------------------
# Rule formulas: `derived_by` and `requires`
# ---------------------------------------------------------------------------

RULE_ERRORS = [
    (
        "ambiguous_type_vars.yaml",
        "Relationship 'ssn' signature mismatch in the formula "
        "'Person.earns(var_name) AND Person.ssn(var_name)': "
        "expected (Person, SocialSecurityNr) got (Person, Salary)",
    ),
    (
        "undefined_relationship.yaml",
        "Undefined relationship 'Person.gain' in the formula 'Person.gain(Salary)'",
    ),
    (
        "unresolved_var_type.yaml",
        "Unresolved reference 's' in the formula 's > 0 AND Person.earns(Salary)'. "
        "If 's' is a dataset field, use the format 'DATASET_NAME.s'.",
    ),
    (
        "not_declared_concept_in_relationship.yaml",
        "Role concept 'City' in relationship 'Person.live_in' is not declared in the ontology.",
    ),
    (
        "not_declared_concept_in_identifier.yaml",
        "identify_by 'ssn' on concept 'Person' refers to an unknown relationship "
        "in ontology 'Sample model'.",
    ),
    (
        "signature_mismatch.yaml",
        "Relationship 'ssn' signature mismatch in the formula "
        "'Person.earns(Salary) AND Person.ssn(Salary)': "
        "expected (Person, SocialSecurityNr) got (Person, Salary)",
    ),
    (
        "bare_field_in_formula.yaml",
        "Unresolved reference 'sal' in the formula 'Person.earns(Salary(sal))'. "
        "If 'sal' is a dataset field, use the format 'DATASET_NAME.sal'.",
    ),
    (
        "top_level_require_unknown_concept.yaml",
        "Unresolved reference 'Nonexistent' in the formula 'COUNT[Nonexistent] > 0'. "
        "If 'Nonexistent' is a dataset field, use the format 'DATASET_NAME.Nonexistent'.",
    ),
    (
        "top_level_require_free_var.yaml",
        "Unbound reference(s) 'Person' in the ontology-level constraint 'Person.ssn > 0'. "
        "An ontology-level `requires` has no parent scope to bind variables against, so "
        "every reference must be bound within the formula — by an aggregate (e.g. COUNT[...]) "
        "or by wrapping it in EXISTS(...), e.g. 'EXISTS(Person.ssn > 0)'.",
    ),
    (
        "require_sub_exists_unknown_rel.yaml",
        "Undefined relationship 'Company.turnover' in the formula "
        "'Person.name AND EXISTS(Company.turnover > 0)'",
    ),
]


@pytest.mark.parametrize("fixture,message", RULE_ERRORS, ids=[c[0] for c in RULE_ERRORS])
def test_invalid_rule_formula_is_rejected(validation_dir: Path, fixture: str, message: str):
    with pytest.raises(ValueError, match=re.escape(message)):
        parse(validation_dir, fixture)


@pytest.mark.parametrize(
    "fixture",
    [
        # An ontology-level `requires` has no parent scope, but is still parsed
        # and validated — here a bare COUNT aggregate over a concept.
        "top_level_require_valid.yaml",
        # Free variables in a top-level `requires` become legal once EXISTS(...)
        # binds them.
        "top_level_require_exists_valid.yaml",
        # A sub-EXISTS inside a concept- or relationship-level `require` (parent
        # scope is not None): the concept require mixes a head-scoped term with an
        # EXISTS over another concept, the relationship require is a bare EXISTS.
        # The EXISTS body is fully validated either way.
        "require_sub_exists_valid.yaml",
        # Scope binding in depth: a second concept reached through a fact is
        # bound without EXISTS, in either conjunct order; nested binders bind
        # what they enclose; scope travels along a chain of facts. The fixture
        # documents each case inline; nothing referenced it before, so this is
        # the first thing that runs it.
        "scoped_require_scope_valid.yaml",
    ],
)
def test_valid_rule_formula_parses(validation_dir: Path, fixture: str):
    assert parse(validation_dir, fixture) is not None


# ---------------------------------------------------------------------------
# Mapping formulas
# ---------------------------------------------------------------------------

MAPPING_ERRORS = [
    (
        "mapping_formula_unknown_field.yaml",
        "Undefined column 'unknownField' for dataset 'PAYMENTS'",
    ),
    (
        "mapping_formula_agg_with_rel.yaml",
        "Relationship references are not allowed in mapping expressions: 'grossReturns' "
        "in 'SUM[SALES.amount WHERE Item.grossReturns(Store) > 0 GROUP BY SALES.item_id]'.",
    ),
    (
        "mapping_formula_concept_ref.yaml",
        "Concept references are not allowed in mapping expressions: 'Amount' "
        "in 'PAYMENTS.cashReceived + Amount'.",
    ),
]


@pytest.mark.parametrize("fixture,message", MAPPING_ERRORS, ids=[c[0] for c in MAPPING_ERRORS])
def test_invalid_mapping_formula_is_rejected(validation_dir: Path, fixture: str, message: str):
    with pytest.raises(ValueError, match=re.escape(message)):
        parse(validation_dir, fixture)


def test_mapping_formula_reaches_the_object_mapping_as_a_formula(validation_dir: Path):
    """A mapped property backed by an expression carries a parsed Formula.

    Under the plain `MappingFormulaFactory` the same field arrives as a bare
    `Formula` with no AST, so this asserts the parsing factory is the one the
    parser reached for by default.
    """
    model = parse(validation_dir, "mapping_formula_valid.yaml")

    mapping, = model.ontology_mappings
    assert mapping.concept_mappings is not None
    concept_mapping, = mapping.concept_mappings
    assert concept_mapping.concept is not None
    assert concept_mapping.concept.name == "CashPayment"

    link_mapping, = concept_mapping.link_mappings
    assert link_mapping.children is not None
    child, = link_mapping.children
    assert child.relationship is not None
    assert child.relationship.name == "netAmount"

    object_mapping = child.object_mapping
    assert object_mapping is not None
    assert isinstance(object_mapping.expression, Formula)
    assert object_mapping.expression.raw_expr == "PAYMENTS.cashReceived - PAYMENTS.change"


# ---------------------------------------------------------------------------
# The default wiring
# ---------------------------------------------------------------------------

def test_parsing_is_the_default(validation_dir: Path):
    """A bare `OssieParser()` parses and validates formulas.

    It used to keep them as raw text unless a caller passed the factories in,
    which meant an invalid formula parsed cleanly and was then skipped by every
    downstream converter — the error surfaced as a missing rule, if at all.
    """
    from ossie_ontology.expr.formula.model import Formula as ParsedFormula
    from ossie_ontology.parser import OssieParser

    model = OssieParser().parse(validation_dir / "top_level_require_valid.yaml")
    formulas = [f for c in model.ontology.concepts(exclude_builtin=True) for f in c.requires]
    formulas += list(model.ontology.requires)
    assert formulas, "fixture should declare at least one constraint"
    assert all(isinstance(f, ParsedFormula) for f in formulas)


def test_default_parser_rejects_an_invalid_formula(validation_dir: Path):
    """The validation above is reached without the caller opting in."""
    from ossie_ontology.parser import OssieParser

    with pytest.raises(ValueError, match=re.escape("Undefined relationship 'Person.gain'")):
        OssieParser().parse(validation_dir / "undefined_relationship.yaml")


def test_raw_factories_remain_available_as_an_opt_out(validation_dir: Path):
    """Passing the plain factories still skips parsing entirely."""
    from ossie_ontology.model import Formula, FormulaFactory, MappingFormulaFactory
    from ossie_ontology.parser import OssieParser

    # Would raise under the default factories; the raw ones keep it as text.
    model = OssieParser(
        formula_factory=FormulaFactory(),
        mapping_formula_factory=MappingFormulaFactory(),
    ).parse(validation_dir / "undefined_relationship.yaml")

    # The fixture's formula is a relationship `derived_by`, not a concept rule.
    formulas = [f for r in model.ontology.relationships for f in (*r.derived_by, *r.requires)]
    assert formulas and all(type(f) is Formula for f in formulas)
