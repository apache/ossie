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

from ossie import OssieExpression

from .context import ExportContext
from .load_ossie_dialect_expression import (
    validate_ossie_field_dialect_expression,
    validate_ossie_metric_dialect_expression,
)
from .pick_ossie_dialect_expression import pick_ossie_dialect_expression


def load_ossie_field_expression(
    ossie_expression: OssieExpression,
    *,
    ctx: ExportContext,
) -> OssieExpression:
    """Load an Ossie expression declared on an Ossie field.

    Attempts to find the preferred dialect and validates the expression.

    Returns the original expression regardless of validation problems.
    """
    # ASSUMPTION: Ossie field expressions are only allowed to reference underlying
    # physical columns (on database table/view/query) and do not contain logical
    # field references (Ossie FieldExpr of any shape). So we do nothing to validate
    # the parsed output of the dialect expression(s).
    with ctx.problem_scope("dialects"):
        choice = pick_ossie_dialect_expression(ossie_expression.dialects, ctx=ctx)
        if choice is not None:
            with ctx.problem_scope(choice.dialect.value):
                validate_ossie_field_dialect_expression(choice, ctx=ctx)
        return ossie_expression


def load_ossie_metric_expression(
    expression: OssieExpression,
    *,
    field_names: list[tuple[str, str]],
    ctx: ExportContext,
) -> OssieExpression:
    """Load an Ossie expression declared on an Ossie metric.

    Args:
      - `field_names`: reachable (dataset, field) name pairs in the semantic model.

    Attempts to find the preferred dialect and validates the expression.

    Returns the original expression regardless of validation problems.
    """
    with ctx.problem_scope("dialects"):
        choice = pick_ossie_dialect_expression(expression.dialects, ctx=ctx)
        if choice is not None:
            with ctx.problem_scope(choice.dialect.value):
                validate_ossie_metric_dialect_expression(
                    choice, field_names=field_names, ctx=ctx
                )
        return expression
