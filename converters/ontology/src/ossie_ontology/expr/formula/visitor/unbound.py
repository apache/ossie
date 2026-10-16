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

"""Unbound-reference analysis for a parsed formula.

A formula may only name references that something binds. What binds them is the
formula's *scope* — its head variables and its parent concept — and the joins the
formula itself writes:

    concept Person, requires:
      'Person.employer(Company) AND Company.revenue > 0'   ok, the fact joins them
      'Person.name AND Company.revenue > 0'                'Company' is unbound
      'Person.name AND EXISTS(Company.revenue > 0)'        ok, EXISTS binds it

An ontology-level `requires` has no parent, so its scope is empty and every
reference has to be bound by the formula itself.

This is a read-only pass over the AST that `FormulaValidator` runs; it is kept
apart from `FormulaCollector`, which is about collecting handles and inferring
their types.
"""
from __future__ import annotations

from typing import Optional

from ossie_ontology.expr.formula.model import (
    AggExpr,
    ConceptRefHandle,
    DotJoinHandle,
    Exists,
    FactRef,
    Formula,
    VarHandle,
)
from ossie_ontology.expr.formula.visitor import Visitor
from ossie_ontology.expr.model import BinOp, Expression
from ossie_ontology.expr import Node
from ossie_ontology.model import Concept, FormulaParent, Relationship

Ref = VarHandle | ConceptRefHandle


class UnboundRefFinder(Visitor):
    """Finds the references a formula names but never binds.

    Construct it with the formula and ask it for `unbound_refs()`. The walk
    collects three things, which that method then combines:

    * the references that appear outside any binder — only these have to be
      bound, since a binder scopes its own (see `visit_exists`);
    * the references each fact atom joins, because traversing a fact binds its
      participants to one another;
    * the references an equality defines a variable from.

    Scope then spreads from the head outwards along the joins and definitions,
    and whatever it never reaches is unbound.
    """

    def __init__(self, formula: Formula):
        self._formula = formula
        self._scope = formula_scope(formula)
        self._refs: list[Ref] = []
        self._joins: list[list[Ref]] = []
        self._definitions: list[tuple[VarHandle, list[Ref]]] = []
        self._in_binder = False

    def unbound_refs(self) -> list[Ref]:
        """The references the formula names but never binds, first-seen first."""
        self._refs, self._joins, self._definitions = [], [], []
        for expr in self._formula.expr():
            expr.accept(self)
        return self._unreached_by_scope()

    # ---------------- collecting ------------------------------------------

    def visit_varhandle(self, node: VarHandle, parent: Optional[Node]):
        # The step after a dot names a relationship, not a variable. It only
        # shows up as a VarHandle when the left side's type is unknown until
        # inference, as with the 'sub.staff' of
        # 'Company.subsidiary(sub) AND sub.staff > 0' — `_validate_dot_join`
        # resolves it against the relationship, so it is never a free variable.
        if isinstance(parent, DotJoinHandle) and parent._handle2 is node:
            return
        self._collect_ref(node)

    def visit_conceptrefhandle(self, node: ConceptRefHandle, parent: Optional[Node]):
        self._collect_ref(node)

    def visit_aggexpr(self, node: AggExpr, parent: Optional[Node]):
        # 'Person.name AND COUNT[Company] > 0' — the aggregate binds `Company`,
        # so nothing under it is collected. Saving the outer value instead of
        # counting is what handles nesting: an inner binder restores `True` on
        # its way out, and only the outermost one restores `False`.
        outer, self._in_binder = self._in_binder, True
        super().visit_aggexpr(node, parent)
        self._in_binder = outer

    def visit_exists(self, node: Exists, parent: Optional[Node]):
        # Same for EXISTS, at any nesting depth and even when the EXISTS is the
        # whole formula, as in an ontology-level 'EXISTS(Person.ssn > 0)'.
        outer, self._in_binder = self._in_binder, True
        super().visit_exists(node, parent)
        self._in_binder = outer

    def visit_factref(self, node: FactRef, parent: Optional[Node]):
        super().visit_factref(node, parent)
        if self._in_binder:
            return
        # Traversing a fact binds its participants to each other, so
        # 'Person.employer(Company)' joins `Person` and `Company`: once either
        # is in scope, so is the other.
        joined = [h for h in (_anchor(node._handle), *node._args) if isinstance(h, (VarHandle, ConceptRefHandle))]
        if len(joined) > 1:
            self._joins.append(joined)

    def visit_expression(self, node: Expression, parent: Optional[Node]):
        # Walks the arguments here rather than in the base visitor so that the
        # references each side contributes can be sliced off `_refs`: an
        # equality defines its variable in terms of the other side, as with the
        # 'extended_price == OrderLineItem.lineitem_extended_price' that puts
        # `extended_price` in scope for the rest of the formula.
        self._walk_node(node._op, node)
        per_arg: list[list[Ref]] = []
        for arg in node._args:
            seen = len(self._refs)
            self._walk_node(arg, node)
            per_arg.append(self._refs[seen:])
        if self._in_binder or node._op != BinOp.EQ or len(node._args) != 2:
            return
        for defined, defining in ((0, 1), (1, 0)):
            handle = node._args[defined]
            if isinstance(handle, VarHandle):
                self._definitions.append((handle, per_arg[defining]))

    def _collect_ref(self, node: Ref):
        if not self._in_binder:
            self._refs.append(node)

    # ---------------- deciding --------------------------------------------

    def _unreached_by_scope(self) -> list[Ref]:
        """The collected references that scope does not reach, first-seen first."""
        bound = set(self._scope)
        # A fact binds all of its references as soon as one of them is bound; an
        # equality binds its variable once the side defining it is fully bound.
        # Iterate until nothing new is reached, since the formula may join the
        # scope in any order — 'A.r(B) AND C.s(A)' reaches `B` before `C`.
        expanding = True
        while expanding:
            expanding = False
            for joined in self._joins:
                if any(h in bound for h in joined):
                    fresh = [h for h in joined if h not in bound]
                    bound.update(fresh)
                    expanding = expanding or bool(fresh)
            for defined, defining in self._definitions:
                if defined not in bound and all(h in bound for h in defining):
                    bound.add(defined)
                    expanding = True

        unbound: list[Ref] = []
        for ref in self._refs:
            if ref not in bound and ref not in unbound:
                unbound.append(ref)
        return unbound


def formula_scope(formula: Formula) -> set[Ref]:
    """What `formula` may reference for free: its head vars plus the concepts of
    its parent. Empty for an ontology-level `requires`, which has no parent."""
    scope: set[Ref] = set(formula.head_vars())
    scope.update(ConceptRefHandle(c) for c in _scope_concepts(formula.parent))
    return scope


def _scope_concepts(parent: FormulaParent) -> set[Concept]:
    """The parent concept, or every role player of the parent relationship (its
    first role is the containing concept), together with their supertypes — a
    subtype carves itself out of a supertype, so 'Payment.type == 'card'' is in
    scope for `Card extends Payment`."""
    roots: list[Concept] = []
    if isinstance(parent, Concept):
        roots.append(parent)
    elif isinstance(parent, Relationship):
        roots.extend(role.player for role in parent.roles)
    elif isinstance(parent, tuple):
        concept, rel = parent
        roots.append(concept)
        roots.extend(role.player for role in rel.roles)

    concepts: set[Concept] = set()
    while roots:
        concept = roots.pop()
        if concept in concepts:
            continue
        concepts.add(concept)
        roots.extend(concept.extends)
    return concepts


def _anchor(handle: Node) -> Node:
    """The leftmost atom of a handle chain — the reference a traversal such as
    'Person.employer' or 'Person.employment(Company).level' starts from."""
    while isinstance(handle, (DotJoinHandle, FactRef)):
        handle = handle._handle1 if isinstance(handle, DotJoinHandle) else handle._handle
    return handle