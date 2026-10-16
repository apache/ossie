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


"""Unary minus binds tighter than the binary arithmetic operators.

`FormulaParser` desugars `-x` to `0 - x`, so each case compares the parsed tree
against the grouping it must produce.
"""

from __future__ import annotations

import pytest

from ossie_ontology.model import Concept, ConceptType, OntologyComponent
from ossie_ontology.expr.model import Expression
from ossie_ontology.expr.formula.model import ConceptRefHandle, LiteralHandle, VarHandle
from ossie_ontology.expr.formula.parser import FormulaParser

_AMOUNT = Concept(name="Amount", type=ConceptType.VALUE_TYPE)
HEAD_VARS: dict[VarHandle | ConceptRefHandle, Concept] = {
    VarHandle(name): _AMOUNT for name in ("a", "b")
}


def tree(node):
    """Render an AST as nested tuples: `(op, left, right)` or a leaf's text."""
    if isinstance(node, Expression):
        return (node.op().value, *(tree(arg) for arg in node.args()))
    assert isinstance(node, (LiteralHandle, VarHandle)), node
    return str(node)


def neg(x):
    return ("-", "0", x)


@pytest.mark.parametrize(
    "formula, expected",
    [
        ("-a + b", ("+", neg("a"), "b")),
        ("-a - b", ("-", neg("a"), "b")),
        ("-a * b", ("*", neg("a"), "b")),
        ("-a / b", ("/", neg("a"), "b")),
        ("-1 + 3", ("+", neg("1"), "3")),
        ("-1 - 3", ("-", neg("1"), "3")),
        ("b - -a", ("-", "b", neg("a"))),
        ("-(a + b)", neg(("+", "a", "b"))),
    ],
)
def test_unary_minus_precedence(formula: str, expected):
    parsed = FormulaParser(OntologyComponent()).parse_formula(formula, head_vars=HEAD_VARS)
    assert tree(parsed) == expected
