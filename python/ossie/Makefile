.PHONY: format lint typecheck test lint-imports audit-file-size mutation check

format:
	uv run ruff format src tests

lint:
	uv run ruff check src tests

# Strict typing is enforced on the implementation (src). The property-test
# suite is intentionally dynamic (hypothesis), so tests are not strict-typed.
typecheck:
	uv run mypy --config-file ../../mypy.ini src

test:
	uv run pytest

# Closed-algebra layering guard: the algebra may not import higher layers.
# Contract lives in pyproject.toml ([tool.importlinter]).
lint-imports:
	uv run lint-imports

# Keep modules small enough to review. The closed algebra is deliberately
# compact; a module crossing the cap should be split, not exempted.
audit-file-size:
	@bad=$$(find src/ossie -name '*.py' -exec wc -l {} + \
		| awk '$$2 != "total" && $$1 > 700 {print}'); \
	if [ -n "$$bad" ]; then \
		echo "ERROR: modules over 700 lines:"; echo "$$bad"; exit 1; \
	else echo "OK: every module <= 700 lines"; fi

# Mutation testing of the correctness core. Slow — opt-in, NOT run in
# pre-commit. Target: >= 90% killed over src/ossie/algebra (see
# src/ossie/algebra/ARCHITECTURE.md).
mutation:
	uv run mutmut run

# Everything a PR must pass (mutation excluded — run it separately).
check: lint typecheck lint-imports audit-file-size test
