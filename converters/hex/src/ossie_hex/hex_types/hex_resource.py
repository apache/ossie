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

from typing import Any

from pydantic_core import PydanticCustomError

from .hex_model import HexModel
from .hex_view import HexView

HexResource = HexModel | HexView

DEFAULT_HEX_RESOURCE_TYPE = "model"


def parse_hex_resource(data: dict[str, Any]) -> HexResource:
    """Parse a single Hex resource document into a typed model."""
    resource_type = data.get("type", DEFAULT_HEX_RESOURCE_TYPE)
    if resource_type == "view":
        return HexView.model_validate(data)
    if resource_type == "model":
        return HexModel.model_validate(data)
    raise PydanticCustomError(
        "custom.literal_error",
        "Unknown Hex resource type '{resource_type}'",
        {"resource_type": resource_type},
    )
