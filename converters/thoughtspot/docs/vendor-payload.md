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

<!-- GENERATED FILE -- do not edit by hand.
     Produced by `tools/generate_reference_docs.py` from:
     - `src/ossie_thoughtspot/constants.py`
     Regenerate with:
       uv run --python 3.13 python tools/generate_reference_docs.py
     tests/test_reference_docs_current.py fails the suite if this file
     drifts from what the generator currently produces. -->

# The `custom_extensions[THOUGHTSPOT]` Payload

TML carries properties Ossie's core specification has no field for. `TML -> Ossie` stashes each one under a single `custom_extensions` entry attached to the Ossie object it came from; `Ossie -> TML` reads the same entry back. This page is generated from `constants.py`'s own key vocabulary and `STASH_KEY_CLASSIFICATION` — the table `test_stash_key_classification.py` enforces every key read on the `Ossie -> TML` direction must appear in.

## Envelope

Every `custom_extensions` entry this converter writes uses `vendor_name` `THOUGHTSPOT`. `data` is a single JSON-encoded string (never a nested object) whose own top-level `_v` field is the shape version (`1` today) — bumped only when the payload's shape changes, never for a value change; an unrecognised version is a hard failure rather than a silent misread.

## Payload keys

| Key | Scope | Classification | Treatment on the return trip |
|---|---|---|---|
| `tml_name` | Shared | shadows_derivable | Restored only if reconstructing it from the live document still agrees with the stashed value (self-verifying — no separate witness key); disagreement re-derives instead. |
| `db_column_name` | Field | shadows_derivable | Restored only if its witness companion key still matches the live document's current value; a mismatch means the document changed since the stash was written, so the value is re-derived instead. |
| `data_type` | Field | shadows_derivable | Restored only if its witness companion key still matches the live document's current value; a mismatch means the document changed since the stash was written, so the value is re-derived instead. |
| `column_properties` | Field | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `shape` | Metric | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `tml_object` | Dataset | shadows_derivable | Restored only if its witness companion key still matches the live document's current value; a mismatch means the document changed since the stash was written, so the value is re-derived instead. |
| `source_parts` | Dataset | shadows_derivable | Restored only if reconstructing it from the live document still agrees with the stashed value (self-verifying — no separate witness key); disagreement re-derives instead. |
| `connection_name` | Dataset | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `table_name` | Dataset | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `alias` | Dataset | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `table_properties` | Dataset | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `unsurfaced_columns` | Dataset | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. Each list entry is additionally checked against live coverage before being restored: an entry now covered by a live field is dropped rather than duplicated. |
| `sql_output_columns` | Dataset | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `on_expression` | Relationship | shadows_derivable | Restored only if its witness companion key still matches the live document's current value; a mismatch means the document changed since the stash was written, so the value is re-derived instead. |
| `type` | Relationship | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `cardinality` | Relationship | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `referencing_join` | Relationship | shadows_derivable | Restored only if reconstructing it from the live document still agrees with the stashed value (self-verifying — no separate witness key); disagreement re-derives instead. |
| `join_shape` | Relationship | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `unattributed_formulas` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `unrepresentable_joins` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `model_properties` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `parameters` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `filters` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `column_groups` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `lesson_plans` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `action_object_associations` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `constraints` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |
| `model_joins_with` | Model | information_only | Restored as-is whenever present — nothing on the Ossie side could have diverged from it. |

## Witness companion keys

A `SHADOWS_DERIVABLE` key's stashed value is checked for currency before being restored; these are the witness copies that check does it against (see `stash.restore`).

| Witness key | Checks currency for |
|---|---|
| `tml_object_source_witness` | `tml_object` |
| `data_type_ossie_datatype_witness` | `data_type` |
| `db_column_name_display_name_witness` | `db_column_name` |
| `on_expression_equality_witness` | `on_expression` |

## Nested keys under `source_parts`

| Sub-key | Full path |
|---|---|
| `db` | `source_parts.db` |
| `db_table` | `source_parts.db_table` |
| `schema` | `source_parts.schema` |

## `METRIC_STASH_SHAPE` value vocabulary

| Constant | Value |
|---|---|
| `METRIC_SHAPE_COLUMN_AGGREGATION` | `column_aggregation` |
| `METRIC_SHAPE_FORMULA` | `formula` |
| `METRIC_SHAPE_SCALAR_FORMULA_PLUS_AGGREGATION` | `scalar_formula_plus_aggregation` |

## Reclassified on a stash-only carrier

The same key name, reclassified when it is read off a stash-only carrier (an `unrepresentable_joins[]` or `unattributed_formulas[]` entry) that has no independent Relationship/Metric/Field object of its own to diverge from.

| Key | Classification (primary carrier) | Classification (stash-only carrier) |
|---|---|---|
| `on_expression` | shadows_derivable | information_only |
| `type` | information_only | information_only |
| `cardinality` | information_only | information_only |
| `column_properties` | information_only | information_only |

