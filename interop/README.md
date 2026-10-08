<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements. See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership. The ASF licenses this file to you under the
  Apache License, Version 2.0 (the "License"); you may not use this file except
  in compliance with the License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
-->

# Cross-converter interoperability

The `interop` harness tests the hub-and-spoke contract across converter boundaries rather than only testing each converter's own round trip.

## Tiers

**Tier A** sends the canonical Ossie example through every declared export capability. A successful process is not sufficient: the manifest also declares a converter-specific non-empty assertion, so a loadable vendor document with no meaningful members is graded `EMPTY` rather than `OK`.

**Tier B** starts from each declared native fixture and evaluates `source native -> Ossie -> target native -> Ossie`. The final Ossie document is validated with `validation/validate.py`. A target must declare both export and import capabilities to participate.

The report grades cells as `OK`, `LOSSY`, `EMPTY`, `FAIL`, or `SKIP`. Warning counts are retained so report-only CI can expose known loss before individual cells are promoted to blocking gates. Build/setup warnings are deliberately excluded from semantic loss grading.

**Tier C** is available for converters that expose an independent native compiler or validator. A manifest `native_gate` can be unconditional or guarded by an environment variable; the tier is emitted when at least one selected converter declares a gate.

## Manifest

`manifest.yaml` is the compatibility boundary between the harness and converter-specific command lines. Each converter directory must have an entry, even when it is currently skipped. Executable capabilities declare their command, output shape, and non-empty assertion.

This keeps CLI differences out of the runner and makes unsupported toolchains explicit. GoodData is wired through a thin Python API adapter. The ontology converter participates asymmetrically through its real Palantir import boundary: a committed minimal Palantir export feeds Tier B as a source, while Tier A and ontology-as-target cells remain `SKIP` because no Ossie-to-Palantir exporter exists. Salesforce runs through its shaded executable JAR. Polaris runs its public CLI against an in-process Iceberg REST catalog, which exercises the real HTTP boundary without requiring an external service. Wisdom is installed transiently with `uv --with`, so its missing converter-local lockfile does not mutate the checkout. The legacy `converters/gsf` compatibility directory is the only intentional hard skip; the executable NVIDIA GSF converter is represented by `nvidia`.

## Running

```bash
cd interop
uv sync --frozen
uv run pytest
cd ..
uv run --project interop python interop/runner.py --report interop-report.md --report-only
```

Use `--include databricks,nvidia` for a focused run, `--tier a`, `--tier b`, or `--tier c` to select one tier, and omit `--report-only` when failures should produce a non-zero exit code. Tier C internally materializes the Tier B target output before invoking native gates.
