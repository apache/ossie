# apache-ossie-sigma

Converts between [Sigma Computing](https://www.sigmacomputing.com/) Data Models (the
"code representation" spec returned by `GET /v2/dataModels/{id}/spec`, and accepted by
`POST`/`PUT` on the same resource) and the [Apache Ossie](https://github.com/apache/ossie)
format.

Both conversion directions are supported:

- `sigma-to-ossie` — Sigma data model spec JSON → Ossie YAML
- `ossie-to-sigma` — Ossie YAML → Sigma data model spec JSON

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
ossie-sigma sigma-to-ossie -i data_model.json -o semantic_model.yaml
```

### Apache Ossie → Sigma

```bash
ossie-sigma ossie-to-sigma -i semantic_model.yaml -o data_model.json
```

The output is a Sigma data model spec JSON document suitable for
`sigcli data-models spec create`/`update`.

### Help

```bash
ossie-sigma --help
ossie-sigma sigma-to-ossie --help
ossie-sigma ossie-to-sigma --help
```

## Python API

```python
import json
from pathlib import Path

from ossie_sigma import SigmaToOssieConverter, OssieToSigmaConverter

spec = json.loads(Path("data_model.json").read_text())
result = SigmaToOssieConverter().convert(spec)
for issue in result.issues:
    print(f"[warning] {issue.issue_type.value}: {issue.element_name}")
Path("semantic_model.yaml").write_text(result.output.to_osi_yaml())

# Ossie -> Sigma
from ossie import OssieDocument
import yaml

document = OssieDocument.model_validate(yaml.safe_load(Path("semantic_model.yaml").read_text()))
result = OssieToSigmaConverter().convert(document)
Path("data_model.json").write_text(json.dumps(result.output, indent=2))
```

## Mapping overview

| Sigma concept | Ossie concept | Notes |
|---|---|---|
| Data model (`name`, `description`) | `OssieSemanticModel` | `dataModelId`, `folderId`, `documentVersion`, `schemaVersion`, `createdAt`, `createdBy`, `updatedAt`, `updatedBy`, `ownerId`, `url` preserved in `custom_extensions` and written back on export |
| Page | *(none)* | Ossie has no page/folder-of-elements concept, but `pages` is a required part of the spec, so page membership is preserved per-dataset in `custom_extensions` and rebuilt on export |
| Element (`kind: table`) | `OssieDataset` | `source` = warehouse path joined with `.` for `warehouse-table`; the other five source kinds get a marker plus the native `source` block in `custom_extensions` |
| Element (any other `kind`) | *not modeled* | Preserved verbatim in a model-level `custom_extensions` entry so `ossie-to-sigma` restores it unchanged |
| Column (`formula`) | `OssieField.expression` | See [Expression translation](#expression-translation) |
| Element `uniqueKeys` | `OssieDataset.primary_key` | Column ids resolved to field names; the raw `uniqueKeys` list (including any entry that failed to resolve) is also preserved in `custom_extensions` for exact round-trip reconstruction |
| Element `metrics[]` | `OssieMetric` | Promoted to model level (Ossie metrics are not dataset-scoped); the formula is re-qualified with the owning dataset name |
| `relationships[]` (join keys) | `OssieRelationship` | See [Relationship resolution](#relationship-resolution) |
| Column/element/relationship native `id` | *(preserved, not surfaced)* | Stashed in `custom_extensions` (`vendor_name: SIGMA`) so re-export can reuse Sigma's own stable ids rather than minting new ones — see [Stable ids](#stable-ids) |
| Column `format` | `datatype` (coarse) + `custom_extensions` | Sigma has no column datatype, only a display format with two kinds (`number`, `date`); anything else becomes `Opaque`. The native format object is always preserved |
| `filters`, `folders`, `order`, `sort`, `summary`, `groupings`, `columnSecurities`, `visibleAsSource`, `hidden`, metric `timeline`/`isHighlighted`/`format`, `relationshipType` | *not modeled* | Presentation/governance state with no Ossie equivalent, preserved verbatim under a `native` key in `custom_extensions`. Captured by subtraction, so fields added by a future `schemaVersion` round-trip too |

### Expression translation

Sigma's formula language is not SQL, so `ossie_sigma.sigma_formula` tokenizes and parses formulas
like `Sum([Orders/Amount])` or `If([Status] = "closed", 1, 0)` with a recursive-descent parser.
From there it does what the SQL-native converters in this repo do: it builds a **sqlglot expression
tree** and lets sqlglot's generator emit the SQL, so identifier quoting, string escaping, and
operator precedence are the library's job, and targeting a warehouse dialect instead of ANSI is a
`dialect=` argument rather than a second renderer. The reverse direction walks a sqlglot tree back
into formula text, so both directions share one intermediate representation.

Translation is deliberately conservative: a formula using a construct with no portable SQL meaning
(e.g. table calculations like `RunningSum`, whose partition/order context comes from UI
configuration rather than from a formula argument) is **not** translated.

Every `OssieExpression` produced by `sigma-to-ossie` always carries **both**:

1. A `SIGMA`-dialect entry with the original Sigma formula text, verbatim — this is what guarantees
   lossless round-tripping regardless of how much the ANSI SQL translator understands.
2. An `ANSI_SQL`-dialect entry, present only when the formula translated successfully.

`ossie-to-sigma` prefers the `SIGMA` dialect entry when present (perfect fidelity for anything that
came from Sigma); for expressions authored by another tool it falls back to translating the
`ANSI_SQL` entry back into Sigma formula syntax. If neither is possible, the column or metric is
**omitted** and flagged in `ConverterResult.issues`
(`ConverterIssueType.EXPRESSION_NOT_TRANSLATABLE`). `formula` is a required property and the data
model API validates the whole document before applying any of it, so emitting a placeholder would
fail the entire upload rather than degrade one field.

### Relationship resolution

Sigma relationships (`element.relationships[]`) join two *elements*, not two *Ossie datasets*
directly, and their `keys[].sourceColumnId`/`targetColumnId` address columns by Sigma's internal
column id — which is **not** the same id space as the modeled column's own `id` when the key
references a column that isn't explicitly redefined by the element (Sigma addresses those via an
`inode-<file>/<PHYSICAL_COLUMN_NAME>` reference straight to the underlying warehouse table/column,
bypassing the element's own column list entirely). `sigma_to_ossie.py` resolves both addressing
schemes to a modeled column name using the element's own column formulas; when resolution succeeds,
`OssieRelationship.from_columns`/`to_columns` reference the Ossie field name. When it cannot be
resolved (the physical column has no corresponding modeled column, e.g. it was never referenced
anywhere in the element as a column), the physical column name is used verbatim and a converter
issue is recorded. **The raw, unresolved `sourceColumnId`/`targetColumnId` values are always
preserved in the relationship's `custom_extensions`,** so `ossie-to-sigma` reconstructs the exact
original join regardless of whether name resolution succeeded — see [Limitations](#limitations).

### Stable ids

Sigma column, element, and relationship ids are load-bearing: other parts of a Sigma workbook
(controls, other data models' relationships, materializations) reference them, so an export that
mints new ids for unchanged objects would silently break those references. `sigma_to_ossie.py`
therefore never invents an id for anything that already has one — it always preserves the native
Sigma id in that object's `custom_extensions` and `ossie-to-sigma` reuses it verbatim. Ids are only
synthesized (as a deterministic `uuid5` of a fixed namespace plus the object's dataset/field path)
for objects that originate purely in Ossie and have never been round-tripped through Sigma before.

## Limitations

See [`LIMITATIONS.md`](LIMITATIONS.md) for a full accounting of what this converter does not (yet)
handle faithfully and why.

## Development

```bash
cd converters/sigma
uv sync
uv run pytest
```
