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

from __future__ import annotations
from typing import Optional, TypeVar, Generic
from abc import abstractmethod

from ossie_ontology.expr.formula.model import AggExpr, AggregationMethod, Negation, VarHandle, LiteralHandle, \
    DotJoinHandle, FactRef, RelationshipRefHandle, ConceptRefHandle, DatasetRefHandle, DatasetFieldHandle, \
    Conjunction, Disjunction, Exists, WhereExpr
from ossie_ontology.expr.model import Expression, BinOp
from ossie_ontology.expr import Node


# --------------------------------------------------
# Visitor Abstraction for formulas
# --------------------------------------------------

Result = TypeVar('Result')


class GenericVisitor(Generic[Result]):

    """
    Abstract visitor with handlers for each node type.
    Each handler should return a value of type `Result`.

    Actual behavior (e.g., AST traversal) should be implemented in
    subclasses of `GenericVisitor`.
    """

    @abstractmethod
    def visit_aggexpr(self, node: AggExpr, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_aggregationmethod(self, node: AggregationMethod, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_expression(self, node: Expression, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_binop(self, node: BinOp, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_negation(self, node: Negation, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_conjunction(self, node: Conjunction, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_disjunction(self, node: Disjunction, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_exists(self, node: Exists, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_whereexpr(self, node: WhereExpr, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_varhandle(self, node: VarHandle, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_literalhandle(self, node: LiteralHandle, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_conceptrefhandle(self, node: ConceptRefHandle, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_relationshiprefhandle(self, node: RelationshipRefHandle, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_datasetrefhandle(self, node: DatasetRefHandle, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_datasetfieldhandle(self, node: DatasetFieldHandle, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_factref(self, node: FactRef, parent: Optional[Node]) -> Result:
        pass

    @abstractmethod
    def visit_dotjoinhandle(self, node: DotJoinHandle, parent: Optional[Node]) -> Result:
        pass


class Visitor(GenericVisitor[None]):
    """
    A visitor that just walks the Expression tree, performing no changes.
    """

    def enter(self, node: Node, parent: Optional[Node] = None) -> Visitor:
        """ Visit the node, possibly returning a new visitor to be used to visit the node's children. """
        return self

    def leave(self, node: Node, parent: Optional[Node] = None) -> Node:
        """ Visit the node after visiting it and its children. """
        return node

    def _walk_node(self, node: Node, parent: Optional[Node] = None):
        # Enter the node, returning a new context.
        v = self.enter(node, parent)

        # Dispatch to the handler for the node.
        # By default the handler traverses the node's children and reconstructs the node.
        node.accept(v, parent)

        self.leave(node, parent)

    def visit_aggexpr(self, node: AggExpr, parent: Optional[Node]):
        self._walk_node(node._method, node)
        for var in node._var_seq:
            self._walk_node(var, node)
        if node._formula is not None:
            self._walk_node(node._formula, node)
        for var in node._group_by:
            self._walk_node(var, node)

    def visit_aggregationmethod(self, node: AggregationMethod, parent: Optional[Node]):
        pass

    def visit_expression(self, node: Expression, parent: Optional[Node]):
        self._walk_node(node._op, node)
        for arg in node._args:
            self._walk_node(arg, node)

    def visit_binop(self, node: BinOp, parent: Optional[Node]):
        pass

    def visit_negation(self, node: Negation, parent: Optional[Node]):
        self._walk_node(node._arg, node)

    def visit_varhandle(self, node: VarHandle, parent: Optional[Node]):
        pass

    def visit_literalhandle(self, node: LiteralHandle, parent: Optional[Node]):
        pass

    def visit_conceptrefhandle(self, node: ConceptRefHandle, parent: Optional[Node]):
        pass

    def visit_relationshiprefhandle(self, node: RelationshipRefHandle, parent: Optional[Node]):
        pass

    def visit_datasetrefhandle(self, node: DatasetRefHandle, parent: Optional[Node]):
        pass

    def visit_datasetfieldhandle(self, node: DatasetFieldHandle, parent: Optional[Node]):
        pass

    def visit_factref(self, node: FactRef, parent: Optional[Node]):
        self._walk_node(node._handle, node)
        for arg in node._args:
            self._walk_node(arg, node)

    def visit_dotjoinhandle(self, node: DotJoinHandle, parent: Optional[Node]):
        self._walk_node(node._handle1, node)
        self._walk_node(node._handle2, node)

    def visit_conjunction(self, node: Conjunction, parent: Optional[Node]):
        self._walk_node(node.left(), node)
        self._walk_node(node.right(), node)

    def visit_disjunction(self, node: Disjunction, parent: Optional[Node]):
        self._walk_node(node.left(), node)
        self._walk_node(node.right(), node)

    def visit_exists(self, node: Exists, parent: Optional[Node]):
        self._walk_node(node.body(), node)

    def visit_whereexpr(self, node: WhereExpr, parent: Optional[Node]):
        self._walk_node(node.expr(), node)
        self._walk_node(node.formula(), node)


class GenericFormulaConverter(GenericVisitor):
    """Base class for formula-to-PyRel converters. Provides shared operator evaluation."""

    @staticmethod
    def _apply_operator(left, op: BinOp, right):
        match op:
            case BinOp.EQ:
                return left == right
            case BinOp.NE:
                return left != right
            case BinOp.LE:
                return left <= right
            case BinOp.GE:
                return left >= right
            case BinOp.PLUS:
                return left + right
            case BinOp.MINUS:
                return left - right
            case BinOp.TIMES:
                return left * right
            case BinOp.DIVIDE:
                return left / right
            case BinOp.LT:
                return left < right
            case BinOp.GT:
                return left > right
            case _:
                raise ValueError(f"Unknown operator: {op}")
