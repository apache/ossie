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

from ossie_ontology.expr import acceptor, Node
from enum import Enum


@acceptor
class BinOp(str, Node, Enum):
    # comparison operators
    EQ = "=="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    comparison_ops = {EQ, NE, LT, LE, GT, GE}

    # arithmetic operators
    PLUS = "+"
    MINUS = "-"
    TIMES = "*"
    DIVIDE = "/"
    multiplicative_ops = {TIMES, DIVIDE}
    arithmetic_ops = {PLUS, MINUS, TIMES, DIVIDE}

    @classmethod
    def from_value(cls, value: str) -> BinOp:
        """
        Look up an enum instance by a string value (case-insensitive).
        """
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        try:
            return cls(value.strip().upper())
        except ValueError as e:
            raise ValueError(f"Unknown comparison operator: {value}") from e


@acceptor
class Expression(Node):
    def __init__(self, op: BinOp, *args):
        self._op = op
        self._args = args

    def args(self):
        return self._args

    def op(self):
        return self._op

    def __hash__(self):
        return hash((self._op, *self._args))

    def __str__(self):
        left, right = self._args
        return f"{left} {self._op.value} {right}"

    def __repr__(self):
        args = ", ".join([a.__repr__() for a in self._args])
        return f"Expression{{({self._op.value} {args})}}"
