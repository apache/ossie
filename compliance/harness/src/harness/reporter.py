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

"""Report generation for the OSI compliance test suite."""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .models import SuiteResult, TestResult, TestStatus

PROPOSALS_FILE_NAME = "proposals.yaml"


def write_reports(suite: SuiteResult, output_dir: Path) -> tuple[Path, Path]:
    """Write failure CSV and summary MD to output_dir. Returns (csv_path, md_path)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / "failures.csv"
    md_path = output_dir / "summary.md"

    _write_failures_csv(suite, csv_path)
    _write_summary_md(suite, md_path)

    return csv_path, md_path


def _write_failures_csv(suite: SuiteResult, path: Path) -> None:
    failures = [r for r in suite.results if r.status in (TestStatus.FAIL, TestStatus.ERROR)]

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "test_id",
                "area",
                "difficulty",
                "spec_refs",
                "error_type",
                "details",
            ]
        )
        for r in failures:
            writer.writerow(
                [
                    r.test_id,
                    r.area,
                    r.difficulty,
                    "; ".join(r.spec_refs),
                    r.error_type,
                    r.error_detail,
                ]
            )


def _write_summary_md(suite: SuiteResult, path: Path) -> None:
    buf = io.StringIO()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    buf.write("# OSI Compliance Test Suite — Summary\n\n")
    buf.write(f"**Adapter**: {suite.adapter}  \n")
    buf.write(f"**Date**: {now}  \n\n")

    pct = (suite.passed / suite.total * 100) if suite.total > 0 else 0
    buf.write("## Overall\n\n")
    buf.write("| Metric | Count |\n")
    buf.write("|--------|-------|\n")
    buf.write(f"| Total | {suite.total} |\n")
    buf.write(f"| Passed | {suite.passed} |\n")
    buf.write(f"| Failed | {suite.failed} |\n")
    buf.write(f"| Errors | {suite.errors} |\n")
    buf.write(f"| Skipped | {suite.skipped} |\n")
    buf.write(f"| **Compliance** | **{pct:.1f}%** |\n\n")

    _write_breakdown(buf, "By Area", suite.results, key=lambda r: r.area)
    _write_breakdown(buf, "By Difficulty", suite.results, key=lambda r: r.difficulty)
    _write_proposals_status(buf, suite)

    failures = [r for r in suite.results if r.status in (TestStatus.FAIL, TestStatus.ERROR)]
    if failures:
        buf.write("## Failures\n\n")
        buf.write("| Test | Area | Difficulty | Error |\n")
        buf.write("|------|------|------------|-------|\n")
        for r in failures:
            detail = r.error_detail[:80].replace("|", "\\|") if r.error_detail else ""
            buf.write(f"| {r.test_id} | {r.area} | {r.difficulty} | {detail} |\n")
        buf.write("\n")

    path.write_text(buf.getvalue())


def _write_breakdown(
    buf: io.StringIO,
    title: str,
    results: list[TestResult],
    key,
) -> None:
    groups: dict[str, list[TestResult]] = defaultdict(list)
    for r in results:
        groups[key(r)].append(r)

    buf.write(f"## {title}\n\n")
    buf.write("| Group | Total | Pass | Fail | Error | Skip | % |\n")
    buf.write("|-------|-------|------|------|-------|------|---|\n")

    for group_name in sorted(groups.keys()):
        group = groups[group_name]
        total = len(group)
        passed = sum(1 for r in group if r.status == TestStatus.PASS)
        failed = sum(1 for r in group if r.status == TestStatus.FAIL)
        errored = sum(1 for r in group if r.status == TestStatus.ERROR)
        skipped = sum(1 for r in group if r.status == TestStatus.SKIP)
        pct = (passed / total * 100) if total > 0 else 0
        buf.write(f"| {group_name} | {total} | {passed} | {failed} " f"| {errored} | {skipped} | {pct:.0f}% |\n")
    buf.write("\n")


def _load_proposal_registry(*starts: Path) -> dict[str, str]:
    """Return ``{proposal_id: status}``, searching upwards for ``proposals.yaml``.

    Walks up from every given start path. Empty dict when the registry
    isn't findable; the caller falls back to the inferred set of IDs
    seen across results.
    """
    tried: set[Path] = set()
    for start in starts:
        cursor = start.resolve()
        for _ in range(6):
            if cursor in tried:
                break
            tried.add(cursor)
            candidate = cursor / PROPOSALS_FILE_NAME
            if candidate.exists():
                data = yaml.safe_load(candidate.read_text()) or {}
                return {p["id"]: p.get("status", "") for p in data.get("proposals", [])}
            if cursor.parent == cursor:
                break
            cursor = cursor.parent
    return {}


def _write_proposals_status(buf: io.StringIO, suite: SuiteResult) -> None:
    """Emit the Proposals-status section.

    For every proposal ID that is either advertised by the adapter or
    referenced by at least one result, report:

    * whether the adapter advertised it (``enabled``),
    * total tests that require it,
    * tests that ran vs. were skipped,
    * pass rate among the ones that ran.
    """
    registry = _load_proposal_registry(Path.cwd(), Path(__file__).parent)

    referenced: set[str] = set()
    for r in suite.results:
        referenced.update(r.required_features)
    advertised: set[str] = set(suite.adapter_features or ())
    all_ids = sorted(referenced | advertised | set(registry.keys()))
    if not all_ids:
        return

    counts_total: dict[str, int] = defaultdict(int)
    counts_ran: dict[str, int] = defaultdict(int)
    counts_passed: dict[str, int] = defaultdict(int)
    counts_skipped: dict[str, int] = defaultdict(int)

    for r in suite.results:
        for feat in r.required_features:
            counts_total[feat] += 1
            if r.status == TestStatus.SKIP and r.error_type == "unsupported_proposal":
                counts_skipped[feat] += 1
            else:
                counts_ran[feat] += 1
                if r.status == TestStatus.PASS:
                    counts_passed[feat] += 1

    buf.write("## Proposals Status\n\n")
    if suite.adapter_features is None:
        buf.write("_No `--proposals` filter applied; every proposal implicitly enabled._\n\n")
    else:
        buf.write(f"Adapter advertised {len(advertised)} proposal(s): " f"`{', '.join(sorted(advertised)) or '(none)'}`\n\n")

    buf.write("| Proposal | Status | Enabled | Tests | Ran | Passed | Skipped | Pass% |\n")
    buf.write("|----------|--------|---------|-------|-----|--------|---------|-------|\n")
    for pid in all_ids:
        status = registry.get(pid, "unknown")
        enabled = "yes" if (suite.adapter_features is None or pid in advertised) else "no"
        total = counts_total.get(pid, 0)
        ran = counts_ran.get(pid, 0)
        passed = counts_passed.get(pid, 0)
        skipped = counts_skipped.get(pid, 0)
        pct = f"{(passed / ran * 100):.0f}%" if ran else "—"
        buf.write(f"| `{pid}` | {status} | {enabled} | {total} | {ran} | {passed} | {skipped} | {pct} |\n")
    buf.write("\n")


def _normalize_decision_test_path(raw: str) -> str:
    """Normalize a ``decisions.yaml`` test path to a runner ``test_id``.

    ``decisions.yaml`` lists tests as e.g.
    ``tests/cross_grain/moderate/t-005a-single-step-sum/``; the runner's
    ``test_id`` for the same case is
    ``cross_grain/moderate/t-005a-single-step-sum``. Strip the leading
    ``tests/`` and any surrounding slashes so the two line up.
    """
    p = raw.strip().strip("/")
    if p.startswith("tests/"):
        p = p[len("tests/"):]
    return p


def write_decisions_coverage(
    suite: SuiteResult,
    decisions_path: Path,
    output_dir: Path,
) -> Path | None:
    """Write ``decisions_coverage.md`` mapping each ``D-NNN`` to its tests.

    Reads the suite's ``decisions.yaml`` registry and, for every decision,
    reports whether it has witness tests and how those tests fared in this
    run. Decisions with an empty ``tests:`` list surface as coverage gaps —
    the suite's primary PR-review signal. Returns the written path, or
    ``None`` if no registry is found (the run still succeeds without it).
    """
    if decisions_path is None or not decisions_path.exists():
        return None

    data = yaml.safe_load(decisions_path.read_text()) or {}
    decisions = data.get("decisions", []) or []

    status_by_id = {r.test_id: r.status for r in suite.results}
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "decisions_coverage.md"

    rows: list[tuple[str, str, str, str]] = []
    n_total = 0
    n_with_tests = 0
    n_passing = 0
    n_gap = 0

    for decision in decisions:
        did = decision.get("id", "?")
        status = decision.get("status", "")
        raw_tests = decision.get("tests", []) or []
        n_total += 1

        test_ids = [_normalize_decision_test_path(t) for t in raw_tests]
        present = [t for t in test_ids if t in status_by_id]
        passed = [t for t in present if status_by_id[t] == TestStatus.PASS]

        if not test_ids:
            outcome = "⚠️ no tests"
            n_gap += 1
        elif not present:
            outcome = f"— not run ({len(test_ids)} defined)"
        elif len(passed) == len(present) == len(test_ids):
            outcome = f"✅ pass ({len(passed)}/{len(test_ids)})"
            n_with_tests += 1
            n_passing += 1
        else:
            outcome = f"❌ {len(passed)}/{len(test_ids)} passing"
            n_with_tests += 1

        rows.append((did, status, str(len(test_ids)), outcome))

    buf = io.StringIO()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    buf.write("# OSI Compliance — Decision Coverage\n\n")
    buf.write(f"**Adapter**: {suite.adapter}  \n")
    buf.write(f"**Date**: {now}  \n")
    buf.write(f"**Source**: {decisions_path.name}  \n\n")

    buf.write("## Overall\n\n")
    buf.write("| Metric | Count |\n")
    buf.write("|--------|-------|\n")
    buf.write(f"| Decisions (total) | {n_total} |\n")
    buf.write(f"| With a witness test | {n_total - n_gap} |\n")
    buf.write(f"| Uncovered (no tests) | {n_gap} |\n")
    buf.write(f"| Passing this run | {n_passing} |\n\n")

    buf.write("## By decision\n\n")
    buf.write("| Decision | Reg. status | Tests | This run |\n")
    buf.write("|----------|-------------|-------|----------|\n")
    for did, status, count, outcome in rows:
        buf.write(f"| {did} | {status} | {count} | {outcome} |\n")
    buf.write("\n")

    path.write_text(buf.getvalue())
    return path


def format_summary_console(suite: SuiteResult) -> str:
    """Format a brief console summary."""
    pct = (suite.passed / suite.total * 100) if suite.total > 0 else 0
    lines = [
        f"\n{'=' * 50}",
        f"OSI Compliance Test Results — {suite.adapter}",
        f"{'=' * 50}",
        f"  Total:   {suite.total}",
        f"  Passed:  {suite.passed}",
        f"  Failed:  {suite.failed}",
        f"  Errors:  {suite.errors}",
        f"  Skipped: {suite.skipped}",
        f"  Compliance: {pct:.1f}%",
    ]
    if suite.adapter_features is not None:
        lines.append(f"  Proposals: {', '.join(sorted(suite.adapter_features)) or '(none)'}")
    lines.append(f"{'=' * 50}")
    return "\n".join(lines)
