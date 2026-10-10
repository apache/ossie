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


"""Token-level coverage for `FormulaLexer` numeric literals."""

from __future__ import annotations

import pytest

from ossie_ontology.expr.formula.lexer import FormulaLexer


def tokens(text: str) -> list[tuple[str, object]]:
    lexer = FormulaLexer().lexer
    lexer.input(text)
    return [(tok.type, tok.value) for tok in lexer]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("10.5", [("FLOAT", 10.5)]),
        ("100.0", [("FLOAT", 100.0)]),
        ("31.4", [("FLOAT", 31.4)]),
        ("1.5", [("FLOAT", 1.5)]),
        ("42", [("INTEGER", 42)]),
        ("31.4 + 10.5", [("FLOAT", 31.4), ("PLUS", "+"), ("FLOAT", 10.5)]),
        ("100 + 2.25", [("INTEGER", 100), ("PLUS", "+"), ("FLOAT", 2.25)]),
    ],
)
def test_numeric_literals(text: str, expected: list[tuple[str, object]]):
    assert tokens(text) == expected
