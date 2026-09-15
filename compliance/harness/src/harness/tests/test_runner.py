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

"""End-to-end exercise of the runner mechanics: discovery, adapter
invocation over the documented CLI contract, DuckDB-backed row
comparison, and reporting.

This suite does not depend on any real OSI implementation. It stands
in a tiny fake "adapter" (a Python script satisfying the CLI contract
in ``compliance/ADAPTER_INTERFACE.md``) whose behaviour is driven by
the content of the ``--model`` file it's given, so each test can pick
a PASS / FAIL / ERROR / expected-error outcome without needing a real
query planner. It exists so the plumbing this bootstrap slice ports
over — ``discover_tests``, ``run_test``, ``run_suite``, the DuckDB
fixture loading, row comparison, and report writing — is proven to
work end to end, independent of when a real adapter lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from harness.db_manager import DBManager
from harness.models import TestCase, TestResult, TestStatus
from harness.runner import (
    discover_tests,
    list_tests,
    load_conformance_levels,
    run_suite,
    run_test,
)

# A fake adapter that sleeps forever, to exercise the timeout path.
SLEEPING_ADAPTER = """\
import time
time.sleep(30)
"""

# A fake adapter satisfying the CLI contract in ADAPTER_INTERFACE.md:
# `<adapter> sql --model <model.yaml> --query-file <query.json> --dialect <dialect>`.
# The "model" file IS the payload for these tests: if its content starts
# with "ERROR:" the fake adapter fails with that message on stderr;
# otherwise the content is treated as the SQL to print on stdout.
FAKE_ADAPTER = '''\
import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("mode")
parser.add_argument("--model", required=True)
parser.add_argument("--query-file", required=True)
parser.add_argument("--dialect", required=True)
args = parser.parse_args()

payload = open(args.model).read()
if payload.startswith("ERROR:"):
    sys.stderr.write(payload[len("ERROR:"):])
    sys.exit(1)
sys.stdout.write(payload)
'''

DATASET_NAME = "d_numbers"

SCHEMA_SQL = """
CREATE TABLE numbers (n INTEGER);
INSERT INTO numbers VALUES (1), (2), (3);
"""


@pytest.fixture
def suite_root(tmp_path: Path) -> Path:
    """Build a minimal, self-contained suite tree under tmp_path."""
    (tmp_path / "adapter.py").write_text(FAKE_ADAPTER)

    dataset_dir = tmp_path / "datasets" / DATASET_NAME
    dataset_dir.mkdir(parents=True)
    (dataset_dir / "schema.sql").write_text(SCHEMA_SQL)

    return tmp_path


@pytest.fixture
def db():
    """A DBManager that is always closed, even if the test assertion fails."""
    manager = DBManager()
    yield manager
    manager.close()


def _run(case: TestCase, suite_root: Path, db: DBManager) -> TestResult:
    return run_test(case, suite_root / "adapter.py", db, suite_root / "datasets")


def _write_test(
    tests_dir: Path,
    rel: str,
    *,
    model_payload: str,
    gold_sql: str = "SELECT SUM(n) AS total FROM numbers",
    query: str = "{}",
    status: str = "active",
    expected_error: bool = False,
    expected_error_code: str = "",
    area: str = "arith",
    difficulty: str = "easy",
) -> Path:
    test_dir = tests_dir / rel
    test_dir.mkdir(parents=True)
    (test_dir / "model.yaml").write_text(model_payload)
    (test_dir / "query.json").write_text(query)
    (test_dir / "gold.sql").write_text(gold_sql)
    meta = [
        f"name: {test_dir.name}",
        "description: synthetic runner smoke test",
        f"area: {area}",
        f"difficulty: {difficulty}",
        f"dataset: {DATASET_NAME}",
        f"status: {status}",
    ]
    if expected_error:
        meta.append("expected_error: true")
    if expected_error_code:
        meta.append(f"expected_error_code: {expected_error_code}")
    (test_dir / "metadata.yaml").write_text("\n".join(meta) + "\n")
    return test_dir


def test_run_test_pass(suite_root: Path, db: DBManager) -> None:
    tests_dir = suite_root / "tests"
    _write_test(
        tests_dir,
        "pass_case",
        model_payload="SELECT SUM(n) AS total FROM numbers",
    )
    (case,) = discover_tests(tests_dir)
    assert case.test_id == "pass_case"

    result = _run(case, suite_root, db)

    assert result.status == TestStatus.PASS
    assert result.generated_rows == result.gold_rows == [{"total": 6}]


def test_run_test_fail_on_row_mismatch(suite_root: Path, db: DBManager) -> None:
    tests_dir = suite_root / "tests"
    _write_test(
        tests_dir,
        "fail_case",
        model_payload="SELECT SUM(n) + 1 AS total FROM numbers",
    )
    (case,) = discover_tests(tests_dir)

    result = _run(case, suite_root, db)

    assert result.status == TestStatus.FAIL
    assert result.error_type == "result_mismatch"
    assert result.generated_rows == [{"total": 7}]
    assert result.gold_rows == [{"total": 6}]


def test_run_test_error_on_invalid_sql(suite_root: Path, db: DBManager) -> None:
    tests_dir = suite_root / "tests"
    _write_test(
        tests_dir,
        "bad_sql_case",
        model_payload="SELECT NOT VALID SQL HERE",
    )
    (case,) = discover_tests(tests_dir)

    result = _run(case, suite_root, db)

    assert result.status == TestStatus.ERROR
    assert result.error_type == "generated_sql_error"


def test_run_test_expected_error_pass(suite_root: Path, db: DBManager) -> None:
    tests_dir = suite_root / "tests"
    _write_test(
        tests_dir,
        "expected_error_case",
        model_payload="ERROR:E_SOME_CODE: deliberately rejected\n",
        expected_error=True,
        expected_error_code="E_SOME_CODE",
    )
    (case,) = discover_tests(tests_dir)
    assert case.expected_error
    assert case.expected_error_code == "E_SOME_CODE"

    result = _run(case, suite_root, db)

    assert result.status == TestStatus.PASS


def test_run_test_expected_error_wrong_code_fails(suite_root: Path, db: DBManager) -> None:
    tests_dir = suite_root / "tests"
    _write_test(
        tests_dir,
        "wrong_error_code_case",
        model_payload="ERROR:E_OTHER_CODE: rejected for a different reason\n",
        expected_error=True,
        expected_error_code="E_SOME_CODE",
    )
    (case,) = discover_tests(tests_dir)

    result = _run(case, suite_root, db)

    assert result.status == TestStatus.FAIL
    assert result.error_type == "wrong_error_code"


def test_discover_tests_skips_planned_unless_included(suite_root: Path) -> None:
    tests_dir = suite_root / "tests"
    _write_test(
        tests_dir,
        "planned_case",
        model_payload="SELECT SUM(n) AS total FROM numbers",
        status="planned",
    )

    assert discover_tests(tests_dir) == []
    (case,) = discover_tests(tests_dir, include_planned=True)
    assert case.status == "planned"


def test_run_suite_end_to_end_writes_reports(suite_root: Path, capsys) -> None:
    tests_dir = suite_root / "tests"
    _write_test(
        tests_dir,
        "pass_case",
        model_payload="SELECT SUM(n) AS total FROM numbers",
    )
    _write_test(
        tests_dir,
        "fail_case",
        model_payload="SELECT SUM(n) + 1 AS total FROM numbers",
    )
    _write_test(
        tests_dir,
        "planned_case",
        model_payload="SELECT SUM(n) AS total FROM numbers",
        status="planned",
    )

    output_dir = suite_root / "results" / "latest"
    suite = run_suite(
        adapter_path=suite_root / "adapter.py",
        tests_dir=tests_dir,
        datasets_dir=suite_root / "datasets",
        output_dir=output_dir,
        include_planned=True,
    )

    assert suite.total == 3
    assert suite.passed == 2  # pass_case and planned_case (included via --include-planned)
    assert suite.failed == 1

    csv_path = output_dir / "failures.csv"
    md_path = output_dir / "summary.md"
    assert csv_path.exists()
    assert md_path.exists()
    assert "fail_case" in csv_path.read_text()
    assert "## Overall" in md_path.read_text()

    console = capsys.readouterr().out
    assert "PASS" in console
    assert "FAIL" in console


def test_run_suite_skips_tests_with_unsupported_proposal(suite_root: Path) -> None:
    tests_dir = suite_root / "tests"
    test_dir = _write_test(
        tests_dir,
        "needs_feature",
        model_payload="SELECT SUM(n) AS total FROM numbers",
    )
    meta_path = test_dir / "metadata.yaml"
    meta_path.write_text(meta_path.read_text() + "required_features: [some_feature]\n")

    suite = run_suite(
        adapter_path=suite_root / "adapter.py",
        tests_dir=tests_dir,
        datasets_dir=suite_root / "datasets",
        output_dir=suite_root / "results" / "latest",
        adapter_features=set(),
    )

    assert suite.total == 1
    assert suite.skipped == 1
    assert suite.results[0].error_type == "unsupported_proposal"


def test_run_test_times_out(suite_root: Path, db: DBManager) -> None:
    (suite_root / "sleeper.py").write_text(SLEEPING_ADAPTER)
    tests_dir = suite_root / "tests"
    case_dir = _write_test(
        tests_dir,
        "slow_case",
        model_payload="SELECT SUM(n) AS total FROM numbers",
    )

    (case,) = discover_tests(tests_dir)
    result = run_test(
        case, suite_root / "sleeper.py", db, suite_root / "datasets", timeout=1
    )

    assert result.status == TestStatus.ERROR
    assert result.error_type == "adapter_timeout"
    assert "1s" in result.error_detail
    assert case_dir.exists()


def test_load_conformance_levels_reads_registry(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    (suite / "tests" / "area").mkdir(parents=True)
    (suite / "conformance.yaml").write_text(
        "levels:\n"
        "  foundation_v0_1:\n"
        "    description: base\n"
        "  foundation_v0_1_strict:\n"
        "    description: strict\n"
    )

    # Discoverable by searching upward from the tests directory.
    levels = load_conformance_levels(suite / "tests" / "area")
    assert levels == {"foundation_v0_1", "foundation_v0_1_strict"}


def test_load_conformance_levels_missing_returns_empty(tmp_path: Path) -> None:
    assert load_conformance_levels(tmp_path) == set()


def test_list_tests_smoke(suite_root: Path, capsys) -> None:
    tests_dir = suite_root / "tests"
    _write_test(
        tests_dir,
        "pass_case",
        model_payload="SELECT SUM(n) AS total FROM numbers",
    )

    list_tests(tests_dir)
    out = capsys.readouterr().out
    assert "pass_case" in out
    assert "Total: 1 test(s)" in out
