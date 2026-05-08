# OSI <-> Databricks Converter

Bidirectional converter between OSI YAML semantic models and [Databricks Unity
Catalog Metric View](https://docs.databricks.com/aws/en/metric-views/) YAML.
Pure offline conversion — no Databricks workspace connection required.

## Setup

```bash
pip3 install -r requirements.txt
```

## Usage

```bash
# OSI -> Databricks UC Metric View
python3 src/osi_to_databricks_metric_view.py -i input.yaml -o output.yaml

# Databricks UC Metric View -> OSI
python3 src/databricks_metric_view_to_osi.py -i metric_view.yaml -o osi.yaml
```

## Tests

```bash
python3 -m pytest tests/ -q
python3 -m pyflakes src/ tests/
python3 -m ruff check src/ tests/
```

## Mapping summary

| OSI | Databricks UC Metric View |
|---|---|
| `version: 0.1.1` | `version: 1.1` (current UC metric view spec) |
| `semantic_model[].description` + `ai_context` | top-level `comment` |
| `dataset.source` (`db.schema.table` or subquery) | `source` (3-part name or inline SQL) |
| primary fact dataset | `source` of the metric view |
| other datasets reachable via `relationships` | `joins[]` entries (`source` + `sql_on`) |
| `dataset.fields[]` | `dimensions[]` (qualified by dataset name) |
| `metrics[]` | `measures[]` |
| `expression.dialects[DATABRICKS]` (else `ANSI_SQL`) | `expr` |
| `relationships[].from_columns`/`to_columns` | `sql_on:` boolean built from positional pairs |
| `custom_extensions[DATABRICKS]` | applied to top-level `filter`, `comment`, etc. |

## Picking the primary dataset

A single OSI semantic model has N datasets and M relationships. A Databricks
metric view has one `source` plus joined tables. The converter picks the
"primary" dataset using this priority:

1. `custom_extensions[vendor_name=DATABRICKS]` with `{"primary_dataset": "..."}`.
2. The dataset most often on the `from` side of relationships (typically the
   fact table).
3. The first dataset declared.

Datasets unreachable from the primary via the relationship graph are emitted
as a warning and excluded from the metric view; the OSI model should be split
or the user should provide an explicit `primary_dataset` hint.

## Expression qualification

Bare single-identifier expressions (e.g. `expression: customer_id`) are
auto-qualified with their dataset name so they resolve unambiguously after
joins (`customer.customer_id`).

Multi-token expressions (operators, function calls, multi-column
references) are emitted **verbatim** because string-prepending only
qualifies the first identifier, leaving subsequent column references
ambiguous after joins. For computed fields on a non-primary dataset,
provide a `DATABRICKS` dialect entry that's already table-qualified:

```yaml
fields:
  - name: customer_full_name
    expression:
      dialects:
        - dialect: ANSI_SQL
          expression: c_first_name || ' ' || c_last_name
        - dialect: DATABRICKS
          expression: customer.c_first_name || ' ' || customer.c_last_name
```

The converter prefers the `DATABRICKS` dialect when present and emits a
warning when a non-primary multi-token expression has only `ANSI_SQL`.

## Limitations

- Round-trip from a UC metric view that references columns through more than
  one hop of joins may flatten dataset attribution to the immediate join
  source. The converter parses simple `<a>.<col> = <b>.<col>` `sql_on`
  clauses; complex boolean expressions are preserved verbatim in
  `custom_extensions[DATABRICKS].raw_joins` and re-emitted on the inverse
  conversion.
- OSI `dimension.is_time` does not have a dedicated UC counterpart and is
  preserved through `custom_extensions[DATABRICKS]` for round-trip fidelity.
- AI context fields (`ai_context.instructions`, `synonyms`) on relationships
  are dropped on export with a warning.
