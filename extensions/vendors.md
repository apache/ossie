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

## Known vendors

| Vendor | Description |
|--------|-------------|
| `COMMON` | Common/standard extensions |
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
