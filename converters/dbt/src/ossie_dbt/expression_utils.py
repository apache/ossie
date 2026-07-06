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

from typing import Optional, Tuple

import sqlglot
import sqlglot.expressions as exp

from metricflow_semantic_interfaces.type_enums import AggregationType


def _strip_qualifier(col: str) -> str:
    """Strip a leading dataset qualifier, e.g. 'orders.amount' → 'amount'."""
    return col.rsplit(".", 1)[-1] if "." in col else col


def _col_name(node: exp.Expression) -> str:
    """Return the bare (unqualified) column name from a sqlglot expression node."""
    if isinstance(node, exp.Column):
        return node.name
    rendered = node.sql()
    return _strip_qualifier(rendered)


def _extract_agg_info(expression: str) -> Optional[Tuple[AggregationType, str, Optional[float]]]:
    """Parse a SQL aggregation expression using sqlglot.

    Returns ``(agg_type, bare_col, percentile)`` for recognised patterns, ``None`` otherwise.
    ``percentile`` is only set for ``PERCENTILE`` aggregations; it is ``None`` for all others.
    The returned column name has any dataset qualifier stripped.
    """
    try:
        tree = sqlglot.parse_one(expression.strip())
    except sqlglot.errors.ParseError:
        return None

    # COUNT(DISTINCT col)
    if isinstance(tree, exp.Count) and isinstance(tree.this, exp.Distinct):
        cols = tree.this.expressions
        if len(cols) == 1:
            return AggregationType.COUNT_DISTINCT, _col_name(cols[0]), None
        return None

    # COUNT(col)
    if isinstance(tree, exp.Count):
        return AggregationType.COUNT, _col_name(tree.this), None

    # SUM(CASE WHEN col THEN 1 ELSE 0 END) → SUM_BOOLEAN
    if isinstance(tree, exp.Sum) and isinstance(tree.this, exp.Case):
        case = tree.this
        ifs = case.args.get("ifs", [])
        default = case.args.get("default")
        if (
            len(ifs) == 1
            and isinstance(default, exp.Literal)
            and default.name == "0"
            and isinstance(ifs[0].args.get("true"), exp.Literal)
            and ifs[0].args["true"].name == "1"
        ):
            return AggregationType.SUM_BOOLEAN, ifs[0].this.sql(), None
        return None

    # SUM(col)
    if isinstance(tree, exp.Sum):
        return AggregationType.SUM, _col_name(tree.this), None

    if isinstance(tree, exp.Avg):
        return AggregationType.AVERAGE, _col_name(tree.this), None

    if isinstance(tree, exp.Min):
        return AggregationType.MIN, _col_name(tree.this), None

    if isinstance(tree, exp.Max):
        return AggregationType.MAX, _col_name(tree.this), None

    # PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY col)
    # sqlglot parses this as WithinGroup(this=PercentileCont(...), expression=Order(...))
    if isinstance(tree, exp.WithinGroup):
        inner = tree.this
        order = tree.args.get("expression")
        if (
            isinstance(inner, (exp.PercentileCont, exp.PercentileDisc))
            and isinstance(order, exp.Order)
            and order.expressions
        ):
            ordered = order.expressions[0]
            col_node = ordered.this if isinstance(ordered, exp.Ordered) else ordered
            col = _col_name(col_node)
            try:
                p = float(inner.this.name)
            except (AttributeError, ValueError):
                return None
            if p == 0.5 and isinstance(inner, exp.PercentileCont):
                return AggregationType.MEDIAN, col, None
            return AggregationType.PERCENTILE, col, p

    return None


def _try_parse_ratio(expr_str: str) -> Optional[Tuple[str, str]]:
    """Try to parse ``(expr_a) / (expr_b)`` using sqlglot, returning ``(num_expr, den_expr)`` or None."""
    try:
        tree = sqlglot.parse_one(expr_str.strip())
    except sqlglot.errors.ParseError:
        return None

    if not isinstance(tree, exp.Div):
        return None

    num = tree.this
    den = tree.expression

    # Unwrap outer parentheses if present
    if isinstance(num, exp.Paren):
        num = num.this
    if isinstance(den, exp.Paren):
        den = den.this

    return num.sql(), den.sql()


def _get_raw_inner_col(expression: str) -> Optional[str]:
    """Extract the raw column reference from inside a simple aggregation, before stripping qualifiers."""
    try:
        tree = sqlglot.parse_one(expression.strip())
    except sqlglot.errors.ParseError:
        return None

    if not isinstance(tree, exp.AggFunc):
        return None

    inner = tree.this
    if inner is None:
        return None

    # For COUNT(DISTINCT col), unwrap the Distinct node
    if isinstance(inner, exp.Distinct) and inner.expressions:
        return inner.expressions[0].sql()

    return inner.sql()
