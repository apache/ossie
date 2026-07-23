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

"""Tests for the decisions-coverage reporter (decisions_coverage.md)."""

from __future__ import annotations

from pathlib import Path

from harness.models import SuiteResult, TestResult, TestStatus
from harness.reporter import write_decisions_coverage

_DECISIONS_YAML = """\
version: "0.1"
decisions:
  - id: D-001
    title: Covered and passing
    tests:
      - tests/cross_grain/moderate/t-005a-single-step-sum/
    status: must_pass
  - id: D-002
    title: No witness tests yet
    tests: []
    status: must_pass
  - id: D-003
    title: Has a test that did not run this session
    tests:
      - tests/windows/t-099-not-run/
    status: must_pass
"""


def _res(test_id: str, status: TestStatus) -> TestResult:
    return TestResult(test_id=test_id, area="a", difficulty="moderate", status=status)


def _write_decisions(tmp_path: Path) -> Path:
    p = tmp_path / "decisions.yaml"
    p.write_text(_DECISIONS_YAML)
    return p


def test_coverage_report_classifies_each_decision(tmp_path: Path) -> None:
    decisions_path = _write_decisions(tmp_path)
    suite = SuiteResult(
        adapter="adapter.py",
        results=[
            # test_id has no leading "tests/" — matches the runner's form.
            _res("cross_grain/moderate/t-005a-single-step-sum", TestStatus.PASS),
        ],
    )

    out = write_decisions_coverage(suite, decisions_path, tmp_path / "results")
    assert out is not None and out.exists()
    md = out.read_text()

    # D-001: its one test ran and passed.
    d1 = next(line for line in md.splitlines() if line.startswith("| D-001 "))
    assert "✅ pass (1/1)" in d1
    # D-002: no tests => flagged as a gap.
    d2 = next(line for line in md.splitlines() if line.startswith("| D-002 "))
    assert "no tests" in d2
    # D-003: defines a test that wasn't in this run's results.
    d3 = next(line for line in md.splitlines() if line.startswith("| D-003 "))
    assert "not run" in d3

    # Overall counts: 3 total, 1 uncovered, 1 passing.
    assert "| Decisions (total) | 3 |" in md
    assert "| Uncovered (no tests) | 1 |" in md
    assert "| Passing this run | 1 |" in md


def test_coverage_report_marks_failures(tmp_path: Path) -> None:
    decisions_path = _write_decisions(tmp_path)
    suite = SuiteResult(
        adapter="x",
        results=[
            _res("cross_grain/moderate/t-005a-single-step-sum", TestStatus.FAIL),
        ],
    )
    out = write_decisions_coverage(suite, decisions_path, tmp_path / "results")
    assert out is not None
    d1 = next(line for line in out.read_text().splitlines() if line.startswith("| D-001 "))
    assert "❌ 0/1 passing" in d1


def test_returns_none_when_registry_missing(tmp_path: Path) -> None:
    suite = SuiteResult(adapter="x")
    out = write_decisions_coverage(suite, tmp_path / "nope.yaml", tmp_path / "results")
    assert out is None
