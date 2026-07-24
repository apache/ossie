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

"""Data models for the OSI compliance test harness."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Any


class TestStatus(str, Enum):
    __test__ = False  # not a pytest test class despite the "Test" prefix

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"
    SKIP = "skip"


@dataclass(frozen=True)
class TestCase:
    """A single compliance test case loaded from disk."""

    __test__ = False  # not a pytest test class despite the "Test" prefix

    test_id: str
    name: str
    description: str
    area: str
    difficulty: str
    dataset: str
    spec_refs: list[str]
    tags: list[str]
    model_path: Path
    query_path: Path
    gold_sql_path: Path
    test_dir: Path
    expected_error: bool = False
    expected_error_code: str = ""
    # Default matches the base level in foundation/conformance.yaml. Tests
    # may override with any level declared there (e.g. foundation_v0_1_strict).
    conformance_level: str = "foundation_v0_1"
    status: str = "active"  # "active" or "planned" — planned tests skipped unless --include-planned
    required_features: list[str] = field(default_factory=list)  # Feature IDs; skip if adapter doesn't support

    @cached_property
    def has_order_by(self) -> bool:
        """Check if the query specifies an order_by clause."""
        qdict = json.loads(self.query_path.read_text())
        return bool(qdict.get("order_by"))


@dataclass
class TestResult:
    """Result of running a single test case."""

    __test__ = False  # not a pytest test class despite the "Test" prefix

    test_id: str
    area: str
    difficulty: str
    status: TestStatus
    spec_refs: list[str] = field(default_factory=list)
    error_type: str = ""
    error_detail: str = ""
    generated_sql: str = ""
    generated_rows: list[dict[str, Any]] = field(default_factory=list)
    gold_rows: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: float = 0.0
    required_features: list[str] = field(default_factory=list)


@dataclass
class SuiteResult:
    """Aggregate results for a test suite run."""

    adapter: str
    results: list[TestResult] = field(default_factory=list)
    # None means "no filter applied" (all proposals implicitly enabled).
    adapter_features: frozenset[str] | None = None

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.PASS)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.FAIL)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.ERROR)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.status == TestStatus.SKIP)
