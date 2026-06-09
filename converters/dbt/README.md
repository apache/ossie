# osi-dbt

Converts between dbt's [MetricFlow Semantic Interface](https://docs.getdbt.com/docs/build/about-metricflow) (MSI) and the [Open Semantic Interchange](https://github.com/open-semantic-interchange/OSI) (OSI) format.

Both conversion directions are supported:

- `msi-to-osi` — `semantic_manifest.json` (dbt output) → OSI YAML
- `osi-to-msi` — OSI YAML → `semantic_manifest.json`

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
pip install osi-dbt
```

Or with uv:

```bash
uv add osi-dbt
```

## CLI usage

### dbt → OSI

Generate `semantic_manifest.json` from your dbt project first:

```bash
dbt parse
# output: target/semantic_manifest.json
```

Then convert to OSI YAML:

```bash
osi-dbt msi-to-osi -i target/semantic_manifest.json -o semantic_model.yaml
```

By default the OSI semantic model is named `semantic_model`. Override it with `--model-name`:

```bash
osi-dbt msi-to-osi -i target/semantic_manifest.json -o semantic_model.yaml --model-name my_project
```

Conversion issues (e.g. dropped CONVERSION or PRIVATE metrics) are printed as warnings to stderr. The output file is still written.

### OSI → dbt

```bash
osi-dbt osi-to-msi -i semantic_model.yaml -o semantic_manifest.json
```

Produces a `semantic_manifest.json` that metricflow can load.

### Help

```bash
osi-dbt --help
osi-dbt msi-to-osi --help
osi-dbt osi-to-msi --help
```

## Python API

```python
from osi_dbt import MSIToOSIConverter, OSIToMSIConverter
from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest

# dbt → OSI
manifest = parse_manifest_from_dbt_generated_manifest(Path("target/semantic_manifest.json").read_text())
result = MSIToOSIConverter().convert(manifest, osi_model_name="my_project")

for issue in result.issues:
    print(f"[warning] {issue.issue_type.value}: {issue.element_name}")

osi_yaml = result.output.to_osi_yaml()

# OSI → dbt
import yaml
from osi import OSIDocument

document = OSIDocument.model_validate(yaml.safe_load(Path("semantic_model.yaml").read_text()))
result = OSIToMSIConverter().convert(document)
manifest_json = result.output.model_dump_json(by_alias=True, exclude_none=True, indent=2)
```

### Conversion notes

**MSI → OSI** is lossy in the following ways, each recorded as a `ConverterIssue` in the result:

| Issue type | Reason |
|---|---|
| `CONVERSION_METRIC_DROPPED` | OSI has no conversion-funnel metric type |
| `PRIVATE_METRIC_DROPPED` | OSI has no visibility modifiers |
| `NATURAL_ENTITY_DROPPED` | OSI has no natural-key entity type |
| `CUMULATIVE_SEMANTICS_LOSS` | Window/grain semantics cannot be expressed in an OSI expression string; the base aggregation is preserved |

**OSI → MSI** reconstructs a best-effort MSI manifest from OSI's simpler schema. Nothing is dropped, but OSI carries less structural information than MSI, so the converter makes the following choices:

- Single aggregations (`SUM(col)`, `COUNT(DISTINCT col)`, etc.) → SIMPLE metric with `metric_aggregation_params`
- `(expr_a) / (expr_b)` → RATIO metric with auto-generated sub-metrics
- Anything else → SIMPLE metric with the raw expression stored verbatim
- Time dimensions always receive `TimeGranularity.DAY` (OSI carries no granularity field)

## Development

```bash
cd converters/dbt
uv sync
uv run pytest
```

Generate initial syrupy snapshots on first run:

```bash
uv run pytest --snapshot-update
```
