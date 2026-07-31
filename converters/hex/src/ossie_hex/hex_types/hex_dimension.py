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

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from .hex_common import HexVisibility
from .hex_datatype import HexDataType
from .hex_id import HexID, id_to_name


class HexDimension(BaseModel):
    """A field backed by a physical column, SQL expression, or formula-based expression."""

    model_config = ConfigDict(extra="forbid")

    id: HexID
    type: HexDataType
    expr_sql: str | None = None
    expr_calc: str | None = None
    unique: bool = False
    name: str = Field(default_factory=lambda data: id_to_name(data["id"]))
    description: str = ""
    visibility: HexVisibility = HexVisibility.PUBLIC

    @model_validator(mode="after")
    def _expr_validator(self) -> Self:
        if self.expr_sql and self.expr_calc:
            raise PydanticCustomError(
                "custom.extra_forbidden",
                "Only one of `expr_sql` or `expr_calc` can be provided",
                {"conflict_keys": ["expr_sql", "expr_calc"]},
            )
        if not self.expr_sql and not self.expr_calc:
            self.expr_sql = self.id
        return self
