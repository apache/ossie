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

"""Construct validation per the spec's "Not Supported in Expressions" table.

Ossie expressions are scalar/aggregate/window fragments, not full queries.
The spec explicitly disallows ``SELECT``/``FROM``/``JOIN``, ``GROUP BY``,
``WHERE``, subqueries, CTEs, set operations (``UNION``/``INTERSECT``/
``EXCEPT``), and DDL/DML -- each with a documented reason ("use filter
property instead", "use field references instead", etc.).

Per the spec's "Dialect Extensions" section, unrecognized *function names*
pass through by default (see :mod:`ossie_sql.functions`); this module only
rejects the constructs the spec explicitly disallows. It does not maintain
a function-name whitelist.

Bind parameters/placeholders (``:n``, ``?``) are not documented anywhere in
the expression language -- Ossie expressions are static field/metric
bodies, not parameterized queries -- so they're rejected here too.
"""

from __future__ import annotations

from sqlglot import exp

from ossie_sql.windows import first_unsupported_frame

# WHERE/GROUP BY/JOIN clauses can only appear inside a SELECT statement in
# SQL grammar, so rejecting exp.Select transitively covers them; exp.Join is
# listed explicitly too since the spec calls it out by name in its own row.
_DISALLOWED_NODE_TYPES: tuple[type[exp.Expression], ...] = (
    exp.Select,
    exp.With,
    exp.Union,
    exp.Intersect,
    exp.Except,
    exp.Join,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Placeholder,
    exp.Parameter,
)


class UnsupportedConstructError(ValueError):
    """Raised when an expression contains a construct the spec disallows."""

    def __init__(self, node: exp.Expression) -> None:
        self.node = node
        super().__init__(
            f"{type(node).__name__!r} is not a supported Ossie expression construct: {node.sql()!r}"
        )


def validate_expression(expression: exp.Expr) -> None:
    """Raise :class:`UnsupportedConstructError` for any disallowed construct.

    Walks the full AST (not just the top level) so a disallowed construct
    nested inside an otherwise-valid expression (e.g. a subquery inside an
    ``IN`` list) is still caught. Does not check function names -- see the
    module docstring.
    """
    for node in expression.walk():
        if isinstance(node, _DISALLOWED_NODE_TYPES):
            raise UnsupportedConstructError(node)

    unsupported_frame = first_unsupported_frame(expression)
    if unsupported_frame is not None:
        raise UnsupportedConstructError(unsupported_frame)


__all__ = ["UnsupportedConstructError", "validate_expression"]
