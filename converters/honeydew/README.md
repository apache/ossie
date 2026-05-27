# OSI ↔ Honeydew Converter

Bidirectional converter between [OSI](../../core-spec/spec.md) semantic models and [Honeydew](https://honeydew.ai/docs) workspace YAML.

## Overview

| Direction | Input | Output |
|-----------|-------|--------|
| `osi-to-honeydew` | Single OSI YAML file | Honeydew workspace directory |
| `honeydew-to-osi` | Honeydew workspace directory | Single OSI YAML file |

### OSI → Honeydew mapping

| OSI concept | Honeydew concept |
|-------------|-----------------|
| `semantic_model.name` | `workspace.yml name` |
| `dataset` | Entity + dataset files under `schema/<entity>/` |
| `dataset.source` | `dataset.sql` |
| `dataset.primary_key` | `entity.keys` |
| Simple column field | `dataset.attributes` entry |
| Computed field expression | `calculated_attribute` YAML |
| `relationship` (from → to) | `entity.relations` on the "from" entity (`rel_type: many-to-one`) |
| `metric` | `metric` YAML (assigned to entity by expression parse) |

### Honeydew → OSI mapping

| Honeydew concept | OSI concept |
|-----------------|-------------|
| `workspace.name` | `semantic_model.name` |
| Entity + primary dataset | `dataset` |
| `entity.keys` | `dataset.primary_key` |
| `dataset.attributes` (columns) | `fields` with `ANSI_SQL` expression = column name |
| `calculated_attribute` SQL | `fields` with `ANSI_SQL` expression + `HONEYDEW` custom extension |
| `entity.relations` (`many-to-one`) | `relationship` with `from` = this entity |
| `entity.relations` (`one-to-many`) | `relationship` with `from` = target entity |
| `metric.sql` | `metric` expression in `ANSI_SQL` dialect |

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
# OSI YAML → Honeydew workspace directory
python src/honeydew_osi_converter.py osi-to-honeydew -i input.yaml -o output_dir/

# Honeydew workspace directory → OSI YAML
python src/honeydew_osi_converter.py honeydew-to-osi -i workspace_dir/ -o output.yaml
```

## Tests

```bash
python -m pytest tests/
```

## Limitations

- **One dataset per entity**: The converter maps each OSI dataset to a single Honeydew entity with one source dataset. Multiple datasets per entity are not generated.
- **Datatype inference**: OSI fields have no explicit datatype; the converter infers Honeydew datatypes from the `dimension.is_time` flag (`timestamp`) and the presence/absence of the `dimension` key (`string` vs `number`).
- **Honeydew SQL expressions**: Calculated attributes and metrics use Honeydew's `entity.attribute` reference syntax. These are exported as `ANSI_SQL` dialect expressions in OSI; they remain valid for round-tripping but may not run on other databases without adaptation.
- **Perspectives and domains**: Not converted (no OSI equivalent).
- **Connection expressions** (`connection_expr`): Preserved in `HONEYDEW` custom extensions on the OSI relationship and restored on the return trip.
- **`ai_context`**: OSI `ai_context` fields (synonyms, instructions) are stored in Honeydew `metadata` for round-trip recovery. Instructions are also merged into `description` for human readability.
