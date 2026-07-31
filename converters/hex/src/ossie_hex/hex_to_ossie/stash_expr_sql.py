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

from __future__ import annotations

from ..hex_types import HexDimension
from ..util.rewrite_refs import RefResolver, rebuild_hex_expr_sql


def ossie_expression_restores(
    dim: HexDimension,
    ossie_sql: str,
    *,
    model_id: str,
    resolve: RefResolver,
) -> bool:
    """Whether the Ossie expression alone rebuilds this dimension's ``expr_sql``.

    Asks the rewrite the import will run, so the payload carries an expression
    only when the answer differs from what was authored. Two shapes do not come
    back: a reference onto a dimension whose own ``expr_sql`` reads some other
    column, which the rewrite quietly repoints, and a ``${relation.field}`` whose
    relation the import cannot place, which comes back as bare SQL.
    """
    authored = None if dim.expr_sql == dim.id else dim.expr_sql
    rebuilt = rebuild_hex_expr_sql(
        ossie_sql,
        model=model_id,
        field=dim.id,
        dimension_id=dim.id,
        resolve=resolve,
    )
    return rebuilt == authored
