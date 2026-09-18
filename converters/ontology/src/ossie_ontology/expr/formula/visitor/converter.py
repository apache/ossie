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

"""Formula AST -> PyRel rules.

The one module under `ossie_ontology.expr` that imports `relationalai`. It sits
here rather than with the converter because it is a `GenericFormulaConverter`
visitor, alongside `collector` and `unbound` — the difference is only what it
emits. Nothing in the `expr` package imports it, so parsing and validating
formulas still works with the SDK absent; it is reached solely from
`ossie_ontology.converter.ossie_to_relationalai`, which the optional
`relationalai` extra installs.
"""

from __future__ import annotations

from typing import Optional

from relationalai.util.naming import NameCache
import relationalai.semantics as pyrel
from relationalai.semantics import not_
from relationalai.semantics.frontend.base import Ref, Concept as RAIConcept, CoreConcepts, Chain, \
    Relationship as RAIRelationship

from ossie_ontology.reasoner import OntologyReasoner
from ossie_ontology.vendor.relationalai.mappings import ValueMapping
from ossie_ontology.vendor.relationalai.ontology import OntologyModel
from ossie_ontology.expr.formula.model import (
    AggregationMethod,
    Conjunction,
    Disjunction,
    WhereExpr,
    DatasetFieldHandle,
    DatasetRefHandle,
    DotJoinHandle,
    VarHandle,
    ConceptRefHandle,
    RelationshipRefHandle,
    FactRef,
    LiteralHandle,
    Negation,
    AggExpr,
    Formula
)
from ossie_ontology.expr.formula.visitor import GenericFormulaConverter
from ossie_ontology.expr import Node
from ossie_ontology.expr.common import AggMethod as ModelAggMethod
from ossie_ontology.expr.model import Expression, BinOp
from ossie_ontology.model import OntologyComponent, Concept, Relationship, Role

# The Ossie aggregation vocabulary, resolved to the pyrel builtins that implement
# it. This is the only place the two vocabularies meet: `AggMethod` itself is
# vendor-neutral and lives in `ossie_ontology.expr.common` alongside the grammar
# that produces it, so nothing in the Ossie layer has to know pyrel exists.
SEMANTIC_MODEL_TO_PYREL_AGG = {
    ModelAggMethod.SUM: pyrel.sum,
    ModelAggMethod.MIN: pyrel.min,
    ModelAggMethod.MAX: pyrel.max,
    ModelAggMethod.AVG: pyrel.avg,
    ModelAggMethod.COUNT: pyrel.count,
}

class FormulaConverter(GenericFormulaConverter):
    """FormulaConverter — generates PyRel rules from a Formula."""

    def __init__(
        self,
        oc: OntologyComponent,
        ontology: OntologyModel,
        reasoner: OntologyReasoner,
        var_name_cache: NameCache,
        formula: Formula,
    ):
        self._oc = oc
        self._ontology = ontology
        self._reasoner = reasoner
        self._formula = formula
        self._target_var = formula.formula_target()
        self._var_name_cache = var_name_cache
        self._var_refs: dict = {}
        self._body_statements: list = []
        var_handles = dict(self._formula.expr_info().var_handles)
        for var, c in var_handles.items():
            if c is None:
                continue
            pyrel_c = self._ontology.lookup_concept(c.name)
            assert pyrel_c is not None, f"Concept '{c.name}' not found in ontology"
            # Name the ref after the formula's own variable. `ref()` with no
            # name falls back to one derived from the concept, so every
            # Decimal-typed variable in a formula came out as `number` —
            # distinct Vars sharing one name. That is invisible while the model
            # only ever gets compiled (Vars are identified by id), but anything
            # that renders the metamodel back to source collapses them: the
            # `net_revenue == extended_price * (1 - discount)` formula
            # decompiled to `Decimal.number == Decimal.number * (1 - Decimal.number)`.
            # `_var_name` is NameCache-backed, so these stay unique across formulas.
            var_name = self._var_name(var)
            self._var_refs[var_name] = pyrel_c.ref(var_name)
        self._ontology.base_model().define(self._head()).where(*self._body())

    def _head(self):
        formula_parent = self._formula.parent
        if isinstance(formula_parent, Concept):
            pyrel_concept = self._ontology.lookup_concept(formula_parent.name)
            assert pyrel_concept is not None, f"Concept '{formula_parent.name}' not found in ontology"
            if formula_parent.extends:
                subc = self._ontology.lookup_concept(formula_parent.extends[0].name)
                return pyrel_concept(subc)
            else:
                identifiers = {}
                # `ref_scheme` returns None for a concept with no identifier
                # of its own and no supertype to inherit one from — which is
                # exactly this branch, since `extends` was falsy above. The old
                # guard re-tested `isinstance(..., Concept)`, already true here,
                # so the None case reached `for ... in None`.
                for idt in (self._reasoner.ref_scheme(formula_parent) or ()):
                    identifiers[idt.name] = self._role_var(idt.last_role)
                return pyrel_concept.new(**identifiers)
        else:
            if isinstance(formula_parent, Relationship):
                relationship = formula_parent
                concept = relationship.first_role.player
            else:
                assert isinstance(formula_parent, tuple)
                concept, relationship = formula_parent
                assert (
                    self._reasoner.in_subtype_closure(concept, relationship.first_role.player)
                    or relationship.first_role.player is concept
                )
            pyrel_concept = self._ontology.lookup_concept(concept.name)
            assert pyrel_concept is not None, f"Concept '{concept.name}' not found in ontology"
            if self._reasoner.is_identifier_relationship(relationship):
                identifiers = {relationship.name: self._role_var(relationship.last_role)}
                return pyrel_concept.new(**identifiers)
            else:
                args = [self._role_var(r) for r in relationship.roles]
                return self._pyrel_relationship(relationship, pyrel_concept)(*args)

    def _body(self) -> list:
        for e in self._formula.expr():
            result = e.accept(self)
            if result is not None:
                self._body_statements.append(result)
        return self._body_statements

    def visit_varhandle(self, node: VarHandle, parent: Optional[Node]):
        return self._var_refs[self._var_name(node)]

    def visit_literalhandle(self, node: LiteralHandle, parent: Optional[Node]):
        return self._target_var[0].accept(self) == node._val if self._target_var and not parent else node._val

    def visit_conceptrefhandle(self, node: ConceptRefHandle, parent: Optional[Node]):
        return self._ontology.lookup_concept(str(node._concept))

    def visit_relationshiprefhandle(self, node: RelationshipRefHandle, parent: Optional[Node]):
        rel = node._relationship
        if self._target_var and parent and isinstance(parent, Expression):
            rel_args = []
            for r in rel.roles[:-1]:
                rel_args.append(self._role_var(r))
            pyrel_last = self._ontology.lookup_concept(rel.last_role.player.name)
            assert pyrel_last is not None
            # Named for the same reason as the var refs above: an unnamed ref
            # inherits a concept-derived name, so several of these in one
            # formula become indistinguishable once rendered back to source.
            var_ref = pyrel_last.ref(self._var_name(f"{rel.name}_{rel.last_role.player.name}"))
            rel_args.append(var_ref)
            self._body_statements.append(self._pyrel_relationship(rel)(*rel_args))
            return var_ref
        if rel.container:
            pyrel_concept = self._ontology.lookup_concept(rel.container.name)
            assert pyrel_concept is not None
            return pyrel_concept.__getattr__(rel.name)
        else:
            return self._ontology.lookup_relationship(rel.name)

    def visit_datasetrefhandle(self, node: DatasetRefHandle, parent: Optional[Node]):
        return self._ontology.lookup_table(node.name())

    def visit_datasetfieldhandle(self, node: DatasetFieldHandle, parent: Optional[Node]):
        pass

    def visit_factref(self, node: FactRef, parent: Optional[Node]):
        handle = node._handle.accept(self, node)
        args = [arg.accept(self, node) for arg in node._args]
        return handle(*args)

    def visit_expression(self, node: Expression, parent: Optional[Node]):
        left, right = node._args
        pyrel_left = left.accept(self, node)
        pyrel_right = right.accept(self, node)
        pyrel_expr = self._apply_operator(pyrel_left, node._op, pyrel_right)
        if (parent is not None and isinstance(parent, Expression)
                and parent._op in BinOp.multiplicative_ops
                and node._op not in BinOp.multiplicative_ops):
            pyrel_expr = (pyrel_expr)
        if not self._target_var or parent:
            return pyrel_expr
        target = self._target_var[0].accept(self)
        if target is pyrel_left or target is pyrel_right:
            return pyrel_expr
        return target == pyrel_expr

    def visit_aggexpr(self, node: AggExpr, parent: Optional[Node]):
        pyrel_vars = [var.accept(self, node) for var in node._var_seq]
        pyrel_body = self._flatten_formula(node._formula, node) if node._formula is not None else []
        pyrel_per = [var.accept(self, node) for var in node._group_by]
        pyrel_agg = SEMANTIC_MODEL_TO_PYREL_AGG[ModelAggMethod(node._method._val)]
        pyrel_expr = pyrel_agg(*pyrel_vars)
        if pyrel_body:
            pyrel_expr = pyrel_expr.where(*pyrel_body)
        if pyrel_per:
            pyrel_expr = pyrel_expr.per(*pyrel_per)
        return self._target_var[0].accept(self) == pyrel_expr if self._target_var and not parent else pyrel_expr

    def visit_conjunction(self, node: Conjunction, parent: Optional[Node]):
        left_result = node.left().accept(self, node)
        right_result = node.right().accept(self, node)
        if left_result is not None:
            self._body_statements.append(left_result)
        if right_result is not None:
            self._body_statements.append(right_result)

    def visit_disjunction(self, node: Disjunction, parent: Optional[Node]):
        raise NotImplementedError("Disjunction (OR) in formula body is not yet supported")

    def visit_whereexpr(self, node: WhereExpr, parent: Optional[Node]):
        raise NotImplementedError("WHERE expression is not yet supported")

    def visit_negation(self, node: Negation, parent: Optional[Node]):
        return not_(node._arg.accept(self, node))

    def visit_dotjoinhandle(self, node: DotJoinHandle, parent: Optional[Node]):
        handle1 = node._handle1.accept(self)
        if isinstance(node._handle2, DatasetFieldHandle) and self._target_var and not parent:
            return handle1.__getattr__(str(node._handle2))(self._target_var[0].accept(self))
        return handle1.__getattr__(str(node._handle2))

    def visit_aggregationmethod(self, node: AggregationMethod, parent: Optional[Node]):
        pass

    def visit_binop(self, node: BinOp, parent: Optional[Node]):
        pass

    def _flatten_formula(self, formula_node, parent) -> list:
        if isinstance(formula_node, Conjunction):
            return (self._flatten_formula(formula_node.left(), parent)
                    + self._flatten_formula(formula_node.right(), parent))
        result = formula_node.accept(self, parent)
        return [result] if result is not None else []

    def _role_var(self, role: Role) -> RAIConcept | Ref:
        role_name = role.name 
        var_key = self._var_name(role_name)
        if var_key in self._var_refs:
            return self._var_refs[var_key]
        pyrel_c = self._ontology.lookup_concept(role_name)
        assert pyrel_c is not None, f"Concept '{role_name}' not found in ontology"
        return pyrel_c

    def _var_name(self, var: str | VarHandle) -> str:
        name = var.name() if isinstance(var, VarHandle) else var
        return self._var_name_cache.get_name(f"{self._formula.raw_expr}_{name}", name)

    def _pyrel_relationship(self, relationship: Relationship, container: Optional[RAIConcept] = None) -> RAIRelationship | Chain:
        if container is None:
            concept = self._ontology.lookup_concept(relationship.first_role.player.name)
            assert concept is not None, f"Concept '{relationship.first_role.player.name}' not found in ontology"
        else:
            concept = container
        if concept._name in CoreConcepts:
            rel = self._ontology.lookup_relationship(relationship.name)
            assert rel is not None, f"Relationship '{relationship.name}' not found in ontology"
            return rel
        else:
            return concept.__getattr__(relationship.name)

class MappingFormulaConverter(GenericFormulaConverter):
    """Converts a mapping formula AST to a PyRel expression.

    Tables are resolved dynamically from the DatasetField.dataset back-reference rather than requiring a fixed table
    to be passed in. This allows mapping expressions that span multiple datasets.

    The result of the conversion is available via the `result` attribute after construction.
    """

    def __init__(self, ontology: OntologyModel, formula: Formula, concept=None):
        self._ontology = ontology
        self._concept = concept
        expr = formula.expr()[0]
        if isinstance(expr, WhereExpr):
            self.result = expr.accept(self, None)
        else:
            raw = expr.accept(self, None)
            if self._concept is not None:
                raw = self._concept(raw)
            self.result = ValueMapping(raw)

    def visit_datasetfieldhandle(self, node: DatasetFieldHandle, parent):
        field = node._field
        dataset = getattr(field, 'dataset', None)
        if dataset is not None:
            table = self._ontology.lookup_table(dataset.name())
            assert table is not None, f"Table '{dataset.name()}' not found in ontology"
            return table.__getattr__(field.name)
        raise ValueError(f"Cannot resolve table for field '{field.name}' — dataset back-reference is missing")

    def visit_dotjoinhandle(self, node: DotJoinHandle, parent):
        if isinstance(node._handle2, DatasetFieldHandle):
            field = node._handle2._field
            dataset = getattr(field, 'dataset', None)
            if dataset is not None:
                table = self._ontology.lookup_table(dataset.name)
                assert table is not None, f"Table '{dataset.name}' not found in ontology"
                return table.__getattr__(field.name)
            if isinstance(node._handle1, DatasetRefHandle):
                table = self._ontology.lookup_table(node._handle1.name())
                assert table is not None, f"Table '{node._handle1.name()}' not found in ontology"
                return table.__getattr__(field.name)
        raise ValueError(f"Unsupported dot-join in mapping expression: '{node}'")

    def visit_literalhandle(self, node: LiteralHandle, parent):
        return node._val

    def visit_expression(self, node: Expression, parent):
        left = node._args[0].accept(self, node)
        right = node._args[1].accept(self, node)
        return self._apply_operator(left, node._op, right)

    def visit_aggexpr(self, node: AggExpr, parent):
        pyrel_vars = [v.accept(self, node) for v in node._var_seq]
        pyrel_agg_fn = SEMANTIC_MODEL_TO_PYREL_AGG[ModelAggMethod(node._method._val)]
        pyrel_expr = pyrel_agg_fn(*pyrel_vars)
        if node._formula is not None:
            pyrel_expr = pyrel_expr.where(*self._flatten_conditions(node._formula))
        if node._group_by:
            pyrel_expr = pyrel_expr.per(*[g.accept(self, node) for g in node._group_by])
        return pyrel_expr

    def visit_varhandle(self, node: VarHandle, parent):
        raise ValueError(f"Variable references are not allowed in mapping expressions: '{node}'")

    def visit_conceptrefhandle(self, node: ConceptRefHandle, parent):
        raise ValueError(f"Concept references are not allowed in mapping expressions: '{node._concept}'")

    def visit_relationshiprefhandle(self, node: RelationshipRefHandle, parent):
        raise ValueError(f"Relationship references are not allowed in mapping expressions: '{node._relationship}'")

    def visit_datasetrefhandle(self, node: DatasetRefHandle, parent):
        raise ValueError(f"Bare dataset references are not allowed in mapping expressions: '{node.name()}'")

    def visit_factref(self, node: FactRef, parent):
        raise ValueError("Function/apply expressions are not allowed in mapping expressions.")

    def visit_negation(self, node: Negation, parent):
        return not_(node._arg.accept(self, parent))

    def visit_conjunction(self, node: Conjunction, parent):
        raise ValueError("Conjunction (AND) is only valid inside an aggregation WHERE condition in mapping expressions.")

    def visit_disjunction(self, node: Disjunction, parent):
        raise ValueError("Disjunction (OR) is not allowed in mapping expressions.")

    def visit_whereexpr(self, node: WhereExpr, parent):
        left = node.expr().accept(self, node)
        right = node.formula().accept(self, node)
        if self._concept is not None:
            left = self._concept(left)
        return ValueMapping(left).where(right)

    def visit_aggregationmethod(self, node: AggregationMethod, parent):
        pass

    def visit_binop(self, node: BinOp, parent):
        pass

    def _flatten_conditions(self, node) -> list:
        if isinstance(node, Conjunction):
            return self._flatten_conditions(node._left) + self._flatten_conditions(node._right)
        return [node.accept(self, None)]