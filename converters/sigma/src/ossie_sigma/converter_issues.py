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

from dataclasses import dataclass
from enum import Enum
from typing import Generic, List, TypeVar


class ConverterIssueType(Enum):
    """Identifies the kind of information loss or uncertainty during conversion."""

    UNSUPPORTED_ELEMENT_KIND = "UNSUPPORTED_ELEMENT_KIND"
    EXPRESSION_NOT_TRANSLATABLE = "EXPRESSION_NOT_TRANSLATABLE"
    RELATIONSHIP_COLUMN_UNRESOLVED = "RELATIONSHIP_COLUMN_UNRESOLVED"
    UNIQUE_KEY_COLUMN_UNRESOLVED = "UNIQUE_KEY_COLUMN_UNRESOLVED"
    DERIVED_ELEMENT_NOT_MODELED = "DERIVED_ELEMENT_NOT_MODELED"
    FILTER_NOT_MODELED = "FILTER_NOT_MODELED"
    CROSS_DATASET_METRIC_DROPPED = "CROSS_DATASET_METRIC_DROPPED"
    OPAQUE_DATATYPE = "OPAQUE_DATATYPE"
    EXTRA_MODEL_DROPPED = "EXTRA_MODEL_DROPPED"
    MISSING_ID = "MISSING_ID"


class ConverterError(Exception):
    """Raised when the input cannot be converted at all, as opposed to a partial,
    lossy conversion that a :class:`ConverterIssue` can describe."""


@dataclass(frozen=True)
class ConverterIssue:
    """Records a single instance of information loss or uncertainty during conversion."""

    issue_type: ConverterIssueType
    element_name: str
    detail: str = ""


T = TypeVar("T")


@dataclass(frozen=True)
class ConverterResult(Generic[T]):
    """Return value of a converter's convert() method, pairing the output with any conversion issues."""

    output: T
    issues: List[ConverterIssue]
