.PHONY: help install install-dev precommit-install \
        test test-unit test-property test-golden test-e2e test-adapter \
        golden-refresh bench \
        lint typecheck architecture format check audit-file-size \
        mutation mutation-fast \
        conformance conformance-all \
        clean

PYTHON ?= python
PIP    ?= $(PYTHON) -m pip

help:
	@echo "OSI Python reference implementation — development commands"
	@echo ""
	@echo "Setup"
	@echo "  make install-dev        Install runtime + dev deps; install pre-commit hook"
	@echo "  make precommit-install  (Re)install the project-local pre-commit hook"
	@echo ""
	@echo "Tests"
	@echo "  make test               Run all tests (unit + property + golden + e2e)"
	@echo "  make test-unit          Unit tests only"
	@echo "  make test-property      Hypothesis property tests only"
	@echo "  make test-golden        Snapshot (plan + SQL) tests only"
	@echo "  make test-e2e           DuckDB-executed tests only"
	@echo "  make test-adapter       Conformance adapter smoke tests only"
	@echo "  make golden-refresh     Refresh golden snapshots (EXPLICIT ACTION)"
	@echo "  make bench              Run benchmark tests"
	@echo ""
	@echo "Static analysis"
	@echo "  make lint               black --check + isort --check + flake8"
	@echo "  make typecheck          mypy strict"
	@echo "  make architecture       import-linter (one-way flow enforcement)"
	@echo "  make format             Auto-format with black + isort"
	@echo ""
	@echo "Mutation testing (INFRA.md §1.1)"
	@echo "  make mutation-fast      Mutation on src/osi/planning/algebra/ (~5 min)"
	@echo "  make mutation           Full mutation run (~30 min)"
	@echo ""
	@echo "Conformance (compliance/foundation-v0.1)"
	@echo "  make conformance        Run the Foundation suite filtered to enabled_proposals.yaml"
	@echo "  make conformance-all    Run the full Foundation suite (no proposal filter)"
	@echo ""
	@echo "CI equivalent"
	@echo "  make check              lint + typecheck + architecture + test"

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev]"
	$(PYTHON) -m pre_commit install --config .pre-commit-config.yaml

precommit-install:
	$(PYTHON) -m pre_commit install --config .pre-commit-config.yaml

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

test:
	$(PYTHON) -m pytest tests/ conformance/tests/

test-unit:
	$(PYTHON) -m pytest tests/unit/

test-property:
	$(PYTHON) -m pytest tests/properties/

test-golden:
	$(PYTHON) -m pytest tests/golden/

test-e2e:
	$(PYTHON) -m pytest tests/e2e/

test-adapter:
	$(PYTHON) -m pytest conformance/tests/

golden-refresh:
	@echo ">>> Refreshing golden snapshots. Commit the result ONLY if the changes are intentional."
	$(PYTHON) -m pytest tests/golden/ --snapshot-update

bench:
	$(PYTHON) -m pytest tests/ -m benchmark --benchmark-enable --benchmark-only

# ---------------------------------------------------------------------------
# Static analysis
# ---------------------------------------------------------------------------

lint:
	$(PYTHON) -m black --check src tests conformance
	$(PYTHON) -m isort --check-only src tests conformance
	$(PYTHON) -m flake8 src tests conformance

typecheck:
	$(PYTHON) -m mypy src

architecture:
	$(PYTHON) -m pip show import-linter >/dev/null 2>&1 || { echo "import-linter not installed"; exit 1; }
	lint-imports --config pyproject.toml

format:
	$(PYTHON) -m black src tests conformance
	$(PYTHON) -m isort src tests conformance

# Enforces the 600-LOC cap on files under src/osi/ (INFRA.md §1.2 /
# [I-DEC-6]). Pre-commit already does this per-file; this target audits
# the whole tree.
#
# EXCEPTION_FILES below is the documented temporary exception list — each
# file here must have a corresponding INFRA.md §3 roadmap item tracking
# the split that brings it back under the cap. Adding a new entry
# requires updating both INFRA.md §1.2 and INFRA.md §3.
EXCEPTION_FILES := \
	src/osi/planning/planner_bridge.py \
	src/osi/planning/planner.py \
	src/osi/planning/steps.py
EXCEPTION_CAP   := 700

audit-file-size:
	@fail=0; \
	for f in $$(find src/osi -name '*.py' -type f); do \
	    lines=$$(wc -l < "$$f"); \
	    cap=600; \
	    for e in $(EXCEPTION_FILES); do \
	        if [ "$$f" = "$$e" ]; then cap=$(EXCEPTION_CAP); break; fi; \
	    done; \
	    if [ "$$lines" -gt "$$cap" ]; then \
	        echo "ERROR: $$f has $$lines lines, cap is $$cap."; fail=1; \
	    fi; \
	done; \
	if [ $$fail -eq 0 ]; then echo "OK: all files within cap (default 600, exception 700)."; fi; \
	exit $$fail

# ---------------------------------------------------------------------------
# Mutation testing
# ---------------------------------------------------------------------------

mutation-fast:
	$(PYTHON) -m mutmut run --paths-to-mutate src/osi/planning/algebra/

mutation:
	$(PYTHON) -m mutmut run

# ---------------------------------------------------------------------------
# Conformance — run the OSI Foundation compliance suite against this impl
# ---------------------------------------------------------------------------

SUITE_ROOT ?= $(abspath ../../compliance/foundation-v0.1)
ADAPTER    := $(abspath conformance/adapter.py)
ENABLED    := conformance/enabled_proposals.yaml

conformance:
	@test -d "$(SUITE_ROOT)" || { echo "compliance/foundation-v0.1 not found at $(SUITE_ROOT)"; exit 1; }
	@proposals=$$($(PYTHON) -c "import yaml,sys; d=yaml.safe_load(open('$(ENABLED)')) or {}; print(' '.join(d.get('enabled') or []))"); \
	echo ">>> Running conformance with --proposals [$$proposals]"; \
	cd "$(SUITE_ROOT)" && $(PYTHON) -m harness.runner \
	    --adapter "$(ADAPTER)" \
	    --tests tests \
	    --datasets datasets \
	    --output results/osi_python \
	    $$( [ -n "$$proposals" ] && echo "--proposals $$proposals" || echo "--proposals" )

conformance-all:
	@test -d "$(SUITE_ROOT)" || { echo "compliance/foundation-v0.1 not found at $(SUITE_ROOT)"; exit 1; }
	cd "$(SUITE_ROOT)" && $(PYTHON) -m harness.runner \
	    --adapter "$(ADAPTER)" \
	    --tests tests \
	    --datasets datasets \
	    --output results/osi_python_all

# ---------------------------------------------------------------------------
# CI gate
# ---------------------------------------------------------------------------

check: lint typecheck architecture audit-file-size test

# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------

clean:
	rm -rf build/ dist/ .pytest_cache/ .mypy_cache/ \
	       .coverage coverage.json htmlcov/ \
	       .benchmarks/ .hypothesis/ .mutmut-cache/
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name '*.egg-info' -prune -exec rm -rf {} +
