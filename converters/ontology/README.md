# OSI Ontology Converters

Converters between OSI, Palantir, and Spec ontology formats.

| Converter | Direction |
|-----------|-----------|
| `palantir_to_osi` | Palantir ontology → OSI model |
| `osi_to_spec` | OSI model → Spec YAML |
| `spec_to_osi` | Spec YAML → OSI model |

## Prerequisites

- [pyenv](https://github.com/pyenv/pyenv) — manages the Python version

Install pyenv if you don't have it:

```bash
brew install pyenv
```

Add to your shell profile (`~/.zshrc` or `~/.bashrc`) and restart the shell:

```bash
export PYENV_ROOT="$HOME/.pyenv"
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"
```

## Setup

```bash
pyenv install 3.11
pyenv local 3.11
pip install --upgrade pip
pip install virtualenv
python -m virtualenv venv
source ./venv/bin/activate
pip install -r requirements.lock
pip install -e ".[dev]"
```

## Generating / updating the lock file

`requirements.lock` is produced by [pip-tools](https://github.com/jazzband/pip-tools) from `pyproject.toml`.
Run this whenever you add or change a dependency:

```bash
pip-compile --output-file requirements.lock pyproject.toml
```

## Usage

The package is importable as `osi` after installation:

```python
from osi.converter.palantir_to_osi.converter import PalantirToOsiConverter
from osi.converter.osi_to_spec.converter import OsiToSpecConverter
from osi.converter.spec_to_osi.converter import SpecToOsiConverter
```

## Scripts

### `scripts/palantir_to_osi.py`

Converts a Palantir ontology export (`.zip` file containing a Palantir ontology JSON and one or more dataset spec JSON files) into an OSI-compliant YAML representation, printed to stdout.

**Usage:**

```bash
python scripts/palantir_to_osi.py path/to/palantir_export.zip
```

Warnings are written to stderr; the OSI YAML is written to stdout.

**Environment variables (optional):**

| Variable                  | Default    | Description                                              |
|---------------------------|------------|----------------------------------------------------------|
| `SNOWFLAKE_DATABASE_NAME` | `PALANTIR` | Snowflake database name used to qualify table references |
| `SNOWFLAKE_SCHEMA_NAME`   | `PALANTIR` | Snowflake schema name used to qualify table references   |

If already set in your environment they will be picked up automatically. To override them for a single run:

```bash
SNOWFLAKE_DATABASE_NAME=MY_DB SNOWFLAKE_SCHEMA_NAME=MY_SCHEMA \
  python scripts/palantir_to_osi.py path/to/palantir_export.zip
```

## Deactivating the environment

```bash
deactivate
```