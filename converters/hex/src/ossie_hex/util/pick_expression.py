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

from ossie import OSIDialect, OSIExpression


def pick_expression(
    osi_expression: OSIExpression | None,
    preferred: OSIDialect | None = None,
) -> str | None:
    """Choose an SQL string from an Ossie expression.

    Preference: caller dialect, then ANSI_SQL, then first available.
    """
    dialects = {
        entry.dialect: entry.expression
        for entry in (osi_expression.dialects if osi_expression is not None else [])
    }
    if preferred and dialects.get(preferred):
        return dialects[preferred]
    if dialects.get(OSIDialect.ANSI_SQL):
        return dialects[OSIDialect.ANSI_SQL]
    for expression in dialects.values():
        if expression:
            return expression
    return None
