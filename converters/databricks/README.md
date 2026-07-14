# osi-databricks

Bidirectional converter between [Databricks Unity Catalog Metric View YAML](https://docs.databricks.com/aws/metric-views/yaml-ref) and the [Open Semantic Interchange (OSI)](../../core-spec/spec.md) format.

Part of the OSI hub-and-spoke converter architecture — see the [converter guide](../index.md) for background.

## Installation

**With uv (recommended):**

```bash
cd converters/databricks
uv sync
```

**With pip:**

```bash
cd converters/databricks
pip install -e .
```

### Dependencies

- Python ≥ 3.11
- `osi-python >= 0.2.0.dev0`
- `pydantic >= 2.0`
- `PyYAML >= 6.0`

## Usage

The package provides a CLI entry point `osi-databricks` with two subcommands.

### Import: Metric View → OSI

Convert a Databricks Metric View YAML file to OSI format:

```bash
osi-databricks import -i metric_view.yaml -o output_osi.yaml

# Specify a custom model name
osi-databricks import -i metric_view.yaml -o output_osi.yaml --model-name my_model
```

### Export: OSI → Metric View

Convert an OSI YAML file to one or more Metric View YAML files (one per dataset):

```bash
osi-databricks export -i osi_model.yaml -o ./output_dir/
```

Output files are named `{dataset_name}.yaml`. The output directory is created automatically if it doesn't exist.

### Programmatic Usage

```python
from osi_databricks.models import MetricViewModel
from osi_databricks.metric_view_to_osi import metric_view_to_osi
from osi_databricks.osi_to_metric_view import osi_to_metric_view

# Import
mv = MetricViewModel.from_yaml(open("metric_view.yaml").read())
osi_doc = metric_view_to_osi(mv, model_name="my_model")
print(osi_doc.to_osi_yaml())

# Export
from osi import OSIDocument
import yaml

raw = yaml.safe_load(open("osi_model.yaml").read())
doc = OSIDocument.model_validate(raw)
results = osi_to_metric_view(doc)
for name, model in results:
    open(f"{name}.yaml", "w").write(model.to_yaml())
```

## Mapping Reference

### Core Constructs

| Metric View YAML | OSI Equivalent | Direction | Notes |
|---|---|---|---|
| `source` (three-part name) | `dataset.source` | ↔ | Direct pass-through |
| `source` (SQL query) | `custom_extension` (DATABRICKS, `source_query`) | ↔ | Detected by SQL keywords |
| `fields[].name` | `field.name` | ↔ | Direct |
| `fields[].expr` | `field.expression.dialects[DATABRICKS]` | ↔ | Always stored as DATABRICKS dialect |
| `measures[].name` | `metric.name` | ↔ | Direct |
| `measures[].expr` | `metric.expression.dialects[DATABRICKS]` | ↔ | Same dialect logic as fields |
| `joins[].name` | `relationship.name` | ↔ | Direct |
| `joins[].source` | `relationship.to` | ↔ | Dataset name extracted from source |
| `joins[].on` | `relationship.from_columns` / `to_columns` | ↔ | Parsed from `a.col = b.col` pattern |
| `joins[].using` | `from_columns = to_columns = using` | → OSI | Columns same on both sides |
| `comment` (top-level) | `semantic_model.description` | ↔ | Direct |

### Metadata Mapping

| Metric View YAML | OSI Equivalent | Direction | Notes |
|---|---|---|---|
| `fields[].comment` | `field.description` | ↔ | Direct |
| `fields[].display_name` | `field.ai_context.synonyms[0]` | ↔ | First synonym = display_name |
| `fields[].synonyms` | `field.ai_context.synonyms[1:]` | ↔ | Remaining synonyms |
| `fields[].format` | `field.custom_extension` (DATABRICKS) | ↔ | No OSI core equivalent |
| `measures[].window` | `metric.custom_extension` (DATABRICKS) | ↔ | No OSI core equivalent |
| `filter` | `dataset.custom_extension` (DATABRICKS) | ↔ | No OSI core equivalent |
| `materialization` | `semantic_model.custom_extension` (DATABRICKS) | ↔ | No OSI core equivalent |

### Dialect Handling

On **import** (MV → OSI):
- Expressions are stored as `DATABRICKS` dialect
- If the expression uses only standard SQL (no `FILTER()`, `MEASURE()`, `QUALIFY`, `::`), an `ANSI_SQL` entry is also generated

On **export** (OSI → MV):
- Prefers `DATABRICKS` dialect expression
- Falls back to `ANSI_SQL` if `DATABRICKS` is unavailable
- Skips the field/metric with a warning if neither dialect is present

## Development

```bash
cd converters/databricks
uv sync

# Run tests
uv run pytest

# Run linter
uv run ruff check src/ tests/
```

## Known Limitations

- **Time dimension inference is heuristic.** The importer infers `is_time` from expression content (looking for DATE, TIME, TIMESTAMP, etc.). This may produce false positives or miss custom time expressions.
- **ON clause parsing is pattern-based.** Only `alias.column = alias.column` patterns are parsed. Complex ON conditions (functions, nested expressions) are stored in a custom extension with a warning.
- **Nested joins (snowflake schema) are flattened.** Metric View supports nested join definitions; during import these are flattened into a list of OSI relationships. The nesting structure is not preserved during round-trip.
- **USING clause becomes ON on round-trip.** Joins defined with `USING` are converted to explicit column pairs in OSI. On export, they are reconstructed as `ON` clauses rather than `USING`.
- **No model-level description in Metric View.** The top-level `comment` is the closest equivalent. Model-level `ai_context.instructions` is stored in a custom extension.
- **Custom extensions for other vendors are preserved but not applied.** Extensions for SNOWFLAKE, DBT, etc. pass through without modification.
