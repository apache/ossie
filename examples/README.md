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

# Ossie examples

This directory contains valid Ossie documents, from a minimal model to a complete TPC-DS example.

Semantic models are standalone documents. `version` and the model properties are at the root; there is no `semantic_model` wrapper. See [the core specification](../core-spec/spec.md) and the linked schemas for the normative rules.

## Semantic models

- [`minimal.yaml`](minimal.yaml) and [`minimal.json`](minimal.json) contain the same small semantic model in YAML and JSON. The model has one dataset, one field, one primary key, and one model-scoped metric.
- [`tpcds_semantic_model.yaml`](tpcds_semantic_model.yaml) is a complete semantic model for TPC-DS.
- [`renamed_fields.yaml`](renamed_fields.yaml) shows a field whose semantic name differs from its source column name. Keys, relationships, and metric expressions use the field name.

## Ontology and mappings

- [`flights.yaml`](flights.yaml), [`flights.ontology.yaml`](flights.ontology.yaml), and [`foaf_owl_import.yaml`](foaf_owl_import.yaml) are ontology documents validated against [`ontology/ontology.json`](../ontology/ontology.json).
- [`flights.semantic_model.yaml`](flights.semantic_model.yaml) is the semantic model referenced by the flights mapping.
- [`flights.mapping.yaml`](flights.mapping.yaml) and [`sap_business_partner.mapping.yaml`](sap_business_partner.mapping.yaml) are mapping documents validated against [`ontology/mapping.json`](../ontology/mapping.json).
- [`sap_business_partner.ontology.yaml`](sap_business_partner.ontology.yaml) and [`sap_business_partner.semantic_model.yaml`](sap_business_partner.semantic_model.yaml) are the ontology and semantic model for the SAP example.

## Validation

From the repository root:

```sh
uv run validation/validate.py examples/minimal.yaml
uv run validation/validate.py examples/minimal.json
uv run validation/validate.py examples/flights.yaml --schema ontology/ontology.json
uv run validation/validate.py examples/flights.mapping.yaml --schema ontology/mapping.json
```

The validator exits with status 0 when there are no errors. Warnings are printed but do not affect the exit status. See [`validation/validate.py`](../validation/validate.py) for the available options.
