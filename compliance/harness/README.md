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

# OSI Compliance Harness

Shared runner / reporter / DB manager for the OSI compliance suite.

This package is the engine behind every per-version compliance suite
under `compliance/`. It is **engine-agnostic** — it does not know about
any specific OSI implementation. Engines plug in via an *adapter* that
implements the CLI contract documented in
[`../ADAPTER_INTERFACE.md`](../ADAPTER_INTERFACE.md).

## Install

This package (`osi_compliance_harness`, imported as `harness`) is a member of
the [`compliance/`](../) [`uv`](https://docs.astral.sh/uv/) workspace, so you do
**not** install it on its own. A single `uv sync` at the workspace root installs
it alongside every suite:

```bash
cd ..        # compliance/ (the workspace root)
uv sync
```

The compliance suites depend on this package to run their tests; `uv run` picks
it up automatically from any directory under `compliance/`.

## Run

The harness resolves ``--output`` relative to the current working
directory, so run it from the suite root (per-run artifacts then land
under ``<suite>/results/latest/`` by default):

```bash
cd ../foundation
uv run python -m harness.runner \
    --adapter adapters/osi_python_adapter.py \
    --tests tests/ \
    --datasets datasets/
```

See [`../foundation/README.md`](../foundation/README.md) for
the suite-level entry point and reporting layout, and
[`../ARCHITECTURE.md`](../ARCHITECTURE.md) for how the harness, adapters, and
spec fit together.
