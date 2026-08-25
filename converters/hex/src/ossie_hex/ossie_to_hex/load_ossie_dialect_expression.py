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

from typing import assert_never

from ossie import OssieDialect, OssieDialectExpression

from ..util.parse_sql import ParsedExpr, SQLGlotDialect, exp, parse_one
from .context import ExportContext


def validate_ossie_field_dialect_expression(
    ossie_dialect_expression: OssieDialectExpression,
    *,
    ctx: ExportContext,
) -> bool:
    """Validate an Ossie dialect expression of an Ossie field.

    An expression is valid if it can be parsed according to its dialect.

    Returns whether the expression is valid.
    """
    parsed = parse_ossie_dialect_expression(ossie_dialect_expression, ctx=ctx)
    return parsed is not None


def validate_ossie_metric_dialect_expression(
    ossie_dialect_expression: OssieDialectExpression,
    *,
    field_names: list[tuple[str, str]],
    ctx: ExportContext,
) -> bool:
    """Validate an Ossie dialect expression of an Ossie metric.

    An expression is valid if
        - it can be parsed according to its dialect.
        - all referenced fields are in the semantic model.

    Returns whether the expression is valid.
    """
    valid = True
    parsed = parse_ossie_dialect_expression(ossie_dialect_expression, ctx=ctx)
    if not parsed:
        valid = False
    else:
        for identifier in parsed.expr.find_all(exp.Column):
            pair = (identifier.table, identifier.name)
            if pair not in field_names:
                ctx.error(
                    f"Field expression references field not in semantic model: {pair[0]}.{pair[1]}"
                )
                valid = False
    return valid


def parse_ossie_dialect_expression(
    ossie_dialect_expression: OssieDialectExpression,
    *,
    ctx: ExportContext,
) -> ParsedExpr | None:
    """Parse an Ossie dialect expression.

    Returns the parsed expression, or None if the expression cannot be parsed.
    """
    dialect = _to_sqlglot_dialect(ossie_dialect_expression.dialect)
    with ctx.problem_scope("expression"):
        try:
            expr = parse_one(ossie_dialect_expression.expression, read=dialect)
        except Exception as e:  # noqa: BLE001
            ctx.error(f"Unable to parse: {e}")
            return None
    return ParsedExpr(expr=expr, dialect=dialect)


def _to_sqlglot_dialect(ossie_dialect: OssieDialect) -> SQLGlotDialect | None:
    """Translate an Ossie dialect to a sqlglot dialect.

    Returns a sqlglot dialect or None (signal for sqlglot base behavior).
    """

    if ossie_dialect == OssieDialect.BIGQUERY:
        read = SQLGlotDialect.BIGQUERY
    elif ossie_dialect == OssieDialect.DATABRICKS:
        read = SQLGlotDialect.DATABRICKS
    elif ossie_dialect == OssieDialect.SNOWFLAKE:
        read = SQLGlotDialect.SNOWFLAKE
    elif ossie_dialect == OssieDialect.TABLEAU:
        read = SQLGlotDialect.TABLEAU
    elif ossie_dialect in (
        OssieDialect.ANSI_SQL,
        OssieDialect.MDX,
        OssieDialect.MAQL,
        OssieDialect.THOUGHTSPOT,
    ):
        # There's no parallel for these
        read = None
    else:
        assert_never(ossie_dialect)

    return read
