# apache-ossie-sql

A [SQLGlot](https://github.com/tobymao/sqlglot) dialect implementing the
Ossie expression language defined in
[`core-spec/expression_language.md`](../../../core-spec/expression_language.md)
("Ossie_SQL_2026").

This package covers only the expression grammar: a custom SQLGlot `Dialect`
(tokenizer/parser/generator) so `sqlglot.parse_one(sql, read="ossie")` parses
and round-trips the spec's SQL subset (aggregate/window/date/string/math/
conditional functions, typed literals, `CASE`, `CAST`/`TRY_CAST`, etc.), plus
a validator that rejects the constructs the spec explicitly disallows
(`SELECT`/`FROM`/`JOIN`, `GROUP BY`, `WHERE`, subqueries, CTEs, set
operations, DDL/DML). Wiring the dialect into the Ossie YAML model (the
spec's "Changes to YAML" section) is out of scope here.

## Development

This package uses [`uv`](https://docs.astral.sh/uv/) for dependency
management.

```bash
uv sync

# Run the test suite
uv run pytest

# Format code (auto-fixes in place)
uv run ruff format src tests

# Check formatting without modifying files
uv run ruff format --check src tests

# Lint
uv run ruff check src tests

# Type-check (strict; rules come from the shared repo-root mypy.ini)
uv run mypy --config-file ../../mypy.ini src tests
```

Or via the `Makefile`:

```bash
make format
make lint
make typecheck
make test
```

### Enforcing formatting/lint/type-checking locally

Install the pre-commit hooks scoped to this package so `ruff format`,
`ruff check`, and `mypy` run automatically before each commit:

```bash
uv run pre-commit install -c python/ossie-sql/.pre-commit-config.yaml
```
