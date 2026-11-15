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

# Dialect Extensions Catalog

A registry of `dialect` tokens the community uses (see
[`README.md`](README.md) for how these lists are used). `OSSIE_SQL_2026` is the
only dialect the specification defines; all others are opaque to Ossie.

## Known dialects

Rows are sorted alphabetically by token. Keep new rows in order.

| Dialect | Description |
|---------|-------------|
| `ANSI_SQL` | Standard SQL dialect |
| `BIGQUERY` | Google BigQuery (GoogleSQL) |
| `CLICKHOUSE` | ClickHouse SQL |
| `DATABRICKS` | Databricks SQL |
| `DAX` | Data Analysis Expressions (Power BI / Analysis Services) |
| `HOLISTICS_AQL` | Holistics AQL (Analytical Query Language) |
| `MAQL` | GoodData MAQL (Multi-Dimensional Analytical Query Language) |
| `MDX` | Multi-Dimensional Expressions |
| `OMNI` | Omni SQL |
| `OSSIE_SQL_2026` | Ossie's portable SQL expression language, defined in [`expression_language.md`](../core-spec/expression_language.md) |
| `POSTGRES` | PostgreSQL |
| `PYICEBERG` | PyIceberg predicate strings for boolean fields; see [`dialects/pyiceberg.md`](dialects/pyiceberg.md) |
| `REDSHIFT` | Amazon Redshift SQL |
| `SIGMA` | Sigma Computing's spreadsheet-style formula language |
| `SNOWFLAKE` | Snowflake SQL |
| `SPARQL` | SPARQL Query Language |
| `TABLEAU` | Tableau calculations |
| `THOUGHTSPOT` | ThoughtSpot formula language |
| `TRINO` | Trino SQL |
