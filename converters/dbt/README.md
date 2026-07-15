<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements.  See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership.  The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License.  You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied.  See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# apache-ossie-dbt

Converts between dbt's [MetricFlow Semantic Interface](https://docs.getdbt.com/docs/build/about-metricflow) (MSI) and the [Apache Ossie](https://github.com/apache/ossie) format.

Both conversion directions are supported:

- `msi-to-osi` — `semantic_manifest.json` (dbt output) → Ossie YAML
- `osi-to-msi` — Ossie YAML → `semantic_manifest.json`

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
pip install apache-ossie-dbt
```

Or with uv:

```bash
uv add apache-ossie-dbt
```

## CLI usage

### dbt → Apache Ossie

Generate `semantic_manifest.json` from your dbt project first:

```bash
dbt parse
# output: target/semantic_manifest.json
```

Then convert to Ossie YAML:

```bash
ossie-dbt msi-to-osi -i target/semantic_manifest.json -o semantic_model.yaml
```

By default the Ossie semantic model is named `semantic_model`. Override it with `--model-name`:

```bash
ossie-dbt msi-to-osi -i target/semantic_manifest.json -o semantic_model.yaml --model-name my_project
```

Conversion issues (e.g. dropped CONVERSION or PRIVATE metrics) are printed as warnings to stderr. The output file is still written.

### Apache Ossie → dbt

```bash
ossie-dbt osi-to-msi -i semantic_model.yaml -o semantic_manifest.json
```

Produces a `semantic_manifest.json` that metricflow can load.

### Help

```bash
ossie-dbt --help
ossie-dbt msi-to-osi --help
ossie-dbt osi-to-msi --help
```

## Python API

```python
from ossie_dbt import MSIToOSIConverter, OSIToMSIConverter
from metricflow_semantics.model.dbt_manifest_parser import parse_manifest_from_dbt_generated_manifest

# dbt → Apache Ossie
manifest = parse_manifest_from_dbt_generated_manifest(Path("target/semantic_manifest.json").read_text())
result = MSIToOSIConverter().convert(manifest, osi_model_name="my_project")

for issue in result.issues:
    print(f"[warning] {issue.issue_type.value}: {issue.element_name}")

osi_yaml = result.output.to_osi_yaml()

# Apache Ossie → dbt
import yaml
from ossie import OSIDocument

document = OSIDocument.model_validate(yaml.safe_load(Path("semantic_model.yaml").read_text()))
result = OSIToMSIConverter().convert(document)
manifest_json = result.output.model_dump_json(by_alias=True, exclude_none=True, indent=2)
```

### Conversion notes

**MSI → Ossie** is lossy in the following ways, each recorded as a `ConverterIssue` in the result:

| Issue type | Reason |
|---|---|
| `CONVERSION_METRIC_DROPPED` | Ossie has no conversion-funnel metric type |
| `PRIVATE_METRIC_DROPPED` | Ossie has no visibility modifiers |
| `NATURAL_ENTITY_DROPPED` | Ossie has no natural-key entity type |
| `CUMULATIVE_SEMANTICS_LOSS` | Window/grain semantics cannot be expressed in an Ossie expression string; the base aggregation is preserved |

**Ossie → MSI** reconstructs a best-effort MSI manifest from Ossie's simpler schema. Nothing is dropped, but Ossie carries less structural information than MSI, so the converter makes the following choices:

- Single aggregations (`SUM(col)`, `COUNT(DISTINCT col)`, etc.) → SIMPLE metric with `metric_aggregation_params`
- `(expr_a) / (expr_b)` → RATIO metric with auto-generated sub-metrics
- Anything else → SIMPLE metric with the raw expression stored verbatim
- Time dimensions always receive `TimeGranularity.DAY` (Ossie carries no granularity field)

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
