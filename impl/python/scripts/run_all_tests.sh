#!/usr/bin/env bash
# run_all_tests.sh — run every test category for impl/python and emit a
# single readable Markdown report at test-results/REPORT.md.
#
# Usage:
#   scripts/run_all_tests.sh                       # everything except full mutation
#   scripts/run_all_tests.sh --with-mutation-fast  # + algebra mutation (~5 min)
#   scripts/run_all_tests.sh --with-mutation       # + full mutation (~30 min)
#   scripts/run_all_tests.sh --skip-static         # skip lint/typecheck/architecture
#
# The script never aborts on the first failure — every stage runs and its
# status is captured. Final exit code is non-zero if any stage failed.

set -u  # do NOT set -e; we want every stage to run and be reported

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ_ROOT"

RESULTS_DIR="$PROJ_ROOT/test-results"
RAW_DIR="$RESULTS_DIR/raw"
mkdir -p "$RAW_DIR"

PYTHON="${PYTHON:-python}"

# ----------------------------------------------------------------------
# Flags
# ----------------------------------------------------------------------
WITH_MUTATION=""
SKIP_STATIC=""

for arg in "$@"; do
    case "$arg" in
        --with-mutation)      WITH_MUTATION="full" ;;
        --with-mutation-fast) WITH_MUTATION="fast" ;;
        --skip-static)        SKIP_STATIC="1" ;;
        -h|--help)
            grep '^# ' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg" >&2
            exit 2
            ;;
    esac
done

# ----------------------------------------------------------------------
# Result tracking
# ----------------------------------------------------------------------
declare -a STAGE_NAMES
declare -a STAGE_STATUSES
declare -a STAGE_LOGS
declare -a STAGE_DURATIONS

run_stage() {
    # run_stage <name> <log_basename> -- <cmd...>
    local name="$1"; shift
    local log_basename="$1"; shift
    shift  # discard "--"
    local logfile="$RAW_DIR/${log_basename}.log"

    echo
    echo ">>> [${name}]"
    local start_ts="$(date +%s)"
    if "$@" > "$logfile" 2>&1; then
        local status="PASS"
    else
        local status="FAIL"
    fi
    local end_ts="$(date +%s)"
    local duration="$((end_ts - start_ts))"

    STAGE_NAMES+=("$name")
    STAGE_STATUSES+=("$status")
    STAGE_LOGS+=("$logfile")
    STAGE_DURATIONS+=("$duration")
    echo "    -> $status  (${duration}s)  $logfile"
}

# ----------------------------------------------------------------------
# Stages
# ----------------------------------------------------------------------

if [[ -z "$SKIP_STATIC" ]]; then
    run_stage "Lint (black/isort/flake8)"      lint        -- make lint
    run_stage "Typecheck (mypy strict)"        typecheck   -- make typecheck
    run_stage "Architecture (import-linter)"   architecture -- make architecture
    run_stage "File-size cap (600 LOC)"        file_size   -- make audit-file-size
fi

# Always produce JUnit XML so we can extract per-test detail.
JUNIT="$RAW_DIR/pytest.junit.xml"
COV_JSON="$RAW_DIR/coverage.json"

run_stage "Unit tests"                          test_unit     -- $PYTHON -m pytest tests/unit/      --junit-xml="$RAW_DIR/junit_unit.xml"     --no-cov
run_stage "Property tests (Hypothesis)"         test_property -- $PYTHON -m pytest tests/properties/ --junit-xml="$RAW_DIR/junit_property.xml" --no-cov
run_stage "Golden tests (plan / SQL snapshots)" test_golden   -- $PYTHON -m pytest tests/golden/    --junit-xml="$RAW_DIR/junit_golden.xml"   --no-cov
run_stage "E2E tests (DuckDB)"                  test_e2e      -- $PYTHON -m pytest tests/e2e/       --junit-xml="$RAW_DIR/junit_e2e.xml"      --no-cov
run_stage "Adapter smoke tests"                 test_adapter  -- $PYTHON -m pytest conformance/tests/ --junit-xml="$RAW_DIR/junit_adapter.xml" --no-cov

# Coverage across the union of test categories that produce signal.
run_stage "Coverage (combined)" coverage -- $PYTHON -m pytest \
    tests/unit/ tests/properties/ tests/golden/ tests/e2e/ conformance/tests/ \
    --cov=osi --cov-branch \
    --cov-report=term --cov-report=html:"$RESULTS_DIR/htmlcov" \
    --cov-report=json:"$COV_JSON" \
    --junit-xml="$JUNIT"

# Mutation
if [[ "$WITH_MUTATION" == "fast" ]]; then
    run_stage "Mutation testing (algebra fast-path)" mutation_fast -- make mutation-fast
elif [[ "$WITH_MUTATION" == "full" ]]; then
    run_stage "Mutation testing (full)" mutation_full -- make mutation
fi

# ----------------------------------------------------------------------
# Write report
# ----------------------------------------------------------------------

STAGES_FILE="$RAW_DIR/stages.tsv"
{
    printf '%s\n' "${#STAGE_NAMES[@]}"
    for i in "${!STAGE_NAMES[@]}"; do
        printf '%s\t%s\t%s\t%s\n' \
            "${STAGE_NAMES[$i]}" \
            "${STAGE_STATUSES[$i]}" \
            "${STAGE_LOGS[$i]}" \
            "${STAGE_DURATIONS[$i]}"
    done
} > "$STAGES_FILE"

REPORT="$RESULTS_DIR/REPORT.md"
$PYTHON "$SCRIPT_DIR/write_test_report.py" \
    --raw-dir "$RAW_DIR" \
    --results-dir "$RESULTS_DIR" \
    --output "$REPORT" \
    --mutation "${WITH_MUTATION:-none}" \
    --stages-file "$STAGES_FILE"

echo
echo "==============================================================="
echo " Report: $REPORT"
echo "==============================================================="

# Exit nonzero if any stage failed.
for s in "${STAGE_STATUSES[@]}"; do
    if [[ "$s" != "PASS" ]]; then
        exit 1
    fi
done
exit 0
