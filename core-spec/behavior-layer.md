# OSI Extension: Behavior Layer (Actions, Rules, Effects)

**Applies to:** OSI Core v0.1.2  
**Goal:** Provide a vendor-agnostic behavior layer for deterministic action planning and attribution (what changed/why), without breaking OSI Core compatibility.

## 1) Placement & Compatibility

This extension supports two equivalent placements:

1. **Preferred (first-class):** `semantic_model[].behavior`  
2. **Legacy (embedded):** behavior JSON embedded in `dataset.custom_extensions[].data` (as a JSON string)

Backwards compatibility guidance:
- New models SHOULD use `semantic_model.behavior`.
- Tools MAY continue to support legacy embedded behavior for older models.
- If both are present, tools SHOULD define precedence (recommended: first-class `semantic_model.behavior` wins).

## 2) Top-level object: Behavior

Minimal structure:

| Field | Type | Required | Notes |
|---|---|---:|---|
| `namespace` | string | Yes | Grouping namespace (e.g. `SAP_P2P`) |
| `behavior_layer_version` | string | Yes | Schema evolution version |
| `actions` | array | No* | Preferred list of actions (`actions` or `action_types` must exist) |
| `action_types` | array | No* | Legacy alias of `actions` |
| `rules` | array | Yes | Constraints/guards for planning and governance |
| `metadata` | object | No | Optional governance metadata |

## 3) Actions

Actions describe what a tool/agent can do (API calls, workflows, scripts). OSI does not assume SQL.

Recommended action fields:
- `id` (stable identifier, e.g. `suppliers/block`)
- `title`, `description`
- `kind`: `command` or `query`
- `operation`: free-form operation name (e.g. `block`, `unblock`, `analyze`)
- `entity_name`: dataset name or conceptual entity
- `io_schema`: optional input/output JSON schema
- `effects`: optional machine-readable impact annotations

## 4) Effects (impact annotations)

Effects encode how an action reads/writes/derives datasets/fields.

Minimal effect fields:
- `entity`: dataset / field / metric / relationship
- `mode`: read / write / derive
- `selectors`: `{ dataset, field_names[] }` (or other selectors)
- optional: `impact_type`, `transition`, `set_value`, `confidence`, `notes`

Typical uses:
- Deterministic planning: validate state transitions and required prerequisites
- Attribution: explain “what changed/why” for a field or dataset

## 5) Legacy embedded example (dataset.custom_extensions)

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

