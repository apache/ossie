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

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Generic, TypeVar

from .problem import KeyPath, PhaseName, Problem, ProblemSeverity

ProblemCodeT = TypeVar("ProblemCodeT", bound=str)


class Context(Generic[ProblemCodeT]):
    """Base context for all conversion processes."""

    def __init__(self, *, logger: logging.Logger) -> None:
        self.problems: list[Problem] = []
        self.current_problem_path: KeyPath = []
        self.current_phase_name: PhaseName | None = None
        self._logger = logger

    @contextmanager
    def phase_scope(self, phase_name: PhaseName) -> Iterator[None]:
        self.current_phase_name = phase_name
        try:
            yield
        finally:
            self.current_phase_name = None

    @contextmanager
    def problem_scope(self, *keys: str | int) -> Iterator[None]:
        self.current_problem_path.extend(keys)
        try:
            yield
        finally:
            if keys:
                del self.current_problem_path[-len(keys) :]

    def report_problem(
        self,
        severity: ProblemSeverity,
        message: str,
        *,
        path: KeyPath,
        internal_message: str = "",
    ) -> None:
        phase = self.current_phase_name
        cause_path: KeyPath = [*self.current_problem_path, *path]
        problem = Problem(
            severity=severity,
            message=message,
            cause_path=cause_path,
            phase=phase,
        )
        if severity == "fatal" or internal_message:
            self._logger.error(
                "%s\nINTERNAL: %s",
                problem.to_str(),
                internal_message,
            )
        self.problems.append(problem)

    def fatal(
        self,
        message: str,
        *,
        path: KeyPath | None = None,
        internal_message: str = "",
    ) -> None:
        """Report a critical error that cannot be recovered from,
        or an unexpected internal error.
        """
        self.report_problem(
            severity="fatal",
            message=message,
            path=path or [],
            internal_message=internal_message,
        )

    def error(
        self,
        message: str,
        *,
        path: KeyPath | None = None,
        internal_message: str = "",
    ) -> None:
        """Report an issue that invalidates a definition."""
        self.report_problem(
            severity="error",
            message=message,
            path=path or [],
            internal_message=internal_message,
        )

    def warn(
        self,
        message: str,
        *,
        path: KeyPath | None = None,
        internal_message: str = "",
    ) -> None:
        """Report a potential issue that may cause unexpected behavior,
        but does not invalidate a definition.
        """
        self.report_problem(
            severity="warning",
            message=message,
            path=path or [],
            internal_message=internal_message,
        )

    def info(
        self,
        message: str,
        *,
        path: KeyPath | None = None,
        internal_message: str = "",
    ) -> None:
        """Report an informational message that is not an issue."""
        self.report_problem(
            severity="info",
            message=message,
            path=path or [],
            internal_message=internal_message,
        )
