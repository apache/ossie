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


def _unqualify_column(node: exp.Expression) -> exp.Expression:
    """Drop the table/schema/database parts of a column reference; other nodes pass through."""
    return exp.Column(this=node.this) if isinstance(node, exp.Column) else node


def _col_name(node: exp.Expression) -> str:
    """Return an aggregate argument with the dataset qualifier stripped from every column reference.

    MSI evaluates a metric's ``expr`` inside its own semantic model, so column
    references must be unqualified: ``orders.amount`` → ``amount`` and
    ``orders.gross - orders.tax`` → ``gross - tax``.
    """
    if isinstance(node, exp.Column):
        return node.name
    return node.transform(_unqualify_column).sql()


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


def _is_constant_expr(expr: str) -> bool:
    """Return True when ``expr`` is a non-null constant such as ``1``, ``2`` or ``TRUE``, not a column."""
    try:
        node = sqlglot.parse_one(expr)
    except sqlglot.errors.SqlglotError:
        return False
    return isinstance(node, exp.Boolean) or (isinstance(node, exp.Literal) and not node.is_string)


def _contains_distinct_row_count(expression: str) -> bool:
    """Return True if ``expression`` contains ``COUNT(DISTINCT <row-count argument>)`` anywhere in its tree.

    ``COUNT(DISTINCT *)`` / ``COUNT(DISTINCT 1)`` and friends parse and run as SQL, but counting distinct
    values of ``*`` or a constant is not a sensible aggregation for a semantic layer: it answers whether
    any row exists (0 or 1), not a meaningful total. The caller should drop the metric with an issue
    rather than fall back to a raw expression, which would wrap this inside another aggregate
    (``SUM(COUNT(DISTINCT ...))``, not valid for MetricFlow to run) and guess a dataset the way a row
    count must not.

    Searches the whole tree, not just the top node, so a wrapped or combined form such as
    ``(COUNT(DISTINCT *))``, ``COUNT(DISTINCT *) * 100`` or ``COALESCE(COUNT(DISTINCT 1), 0)`` is still
    caught, not only a bare ``COUNT(DISTINCT *)`` as the entire expression. The DISTINCT operand is
    unnested before the check, so ``COUNT(DISTINCT (*))`` is caught the same way as ``COUNT(DISTINCT *)``.
    """
    try:
        tree = sqlglot.parse_one(expression.strip())
    except sqlglot.errors.SqlglotError:
        return False
    for count in tree.find_all(exp.Count):
        argument = count.this
        if count.args.get("expressions") or not isinstance(argument, exp.Distinct):
            continue
        operands = argument.expressions
        if len(operands) == 1 and _is_row_count_argument(operands[0].unnest()):
            return True
    return False


def _extract_agg_info(expression: str) -> Optional[Tuple[AggregationType, str, Optional[float], bool]]:
    """Parse a SQL aggregation expression using sqlglot.

    Returns ``(agg_type, expr, percentile, use_discrete_percentile)`` for recognised patterns,
    ``None`` otherwise. ``percentile`` is only set for ``PERCENTILE`` aggregations; it is ``None``
    for all others. ``use_discrete_percentile`` is ``True`` only for ``PERCENTILE_DISC``.
    ``expr`` is the aggregate argument with the dataset qualifier stripped from every column
    reference (a bare column name in the common case). ``COUNT`` of ``*`` or of any non-null constant
    (``COUNT(1)``, ``COUNT(TRUE)``, ...) returns ``ROW_COUNT_EXPR`` instead of a column name;
    ``SUM`` of a constant returns the constant itself (``SUM(2)`` → ``'2'``). ``COUNT(DISTINCT ...)`` of a
    row-count argument, and multi-argument ``COUNT``, return ``None``.
    """
    try:
        tree = sqlglot.parse_one(expression.strip())
    except sqlglot.errors.SqlglotError:
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
        if _is_row_count_argument(argument.unnest()):
            # COUNT(*), COUNT(1), COUNT(TRUE), ... → count all rows; COUNT(DISTINCT ...) of one is not valid SQL.
            # Unnested so a redundant paren, e.g. COUNT(DISTINCT (*)), is still recognised.
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

    # SUM(col), or SUM(<constant>). A constant keeps its own value (SUM(2) is twice the row count,
    # not SUM(1)); the caller uses _is_constant_expr to send it through the same dataset check as
    # COUNT(*), since a constant has no column to place it in a dataset.
    if isinstance(tree, exp.Sum):
        argument = tree.this.unnest()
        if _is_constant_expr(argument.sql()):
            return AggregationType.SUM, argument.sql(), None, False
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
    except sqlglot.errors.SqlglotError:
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
    except sqlglot.errors.SqlglotError:
        return None

    qualifiers = {
        ".".join(part.sql() for part in column.parts[:-1])
        for column in tree.find_all(exp.Column)
        if len(column.parts) > 1
    }
    return qualifiers.pop() if len(qualifiers) == 1 else None
