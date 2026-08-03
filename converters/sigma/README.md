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

# apache-ossie-sigma

Converts between [Sigma Computing](https://www.sigmacomputing.com/) Data Models (the
"code representation" spec returned by `GET /v2/dataModels/{id}/spec`, and accepted by
`POST`/`PUT` on the same resource) and the [Apache Ossie](https://github.com/apache/ossie)
format.

Both conversion directions are supported:

- `sigma-to-osi` — Sigma data model spec JSON → Ossie YAML
- `osi-to-sigma` — Ossie YAML → Sigma data model spec JSON

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Installation

```bash
pip install apache-ossie-sigma
```

Or with uv:

```bash
uv add apache-ossie-sigma
```

## CLI usage

### Sigma → Apache Ossie

Export a data model's spec from Sigma (e.g. with [sigcli](https://pypi.org/project/sigcli/)):

```bash
sigcli data-models spec get --params '{"dataModelId": "<id>"}' > data_model.json
ossie-sigma sigma-to-osi -i data_model.json -o semantic_model.yaml
```

### Apache Ossie → Sigma

```bash
ossie-sigma osi-to-sigma -i semantic_model.yaml -o data_model.json
```

The output is a Sigma data model spec JSON document suitable for
`sigcli data-models spec create`/`update`.

### Help

```bash
ossie-sigma --help
ossie-sigma sigma-to-osi --help
ossie-sigma osi-to-sigma --help
```

## Python API

```python
import json
from pathlib import Path

from ossie_sigma import SigmaToOSIConverter, OSIToSigmaConverter

spec = json.loads(Path("data_model.json").read_text())
result = SigmaToOSIConverter().convert(spec)
for issue in result.issues:
    print(f"[warning] {issue.issue_type.value}: {issue.element_name}")
Path("semantic_model.yaml").write_text(result.output.to_osi_yaml())

# Ossie -> Sigma
from ossie import OSIDocument
import yaml

document = OSIDocument.model_validate(yaml.safe_load(Path("semantic_model.yaml").read_text()))
result = OSIToSigmaConverter().convert(document)
Path("data_model.json").write_text(json.dumps(result.output, indent=2))
```

## Mapping overview

| Sigma concept | Ossie concept | Notes |
|---|---|---|
| Data model (`name`, `description`) | `OSISemanticModel` | `dataModelId`, `folderId`, `documentVersion` preserved in `custom_extensions` |
| Page | *(none)* | Ossie has no page/folder-of-elements concept; page membership is preserved per-dataset in `custom_extensions` so it can be reconstructed on export |
| Element (`kind: table`) | `OSIDataset` | `source` = warehouse path joined with `.`; `connectionId` preserved in `custom_extensions` |
| Element (`kind: control`) | *not modeled* | See [Limitations](#limitations) — the entire native control element is preserved verbatim in a model-level `custom_extensions` entry so `osi-to-sigma` can restore it unchanged |
| Column (`formula`) | `OSIField.expression` | See [Expression translation](#expression-translation) |
| Element `metrics[]` | `OSIMetric` | Promoted to model level (Ossie metrics are not dataset-scoped); the formula is re-qualified with the owning dataset name |
| `relationships[]` (join keys) | `OSIRelationship` | See [Relationship resolution](#relationship-resolution) |
| Column/element/relationship native `id` | *(preserved, not surfaced)* | Stashed in `custom_extensions` (`vendor_name: SIGMA`) so re-export can reuse Sigma's own stable ids rather than minting new ones — see [Stable ids](#stable-ids) |
| Unmapped/unknown column format | `datatype: Opaque` | Only used when Sigma's column format has no portable equivalent; the original Sigma format is preserved in `custom_extensions` |

### Expression translation

Sigma column and metric formulas (e.g. `Sum([Orders/Amount])`, `If([Status] = "closed", 1, 0)`)
are parsed by a small recursive-descent parser (`ossie_sigma.sigma_formula`) into an AST, which is
then rendered to ANSI SQL wherever a faithful translation exists (see the module docstring for the
full function/operator coverage table). This is deliberately conservative: a formula that uses a
Sigma function or operator with no portable SQL meaning (e.g. table calculations like `RunningSum`,
which depend on UI-configured partition/order context that is not passed as a formula argument) is
**not** translated.

Every `OSIExpression` produced by `sigma-to-osi` always carries **both**:

1. A `SIGMA`-dialect entry with the original Sigma formula text, verbatim — this is what guarantees
   lossless round-tripping regardless of how much the ANSI SQL translator understands.
2. An `ANSI_SQL`-dialect entry, present only when the formula translated successfully.

`osi-to-sigma` prefers the `SIGMA` dialect entry when present (perfect fidelity for anything that
came from Sigma); for expressions authored by another tool (no `SIGMA` dialect entry), it falls
back to translating the `ANSI_SQL` entry back into Sigma formula syntax, using the same function
table in reverse. If neither direction is possible, the expression is preserved as an opaque
Sigma formula-language string comment plus the raw SQL, and the field is flagged in
`ConverterResult.issues` (`ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE`) rather than silently
producing an invalid Sigma formula.

### Relationship resolution

Sigma relationships (`element.relationships[]`) join two *elements*, not two *Ossie datasets*
directly, and their `keys[].sourceColumnId`/`targetColumnId` address columns by Sigma's internal
column id — which is **not** the same id space as the modeled column's own `id` when the key
references a column that isn't explicitly redefined by the element (Sigma addresses those via an
`inode-<file>/<PHYSICAL_COLUMN_NAME>` reference straight to the underlying warehouse table/column,
bypassing the element's own column list entirely). `sigma_to_osi.py` resolves both addressing
schemes to a modeled column name using the element's own column formulas; when resolution succeeds,
`OSIRelationship.from_columns`/`to_columns` reference the Ossie field name. When it cannot be
resolved (the physical column has no corresponding modeled column, e.g. it was never referenced
anywhere in the element as a column), the physical column name is used verbatim and a converter
issue is recorded. **The raw, unresolved `sourceColumnId`/`targetColumnId` values are always
preserved in the relationship's `custom_extensions`,** so `osi-to-sigma` reconstructs the exact
original join regardless of whether name resolution succeeded — see [Limitations](#limitations).

### Stable ids

Sigma column, element, and relationship ids are load-bearing: other parts of a Sigma workbook
(controls, other data models' relationships, materializations) reference them, so an export that
mints new ids for unchanged objects would silently break those references. `sigma_to_osi.py`
therefore never invents an id for anything that already has one — it always preserves the native
Sigma id in that object's `custom_extensions` and `osi-to-sigma` reuses it verbatim. Ids are only
synthesized (as a deterministic `uuid5` of a fixed namespace plus the object's dataset/field path)
for objects that originate purely in Ossie and have never been round-tripped through Sigma before.

## Limitations

See [`LIMITATIONS.md`](LIMITATIONS.md) for a full accounting of what this converter does not (yet)
handle faithfully, why, and what the general-purpose OSI-native alternative would be instead of a
Sigma-specific workaround.

## Development

```bash
cd converters/sigma
uv sync
uv run pytest
```
