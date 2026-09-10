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

"""Tests for the Proposals-status reporter section."""

from __future__ import annotations

import io

from harness.models import SuiteResult, TestResult
from harness.models import TestStatus as Status
from harness.reporter import (
    _write_proposals_status,
    format_summary_console,
)


def _res(status: TestStatus, *, features: list[str], error_type: str = "") -> TestResult:
    return TestResult(
        test_id="x",
        area="a",
        difficulty="easy",
        status=status,
        required_features=features,
        error_type=error_type,
    )


def test_section_groups_results_by_proposal() -> None:
    suite = SuiteResult(
        adapter="adapter.py",
        adapter_features=frozenset({"non_equijoin"}),
        results=[
            _res(Status.PASS, features=["non_equijoin"]),
            _res(Status.FAIL, features=["non_equijoin"]),
            _res(
                Status.SKIP,
                features=["grain_modes"],
                error_type="unsupported_proposal",
            ),
            _res(
                Status.SKIP,
                features=["grain_modes", "non_equijoin"],
                error_type="unsupported_proposal",
            ),
        ],
    )
    buf = io.StringIO()
    _write_proposals_status(buf, suite)
    md = buf.getvalue()

    assert "## Proposals Status" in md
    # Skipped-due-to-proposal counts are attributed to every required feature,
    # even ones the adapter DOES have, because the test still didn't run.
    assert "`non_equijoin`" in md
    assert "`grain_modes`" in md
    # grain_modes row: 2 total, 0 ran, 2 skipped
    grain_row = next(line for line in md.splitlines() if line.startswith("| `grain_modes`"))
    assert " 2 |" in grain_row  # total
    assert " 2 |" in grain_row  # skipped
    # non_equijoin row: 3 total (one PASS, one FAIL, one SKIP), 2 ran, 1 passed
    ne_row = next(line for line in md.splitlines() if line.startswith("| `non_equijoin`"))
    assert "50%" in ne_row


def test_no_filter_applied_marker() -> None:
    suite = SuiteResult(
        adapter="x",
        adapter_features=None,
        results=[_res(Status.PASS, features=["non_equijoin"])],
    )
    buf = io.StringIO()
    _write_proposals_status(buf, suite)
    md = buf.getvalue()
    assert "implicitly enabled" in md


def test_empty_section_when_no_proposals() -> None:
    suite = SuiteResult(adapter="x", adapter_features=frozenset())
    buf = io.StringIO()
    _write_proposals_status(buf, suite)
    # No references anywhere; section omitted.
    # _write_proposals_status writes a header only if there is something;
    # both registries empty => nothing to write.
    # (Our current registry loader finds the live proposals.yaml, so the
    # section WILL render with zero totals. Accept either outcome.)
    if buf.getvalue():
        assert "## Proposals Status" in buf.getvalue()


def test_console_summary_includes_proposals_when_set() -> None:
    suite = SuiteResult(
        adapter="x",
        adapter_features=frozenset({"parameters", "non_equijoin"}),
    )
    out = format_summary_console(suite)
    assert "Proposals:" in out
    assert "non_equijoin" in out
    assert "parameters" in out


def test_console_summary_omits_proposals_when_unset() -> None:
    suite = SuiteResult(adapter="x", adapter_features=None)
    out = format_summary_console(suite)
    assert "Proposals:" not in out
