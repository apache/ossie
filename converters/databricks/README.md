# OSI <-> Databricks Unity Catalog Metric View converter

Bidirectional, offline conversion between an OSI semantic model and a Databricks
[Unity Catalog Metric View](https://docs.databricks.com/aws/en/metric-views/) (YAML
`1.1`). No Databricks connection required.

- **Export** (`osi-databricks export`): OSI -> Metric View (one fact
  `source` with a nested `joins` tree and a flat `dimensions` list).
- **Import** (`osi-databricks import`): Metric View -> OSI. Metric View features OSI has
  no native field for are preserved in `custom_extensions[DATABRICKS]`, so
  `MV -> OSI -> MV` is lossless.

On **export** (OSI -> Metric View), OSI features with no Metric View slot -- relationship
`ai_context`, `dimension.is_time`, non-`DATABRICKS`/`ANSI_SQL` dialects, foreign-vendor
`custom_extensions` -- are **dropped with a warning**. On **import** (Metric View -> OSI),
Metric View only features (filter, window, format, rely, ...) are instead **preserved** in
`custom_extensions[DATABRICKS]`, so `MV -> OSI -> MV` is lossless. Any input that breaks a
[requirement](#requirements) **raises a `ConversionError`** -- the converter never
silently drops a field or produces an invalid result.

## Installation

```bash
pip install osi-databricks        # once published to PyPI
# or, from a checkout of this directory:
pip install -e .
```

The only runtime dependency is `PyYAML`. Python 3.9+.

## Usage

### Command line

```bash
osi-databricks export -i model.yaml -o view.yaml [--source orders]   # OSI -> Metric View
osi-databricks import -i view.yaml  -o model.yaml [--name my_model]   # Metric View -> OSI
```

With no `-o`, output goes to stdout. `--source` (export) picks the fact/grain (default:
the FK-sink dataset; naming a coarser-grain dataset produces `one_to_many` joins);
`--name` (import) sets the OSI model name (default: the source's last identifier).

### Python API

```python
from osi_databricks import convert_osi_to_metric_view, convert_metric_view_to_osi

metric_view_yaml = convert_osi_to_metric_view(osi_yaml_str)               # optionally choose the fact/grain, e.g. (osi_yaml_str, source="orders")
osi_yaml = convert_metric_view_to_osi(metric_view_yaml_str, model_name="sales")
```

## Mapping

Each row maps in both directions; the **Notes** flag where a behavior is specific to
**export** (OSI -> Metric View) or **import** (Metric View -> OSI).

| OSI | Metric View (v1.1) | Notes |
|---|---|---|
| `semantic_model.description` | `comment` | Model-level description only. |
| root dataset | `source` | The fact/grain. |
| other `datasets` | nested `joins[]` | Export: the relationship graph is reassembled into the join tree; a dataset reached by two paths (a diamond) fans out into one aliased join per path. |
| `relationship` `from_columns`/`to_columns` | join `on` (differing names) / `using` (shared names) | Decomposed into columns on import; rebuilt into `on`/`using` on export. |
| `relationship.from`/`to` direction | join `cardinality` | Export: source on the many (`from`) side -> `many_to_one`; on the one (`to`) side -> `one_to_many`. |
| `dataset.primary_key` / `unique_keys` | join `rely.at_most_one_match` | Both directions: export sets `at_most_one_match` when a key covers the join columns; import recovers a `unique_keys` from it. |
| `dataset.fields[]` | `dimensions[]` | Export: fields flatten into one list and a joined column is qualified by its full join path (`customer.c_name`; `customer.region.r_name` when nested). |
| `field.expression.dialects[]` | `expr` | Export: prefer the `DATABRICKS` dialect, else `ANSI_SQL`. |
| `metrics[]` | `measures[]` | Export: fact columns are referenced bare (`SUM(amount)`). |
| `field.label` | `display_name` | |
| `field` / `metric` `description` | `comment` | |
| `ai_context.synonyms` | `synonyms` | |
| `custom_extensions[DATABRICKS]` | `filter`, `window`, `format`, `rely`, `materialization` | Import stashes Metric View only features here; export restores them -- keeping `MV -> OSI -> MV` lossless. |

## Requirements

Conversion raises a `ConversionError` (rather than guessing or emitting something
invalid) when an input breaks one of these:

- the Metric View `version` is not `1.1`;
- a `source` is not a 3-part `catalog.schema.table` name or a `SELECT`/`WITH` subquery;
- the relationship graph is not acyclic and resolvable to a single fact -- a cycle, or
  multiple candidate facts without `--source`, is rejected (a diamond is allowed and
  fanned out);
- a join has no condition (a cross join has no OSI relationship form);
- a join condition is non-equi or otherwise can't be decomposed into equi-join columns
  (OSI relationships are equi-joins, so the join has no OSI representation);
- the input YAML is malformed.

## Development

```bash
pip install -e ".[dev]"
python3 -m pytest tests/
```

Example-based unit tests plus Hypothesis property-based round-trip tests
(`test_roundtrip_properties.py`, which skip if `hypothesis` is not installed).

## Future effort

Both the OSI specification and the Databricks Unity Catalog Metric View YAML are still
evolving. As either side adds or changes fields, this converter will be updated to track
them -- extending the mapping and coverage in both directions to keep the conversion
current and to support as much as each format allows over time.
