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


from collections.abc import Callable

from ossie import OssieDialectExpression

from ..hex import HexSql
from ..util.parse_sql import exp
from .context import ExportContext
from .load_ossie_dialect_expression import parse_ossie_dialect_expression

OssieRefResolver = Callable[[str, str], tuple[str | None, str]]
"""A function that resolves components of an Ossie field expression (i.e. 
reference) to components of a Hex semantic reference.

In this case, only qualified field expressions (e.g. `dataset.field`) are supported.
"""


def convert_ossie_dialect_expression(
    ossie_dialect_expression: OssieDialectExpression | None,
    *,
    resolve: OssieRefResolver | None,
    ctx: ExportContext,
) -> HexSql | None:
    """Convert an Ossie dialect expression to an equivalent Hex SQL string.

    If a resolver is provided, it is used to resolve Ossie field expression syntax
    to Hex semantic reference syntax.
    """
    if ossie_dialect_expression is None:
        return None

    if resolve is None:
        sql: HexSql = ossie_dialect_expression.expression
        return sql

    def replace_reference(node: exp.Expression) -> exp.Expression:
        if not isinstance(node, exp.Column) or not node.table:
            return node
        hex_relation_id, hex_item_id = resolve(node.table, node.name)
        if hex_relation_id is None:
            replacement = f"${{{hex_item_id}}}"
        else:
            replacement = f"${{{hex_relation_id}.{hex_item_id}}}"
        return exp.Var(this=replacement)

    parsed = parse_ossie_dialect_expression(ossie_dialect_expression, ctx=ctx)
    if parsed is None:
        return None
    transformed = parsed.expr.transform(replace_reference)
    sql = transformed.sql(dialect=parsed.dialect)
    return sql
