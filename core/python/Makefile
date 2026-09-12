.PHONY: format lint typecheck test

format:
	uv run ruff format src tests

lint:
	uv run ruff check src tests

typecheck:
	uv run mypy --config-file ../../mypy.ini src tests

test:
	uv run pytest
