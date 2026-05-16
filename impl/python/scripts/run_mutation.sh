#!/usr/bin/env bash
# run_mutation.sh — run mutation testing and summarise the result into the
# main test report.
#
# Usage:
#   scripts/run_mutation.sh          # full mutation run (~30 min)
#   scripts/run_mutation.sh --fast   # algebra only (~5 min)
#
# This is a thin wrapper around `make mutation` / `make mutation-fast` that
# captures the mutmut summary into test-results/raw/ so write_test_report.py
# can fold it into REPORT.md.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJ_ROOT"

RAW_DIR="$PROJ_ROOT/test-results/raw"
mkdir -p "$RAW_DIR"

PYTHON="${PYTHON:-python}"

MODE="full"
for arg in "$@"; do
    case "$arg" in
        --fast) MODE="fast" ;;
        --full) MODE="full" ;;
        -h|--help)
            grep '^# ' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *) echo "Unknown flag: $arg" >&2; exit 2 ;;
    esac
done

if [[ "$MODE" == "fast" ]]; then
    LOG="$RAW_DIR/mutation_fast.log"
    echo ">>> mutation-fast (algebra only, ~5 min) — log: $LOG"
    make mutation-fast 2>&1 | tee "$LOG"
else
    LOG="$RAW_DIR/mutation_full.log"
    echo ">>> mutation full (~30 min) — log: $LOG"
    make mutation 2>&1 | tee "$LOG"
fi

# Capture mutmut's textual summary.
SUMMARY="$RAW_DIR/mutation_${MODE}_summary.txt"
$PYTHON -m mutmut results > "$SUMMARY" 2>&1 || true
echo
echo ">>> mutmut results captured: $SUMMARY"
echo ">>> Re-run scripts/run_all_tests.sh to refresh REPORT.md"
