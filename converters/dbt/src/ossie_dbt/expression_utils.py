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

# expr for "count all rows": MetricFlow wraps a count's expr in CASE WHEN, where a bare * is invalid
ROW_COUNT_EXPR = "1"


def _strip_qualifier(col: str) -> str:
    """Strip a leading dataset qualifier, e.g. 'orders.amount' → 'amount'."""
    return col.rsplit(".", 1)[-1] if "." in col else col


def _col_name(node: exp.Expression) -> str:
    """Return the bare (unqualified) column name from a sqlglot expression node."""
    if isinstance(node, exp.Column):
        return node.name
    rendered = node.sql()
    return _strip_qualifier(rendered)


def _is_row_count_argument(node: exp.Expression) -> bool:
    """Return True for ``*`` (bare or qualified) and for any non-null constant.

    None of these can ever be NULL, so ``COUNT()`` of one counts every row. A string literal is left
    alone, since ``COUNT('x')`` is not a row-count idiom anyone writes on purpose.
    """
    if isinstance(node, exp.Star) or (isinstance(node, exp.Column) and isinstance(node.this, exp.Star)):
        return True
    if isinstance(node, exp.Boolean):
        return True
    if isinstance(node, exp.Literal) and not node.is_string:
        return True
    return False


def _extract_agg_info(expression: str) -> Optional[Tuple[AggregationType, str, Optional[float], bool]]:
    """Parse a SQL aggregation expression using sqlglot.

    Returns ``(agg_type, bare_col, percentile, use_discrete_percentile)`` for recognised patterns,
    ``None`` otherwise. ``percentile`` is only set for ``PERCENTILE`` aggregations; it is ``None``
    for all others. ``use_discrete_percentile`` is ``True`` only for ``PERCENTILE_DISC``.
    The returned column name has any dataset qualifier stripped. ``COUNT`` of ``*`` or of any non-null constant
    (``COUNT(1)``, ``COUNT(TRUE)``, ...) returns ``ROW_COUNT_EXPR`` instead of a column name;
    ``COUNT(DISTINCT ...)`` of one of those, and multi-argument ``COUNT``, return ``None``.
    """
    try:
        tree = sqlglot.parse_one(expression.strip())
    except sqlglot.errors.ParseError:
        return None

    if isinstance(tree, exp.Count):
        # COUNT(a, b) has no single-column equivalent
        if tree.args.get("expressions"):
            return None
        argument, distinct = tree.this, False
        if isinstance(argument, exp.Distinct):
            operands = argument.expressions
            if len(operands) != 1:
                return None
            argument, distinct = operands[0], True
        if _is_row_count_argument(argument):
            # COUNT(*), COUNT(1), COUNT(TRUE), ... → count all rows; COUNT(DISTINCT ...) of one is not valid SQL
            return None if distinct else (AggregationType.COUNT, ROW_COUNT_EXPR, None, False)
        return (AggregationType.COUNT_DISTINCT if distinct else AggregationType.COUNT), _col_name(argument), None, False

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
            return AggregationType.SUM_BOOLEAN, ifs[0].this.sql(), None, False
        return None

    # SUM(col)
    if isinstance(tree, exp.Sum):
        return AggregationType.SUM, _col_name(tree.this), None, False

    if isinstance(tree, exp.Avg):
        return AggregationType.AVERAGE, _col_name(tree.this), None, False

    if isinstance(tree, exp.Min):
        return AggregationType.MIN, _col_name(tree.this), None, False

    if isinstance(tree, exp.Max):
        return AggregationType.MAX, _col_name(tree.this), None, False

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
                return AggregationType.MEDIAN, col, None, False
            return AggregationType.PERCENTILE, col, p, isinstance(inner, exp.PercentileDisc)

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


def _get_dataset_qualifier(expression: str) -> Optional[str]:
    """Return the sole dataset qualifier referenced by an expression, if present."""
    try:
        tree = sqlglot.parse_one(expression.strip())
    except sqlglot.errors.ParseError:
        return None

    qualifiers = {
        ".".join(part.sql() for part in column.parts[:-1])
        for column in tree.find_all(exp.Column)
        if len(column.parts) > 1
    }
    return qualifiers.pop() if len(qualifiers) == 1 else None
