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


class HexVisibility(str, Enum):
    """Controls where a resource can be used and who can see it."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PRIVATE = "private"


DEFAULT_HEX_VISIBILITY = HexVisibility.PUBLIC


def is_default_hex_visibility(value: HexVisibility) -> bool:
    return value == DEFAULT_HEX_VISIBILITY
