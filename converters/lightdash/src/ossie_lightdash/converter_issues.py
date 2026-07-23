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
    """Identifies the kind of information loss that occurred during conversion."""

    # Import: the dataset source could not be qualified with a schema/database.
    SOURCE_UNQUALIFIED = "SOURCE_UNQUALIFIED"
    # Import: a join's sql_on could not be parsed into column pairs.
    JOIN_SQL_UNPARSED = "JOIN_SQL_UNPARSED"
    # Export: a metric references more than one dataset, which a Lightdash
    # model metric cannot express.
    CROSS_DATASET_METRIC_DROPPED = "CROSS_DATASET_METRIC_DROPPED"
    # Export: a relationship's from_columns/to_columns differ in length, so a
    # correct sql_on cannot be built.
    RELATIONSHIP_COLUMNS_MISMATCHED = "RELATIONSHIP_COLUMNS_MISMATCHED"
    # Export: a custom extension from another vendor cannot be carried into
    # Lightdash meta (it remains in the OSI document itself).
    FOREIGN_EXTENSION_IGNORED = "FOREIGN_EXTENSION_IGNORED"


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
