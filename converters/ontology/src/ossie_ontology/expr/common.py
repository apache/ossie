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


class AggMethod(str, Enum):
    SUM = "SUM"
    MIN = "MIN"
    MAX = "MAX"
    COUNT = "COUNT"
    AVG = "AVG"

    @classmethod
    def from_value(cls, value) -> AggMethod:
        """
        Look up an enum instance by a string value (case-insensitive).
        """
        if not isinstance(value, str):
            raise TypeError("value must be a string")
        try:
            return cls(value.strip().upper())
        except ValueError as e:
            raise ValueError(f"Unknown aggregation method: {value}") from e
