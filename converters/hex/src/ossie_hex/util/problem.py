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

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class Problem(BaseModel):
    """
    A problem encountered during the conversion between Ossie and Hex.
    """

    model_config = ConfigDict(title="Problem")
    severity: ProblemSeverity
    message: str = Field(json_schema_extra={"title": "ProblemMessage"})

    cause_path: KeyPath = Field(
        default_factory=list,
        json_schema_extra={
            "title": "ProblemCausePath",
        },
    )
    """
    The path to the key that caused the problem.

    If empty, a cause cannot be determined.
    """

    phase: PhaseName | None = Field(
        default=None,
        json_schema_extra={
            "title": "ProblemPhase",
        },
    )
    """The phase of the conversion process in which the problem occurred."""

    def to_str(self, *, include_cause: bool = True, include_phase: bool = False) -> str:
        sev = self.severity.upper()
        cause = str(self.cause_path)
        phase = self.phase.upper() if self.phase else "UNKNOWN"
        return "\n".join(
            s
            for s in [
                (f"[{phase}] " if include_phase else "") + f"[{sev}] {self.message}",
                *([f"Cause: {cause}"] if include_cause else []),
            ]
            if s
        )


ProblemSeverity = Annotated[
    Literal["fatal", "error", "warning", "info"],
    Field(title="ProblemSeverity"),
]
"""
The severity of a problem.

- `fatal`: The problem causes invalidation that cannot be recovered from, or
           an unexpected internal error.

- `error`: The problem invalidates a definition which must be omitted from
           the result. The associated definition(s) have been omitted
           from the result.

- `warning`: The problem is a potential issue that probably should be addressed,
             but is not critical. The associated definitions may behave unexpectedly,
             but are included in the result.

- `info`: The problem is a general informational message that is not an issue.
"""


KeyPath = Annotated[
    list[str | int],
    Field(title="ProblemKeyPath"),
]
"""
The key path to a problem's cause or impacted key, starting from the root of
the specification and ending at the key that caused the problem. Each key is
a declared identifier.

If a key begins with `?` then it is a best guess at the location.
If a keypath ends with `:`, then the problem should be reported on the key
itself, not the value the key points to.

This is an empty list if it applies globally.
"""


PhaseName = Annotated[
    Literal["load", "convert", "dump"], Field(title="ProblemPhaseName")
]
"""
The stage of processing that the problem occurred in.

- `load`: The problem occurred during the loading phase: reading, parsing, and
          validating data from the file system into memory.

- `convert`: The problem occurred during the conversion phase: transforming
             representation from the source specification into the target 
             specification.

- `dump`: The problem occurred during the dumping phase: serializing and writing 
          data in-memory to the file system.
"""
