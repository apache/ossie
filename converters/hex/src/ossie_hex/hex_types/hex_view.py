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

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .hex_id import HexID, id_to_name


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
