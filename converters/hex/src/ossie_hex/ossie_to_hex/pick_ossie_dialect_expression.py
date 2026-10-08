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

from ossie import OssieDialect, OssieDialectExpression

from .context import ExportContext


def pick_ossie_dialect_expression(
    ossie_dialect_expressions: list[OssieDialectExpression],
    *,
    ctx: ExportContext,
) -> OssieDialectExpression | None:
    """Pick an Ossie dialect expression from a list of Ossie dialect expressions.

    Prefers to pick the current dialect, then falls back to ANSI SQL, then the first in the list.

    Returns a dialect expression or None if no fallback is found.
    """
    fallback: OssieDialectExpression | None = None
    for entry in ossie_dialect_expressions:
        if entry.dialect == ctx.ossie_dialect:
            return entry
        # fallback preference: (1) ansi sql, (2) first in the list
        elif entry.dialect == OssieDialect.ANSI_SQL or fallback is None:
            fallback = entry
    if ctx.current_phase_name == "load":
        if fallback is not None:
            ctx.warn(
                f"Preferred dialect {ctx.ossie_dialect} not found for expression; using fallback dialect {fallback.dialect}"
            )
        else:
            ctx.error(
                f"Preferred dialect {ctx.ossie_dialect} not found for expression and no fallback dialect found"
            )
    return fallback
