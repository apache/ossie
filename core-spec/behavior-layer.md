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

# Apache Ossie Behavior Layer (Actions, Rules, Effects)

**Applies to:** Apache Ossie Core `0.2.0.dev0` (draft)

Behavior supplies vendor-neutral metadata for action planning and attribution.
It does not execute actions or enforce business rules.

## Canonical schema and placement

[behavior-layer.schema.json](behavior-layer.schema.json) is the sole canonical
schema for Behavior, Action, Effect, and Rule. [ossie-schema.json](ossie-schema.json)
references it with `$ref`; it does not maintain inline copies of those definitions.
The YAML and Markdown specifications describe this same contract.

New model documents SHOULD use the optional root-level `behavior` property.
Earlier documents may carry serialized behavior JSON in
`custom_extensions[].data`. Core validation checks that `data` is a string;
tools supporting this legacy placement must parse the payload and validate it
against the same canonical behavior schema. This PR does not add automatic
parsing of embedded JSON. If both placements exist, consumers SHOULD define
precedence; root-level `behavior` is recommended.

## Behavior

| Field | Type | Required | Notes |
|---|---|---:|---|
| `namespace` | non-empty string | Yes | Grouping namespace, e.g. `SAP_P2P` |
| `behavior_layer_version` | non-empty string | Yes | Behavior schema version |
| `actions` | array of actions | No* | Preferred action list |
| `action_types` | array of actions | No* | Legacy alias; same item constraints |
| `rules` | array of rules | Yes | May be empty |
| `metadata` | object | No | Governance metadata |

At least one of `actions` and `action_types` must be present. Empty lists and
both aliases are accepted, preserving the original contract. The schema does
not merge or normalize the aliases. Behavior objects and their actions, effects,
and rules allow additional properties for extensions.

## Actions

Each action requires non-empty `id` and `title` strings. Optional `kind` is
`command` or `query`; optional `idempotency` is `idempotent`, `non_idempotent`,
or `unknown`. `operation`, `aggregate`, `entity_name`, `description`, and
`version` are strings. `examples`, `tags`, and `synonyms` are arrays of strings;
`deprecated` is a boolean. `applies_to`, `io_schema`, and `tool_hint` are open
objects. Optional `effects` is an array of effects.

## Effects

Each effect requires:

- `entity`: `dataset`, `field`, `metric`, or `relationship`.
- `mode`: `read`, `write`, or `derive`.

Optional `impact_type` is one of `state_transition`, `master_data_mutation`,
`transactional_write`, `derived_metric_change`, or `other`.
Optional `confidence` is `guaranteed`, `likely`, or `unknown`.

`selectors` and `transition` are optional open objects. When supplied,
`selectors.dataset` is a string and `selectors.field_names` is an array of
strings; `transition.from` and `transition.to` are strings. These members are
not required. `set_value` and `notes` are strings; `tags` is an array of strings.

## Rules

Each rule requires non-empty `id`, `title`, and `message` strings, `severity`
(`error`, `warn`, or `info`), and open `when` and `constraint` objects.
Optional `if` is an open object; `description` and `remediation` are strings,
`references` is an array of open objects, and `tags` is an array of strings.

## Examples and validation

See the complete [SAP P2P example](../examples/p2p_behavior_effects_minimal.yaml).
Validate it offline with the existing local schema resolver:

```sh
python3 validation/validate.py examples/p2p_behavior_effects_minimal.yaml
```

Legacy placement (dataset fragment):

```yaml
datasets:
  - name: suppliers
    source: sap.p2p.suppliers
    custom_extensions:
      - vendor_name: COMMON
        data: |
          {
            "namespace": "SAP_P2P",
            "behavior_layer_version": "0.1",
            "action_types": [],
            "rules": []
          }
```
