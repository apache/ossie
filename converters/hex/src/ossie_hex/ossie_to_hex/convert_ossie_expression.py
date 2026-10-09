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

from ..hex import HexSql
from .context import ExportContext
from .convert_ossie_dialect_expression import (
    OssieRefResolver,
    convert_ossie_dialect_expression,
)
from .pick_ossie_dialect_expression import pick_ossie_dialect_expression


def convert_ossie_expression(
    ossie_expression: OssieExpression,
    *,
    resolve: OssieRefResolver | None,
    ctx: ExportContext,
) -> HexSql | None:
    """Convert an Ossie expression to a Hex SQL string.

    Picks the dialect expression according to the value set in context.
    If a resolver is provided, it is used to resolve Ossie expression syntax
    to Hex semantic reference syntax.
    """
    ossie_dialect_expression = pick_ossie_dialect_expression(
        ossie_expression.dialects, ctx=ctx
    )
    hex_sql = convert_ossie_dialect_expression(
        ossie_dialect_expression,
        resolve=resolve,
        ctx=ctx,
    )
    return hex_sql
