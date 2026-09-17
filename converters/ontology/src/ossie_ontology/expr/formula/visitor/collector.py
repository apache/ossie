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
from typing import Optional

from ossie_ontology.expr.formula.model import (
    VarHandle,
    LiteralHandle,
    DotJoinHandle,
    FactRef,
    RelationshipRefHandle,
    ConceptRefHandle,
    DatasetRefHandle,
    DatasetFieldHandle,
    FormulaExpressionInfo,
    Var,
)
from ossie_ontology.expr.formula.visitor import Visitor
from ossie_ontology.expr.model import Expression, BinOp
from ossie_ontology.expr import Node
from ossie_ontology.model import Concept


class FormulaCollector(Visitor):
    """FormulaCollector — collects Handles in an Expression
    tree while inferring concept types against model's Concept."""

    def __init__(self, head_vars: dict[Var, Concept]):
        self._var_handles: list[tuple[VarHandle, Optional[Concept]]] = []
        # Seeded from the head vars, which are keyed by either handle kind, then
        # extended with the body's plain vars as their types are inferred.
        self._resolved_var_handles: dict[Var, Optional[Concept]] = dict(head_vars)
        self._literal_handles: list[LiteralHandle] = []
        self._concept_ref_handles: list[tuple[ConceptRefHandle, Concept]] = []
        self._relationship_ref_handles: list[RelationshipRefHandle] = []
        self._dataset_ref_handles: list[DatasetRefHandle] = []
        self._dataset_field_handles: list[DatasetFieldHandle] = []
        self._fact_refs: list[FactRef] = []
        self._dot_join_handles: list[DotJoinHandle] = []

    def expression_info(self) -> FormulaExpressionInfo:
        return FormulaExpressionInfo(
            var_handles=self._var_handles,
            literal_handles=self._literal_handles,
            concept_ref_handles=self._concept_ref_handles,
            relationship_ref_handles=self._relationship_ref_handles,
            dataset_ref_handles=self._dataset_ref_handles,
            dataset_field_handles=self._dataset_field_handles,
            fact_refs=self._fact_refs,
            dot_join_handles=self._dot_join_handles,
        )

    def visit_varhandle(self, node: VarHandle, parent: Optional[Node]):
        var_type = self._resolved_var_handles.get(node)
        if not var_type:
            var_type = self._infer_type(node, parent)
            if var_type:
                self._resolved_var_handles.setdefault(node, var_type)
        self._var_handles.append((node, var_type))

    def visit_literalhandle(self, node: LiteralHandle, parent: Optional[Node]):
        self._literal_handles.append(node)

    def visit_conceptrefhandle(self, node: ConceptRefHandle, parent: Optional[Node]):
        self._concept_ref_handles.append((node, node._concept))

    def visit_relationshiprefhandle(self, node: RelationshipRefHandle, parent: Optional[Node]):
        if isinstance(parent, Expression):
            self._collect_relationship_crh(node)
        self._relationship_ref_handles.append(node)

    def visit_datasetrefhandle(self, node: DatasetRefHandle, parent: Optional[Node]):
        self._dataset_ref_handles.append(node)

    def visit_datasetfieldhandle(self, node: DatasetFieldHandle, parent: Optional[Node]):
        self._dataset_field_handles.append(node)

    def visit_factref(self, node: FactRef, parent: Optional[Node]):
        super().visit_factref(node, parent)
        self._fact_refs.append(node)

    def visit_dotjoinhandle(self, node: DotJoinHandle, parent: Optional[Node]):
        if isinstance(parent, Expression) and isinstance(node._handle2, RelationshipRefHandle):
            self._collect_relationship_crh(node._handle2)
        super().visit_dotjoinhandle(node, parent)
        self._dot_join_handles.append(node)

    def _collect_relationship_crh(self, rel_handle: RelationshipRefHandle):
        for r in rel_handle._relationship._roles:
            self._concept_ref_handles.append((ConceptRefHandle(r._player), r._player))

    def _infer_type(self, node: VarHandle, parent: Optional[Node]) -> Optional[Concept]:
        if parent is None:
            return None
        if isinstance(parent, DotJoinHandle) and isinstance(parent._handle2, RelationshipRefHandle):
            if parent._handle1 == node:
                return self._resolve_from_first(parent._handle2)
        elif isinstance(parent, FactRef):
            if isinstance(parent._handle, RelationshipRefHandle):
                return self._resolve_from_args(node, parent._handle, parent._args)
            if isinstance(parent._handle, DotJoinHandle) and isinstance(parent._handle._handle2, RelationshipRefHandle):
                rel_ref = parent._handle._handle2
                args = parent._args if len(parent._args) == rel_ref._relationship.arity else [
                    parent._handle._handle1, *parent._args
                ]
                return self._resolve_from_args(node, rel_ref, args)
        elif isinstance(parent, Expression) and parent._op in BinOp.comparison_ops:
            left, right = parent._args
            target = right if left == node else left
            if isinstance(target, DotJoinHandle) and isinstance(target._handle2, RelationshipRefHandle):
                return self._resolve_from_last(target._handle2)
        return None

    def _resolve_from_args(self, var: VarHandle, rel: RelationshipRefHandle, args) -> Optional[Concept]:
        for i, arg in enumerate(args):
            if arg == var:
                return rel._relationship._roles[i]._player
        return None

    def _resolve_from_first(self, node: RelationshipRefHandle) -> Optional[Concept]:
        return node._relationship._roles[0]._player

    def _resolve_from_last(self, node: RelationshipRefHandle) -> Optional[Concept]:
        return node._relationship._roles[-1]._player