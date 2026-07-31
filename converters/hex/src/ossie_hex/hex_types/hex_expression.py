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

import re
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import PydanticCustomError

from .hex_datatype import HexDataType

# Hex semantic references use ${...} in SQL expressions. Logically these should
# not be within string literals or comments (left to parser).
HEX_REF_PATTERN = r"\$\{\s*([^}]+?)\s*\}"
HEX_REF_RE = re.compile(HEX_REF_PATTERN)


class HexScalarExpression(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: HexDataType
    expr_sql: str | None = None
    expr_calc: str | None = None

    @model_validator(mode="after")
    def _expr_validator(self) -> Self:
        if self.expr_sql and self.expr_calc:
            raise PydanticCustomError(
                "custom.extra_forbidden",
                "Only one of `expr_sql` or `expr_calc` can be provided",
                {"conflict_keys": ["expr_sql", "expr_calc"]},
            )
        return self


class HexScalarExpressionDefaultNumber(HexScalarExpression):
    type: HexDataType = HexDataType.NUMBER


class HexScalarExpressionDefaultBoolean(HexScalarExpression):
    type: HexDataType = HexDataType.BOOLEAN
