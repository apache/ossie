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

# Vendor Extensions Catalog

Custom extensions attach platform-specific metadata to a model (see
[`core-spec/spec.md`](../core-spec/spec.md#custom-extensions)):

```yaml
custom_extensions:
  - vendor_name: string  # Free-form string identifying the vendor
    data: string         # JSON string containing vendor-specific data
```

`vendor_name` is a free-form string and `data` is an opaque string that Ossie
carries but does not interpret; for the precise extensions a given vendor emits
and consumes, see the
[apache/ossie-converters](https://github.com/apache/ossie-converters) repository.
This is a registry of `vendor_name` tokens the community uses (see
[`README.md`](README.md) for how these lists are used).

Each token identifies one vendor or tool. The registry has no shared,
cross-vendor token. Metadata that more than one vendor needs belongs in the
core specification, where it has defined semantics, and not in an opaque
extension payload.

## Validating a vendor's payload

`validation/validate.py` leaves `data` opaque unless a vendor is named. With
`--vendor THOUGHTSPOT` it decodes the `data` of every `THOUGHTSPOT` entry in a
semantic model as JSON, reports the ones that are not JSON, and hands each
decoded payload to the validator the vendor's own package registers under the
entry point group `ossie.vendor_validators`, with the token from the table below
as the entry point name. The validator is a callable `check(payload, site)`:
`payload` is the decoded JSON value, and `site` has `kind` (one of `model`,
`dataset`, `field`, `metric`, `relationship`), `name`, and `dataset` (the owning
dataset of a field or a dataset-scoped metric, otherwise `None`). It returns the
messages to report, a single string for one message, or `None` for none; the
script prefixes each with the vendor and the site (`[Vendor THOUGHTSPOT] Field
'orders.amount' in model 'sales': ...`) and counts it as an error. A validator
that raises, or exits, is reported the same way, with the traceback on stderr.
Vendor checks run on a document that passed the schema and the other checks,
and entries of other vendors are not read. The package has to be installed where
the script runs; for a uv run that is:

```sh
uv run --with <package> validation/validate.py --vendor THOUGHTSPOT model.yaml
```

## Known vendors

| Vendor | Description |
|--------|-------------|
| `SNOWFLAKE` | Snowflake-specific attributes |
| `SALESFORCE` | Salesforce/Tableau-specific attributes |
| `DBT` | dbt-specific attributes |
| `DATABRICKS` | Databricks-specific attributes |
| `GOODDATA` | GoodData-specific attributes |
| `SEMANTIDO` | Semantido-specific attributes |
| `HONEYDEW` | Honeydew-specific attributes |
| `WISDOM` | WisdomAI-specific attributes |
| `POWER_BI` | Microsoft Power BI-specific attributes |
| `SIGMA` | Sigma Computing-specific attributes |
| `THOUGHTSPOT` | ThoughtSpot-specific attributes |
| `HEX` | Hex-specific attributes |
| `ATACCAMA` | Ataccama-specific attributes |
| `OMNI` | Omni-specific attributes |
