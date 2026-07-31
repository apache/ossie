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

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from .hex_common import HexVisibility
from .hex_dimension import HexDimension
from .hex_id import HexID, id_to_name
from .hex_measure import HexMeasure
from .hex_relation import HexRelation


class HexModel(BaseModel):
    """A reusable data table definition with measures, dimensions, and relationships."""

    model_config = ConfigDict(extra="forbid")

    id: HexID
    type: Literal["model"] = "model"
    base_sql_table: str | None = None
    base_sql_query: str | None = None
    dimensions: list[HexDimension] = Field(default_factory=list)
    measures: list[HexMeasure] = Field(default_factory=list)
    relations: list[HexRelation] = Field(default_factory=list)
    name: str = Field(default_factory=lambda data: id_to_name(data["id"]))
    description: str = ""
    visibility: HexVisibility = HexVisibility.PUBLIC

    @model_validator(mode="after")
    def _base_validator(self) -> Self:
        if not self.base_sql_query and not self.base_sql_table:
            raise PydanticCustomError(
                "custom.missing",
                "Either `base_sql_query` or `base_sql_table` must be provided",
            )
        if self.base_sql_query and self.base_sql_table:
            raise PydanticCustomError(
                "custom.extra_forbidden",
                "Only one of `base_sql_query` or `base_sql_table` can be provided",
                {"conflict_keys": ["base_sql_query", "base_sql_table"]},
            )
        return self
