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

"""Issue reporting for lossy conversions, mirroring the ossie_dbt pattern."""

from dataclasses import dataclass
from enum import Enum
from typing import Generic, List, TypeVar


class ConverterIssueType(Enum):
    """Identifies the kind of information loss that occurred during conversion."""

    RELATIONSHIP_JOIN_UNPARSED = "RELATIONSHIP_JOIN_UNPARSED"
    METRIC_DROPPED = "METRIC_DROPPED"
    NON_ANSI_EXPRESSION_DROPPED = "NON_ANSI_EXPRESSION_DROPPED"
    COMPOSITE_PRIMARY_KEY_TRUNCATED = "COMPOSITE_PRIMARY_KEY_TRUNCATED"
    UNMAPPED_DATA_TYPE = "UNMAPPED_DATA_TYPE"


@dataclass(frozen=True)
class ConverterIssue:
    """Records a single instance of information loss during conversion."""

    issue_type: ConverterIssueType
    element_name: str


T = TypeVar("T")


@dataclass(frozen=True)
class ConverterResult(Generic[T]):
    """Return value of a converter's convert() method, pairing the output with any conversion issues."""

    output: T
    issues: List[ConverterIssue]
