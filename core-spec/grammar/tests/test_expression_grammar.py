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

"""Parse the fixture expressions and check operator precedence."""

from pathlib import Path

import yaml
from antlr4 import CommonTokenStream, InputStream
from antlr4.error.ErrorListener import ErrorListener

REPO = Path(__file__).resolve().parents[3]
FIXTURES = REPO / "compliance" / "expressions"
EXAMPLES = REPO / "examples"
PORTABLE_DIALECTS = {"ANSI_SQL", "OSSIE_SQL_2026"}


class Collect(ErrorListener):
    def __init__(self):
        self.errors = []

    def syntaxError(self, recognizer, symbol, line, column, msg, e):
        self.errors.append(f"{line}:{column} {msg}")


def parse(ossie, text):
    """Return (tree, syntax errors) for the whole input."""
    lexer_cls, parser_cls = ossie
    errors = Collect()
    lexer = lexer_cls(InputStream(text))
    lexer.removeErrorListeners()
    lexer.addErrorListener(errors)
    parser = parser_cls(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(errors)
    return parser.expression(), errors.errors


def lines(name):
    """One expression per line; blank lines and lines starting with '#' are skipped."""
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.strip() and not line.startswith("#")]


def top(ossie, text):
    tree, errors = parse(ossie, text)
    assert not errors, f"{text} -> {errors}"
    return tree.expr()


def test_valid_expressions_parse(ossie):
    failures = {line: e for line in lines("valid.txt") if (e := parse(ossie, line)[1])}
    assert not failures


def test_invalid_expressions_are_rejected(ossie):
    accepted = [line for line in lines("invalid.txt") if not parse(ossie, line)[1]]
    assert not accepted


def portable_expressions(node):
    """Yield every OSSIE_SQL_2026 / ANSI_SQL expression string in a loaded YAML document."""
    if isinstance(node, dict):
        if isinstance(node.get("sql"), str):
            yield node["sql"]
        if node.get("dialect") in PORTABLE_DIALECTS and isinstance(node.get("expression"), str):
            yield node["expression"]
        for value in node.values():
            yield from portable_expressions(value)
    elif isinstance(node, list):
        for item in node:
            yield from portable_expressions(item)


def example_expressions():
    for path in sorted(EXAMPLES.glob("*.yaml")):
        for text in portable_expressions(yaml.safe_load(path.read_text(encoding="utf-8"))):
            yield f"{path.name}: {text}", text


def test_example_models_parse(ossie):
    """Every portable expression in examples/ must parse, so grammar and examples stay in sync."""
    found = list(example_expressions())
    assert found, "no expressions found in examples/"
    failures = {label: e for label, text in found if (e := parse(ossie, text)[1])}
    assert not failures


def kind(ctx):
    return type(ctx).__name__.removesuffix("Context")


def test_multiplication_binds_tighter_than_addition(ossie):
    value = top(ossie, "1 + 2 * 3")
    assert kind(value) == "ValueOnlyExpr"
    add = value.valueExpr()
    assert kind(add) == "AdditiveValue"
    assert kind(add.valueExpr(1)) == "MultiplicativeValue"


def test_not_binds_looser_than_comparison(ossie):
    not_expr = top(ossie, "NOT a = b")
    assert kind(not_expr) == "NotExpr"
    assert kind(not_expr.expr()) == "ComparisonExpr"


def test_not_binds_tighter_than_and(ossie):
    and_expr = top(ossie, "NOT a AND b")
    assert kind(and_expr) == "AndExpr"
    assert kind(and_expr.expr(0)) == "NotExpr"


def test_and_binds_tighter_than_or(ossie):
    or_expr = top(ossie, "a OR b AND c")
    assert kind(or_expr) == "OrExpr"
    assert kind(or_expr.expr(1)) == "AndExpr"


def test_between_keeps_its_and_out_of_the_logical_and(ossie):
    and_expr = top(ossie, "x BETWEEN 1 AND 2 AND y")
    assert kind(and_expr) == "AndExpr"
    assert kind(and_expr.expr(0)) == "BetweenExpr"


def test_chained_comparison_needs_parentheses(ossie):
    assert parse(ossie, "a = b = c")[1]
    assert not parse(ossie, "(a = b) = c")[1]
