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

"""Shared helpers for building/reading Ossie ``OssieExpression`` values from Sigma formulas."""

from __future__ import annotations

from typing import Optional

from ossie import OssieDialect, OssieDialectExpression, OssieExpression

from ossie_sigma.sigma_formula import (
    BinOp,
    ColumnRef,
    FormulaNode,
    FormulaParseError,
    FuncCall,
    UnaryOp,
    parse_formula,
    to_ansi_sql,
)


def qualify(node: FormulaNode, table_name: str) -> FormulaNode:
    """Rewrite every unqualified :class:`ColumnRef` in *node* to reference *table_name*.

    Sigma metric formulas are scoped to their owning element and reference sibling
    columns unqualified (e.g. ``Sum([Amount])``); Ossie metrics live at the model
    level and may span datasets via relationships, so their expressions must be
    fully dataset-qualified.
    """
    if isinstance(node, ColumnRef):
        return node if node.table is not None else ColumnRef(table_name, node.column)
    if isinstance(node, UnaryOp):
        return UnaryOp(node.op, qualify(node.operand, table_name))
    if isinstance(node, BinOp):
        return BinOp(node.op, qualify(node.left, table_name), qualify(node.right, table_name))
    if isinstance(node, FuncCall):
        return FuncCall(node.name, tuple(qualify(a, table_name) for a in node.args))
    return node


def build_expression(formula: str, dataset_alias: Optional[str] = None) -> OssieExpression:
    """Build an :class:`OssieExpression` from a raw Sigma formula.

    Always includes a ``SIGMA``-dialect entry carrying the original formula text
    verbatim (guaranteeing lossless round-tripping), plus an ``ANSI_SQL`` entry when
    the formula translates cleanly.
    """
    dialects = [OssieDialectExpression(dialect=OssieDialect.SIGMA, expression=formula)]
    try:
        node = parse_formula(formula)
        sql = to_ansi_sql(node, dataset_alias=dataset_alias)
    except FormulaParseError:
        sql = None
    if sql is not None:
        dialects.append(OssieDialectExpression(dialect=OssieDialect.ANSI_SQL, expression=sql))
    return OssieExpression(dialects=dialects)


def sigma_dialect_text(expression: OssieExpression) -> Optional[str]:
    """Return the raw Sigma formula text from *expression*, if a ``SIGMA`` dialect entry exists."""
    for dialect_expr in expression.dialects:
        if dialect_expr.dialect == OssieDialect.SIGMA:
            return dialect_expr.expression
    return None


def ansi_sql_text(expression: OssieExpression) -> Optional[str]:
    """Return the ``ANSI_SQL`` dialect entry from *expression*, if present."""
    for dialect_expr in expression.dialects:
        if dialect_expr.dialect == OssieDialect.ANSI_SQL:
            return dialect_expr.expression
    return None


def infer_single_dataset_qualifier(sql: str, dataset_names: set) -> Optional[str]:
    """Return the sole known dataset referenced by *sql*'s qualified columns, if unambiguous.

    Used to place a model-level metric with no preserved Sigma ``element_id`` (i.e. one
    authored by, or round-tripped through, a non-Sigma tool) back onto a single Sigma
    element — Sigma metrics are always scoped to one element, unlike Ossie metrics,
    which may span datasets via relationships.
    """
    import sqlglot
    from sqlglot import expressions as exp

    try:
        tree = sqlglot.parse_one(sql)
    except Exception:  # noqa: BLE001
        return None

    qualifiers = {
        column.parts[0].name
        for column in tree.find_all(exp.Column)
        if len(column.parts) > 1 and column.parts[0].name in dataset_names
    }
    return qualifiers.pop() if len(qualifiers) == 1 else None
