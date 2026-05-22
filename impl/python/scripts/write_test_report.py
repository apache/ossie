#!/usr/bin/env python3
"""write_test_report.py — build a single readable Markdown test report.

The report consolidates:

* every stage's PASS / FAIL status and duration
* per-category test counts (parsed from JUnit XML)
* combined coverage (line + branch) from coverage.json
* mutation testing summary (parsed from mutmut output) when applicable
* slowest 10 tests across all categories
* every failing test, with a direct path to its log

The input is the raw output of ``scripts/run_all_tests.sh``. Run that script
to refresh ``test-results/REPORT.md``; do not invoke this writer directly.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class Stage:
    name: str
    status: str
    log_path: str
    duration_s: int


@dataclass
class JUnitSummary:
    category: str
    total: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    slowest: list[tuple[str, float]] = field(default_factory=list)
    failing_tests: list[str] = field(default_factory=list)


def _read_stages(stages_file: Path) -> list[Stage]:
    raw = stages_file.read_text().splitlines()
    if not raw:
        return []
    # First line is stage count; following lines are tab-separated.
    count = int(raw[0])
    stages: list[Stage] = []
    for line in raw[1 : 1 + count]:
        name, status, log, duration = line.split("\t")
        stages.append(Stage(name, status, log, int(duration)))
    return stages


def _summarise_junit(category: str, path: Path) -> JUnitSummary | None:
    if not path.exists():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return None
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    if suite is None:
        return None
    summary = JUnitSummary(category=category)
    summary.total = int(suite.get("tests", "0"))
    summary.failures = int(suite.get("failures", "0"))
    summary.errors = int(suite.get("errors", "0"))
    summary.skipped = int(suite.get("skipped", "0"))
    summary.duration_s = float(suite.get("time", "0") or 0.0)
    cases: list[tuple[str, float]] = []
    for case in suite.iter("testcase"):
        name = f"{case.get('classname', '')}.{case.get('name', '')}"
        t = float(case.get("time", "0") or 0.0)
        cases.append((name, t))
        if case.find("failure") is not None or case.find("error") is not None:
            summary.failing_tests.append(name)
    summary.slowest = sorted(cases, key=lambda c: c[1], reverse=True)[:5]
    return summary


def _read_coverage(cov_json: Path) -> dict[str, float] | None:
    if not cov_json.exists():
        return None
    try:
        data = json.loads(cov_json.read_text())
    except json.JSONDecodeError:
        return None
    totals = data.get("totals") or {}
    return {
        "line_pct": float(totals.get("percent_covered", 0.0)),
        "branch_pct": float(totals.get("percent_covered_branches", 0.0)),
        "missing": int(totals.get("missing_lines", 0)),
        "covered": int(totals.get("covered_lines", 0)),
        "num_statements": int(totals.get("num_statements", 0)),
    }


_MUTMUT_LINE = re.compile(
    r"(\d+/\d+)\s+\((?P<killed>\d+)\s*killed,\s*(?P<survived>\d+)\s*survived",
    re.IGNORECASE,
)


def _read_mutation(raw_dir: Path, mode: str) -> dict[str, int | float] | None:
    if mode == "none":
        return None
    summary_path = raw_dir / f"mutation_{mode}_summary.txt"
    log_path = raw_dir / f"mutation_{mode}.log"
    if not summary_path.exists():
        summary_path = log_path
    if not summary_path.exists():
        return None
    text = summary_path.read_text(errors="replace")
    killed = survived = 0
    suspicious = 0
    timeout = 0
    skipped = 0
    # mutmut 3.x summary lines:
    #   "Killed N out of M (X%)"
    #   "Surviving N"
    for line in text.splitlines():
        if m := re.match(r"^\s*(\d+) killed", line, re.IGNORECASE):
            killed = int(m.group(1))
        if m := re.match(r"^\s*(\d+) survived", line, re.IGNORECASE):
            survived = int(m.group(1))
        if m := re.match(r"^\s*(\d+) timeout", line, re.IGNORECASE):
            timeout = int(m.group(1))
        if m := re.match(r"^\s*(\d+) suspicious", line, re.IGNORECASE):
            suspicious = int(m.group(1))
        if m := re.match(r"^\s*(\d+) skipped", line, re.IGNORECASE):
            skipped = int(m.group(1))
    total = killed + survived + timeout + suspicious + skipped
    score = (killed / total * 100.0) if total else 0.0
    return {
        "killed": killed,
        "survived": survived,
        "timeout": timeout,
        "suspicious": suspicious,
        "skipped": skipped,
        "total": total,
        "score_pct": score,
    }


# ----------------------------------------------------------------------
# Rendering
# ----------------------------------------------------------------------


def _format_duration(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def _badge(status: str) -> str:
    return {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}.get(status, status)


def _render(
    stages: list[Stage],
    junits: list[JUnitSummary],
    coverage: dict[str, float] | None,
    mutation: dict[str, int | float] | None,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    overall_pass = all(s.status == "PASS" for s in stages)

    lines: list[str] = []
    lines.append("# Test Report — impl/python")
    lines.append("")
    lines.append(f"_Generated {now}_")
    lines.append("")
    lines.append(f"**Overall:** {'PASS' if overall_pass else 'FAIL'}")
    lines.append("")

    # Stage summary table.
    lines.append("## Stage summary")
    lines.append("")
    lines.append("| Stage | Status | Duration | Log |")
    lines.append("|:--|:--:|--:|:--|")
    for s in stages:
        rel_log = Path(s.log_path).name
        lines.append(
            f"| {s.name} | {_badge(s.status)} | {_format_duration(s.duration_s)} | "
            f"[`raw/{rel_log}`](raw/{rel_log}) |"
        )
    lines.append("")

    # Test counts.
    if junits:
        lines.append("## Test counts")
        lines.append("")
        lines.append("| Category | Total | Failures | Errors | Skipped | Duration |")
        lines.append("|:--|--:|--:|--:|--:|--:|")
        grand_total = grand_fail = grand_err = grand_skip = 0
        grand_time = 0.0
        for j in junits:
            grand_total += j.total
            grand_fail += j.failures
            grand_err += j.errors
            grand_skip += j.skipped
            grand_time += j.duration_s
            lines.append(
                f"| {j.category} | {j.total} | {j.failures} | {j.errors} | "
                f"{j.skipped} | {_format_duration(j.duration_s)} |"
            )
        lines.append(
            f"| **Total** | **{grand_total}** | **{grand_fail}** | "
            f"**{grand_err}** | **{grand_skip}** | "
            f"**{_format_duration(grand_time)}** |"
        )
        lines.append("")

    # Coverage.
    if coverage:
        lines.append("## Coverage")
        lines.append("")
        lines.append(f"- Line coverage:   **{coverage['line_pct']:.1f}%**")
        if coverage.get("branch_pct"):
            lines.append(f"- Branch coverage: **{coverage['branch_pct']:.1f}%**")
        lines.append(
            f"- Statements: {coverage['covered']}/{coverage['num_statements']} "
            f"covered ({coverage['missing']} missing)"
        )
        lines.append("- HTML report: [`htmlcov/index.html`](htmlcov/index.html)")
        lines.append("")

    # Mutation.
    if mutation:
        lines.append("## Mutation testing")
        lines.append("")
        lines.append(f"- **Mutation score:** {mutation['score_pct']:.1f}%")
        lines.append(f"- Killed:    {mutation['killed']}")
        lines.append(f"- Survived:  {mutation['survived']}  *(P0 if non-zero in algebra/)*")
        lines.append(f"- Timeout:   {mutation['timeout']}")
        lines.append(f"- Suspicious:{mutation['suspicious']}")
        lines.append(f"- Skipped:   {mutation['skipped']}")
        lines.append(f"- Total mutants: {mutation['total']}")
        lines.append("")

    # Failing tests.
    failing: list[tuple[str, str]] = []
    for j in junits:
        for name in j.failing_tests:
            failing.append((j.category, name))
    if failing:
        lines.append("## Failing tests")
        lines.append("")
        for category, name in failing:
            lines.append(f"- `[{category}]` {name}")
        lines.append("")

    # Slowest tests across categories.
    slow: list[tuple[str, str, float]] = []
    for j in junits:
        for name, t in j.slowest:
            slow.append((j.category, name, t))
    slow.sort(key=lambda c: c[2], reverse=True)
    if slow:
        lines.append("## Slowest 10 tests")
        lines.append("")
        lines.append("| Category | Test | Duration |")
        lines.append("|:--|:--|--:|")
        for category, name, t in slow[:10]:
            lines.append(f"| {category} | `{name}` | {t:.2f}s |")
        lines.append("")

    lines.append("## Where to next")
    lines.append("")
    lines.append("- Raw stage logs:    `test-results/raw/`")
    lines.append("- HTML coverage:     `test-results/htmlcov/index.html`")
    lines.append("- JUnit XML (CI):    `test-results/raw/junit_*.xml`")
    lines.append("- This report:       `test-results/REPORT.md`")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--results-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--mutation", default="none",
                        choices=["none", "fast", "full"])
    parser.add_argument("--stages-file", required=True, type=Path)
    args = parser.parse_args(argv)

    stages = _read_stages(args.stages_file)

    categories = [
        ("Unit",        args.raw_dir / "junit_unit.xml"),
        ("Property",    args.raw_dir / "junit_property.xml"),
        ("Golden",      args.raw_dir / "junit_golden.xml"),
        ("E2E",         args.raw_dir / "junit_e2e.xml"),
        ("Adapter",     args.raw_dir / "junit_adapter.xml"),
        ("Combined",    args.raw_dir / "pytest.junit.xml"),
    ]
    junits: list[JUnitSummary] = []
    for cat, path in categories:
        # The "Combined" summary duplicates the others; surface it only if no
        # per-category file was emitted.
        if cat == "Combined" and any(p.exists() for _, p in categories[:-1]):
            continue
        s = _summarise_junit(cat, path)
        if s is not None:
            junits.append(s)

    coverage = _read_coverage(args.raw_dir / "coverage.json")
    mutation = _read_mutation(args.raw_dir, args.mutation)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(_render(stages, junits, coverage, mutation))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
