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

# Configures the shell for recipes to use bash, enabling bash commands and ensuring
# that recipes exit on any command failure (including within pipes).
SHELL = /usr/bin/env bash -o pipefail
.SHELLFLAGS = -ec

## Variables
PYTHON ?= python3
UV ?= uv
PRECOMMIT ?= $(UV) run --no-project --with pre-commit pre-commit
DUCKDB ?= duckdb
PYTHON_DIR := python
CORE_SCHEMA := core-spec/ossie-schema.json
ONTOLOGY_SCHEMA := ontology/ontology.json
MAPPING_SCHEMA := ontology/mapping.json
DEPENDENCIES ?= uv git
OPTIONAL_DEPENDENCIES := java duckdb

## Version information
SPEC_VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' $(PYTHON_DIR)/pyproject.toml)
GIT_COMMIT := $(shell git rev-parse HEAD 2>/dev/null || echo unknown)
UV_REQUIRED_VERSION := $(shell sed -n 's/^required-version = "\(.*\)"/\1/p' $(PYTHON_DIR)/pyproject.toml)
UV_VERSION := $(shell $(UV) --version 2>/dev/null | awk '{print $$2}')

##@ General

.PHONY: help
help: ## Display this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m<target>\033[0m\n"} /^[a-zA-Z_0-9\.-]+:.*?##/ { printf "  \033[36m%-40s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)

.PHONY: version
version: ## Display version information
	@echo "Ossie version: ${SPEC_VERSION}"
	@echo "Git commit: ${GIT_COMMIT}"
	@echo "uv required: ${UV_REQUIRED_VERSION}"
	@echo "uv installed: ${UV_VERSION}"

.PHONY: setup
setup: python-setup ## Set up the local development environment

.PHONY: clean
clean: python-clean ## Remove caches, build artifacts, and virtual environments
	@echo "--- Removing root caches, virtual environment, and Python bytecode ---"
	@rm -rf .coverage .hypothesis .mypy_cache .pytest_cache .ruff_cache .venv
	@find python validation scripts compliance core-spec/grammar -type d -name "__pycache__" -prune -exec rm -rf {} +
	@echo "--- Cleanup complete ---"

.PHONY: test
test: test-validation test-python compliance-test ## Run all test suites

.PHONY: check
check: test validate spec-check compliance-validate ## Run tests, validation, and generated-spec checks

##@ Validation

.PHONY: validate
validate: DEPENDENCIES := $(UV)
validate: check-dependencies ## Validate all canonical example documents
	@echo "--- Validating canonical examples ---"
	@$(UV) run --no-project validation/validate.py examples/tpcds_semantic_model.yaml
	@$(UV) run --no-project validation/validate.py examples/renamed_fields.yaml
	@$(UV) run --no-project validation/validate.py examples/flights.yaml --schema $(ONTOLOGY_SCHEMA)
	@$(UV) run --no-project validation/validate.py examples/flights.ontology.yaml --schema $(ONTOLOGY_SCHEMA)
	@$(UV) run --no-project validation/validate.py examples/flights.semantic_model.yaml
	@$(UV) run --no-project validation/validate.py examples/flights.mapping.yaml --schema $(MAPPING_SCHEMA)
	@echo "--- Canonical example validation complete ---"

.PHONY: test-validation
test-validation: DEPENDENCIES := $(UV)
test-validation: check-dependencies ## Run validator unit and pytest suites
	@echo "--- Running validator unit tests ---"
	@$(UV) run --no-project validation/test_validate.py
	@echo "--- Running validator pytest tests ---"
	@$(UV) run --no-project --with pytest --with pyyaml --with jsonschema --with sqlglot -m pytest validation/tests/
	@echo "--- Validator tests complete ---"

##@ Python SDK

.PHONY: python-setup
python-setup: DEPENDENCIES := $(UV)
python-setup: check-dependencies ## Create and sync the Python SDK virtual environment
	@echo "--- Syncing Python SDK environment ---"
	@$(UV) sync --directory $(PYTHON_DIR)
	@echo "--- Python SDK environment ready ---"

.PHONY: test-python
test-python: python-setup ## Run Python SDK tests
	@echo "--- Running Python SDK tests ---"
	@$(UV) run --directory $(PYTHON_DIR) pytest
	@echo "--- Python SDK tests complete ---"

.PHONY: python-build
python-build: python-setup ## Build Python SDK sdist and wheel
	@echo "--- Building Python SDK distributions ---"
	@$(UV) build --directory $(PYTHON_DIR) --clear
	@echo "--- Python SDK distributions built ---"

.PHONY: python-clean
python-clean: ## Remove Python SDK build artifacts and environment
	@echo "--- Removing Python SDK environment and build artifacts ---"
	@rm -rf $(PYTHON_DIR)/.mypy_cache $(PYTHON_DIR)/.pytest_cache $(PYTHON_DIR)/.ruff_cache $(PYTHON_DIR)/.venv $(PYTHON_DIR)/dist
	@find $(PYTHON_DIR) -type d -name "__pycache__" -prune -exec rm -rf {} +
	@echo "--- Python SDK cleanup complete ---"

##@ Spec

.PHONY: spec-check
spec-check: DEPENDENCIES := $(UV)
spec-check: check-dependencies ## Verify spec.yaml enums match ossie-schema.json
	@echo "--- Checking generated spec types ---"
	@$(UV) run --no-project scripts/generate-spec-types.py --check
	@echo "--- Generated spec types are in sync ---"

.PHONY: spec-generate
spec-generate: DEPENDENCIES := $(UV)
spec-generate: check-dependencies ## Regenerate spec.yaml enums from ossie-schema.json
	@echo "--- Regenerating spec types ---"
	@$(UV) run --no-project scripts/generate-spec-types.py --apply
	@echo "--- Generated spec types updated ---"

.PHONY: test-grammar
test-grammar: DEPENDENCIES := $(UV) java
test-grammar: check-dependencies ## Run ANTLR4 expression grammar tests
	@echo "--- Running expression grammar tests ---"
	@$(UV) run --no-project --with pytest --with pyyaml --with antlr4-python3-runtime==4.13.2 -m pytest core-spec/grammar/tests/
	@echo "--- Expression grammar tests complete ---"

##@ Compliance

.PHONY: compliance
compliance: compliance-test compliance-validate ## Run compliance tests and validate fixtures

.PHONY: compliance-test
compliance-test: DEPENDENCIES := $(UV)
compliance-test: check-dependencies ## Run portability grader tests
	@echo "--- Running compliance grader tests ---"
	@PYTHONPATH=compliance/portability/grader $(UV) run --no-project --with pytest --with pyyaml --with sqlglot -m pytest compliance/portability/grader/test_grade.py
	@echo "--- Compliance grader tests complete ---"

.PHONY: compliance-validate
compliance-validate: DEPENDENCIES := $(UV)
compliance-validate: check-dependencies ## Validate compliance models and portability fixtures
	@echo "--- Validating compliance fixtures ---"
	@$(UV) run --no-project validation/validate.py compliance/portability/m*/*.ossie.yaml compliance/query/scott/scott_model.ossie.yaml
	@echo "--- Compliance fixture validation complete ---"

.PHONY: compliance-query
compliance-query: DEPENDENCIES := $(DUCKDB)
compliance-query: check-dependencies ## Run query compliance reference cases with DuckDB
	@echo "--- Running query compliance reference cases ---"
	@cat compliance/query/scott/scott.sql compliance/query/cases/*/reference.duckdb.sql | $(DUCKDB)
	@echo "--- Query compliance reference cases complete ---"

##@ Quality

.PHONY: license
license: DEPENDENCIES := java curl
license: check-dependencies ## Run Apache RAT license header check
	@echo "--- Running license header check ---"
	@dev/check-license
	@echo "--- License header check complete ---"

.PHONY: pre-commit
pre-commit: DEPENDENCIES := $(UV)
pre-commit: check-dependencies ## Run all pre-commit hooks
	@echo "--- Running pre-commit hooks ---"
	@$(PRECOMMIT) run --all-files
	@echo "--- Pre-commit hooks complete ---"

##@ Dependencies

.PHONY: check-dependencies
check-dependencies: ## Check if all requested dependencies are present
	@echo "--- Checking for requested dependencies ---"
	@for dependency in $(DEPENDENCIES); do \
		echo "Checking for $$dependency..."; \
		if command -v $$dependency >/dev/null 2>&1; then \
			echo "$$dependency is installed."; \
		else \
			echo "$$dependency is NOT installed."; \
			echo "--- ERROR: Dependency '$$dependency' is missing. Please install it to proceed. Exiting. ---"; \
			exit 1; \
		fi; \
	done
	@echo "--- All checks complete. ---"

.PHONY: check-brew
check-brew: ## Check Homebrew installation
	@echo "--- Checking Homebrew installation ---"
	@if command -v brew >/dev/null 2>&1; then \
		echo "--- Homebrew is installed ---"; \
	else \
		echo "--- Homebrew is not installed. Aborting ---"; \
		exit 1; \
	fi

.PHONY: install-dependencies-brew
install-dependencies-brew: check-brew ## Install missing dependencies with Homebrew
	@echo "--- Checking and installing dependencies for this target ---"
	@for dependency in $(DEPENDENCIES); do \
		if command -v $$dependency >/dev/null 2>&1; then \
			echo "$$dependency is installed."; \
		else \
			case $$dependency in \
				java) \
					echo "Java is required for 'make license'. Install Java 11+ manually or run 'brew install openjdk'." ;; \
				*) \
					echo "Installing $$dependency with Homebrew..."; \
					brew install $$dependency; \
					echo "$$dependency installed." ;; \
			esac; \
		fi; \
	done
	@echo "--- All requested dependencies checked/installed ---"

.PHONY: install-optional-dependencies-brew
install-optional-dependencies-brew: ## Install optional dependencies with Homebrew
	@$(MAKE) --no-print-directory install-dependencies-brew DEPENDENCIES="$(OPTIONAL_DEPENDENCIES)"
