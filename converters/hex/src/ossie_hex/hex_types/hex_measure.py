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

from enum import Enum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from .hex_common import HexVisibility
from .hex_datatype import HexDataType
from .hex_expression import (
    HexScalarExpressionDefaultBoolean,
    HexScalarExpressionDefaultNumber,
)
from .hex_id import HexID, id_to_name


class HexMeasureFuncName(str, Enum):
    COUNT = "count"
    COUNT_DISTINCT = "count_distinct"
    SUM = "sum"
    SUM_BOOLEAN = "sum_boolean"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    MEDIAN = "median"
    STDDEV = "stddev"
    STDDEV_POP = "stddev_pop"
    VARIANCE = "variance"
    VARIANCE_POP = "variance_pop"


class HexSemiAdditiveOverMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    pick: Literal["min", "max"] = "max"


class HexSemiAdditive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    over: list[HexSemiAdditiveOverMember] = Field(min_length=1, max_length=1)
    groupings: list[str] = Field(default_factory=list)


class HexMeasure(BaseModel):
    """An aggregation that derives a single value from a group of records."""

    model_config = ConfigDict(extra="forbid")

    id: HexID
    func: HexMeasureFuncName | None = None
    of: str | HexScalarExpressionDefaultNumber | None = None
    func_sql: str | None = None
    func_calc: str | None = None
    type: HexDataType = HexDataType.NUMBER
    filters: list[str | HexScalarExpressionDefaultBoolean] = Field(default_factory=list)
    name: str = Field(default_factory=lambda data: id_to_name(data["id"]))
    description: str = ""
    visibility: HexVisibility = HexVisibility.PUBLIC
    semi_additive: HexSemiAdditive | None = None

    @model_validator(mode="after")
    def _func_validator(self) -> Self:
        specified = [
            key
            for key in ("func", "func_sql", "func_calc")
            if getattr(self, key) is not None
        ]
        if not specified:
            raise PydanticCustomError(
                "custom.missing",
                "One of `func`, `func_sql`, or `func_calc` must be provided",
            )
        if len(specified) > 1:
            raise PydanticCustomError(
                "custom.extra_forbidden",
                "Only one of `func`, `func_sql`, or `func_calc` can be provided",
                {"conflict_keys": specified},
            )
        if self.func:
            if not self.of and self.func != HexMeasureFuncName.COUNT:
                raise PydanticCustomError(
                    "custom.missing",
                    "`of` is required when `func` is provided and is not `count`",
                )
            if self.type != HexDataType.NUMBER:
                raise PydanticCustomError(
                    "custom.literal_error",
                    "When using `func`, data type must be `number`",
                )
        elif self.of:
            used = "func_sql" if self.func_sql else "func_calc"
            raise PydanticCustomError(
                "custom.extra_forbidden",
                f"`of` is not allowed when using `{used}`",
                {"conflict_keys": ["of", used]},
            )
        if self.filters and (self.func_sql or self.func_calc):
            used = "func_sql" if self.func_sql else "func_calc"
            raise PydanticCustomError(
                "custom.extra_forbidden",
                f"`filters` is not supported when using `{used}`",
                {"conflict_keys": ["filters", used]},
            )
        return self
