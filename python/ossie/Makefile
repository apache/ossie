.PHONY: format lint typecheck

format:
	uv run ruff format src

lint:
	uv run ruff check src

typecheck:
	uv run mypy --config-file ../../mypy.ini src
