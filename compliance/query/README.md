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

Query compliance tests
======================

Test cases for the
[Relational Query Interface](../../core-spec/relational_semantics.md).

[`relational_semantics.sql`](relational_semantics.sql) loads the SCOTT schema
and defines an Ossie model over it with dataset-scoped metrics (in comments).
It then gives 29 test cases: 12 on one relation and 17 on joins. The join
cases include fan-out and many-to-many joins, and two queries that are run
with each join type. Each case is an Ossie query that uses `AGG()` (in
comments), an equivalent DuckDB query, and the expected result (in comments).
The DuckDB queries for joins follow the reference evaluation strategy of
Section 9 of the spec.

Run the file with DuckDB. It needs no extensions:

```sh
duckdb < compliance/query/relational_semantics.sql
```

These tests are a light version of the `.ost` test format
([#501](https://github.com/apache/ossie/issues/501)) and the view expander
([#502](https://github.com/apache/ossie/issues/502)). When those are
available, each case can become an `.ost` test.
