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

# GASP conformance scripts

Fifteen cases from the conformance set of the [GASP Standard](https://gaspwiki.com), each a script for [`ost`](../../ost/docs/ost.md). A case takes one metric of the standard over one reporting period. Its directory under `cases/` holds two files:

- `model.ossie.yaml` is the semantic model the case reads, a slice of the standard's full export.
- `case.ost` creates the tables the case needs, loads the model with `modelFile` and asks for the metric through `executeExpand`. The expanded SQL and the result sit under each command.

Twelve cases read a staged dataset, one whose `source` is a query: a cohort, a prior period, a window and the other constructs an expression cannot state. The other three (`c12`, `c13` and `c14`) are direct expressions over a snapshot dataset. Each of those also asks for two wrong readings of the snapshot and shows the values they give.

Run them from the repository root:

```sh
uv run --project ost ost -d examples/gasp_saas_conformance examples/gasp_saas_conformance/cases
```

The files are generated from the standard's formulas by its exporter (https://github.com/GASP-Standard/standard, Apache 2.0). The set they come from also holds the tables as data files, the reference SQL for Postgres and the expected rows as SQL: https://github.com/GASP-Standard/standard/tree/main/standard/exports/ossie/conformance.
