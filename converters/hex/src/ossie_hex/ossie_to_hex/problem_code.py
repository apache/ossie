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

from typing import Literal

ExportProblemCode = Literal[
    "ai-context",
    "composite-primary-key",
    "composite-unique-key",
    "custom-extensions",
    "is-time",
    "missing-dialect",
    "missing-datatype",
    "project-description",
]
"""Codes for problems reported while converting Ossie to Hex."""

EXPORT_PROBLEM_SUMMARIES: dict[ExportProblemCode, str] = {
    "ai-context": "AI context is not preserved in Hex and was dropped.",
    "composite-primary-key": (
        "Composite primary keys are not supported in Hex and were dropped."
    ),
    "composite-unique-key": (
        "Composite unique keys are not supported in Hex and were dropped."
    ),
    "custom-extensions": (
        "Custom extensions are not preserved in Hex and were dropped."
    ),
    "is-time": "Temporal role markers are not supported in Hex and were dropped.",
    "missing-dialect": "No dialect was specified; ANSI SQL was used.",
    "missing-datatype": "A datatype is required in Hex; a default was used.",
    "project-description": (
        "Project descriptions are not supported in Hex and were dropped."
    ),
}
