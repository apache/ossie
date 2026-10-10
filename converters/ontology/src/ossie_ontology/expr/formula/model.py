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

"""Formula AST handle types."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Hashable
from dataclasses import dataclass, field
from ossie_ontology.expr import acceptor, Node
from ossie_ontology.expr.common import AggMethod

from ossie_ontology.model import (
    Concept,
    Relationship,
    Dataset,
    DatasetField,
    FormulaParent,
    Formula as OSIFormula,
)

from ossie_ontology.expr.model import Expression as ExprNode


@acceptor
class AggregationMethod(Node):
    def __init__(self, val: AggMethod):
        self._val = val

    def __str__(self):
        return str(self._val)

    def __repr__(self):
        return f"AggregationMethod{{({self.__str__()})}}"


@acceptor
class AggExpr(Node):

    def __init__(self, meth: AggregationMethod, vseq, formula, grp_by):
        self._method = meth
        self._var_seq = vseq
        self._formula = formula
        self._group_by = grp_by

    def __str__(self):
        vars = ', '.join([str(var) for var in self._var_seq])
        where = f" WHERE {self._formula}" if self._formula is not None else ""
        grp_by = f" GROUP BY {', '.join(str(var) for var in self._group_by)}" if self._group_by else ""
        return f"{self._method} [{vars}{where}{grp_by}]"

    def __repr__(self):
        return f"AggExpr{{({self.__str__()})}}"


@acceptor
class Negation(Node):
    def __init__(self, arg):
        self._arg = arg

    def arg(self):
        return self._arg

    def __hash__(self):
        return hash(("NOT", self._arg.__hash__()))

    def __str__(self):
        return f"not ({str(self._arg)})"

    def __repr__(self):
        return f"Not{{({self._arg.__repr__()})}}"


@acceptor
class Conjunction(Node):
    def __init__(self, left, right):
        self._left = left
        self._right = right

    def left(self):
        return self._left

    def right(self):
        return self._right

    def __hash__(self):
        return hash(("AND", self._left.__hash__(), self._right.__hash__()))

    def __str__(self):
        return f"({self._left} AND {self._right})"

    def __repr__(self):
        return f"Conjunction{{{self._left.__repr__()} AND {self._right.__repr__()}}}"


@acceptor
class Disjunction(Node):
    def __init__(self, left, right):
        self._left = left
        self._right = right

    def left(self):
        return self._left

    def right(self):
        return self._right

    def __hash__(self):
        return hash(("OR", self._left.__hash__(), self._right.__hash__()))

    def __str__(self):
        return f"({self._left} OR {self._right})"

    def __repr__(self):
        return f"Disjunction{{{self._left.__repr__()} OR {self._right.__repr__()}}}"


@acceptor
class Exists(Node):
    def __init__(self, body):
        self._body = body

    def body(self):
        return self._body

    def __hash__(self):
        return hash(("EXISTS", self._body.__hash__()))

    def __str__(self):
        return f"EXISTS ({self._body})"

    def __repr__(self):
        return f"Exists{{{self._body.__repr__()}}}"


@acceptor
class WhereExpr(Node):
    def __init__(self, expr, formula):
        self._expr = expr
        self._formula = formula

    def expr(self):
        return self._expr

    def formula(self):
        return self._formula

    def __hash__(self):
        return hash(("WHERE", self._expr.__hash__(), self._formula.__hash__()))

    def __str__(self):
        return f"({self._expr} WHERE ({self._formula}))"

    def __repr__(self):
        return f"WhereExpr{{{self._expr.__repr__()} WHERE {self._formula.__repr__()}}}"


@acceptor
class Handle(Node):
    """Base for the leaf nodes of a formula AST.

    Identity is declared once per subclass by `_key`, and both `__eq__` and
    `__hash__` are derived from it. Keeping them on one definition is what makes
    them agree: a subclass that hashes its fields while equality compares
    something else puts equal objects in different buckets, and a dict lookup
    then misses the entry it just stored.
    """

    def _key(self) -> Hashable:
        """What identifies this handle. Subclasses override with their fields.

        Annotated as `Hashable` rather than left to inference: several subclasses
        return a tuple, and inferring `str` from this default would make every
        one of those an incompatible override. The only real requirement is that
        whatever comes back can be hashed and compared.
        """
        return str(self)

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self._key() == other._key()
        return False

    def __hash__(self):
        return hash(self._key())


@acceptor
class Atom(Handle):
    pass


@acceptor
class ConceptRefHandle(Atom):
    def __init__(self, concept: Concept):
        self._concept = concept

    def __str__(self):
        return f"{self._concept}"

    def __repr__(self):
        return f"ConceptRefHandle{{{self}}}"


@acceptor
class RelationshipRefHandle(Atom):
    def __init__(self, relationship: Relationship):
        self._relationship = relationship

    def __str__(self):
        return f"{self._relationship}"

    def __repr__(self):
        return f"RelationshipRefHandle{{{self}}}"


@acceptor
class DatasetRefHandle(Atom):
    def __init__(self, dataset: Dataset):
        self._dataset = dataset

    def dataset(self):
        return self._dataset

    def name(self):
        return f"{self.__str__()}"

    def _key(self) -> Hashable:
        return self._dataset.name

    def __str__(self):
        return f"{self._dataset}"

    def __repr__(self):
        return f"DatasetRefHandle{{{self}}}"


@acceptor
class DatasetFieldHandle(Atom):
    def __init__(self, dataset: Dataset, field: DatasetField):
        self._dataset = dataset
        self._field = field

    def _key(self) -> Hashable:
        # The dataset and field objects themselves are unhashable — both are
        # dataclasses, so Python sets their `__hash__` to None — which made
        # hashing this handle raise TypeError. Key on their names instead, with
        # the dataset's source included because two distinct datasets can share
        # a name (see `_expression_key` in the RelationalAI converter) while a
        # bare column name is not unique across datasets at all.
        return self._dataset.source, self._dataset.name, self._field.name

    def __str__(self):
        return f"{self._field}"

    def __repr__(self):
        return f"DatasetFieldHandle{{{self._dataset}.{self._field}}}"


@acceptor
class VarHandle(Atom):
    def __init__(self, v):
        self._var = v

    def name(self):
        return self._var

    def _key(self) -> Hashable:
        return self._var

    def __str__(self):
        return str(self._var)

    def __repr__(self):
        return f"VarHandle{{{self}}}"


@acceptor
class LiteralHandle(Atom):
    def __init__(self, val):
        self._val = val

    def _key(self) -> Hashable:
        return self._val

    def __str__(self):
        return str(self._val)

    def __repr__(self):
        return f"LiteralHandle{{{self}}}"


@acceptor
class FactRef(Atom):
    def __init__(self, handle: Atom, *args):
        self._handle = handle
        self._args = args

    def _key(self) -> Hashable:
        return (self._handle, *self._args)

    def __str__(self):
        return f"{self._handle}({', '.join([str(arg) for arg in self._args])})"

    def __repr__(self):
        expr = f"{self._handle.__repr__()}( {', '.join(arg.__repr__() for arg in self._args)} )"
        return f"FactRef{{{expr}}}"


@acceptor
class DotJoinHandle(Atom):
    def __init__(self, handle1: Atom, handle2: Atom):
        self._handle1 = handle1
        self._handle2 = handle2

    def _key(self) -> Hashable:
        return self._handle1, self._handle2

    def __str__(self):
        return f"{self._handle1}.{self._handle2}"

    def __repr__(self):
        expr = f"{self._handle1.__repr__()} . {self._handle2.__repr__()}"
        return f"DotJoinHandle{{ {expr} }}"

    def last_handle(self):
        if isinstance(self._handle2, DotJoinHandle):
            return self._handle2.last_handle()
        return self._handle2


# ---------------------------------------------------------------------------
# Formula
# ---------------------------------------------------------------------------

@dataclass
class FormulaExpressionInfo:
    var_handles: list[tuple[VarHandle, Concept | None]] = field(default_factory=list)
    literal_handles: list[LiteralHandle] = field(default_factory=list)
    concept_ref_handles: list[tuple[ConceptRefHandle, Concept]] = field(default_factory=list)
    relationship_ref_handles: list[RelationshipRefHandle] = field(default_factory=list)
    dataset_ref_handles: list[DatasetRefHandle] = field(default_factory=list)
    dataset_field_handles: list[DatasetFieldHandle] = field(default_factory=list)
    fact_refs: list[FactRef] = field(default_factory=list)
    dot_join_handles: list[DotJoinHandle] = field(default_factory=list)


class Formula(OSIFormula):
    _expr: list[ExprNode | Handle | Negation]
    _expr_info: FormulaExpressionInfo
    _head_vars: OrderedDict[VarHandle | ConceptRefHandle, Concept]

    def __init__(
        self,
        expr: list[ExprNode | Handle | Negation],
        expr_info: FormulaExpressionInfo,
        head_vars: OrderedDict[VarHandle | ConceptRefHandle, Concept],
        raw_expr: str,
        parent: FormulaParent,
    ):
        super().__init__(raw_expr, parent)
        self._expr = expr
        self._expr_info = expr_info
        self._head_vars = head_vars

    def expr(self):
        return self._expr

    def expr_info(self) -> FormulaExpressionInfo:
        return self._expr_info

    def head_vars(self):
        return self._head_vars

    def last_head_var(self):
        return next(reversed(self._head_vars), None)

    def formula_target(self):
        if len(self._expr) == 1 and isinstance(
            self._expr[0],
            (ExprNode, LiteralHandle, DotJoinHandle, AggExpr),
        ):
            last_head_var = self.last_head_var()
            if last_head_var and self._head_vars[last_head_var].is_primitive:
                return last_head_var, self._head_vars[last_head_var]
        return None

    def is_formula_target(self, var) -> bool:
        target = self.formula_target()
        return target[0] == var if target else False


# What a formula variable can be named by: a plain var, or a concept reference
# standing in for one. Head vars are keyed by either, so anything holding them
# has to accept both.
Var = VarHandle | ConceptRefHandle
