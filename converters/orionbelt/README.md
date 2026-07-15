# apache-ossie-orionbelt

Bidirectional converter between **OBML** (OrionBelt Markup Language) semantic
models and **OSI** ([Open Semantic Interchange](https://open-semantic-interchange.org/)),
the open standard for portable semantic models (metrics, dimensions,
relationships).

This package is licensed under **Apache-2.0** and may be used freely. It is the
OrionBelt converter in the OSI converter ecosystem. The canonical source is
developed in the
[orionbelt-semantic-layer](https://github.com/ralfbecher/orionbelt-semantic-layer)
repository (under `packages/osi-orionbelt`) and published to PyPI from there;
file issues and contributions upstream.

## Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (recommended) or pip

## Install

```bash
pip install apache-ossie-orionbelt
```

Optional deep OBML semantic validation (cycles, duplicate names, invalid refs)
via the full OrionBelt engine:

```bash
pip install "apache-ossie-orionbelt[obml-validation]"
```

Without that extra, OBML validation runs JSON-schema checks only and emits a
warning for the deeper semantic pass.

## CLI

A single `ossie-orionbelt` command with two subcommands (mirroring `ossie-dbt`):

| Subcommand | Direction | In | Out |
|---------|-----------|----|----|
| `obml-to-osi` | OBML -> OSI core-spec | OBML YAML | OSI YAML |
| `obml-to-osi --ontology` | OBML -> OSI ontology | OBML YAML | OSI ontology YAML |
| `osi-to-obml` | OSI core-spec -> OBML | OSI YAML | OBML YAML |

```bash
ossie-orionbelt obml-to-osi -i model.obml.yaml -o model.osi.yaml
ossie-orionbelt obml-to-osi --ontology -i model.obml.yaml -o model.ontology.yaml
ossie-orionbelt osi-to-obml -i model.osi.yaml -o model.obml.yaml
```

`-i/--input` and `-o/--output` are required. Each subcommand prints conversion
warnings and a validation summary to stderr, and exits non-zero when the
produced document fails schema validation (unless `--no-validate`). Run
`ossie-orionbelt --help` or `ossie-orionbelt obml-to-osi --help` for the full
option list.

## Python API

```python
import yaml
from ossie_orionbelt import OBMLtoOSI, OSItoOBML, validate_osi

obml = yaml.safe_load(open("model.obml.yaml"))
osi = OBMLtoOSI(obml, "sales", "Sales model").convert()
result = validate_osi(osi)
assert result.valid

obml_again = OSItoOBML(osi).convert()
```

## Vendor extensions

OSI `custom_extensions` carry vendor-tagged payloads. This converter:

- emits OrionBelt/OBML-proprietary data under the **`ORIONBELT`** vendor on OBML
  to OSI (OBML-only filters, settings, owner, refresh, type info, etc.);
- stashes OSI-native fields that OBML can't represent (unique keys, field
  labels, leftover `ai_context`) under the **`OSI`** vendor when going OSI to
  OBML, restoring them to first-class OSI fields on the way back;
- **preserves third-party vendor extensions verbatim** (e.g. `SNOWFLAKE`,
  `DBT`, `SALESFORCE`, `GOODDATA`) at the model, dataset, field, and
  measure/metric levels, so a full OSI to OBML to OSI roundtrip keeps the
  original vendor and data. OSI has no separate dimension entity, so an OBML
  dimension's foreign extensions surface on its OSI field.

Legacy `COMMON` / `OBSL` tags from earlier converter versions are still accepted
on read.

## Limitations / unsupported constructs

Some OBML constructs have no native OSI equivalent and are carried in vendor
`custom_extensions` (`obml_*` payloads) so they round-trip without loss back to
OBML, but are not interpreted by other OSI consumers:

- **Many-to-many joins** - represented in OBML join cardinality; flagged on
  export.
- **Named secondary join paths** - OBML's multiple join paths between the same
  pair of objects are an OBML-specific topology feature.
- **Measures / metrics and column-level value concepts in the ontology layer** -
  not represented in the OSI ontology export.
- **OSI metrics with no OBML representation** - a metric whose only expression is
  in a non-SQL dialect (`MDX`, `TABLEAU`, `MAQL`), or whose SQL expression cannot
  be decomposed into OBML measures/metrics, is **not** dropped: the original OSI
  metric is preserved verbatim in a model-level `OSI`-vendor `custom_extension`
  (`obml_unconverted_metrics`) and re-emitted on OBML to OSI, so the OSI to OBML
  to OSI roundtrip stays lossless. A `LOSSY:` warning is raised for each such
  metric because it is **not queryable through OBML**. SQL expressions in the
  `ANSI_SQL`, `SNOWFLAKE`, and `DATABRICKS` dialects are all read on import.

OSI v0.1.x inputs are accepted on read via a legacy normalization shim; output
targets OSI **v0.2.0.dev0**.

See [`osi_obml_mapping_analysis.md`](./osi_obml_mapping_analysis.md) for the
full OBML <-> OSI core-spec mapping and
[`osi_obml_ontology_mapping_analysis.md`](./osi_obml_ontology_mapping_analysis.md)
for the ontology-layer mapping and its documented gaps.

## Development

```bash
uv sync          # install
uv run pytest    # run the test suite (includes a TPC-DS baseline)
uv run ruff check && uv run mypy src/ossie_orionbelt
```
