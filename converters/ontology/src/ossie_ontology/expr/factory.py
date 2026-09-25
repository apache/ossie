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

"""The factories that wire the expression parser into the model's formula hooks.

`ossie_ontology.model` declares `FormulaFactory` and `MappingFormulaFactory` as
hook points that keep a formula's raw text and nothing else. The two below fill
them in: they lex and parse the expression into an AST and validate it against
the ontology (and, for a mapping expression, against the semantic model's
columns). They are what `OssieParser`, `SpecToOssieConverter` and
`PalantirToOssieConverter` use unless a caller passes something else.

They sit under `expr` rather than `parser` because that is all they depend on --
`parser` imports `SpecToOssieConverter`, which would make a factory import from
there a cycle.
"""

from collections import OrderedDict
from typing import Callable

from ossie_ontology.expr.formula.model import (
    Var,
    Formula,
    ConceptRefHandle,
    VarHandle
)
from ossie_ontology.expr.formula.parser import FormulaParser
from ossie_ontology.expr.formula.visitor.collector import FormulaCollector
from ossie_ontology.model import (
    Concept,
    Relationship,
    Role,
    OntologyComponent,
    FormulaFactory,
    MappingFormulaFactory,
    FormulaParent,
    Formula as OSIFormula,
    SemanticModel
)
from ossie_ontology.expr.formula.validator import FormulaValidator, MappingFormulaValidator
from ossie_ontology.reasoner import OntologyReasoner

Container = Concept | Relationship
# What a formula can be scoped to: a concept, a relationship, the
# (concept, relationship) pair an identifier rule carries, or nothing at all for
# an ontology-level `requires`, which has no parent scope to bind against.
FormulaScope = Concept | Relationship | tuple[Concept, Relationship] | None


class FormulaParserFactory(FormulaFactory):
    """FormulaFactory that parses raw expressions into enriched model.Formula
    instances carrying a full AST, expression info, and head variables.

    Parsers are cached by ontology identity since FormulaParser construction
    is expensive (yacc compilation). Safe to reuse across multiple convert() calls.

    When parent is None (mapping expression context) falls back to a plain Formula.
    """

    def __init__(self, var_extract_fn: Callable[[FormulaScope], OrderedDict[Var, Concept]] | None = None):
        self._var_extract_fn = var_extract_fn
        self._parsers: dict[int, FormulaParser] = {}
        self._reasoners: dict[int, OntologyReasoner] = {}

    def __call__(
        self,
        raw_expr: str,
        parent: FormulaParent = None,
        ontology: OntologyComponent | None = None,
    ) -> OSIFormula:
        # With neither a parent nor an ontology there is no context to resolve
        # references against, so fall back to a plain, unparsed formula.
        if parent is None and ontology is None:
            return super().__call__(raw_expr=raw_expr, parent=parent, ontology=ontology)
        if ontology is None:
            raise ValueError("FormulaParserFactory requires an ontology to parse rule formulas.")
        # parent may still be None here: ontology-level `requires` constraints
        # (e.g. "COUNT[Airport] > 0") are not scoped to any concept/relationship.
        # They have no head vars, but are still parsed and validated against the ontology.
        var_extract_fn = self._var_extract_fn or extract_head_vars
        key = id(ontology)
        if key not in self._parsers:
            self._parsers[key] = FormulaParser(ontology)
        if key not in self._reasoners:
            reasoner = OntologyReasoner()
            ontology.register(reasoner)
            self._reasoners[key] = reasoner
        return parse_raw_formula(ontology, self._reasoners[key], raw_expr, parent, self._parsers[key], var_extract_fn)


class MappingFormulaParserFactory(MappingFormulaFactory):
    """MappingFormulaFactory that parses raw mapping expressions into enriched
    model.Formula instances, validated with MappingFormulaValidator.

    Both ontology and semantic_model are mandatory — field references in mapping
    expressions must be resolved against the semantic model at parse time.
    Parsers are cached per (ontology, semantic_model) pair.
    """

    def __init__(self):
        self._parsers: dict[tuple[int, int], FormulaParser] = {}

    def __call__(
        self,
        raw_expr: str,
        parent: FormulaParent = None,
        ontology: OntologyComponent | None = None,
        semantic_model: SemanticModel | None = None,
    ) -> OSIFormula:
        if ontology is None:
            raise ValueError("MappingFormulaParserFactory requires an ontology.")
        if semantic_model is None:
            raise ValueError("MappingFormulaParserFactory requires a semantic_model.")
        key = (id(ontology), id(semantic_model))
        if key not in self._parsers:
            self._parsers[key] = FormulaParser(ontology, semantic_model)
        parser = self._parsers[key]
        expr = parser.parse_formula(raw_expr)
        collector = FormulaCollector({})
        expr.accept(collector)
        MappingFormulaValidator.validate(collector.expression_info(), raw_expr)
        return Formula([expr], collector.expression_info(), OrderedDict(), raw_expr, parent)


def extract_head_vars(parent: FormulaScope) -> OrderedDict[Var, Concept]:
    head_vars: OrderedDict[Var, Concept] = OrderedDict()

    # Ontology-level `requires` constraints have no parent scope and therefore
    # no head vars to bind.
    if parent is None:
        return head_vars

    # The pair form is keyed by its relationship, matching how the tuple branch
    # below reads it; `hasattr` would have silently fallen through to repr().
    scope = parent[1] if isinstance(parent, tuple) else parent

    def _add_head_var(role: Role):
        if not role.explicit_name:
            return
        vh = VarHandle(role.explicit_name)
        if vh in head_vars:
            if head_vars[vh] != role.player:
                raise ValueError(
                    f"An ambiguous type for the head var '{vh.name()}' for "
                    f"'{scope.name}': "
                    f"'{vh.name()}' and '{head_vars[vh].name}'"
                )
        else:
            head_vars[vh] = role.player

    if isinstance(parent, Concept):
        for rel in parent.identify_by.values():
            _add_head_var(rel.last_role)
    elif isinstance(parent, Relationship):
        for role in parent.roles:
            _add_head_var(role)
    elif isinstance(parent, tuple):
        _, rel = parent
        for role in rel.roles:
            _add_head_var(role)

    return head_vars


def parse_raw_formula(
    ontology: OntologyComponent,
    reasoner: OntologyReasoner,
    raw_formula: str,
    parent: FormulaScope,
    parser: FormulaParser,
    var_extract_fn: Callable[[FormulaScope], OrderedDict[Var, Concept]],
) -> Formula:
    head_vars = var_extract_fn(parent)
    expr = parser.parse_formula(raw_formula, head_vars)
    collector = FormulaCollector(head_vars)
    expr.accept(collector)
    formula = Formula([expr], collector.expression_info(), head_vars, raw_formula, parent)
    FormulaValidator.validate_rule(ontology, reasoner, formula)
    return formula