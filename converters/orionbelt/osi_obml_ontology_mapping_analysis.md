# OBML → OSI Ontology Mapping Analysis

This pins the rules used by `OBMLtoOSIOntology` to derive an **OSI ontology
document** (OSI ontology format, version `0.2.0.dev0`) from an OBML semantic
model.

The OSI ontology is a **separate document** from the OSI core-spec semantic
model (different `$id`, different required root). It is produced alongside the
core export, never merged into it — OSI's own `validation/validate.py` validates
one document against one schema and reads only the first YAML document, so a
combined or multi-doc file is not portable. See the `include_ontology` flag on
the export endpoints.

## Document shape produced

```yaml
version: 0.2.0.dev0
name: <model name>                 # required
description: <model description>   # optional
ai_context: { instructions: ... }  # optional, from ai_instructions / model
ontology:                          # required, minItems 1
  - concept:
      name: <Entity>               # = OBML dataObject display name
      type: EntityType
      description: ...
    relationships:                 # outgoing joins keyed by this entity
      - name: <A>_to_<B>
        roles: [{ concept: <B> }]  # declaring concept (A) is the implicit first role
        multiplicity: ManyToOne | OneToOne
        verbalizes: ["{<A>} relates to {<B>}"]
ontology_mappings:
  - name: <model name>_map
    semantic_model: { ...full OSI core-spec model... }   # reused from OBMLtoOSI
    concept_mappings:
      - concept: <Entity>
        object_mappings: [{ expression: "<table>.<key_col>" }]
        link_mappings:
          - relationship: <A>_to_<B>
            object_mapping: { concept: <B>, expression: "<table_A>.<fk_col>" }
```

## Mapping rules

| OBML construct | OSI ontology target | Notes |
|----------------|---------------------|-------|
| `dataObject` | `EntityType` `Concept` (one per object) | name = display name (matches OSI dataset name for ref consistency) |
| `dataObject.description` / `.comment` | `concept.description` | first non-empty wins |
| `join` (A → B) | `Relationship` under A's component | declaring concept A is the implicit first role; B is an explicit `role` |
| `join.joinType` | `Relationship.multiplicity` | `many-to-one`→`ManyToOne`, `one-to-one`→`OneToOne` |
| `column.primaryKey` | entity `object_mappings[].expression` | `<table>.<pk_code>`; identifies the entity |
| `join.columnsFrom` (FK) | `link_mappings[].object_mapping.expression` | `<table_A>.<fk_code>`; binds the relationship to its far role |
| whole core model | `ontology_mappings[].semantic_model` | embedded verbatim from `OBMLtoOSI.convert()` |

`<table>` is the final identifier of the dataset `source` (e.g. `db.schema.t` → `t`),
falling back to the dataset name when `source` has no dotted physical table.

## Gaps and warnings (emitted to `warnings`)

| OBML construct | Handling | Reason |
|----------------|----------|--------|
| `joinType: many-to-many` | relationship **skipped** + warning | OSI `Multiplicity` enum is only `ManyToOne`/`OneToOne` |
| missing/unknown `joinType` | defaults to `ManyToOne` + warning | matches core-converter default |
| secondary / `pathName` joins | emitted as ordinary relationships + warning | OSI ontology has no named-alternate-path concept |
| composite primary/foreign keys | first column used + warning | `object_mapping.expression` is a single scalar SQL expression |
| measures / metrics | **not** in the ontology layer | live only in the embedded core `semantic_model`; ontology models entities/relationships |
| columns (non-key) as value concepts | not modeled (entities only) | keeps v1 valid and focused; ORM-style `ValueType` modeling deferred |
| `verbalizes` / `derived_by` / `requires` / `identify_by` | `verbalizes` emitted as a generated stub; others omitted | OBML has no native source for fact-based verbalization or derivation |

## Validation

`validate_osi_ontology()` runs semantic checks only (ossie ships no OSI ontology
schema, and the converter emits ontology documents but never consumes them):
unique concept names; relationship `roles` reference defined concepts;
`concept_mappings` reference defined concepts.

## Stability note

OSI ontology is `0.2.0.dev0` (pre-1.0). This exporter is the supported,
schema-validated direction. An ontology **importer** (OSI ontology → OBML) is
intentionally deferred until OSI drops the `dev` pre-release suffix, because the
gaps above make the reverse direction lossy. Import the OSI **core spec**
instead (already supported).
