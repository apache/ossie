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

Ossie <-> Metric View converters
=================================

A bidirectional converter between [Apache Ossie](https://github.com/apache/ossie) semantic models
and Databricks Unity Catalog Metric Views (YAML v1.1). Conversion is pure YAML text in, YAML text
out: it reads and writes the two formats as parsed maps and lists, independent of any engine.

Layout
------

| Path | Language | Role |
|------|----------|------|
| [`java/`](java/) | Java | The maintained implementation; also ships a command-line tool (`OssieDatabricksConverter`). |
| [`python/`](python/) | Python | The original reference implementation. To be deprecated. |

See [`java/README.md`](java/README.md) and [`python/README.md`](python/README.md) for building and
using each implementation.
