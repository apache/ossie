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

"""Pydantic models for Hex semantic resources."""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)
from pydantic_core import PydanticCustomError
from typing_extensions import TypeAliasType

HEX_ID_PATTERN = r"^[a-z_][a-z0-9_]{1,127}$"
HEX_ID_RE = re.compile(HEX_ID_PATTERN)

HEX_RESERVED_IDS = ["this", "self", "dataset", "model", "view", "metric", "env"]
HEX_RESERVED_ID_PREFIX = "_hex"

# Hex semantic references use ${...} in SQL expressions. Logically these should
# not be within string literals or comments (left to parser).
HEX_REF_PATTERN = r"\$\{\s*([^}]+?)\s*\}"
HEX_REF_RE = re.compile(HEX_REF_PATTERN)


def _exclude_reserved_ids(hex_id: str) -> str:
    if hex_id in HEX_RESERVED_IDS:
        raise PydanticCustomError(
            "custom.string_disallowed",
            "ID '{hex_id}' is a reserved term and cannot be used",
            {"hex_id": hex_id},
        )
    if hex_id.startswith(HEX_RESERVED_ID_PREFIX):
        raise PydanticCustomError(
            "custom.string_disallowed",
            "ID '{hex_id}' cannot begin with '{HEX_RESERVED_ID_PREFIX}'",
            {"hex_id": hex_id, "HEX_RESERVED_ID_PREFIX": HEX_RESERVED_ID_PREFIX},
        )
    return hex_id


HexID = TypeAliasType(
    "HexID",
    Annotated[
        str,
        Field(
            title="HexID",
            description=(
                "An ID between 2 and 128 characters that begins with a lowercase "
                "letter or underscore and contains only lowercase letters, "
                "underscores, and numbers. The IDs ``this``, ``self``, ``dataset``, ``model``, ``view``, ``metric``, and ``env``, and IDs beginning with ``_hex``, are reserved."
            ),
            pattern=HEX_ID_PATTERN,
            min_length=2,
            max_length=128,
        ),
        AfterValidator(_exclude_reserved_ids),
    ],
)


def id_to_name(hex_id: str) -> str:
    words = hex_id.split("_")
    words[0] = words[0].title()
    return " ".join(words)


class HexDialect(str, Enum):
    BIGQUERY = "bigquery"
    DATABRICKS = "databricks"
    DUCKDB = "duckdb"
    SNOWFLAKE = "snowflake"
    SPARK = "spark"


HEX_DIALECTS = [d.value for d in HexDialect]


class HexDataType(str, Enum):
    """The abstract type of the data."""

    NUMBER = "number"
    STRING = "string"
    TIMESTAMP_TZ = "timestamp_tz"
    TIMESTAMP_NAIVE = "timestamp_naive"
    DATE = "date"
    BOOLEAN = "boolean"
    NULL = "null"
    OTHER = "other"


class HexVisibility(str, Enum):
    """Controls where a resource can be used and who can see it."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"


DEFAULT_HEX_VISIBILITY = HexVisibility.PUBLIC


def is_default_hex_visibility(value: HexVisibility) -> bool:
    return value == DEFAULT_HEX_VISIBILITY


class HexRelationType(str, Enum):
    MANY_TO_ONE = "many_to_one"
    ONE_TO_ONE = "one_to_one"
    ONE_TO_MANY = "one_to_many"


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


class HexRelation(BaseModel):
    """Defines how two models connect to each other."""

    model_config = ConfigDict(extra="forbid")

    id: HexID
    target: HexID = Field(default_factory=lambda data: data["id"])
    type: HexRelationType
    join_sql: str
    visibility: HexVisibility = HexVisibility.PUBLIC


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


class HexGroupDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    name: str | None = None
    description: str | None = None


class HexGroupMeasure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measure: str
    name: str | None = None
    description: str | None = None


class HexGroup(BaseModel):
    """A set of dimensions and measures to display to the end user."""

    model_config = ConfigDict(extra="forbid")

    relation: str | None = None
    name: str | None = None
    description: str | None = None
    dimensions: Literal["..."] | list[str | HexGroupDimension] = Field(
        default_factory=list
    )
    measures: Literal["..."] | list[str | HexGroupMeasure] = Field(default_factory=list)
    contents: list[HexGroup] = Field(default_factory=list)


class HexView(BaseModel):
    """A fit-for-purpose model entry point for users conducting self-serve analysis."""

    model_config = ConfigDict(extra="forbid")

    id: HexID
    type: Literal["view"] = "view"
    base: str
    contents: list[HexGroup]
    name: str = Field(default_factory=lambda data: id_to_name(data["id"]))
    description: str = ""


def parse_hex_resource(data: dict[str, Any]) -> HexModel | HexView:
    """Parse a single Hex resource document into a typed model."""
    resource_type = data.get("type", "model")
    if resource_type == "view":
        return HexView.model_validate(data)
    if resource_type == "model":
        return HexModel.model_validate(data)
    raise PydanticCustomError(
        "custom.literal_error",
        "Unknown Hex resource type '{resource_type}'",
        {"resource_type": resource_type},
    )


class HexProject(BaseModel):
    """In-memory Hex project: a name, dialect, and ordered resources."""

    model_config = ConfigDict(extra="forbid")

    name: str
    dialect: str
    resources: list[HexModel | HexView] = Field(default_factory=list)
