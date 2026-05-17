"""Main test runner for the OSI compliance test suite."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import yaml

from .db_manager import DBManager
from .models import SuiteResult, TestCase, TestResult, TestStatus
from .reporter import format_summary_console, write_reports
from .result_compare import compare_results


def discover_tests(
    tests_dir: Path,
    *,
    difficulty: str | None = None,
    area: str | None = None,
    include_planned: bool = False,
) -> list[TestCase]:
    """Walk the tests directory and discover all test cases."""
    test_cases: list[TestCase] = []

    for metadata_path in sorted(tests_dir.rglob("metadata.yaml")):
        test_dir = metadata_path.parent
        meta = yaml.safe_load(metadata_path.read_text())

        test_difficulty = meta.get("difficulty", "")
        test_area = meta.get("area", "")

        if difficulty and test_difficulty != difficulty:
            continue
        if area and test_area != area:
            continue

        model_path = test_dir / "model.yaml"
        query_path = test_dir / "query.json"
        gold_sql_path = test_dir / "gold.sql"

        if not all(p.exists() for p in [model_path, query_path, gold_sql_path]):
            print(
                f"WARN: Skipping {test_dir.name} — missing required files",
                file=sys.stderr,
            )
            continue

        parts = test_dir.relative_to(tests_dir).parts
        test_id = "/".join(parts)

        test_status = meta.get("status", "active")
        if test_status == "planned" and not include_planned:
            continue

        test_cases.append(
            TestCase(
                test_id=test_id,
                name=meta.get("name", test_dir.name),
                description=meta.get("description", ""),
                area=test_area,
                difficulty=test_difficulty,
                dataset=meta.get("dataset", ""),
                spec_refs=meta.get("spec_refs", []),
                tags=meta.get("tags", []),
                model_path=model_path,
                query_path=query_path,
                gold_sql_path=gold_sql_path,
                test_dir=test_dir,
                expected_error=bool(meta.get("expected_error", False)),
                expected_error_code=meta.get("expected_error_code", ""),
                conformance_level=meta.get("conformance_level", "full"),
                status=test_status,
                required_features=meta.get("required_features", []),
            )
        )

    return test_cases


def invoke_adapter(
    adapter_path: Path,
    model_path: Path,
    query_path: Path,
    dialect: str = "duckdb",
    timeout: int = 60,
) -> tuple[str, str, int]:
    """Invoke the adapter subprocess. Returns (stdout, stderr, returncode)."""
    cmd = [
        sys.executable,
        str(adapter_path),
        "sql",
        "--model",
        str(model_path),
        "--query-file",
        str(query_path),
        "--dialect",
        dialect,
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return "", "Adapter timed out", 1


def run_test(
    test: TestCase,
    adapter_path: Path,
    db: DBManager,
    datasets_dir: Path,
) -> TestResult:
    """Run a single test case and return the result."""
    start = time.monotonic()

    try:
        db.reset()
        db.load_dataset(test.dataset, datasets_dir)
    except Exception as e:
        return TestResult(
            test_id=test.test_id,
            area=test.area,
            difficulty=test.difficulty,
            status=TestStatus.ERROR,
            spec_refs=test.spec_refs,
            error_type="dataset_load",
            error_detail=str(e),
            duration_ms=(time.monotonic() - start) * 1000,
        )

    stdout, stderr, rc = invoke_adapter(
        adapter_path,
        test.model_path,
        test.query_path,
    )

    if test.expected_error:
        elapsed = (time.monotonic() - start) * 1000
        if rc != 0:
            # Adapter correctly rejected — check error code if specified
            if test.expected_error_code and test.expected_error_code not in stderr:
                return TestResult(
                    test_id=test.test_id,
                    area=test.area,
                    difficulty=test.difficulty,
                    status=TestStatus.FAIL,
                    spec_refs=test.spec_refs,
                    error_type="wrong_error_code",
                    error_detail=(
                        f"Expected error code '{test.expected_error_code}' "
                        f"in stderr but got: {stderr.strip()[:200]}"
                    ),
                    duration_ms=elapsed,
                )
            return TestResult(
                test_id=test.test_id,
                area=test.area,
                difficulty=test.difficulty,
                status=TestStatus.PASS,
                spec_refs=test.spec_refs,
                error_detail=f"Correctly rejected: {stderr.strip()[:200]}",
                duration_ms=elapsed,
            )
        else:
            return TestResult(
                test_id=test.test_id,
                area=test.area,
                difficulty=test.difficulty,
                status=TestStatus.FAIL,
                spec_refs=test.spec_refs,
                error_type="expected_error_missing",
                error_detail=(
                    "Expected adapter to reject this query with a non-zero "
                    "exit code, but it succeeded"
                ),
                generated_sql=stdout.strip(),
                duration_ms=elapsed,
            )

    if rc != 0:
        return TestResult(
            test_id=test.test_id,
            area=test.area,
            difficulty=test.difficulty,
            status=TestStatus.ERROR,
            spec_refs=test.spec_refs,
            error_type="adapter_error",
            error_detail=stderr.strip() or f"exit code {rc}",
            duration_ms=(time.monotonic() - start) * 1000,
        )

    generated_sql = stdout.strip()

    try:
        generated_rows = db.execute_sql(generated_sql)
    except Exception as e:
        return TestResult(
            test_id=test.test_id,
            area=test.area,
            difficulty=test.difficulty,
            status=TestStatus.ERROR,
            spec_refs=test.spec_refs,
            error_type="generated_sql_error",
            error_detail=f"Generated SQL failed: {e}",
            generated_sql=generated_sql,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    gold_sql = test.gold_sql_path.read_text().strip()
    try:
        gold_rows = db.execute_sql(gold_sql)
    except Exception as e:
        return TestResult(
            test_id=test.test_id,
            area=test.area,
            difficulty=test.difficulty,
            status=TestStatus.ERROR,
            spec_refs=test.spec_refs,
            error_type="gold_sql_error",
            error_detail=f"Gold SQL failed: {e}",
            generated_sql=generated_sql,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    is_ordered = test.has_order_by
    match, detail = compare_results(
        generated_rows,
        gold_rows,
        ordered=is_ordered,
    )

    elapsed = (time.monotonic() - start) * 1000

    if match:
        return TestResult(
            test_id=test.test_id,
            area=test.area,
            difficulty=test.difficulty,
            status=TestStatus.PASS,
            spec_refs=test.spec_refs,
            generated_sql=generated_sql,
            generated_rows=generated_rows,
            gold_rows=gold_rows,
            duration_ms=elapsed,
        )
    else:
        return TestResult(
            test_id=test.test_id,
            area=test.area,
            difficulty=test.difficulty,
            status=TestStatus.FAIL,
            spec_refs=test.spec_refs,
            error_type="result_mismatch",
            error_detail=detail,
            generated_sql=generated_sql,
            generated_rows=generated_rows,
            gold_rows=gold_rows,
            duration_ms=elapsed,
        )


def list_tests(
    tests_dir: Path,
    *,
    difficulty: str | None = None,
    area: str | None = None,
    conformance_level: str | None = None,
    include_planned: bool = False,
) -> None:
    """Print discovered tests without running them."""
    tests = discover_tests(
        tests_dir,
        difficulty=difficulty,
        area=area,
        include_planned=include_planned,
    )
    if conformance_level:
        tests = [t for t in tests if t.conformance_level == conformance_level]

    print(f"{'ID':<60} {'Area':<25} {'Diff':<12} {'Level':<10} {'Status':<8} {'Err?'}")
    print("-" * 125)
    for t in tests:
        err = "yes" if t.expected_error else ""
        status = t.status if t.status != "active" else ""
        print(
            f"{t.test_id:<60} {t.area:<25} {t.difficulty:<12} {t.conformance_level:<10} {status:<8} {err}"
        )
    print(f"\nTotal: {len(tests)} test(s)")


def run_suite(
    adapter_path: Path,
    tests_dir: Path,
    datasets_dir: Path,
    output_dir: Path,
    *,
    difficulty: str | None = None,
    area: str | None = None,
    conformance_level: str | None = None,
    include_planned: bool = False,
    verbose: bool = False,
    adapter_features: set[str] | None = None,
) -> SuiteResult:
    """Run the full test suite and generate reports."""
    tests = discover_tests(
        tests_dir,
        difficulty=difficulty,
        area=area,
        include_planned=include_planned,
    )
    if conformance_level:
        tests = [t for t in tests if t.conformance_level == conformance_level]

    skipped_by_feature: list[TestCase] = []
    runnable_tests: list[TestCase] = tests
    if adapter_features is not None:
        runnable_tests = []
        for t in tests:
            if t.required_features and not set(t.required_features).issubset(
                adapter_features
            ):
                skipped_by_feature.append(t)
                continue
            runnable_tests.append(t)

    if not runnable_tests and not skipped_by_feature:
        print("No test cases found.", file=sys.stderr)
        return SuiteResult(adapter=str(adapter_path))

    print(f"Discovered {len(tests)} test(s)")
    if skipped_by_feature:
        print(
            f"  skipping {len(skipped_by_feature)} test(s) "
            "with unsupported proposals"
        )
    print(f"Adapter: {adapter_path}")
    print(f"Datasets: {datasets_dir}")
    print()

    db = DBManager()
    suite = SuiteResult(
        adapter=str(adapter_path.name),
        adapter_features=(
            frozenset(adapter_features) if adapter_features is not None else None
        ),
    )

    for t in skipped_by_feature:
        missing = sorted(set(t.required_features) - set(adapter_features or ()))
        suite.results.append(
            TestResult(
                test_id=t.test_id,
                area=t.area,
                difficulty=t.difficulty,
                status=TestStatus.SKIP,
                spec_refs=t.spec_refs,
                error_type="unsupported_proposal",
                error_detail=f"required proposals not advertised: {', '.join(missing)}",
                required_features=list(t.required_features),
            )
        )

    for i, test in enumerate(runnable_tests, 1):
        label = f"[{i}/{len(runnable_tests)}] {test.test_id}"
        result = run_test(test, adapter_path, db, datasets_dir)
        result.required_features = list(test.required_features)
        suite.results.append(result)

        if result.status == TestStatus.PASS:
            if test.expected_error:
                print(f"  PASS  {label} (expected error)")
            else:
                print(f"  PASS  {label}")
        elif result.status == TestStatus.FAIL:
            print(f"  FAIL  {label}")
            if verbose:
                print(f"        {result.error_detail}")
        elif result.status == TestStatus.ERROR:
            print(f"  ERR   {label}")
            if verbose:
                print(f"        [{result.error_type}] {result.error_detail}")
        else:
            print(f"  SKIP  {label}")

    db.close()

    csv_path, md_path = write_reports(suite, output_dir)
    print(format_summary_console(suite))
    print("\nReports written to:")
    print(f"  {csv_path}")
    print(f"  {md_path}")

    return suite


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="harness.runner",
        description="OSI Compliance Test Suite Runner",
    )
    parser.add_argument(
        "--adapter",
        help="Path to the adapter script (e.g., adapters/python_adapter.py)",
    )
    parser.add_argument(
        "--tests",
        required=True,
        help="Path to the tests directory",
    )
    parser.add_argument(
        "--datasets",
        help="Path to the datasets directory",
    )
    parser.add_argument(
        "--output",
        default="results/latest",
        help=(
            "Output directory for reports (default: results/latest). "
            "Per-run artifacts (failures.csv, summary.md) go here. "
            "The curated baseline at results/REPORT.md is committed and "
            "must not be overwritten — choose a subdirectory of results/ "
            "(e.g. results/<adapter>/) or another path entirely."
        ),
    )
    parser.add_argument(
        "--difficulty",
        choices=["easy", "moderate", "hard", "conversion"],
        help="Filter tests by difficulty",
    )
    parser.add_argument(
        "--area",
        help="Filter tests by area (e.g., grain_and_lod, filters)",
    )
    parser.add_argument(
        "--conformance-level",
        choices=["core", "full", "extended"],
        help="Filter tests by conformance level",
    )
    parser.add_argument(
        "--include-planned",
        action="store_true",
        help="Include tests with status: planned (normally skipped)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="List discovered tests without running them",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show error details for failures",
    )
    parser.add_argument(
        "--adapter-features",
        "--proposals",
        nargs="*",
        default=None,
        dest="adapter_features",
        help="Proposal IDs the adapter implements (from proposals.yaml). "
        "Tests whose required_features aren't a subset of this set are "
        "recorded as SKIP. Alias: --proposals.",
    )

    args = parser.parse_args()

    if args.list_only:
        list_tests(
            Path(args.tests),
            difficulty=args.difficulty,
            area=args.area,
            conformance_level=args.conformance_level,
            include_planned=args.include_planned,
        )
        return 0

    if not args.adapter:
        parser.error("--adapter is required when running tests")
    if not args.datasets:
        parser.error("--datasets is required when running tests")

    feat = set(args.adapter_features) if args.adapter_features is not None else None
    suite = run_suite(
        adapter_path=Path(args.adapter),
        tests_dir=Path(args.tests),
        datasets_dir=Path(args.datasets),
        output_dir=Path(args.output),
        difficulty=args.difficulty,
        area=args.area,
        conformance_level=args.conformance_level,
        include_planned=args.include_planned,
        verbose=args.verbose,
        adapter_features=feat,
    )

    if suite.failed + suite.errors > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
