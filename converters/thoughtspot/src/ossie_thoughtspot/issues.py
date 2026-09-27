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

"""Structured, never-silent loss reporting.

Discussion apache/ossie#325 treats a silently dropped field as a contract
violation rather than a documentation gap, so every declared loss produces an
issue here at conversion time. The same discussion treats a warning storm as a
defect, which is why severity is first-class and why callers can summarise via
count_by_severity() instead of printing every line.
"""
from collections import Counter
from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True)
class ConverterIssue:
    """One declared loss or degradation, traceable to a specific object.

    object_ref is mandatory: an issue a reader cannot trace to an object cannot
    be acted on, which is the complaint #325 raises about warning noise.
    """

    code: str
    severity: Severity
    message: str
    object_ref: str
    remedy: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "object_ref": self.object_ref,
            "remedy": self.remedy,
        }


@dataclass
class IssueLog:
    """Ordered collection of issues raised during one conversion."""

    issues: list[ConverterIssue] = field(default_factory=list)

    def add(
        self,
        *,
        code: str,
        severity: Severity,
        message: str,
        object_ref: str,
        remedy: str | None = None,
    ) -> None:
        self.issues.append(
            ConverterIssue(
                code=code,
                severity=severity,
                message=message,
                object_ref=object_ref,
                remedy=remedy,
            )
        )

    def extend(self, other: "IssueLog") -> None:
        self.issues.extend(other.issues)

    def as_dicts(self) -> list[dict[str, str | None]]:
        return [i.as_dict() for i in self.issues]

    def has_errors(self) -> bool:
        return any(i.severity is Severity.ERROR for i in self.issues)

    def count_by_severity(self) -> dict[str, int]:
        return dict(Counter(i.severity.value for i in self.issues))
