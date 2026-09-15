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

from ossie import OssieDialectExpression, OssieExpression

from .context import ExportContext
from .load_ossie_dialect_expression import (
    validate_ossie_field_dialect_expression,
    validate_ossie_metric_dialect_expression,
)


def load_ossie_field_expression(
    ossie_expression: OssieExpression,
    *,
    ctx: ExportContext,
) -> OssieExpression | None:
    """Load an Ossie expression declared on an Ossie field.

    An expression is valid if it contains at least one valid Ossie dialect expression.

    Returns an Ossie expression with only valid dialects, or None if the expression is invalid.
    """
    # ASSUMPTION: Ossie field expressions are only allowed to reference underlying
    # physical columns (on database table/view/query) and do not contain logical
    # field references (Ossie FieldExpr of any shape). So we do nothing to validate
    # the parsed output of the dialect expression(s).
    result: OssieExpression | None = None
    with ctx.problem_scope("dialects"):
        dialects = list[OssieDialectExpression]()
        for entry in ossie_expression.dialects:
            with ctx.problem_scope(entry.dialect.value):
                valid = validate_ossie_field_dialect_expression(entry, ctx=ctx)
                if valid:
                    dialects.append(entry)
        result = OssieExpression(dialects=dialects)
        if not result.dialects:
            ctx.error("Expression must have at least one valid dialect")
            result = None
    return result


def load_ossie_metric_expression(
    expression: OssieExpression,
    *,
    field_names: list[tuple[str, str]],
    ctx: ExportContext,
) -> OssieExpression | None:
    """Load an Ossie expression declared on an Ossie metric.

    Args:
      - `field_names`: reachable (dataset, field) name pairs in the semantic model.

    An expression is valid if it contains at least one valid Ossie dialect expression.

    Returns an Ossie expression with only valid dialects, or None if the expression is invalid.
    """
    result: OssieExpression | None = None
    with ctx.problem_scope("dialects"):
        dialects = list[OssieDialectExpression]()
        for entry in expression.dialects:
            with ctx.problem_scope(entry.dialect.value):
                valid = validate_ossie_metric_dialect_expression(
                    entry, field_names=field_names, ctx=ctx
                )
                if valid:
                    dialects.append(entry)
        result = OssieExpression(dialects=dialects)
        if not result.dialects:
            ctx.error("Expression must have at least one valid dialect")
            result = None
    return result
