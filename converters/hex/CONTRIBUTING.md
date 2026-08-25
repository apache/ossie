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

# Contributing to the Ossie–Hex converter

This document covers development of the `ossie-hex` package. Repository-wide
contribution, review, and Apache release requirements are documented in the
[project contribution guide][ossie-contributing].

## Setup

Ensure you have the following tools available in your environment:

- [just][just-install] as a command runner
- [uv][uv-install] as a Python package and project manager
- Python 3.11 or newer, which can be [installed through uv][uv-install-python]

Checkout the Apache Ossie repository and install the project dependencies:

```bash
cd converters/hex
uv sync
```

## Development

TODO(development): {workflow-description}

### Python

TODO(development): {python-description}

### CLI

TODO(development): {cli-description}

## Verification

Run the following commands to verify your changes.

```bash
# lint and formatting checks
just check

# apply automatic fixes for lint and formatting issues
just format

# run complete test suite
just test

# run a single file or test
just test tests/<file>.py
just test tests/<file>.py::<test>
```

### Snapshots

There are two tools available for managing test snapshots [syrupy] and
[inline-snapshot].

In almost all cases, [inline-snapshot] is the preferred tool since it
facilitates a straightforward review where results live beside arrangement
and act blocks.

By default, snapshot differences will fail `just test`. To update snapshots,
use the following commands:

```bash
# Update snapshots interactively (only available for inline-snapshot)
just snapshot-review

# Update snapshots non-interactively
just snapshot-fix
```

### CI/CD

The [continuous integration (CI) workflow][ci-workflow] runs installation,
linting, formatting, and testing on the full range of supported tool versions.

To reproduce the CI tool version matrix locally:

```bash
just test-matrix
```

## Building

Build the source distribution(s) and wheel(s) from this directory:

```bash
just build
```

The artifacts are written to `dist/`. Before publishing, inspect their metadata
and contents:

```bash
just build-inspect
```

Also test installation in a clean environment. This will only work from package
repositories (e.g. PyPI) after all dependencies have been published:

```bash
just build-install
```

## Publishing

Publishing is deferred to the Apache Ossie project, which governs the broader
release cycle. Contributors should not publish this package independently.

<!-- internal links -->
[ossie-contributing]: ../../CONTRIBUTING.md
[ci-workflow]: ../../.github/workflows/converter-hex-ci.yml

<!-- external links -->
[uv-install]: https://docs.astral.sh/uv/installation/
[uv-install-python]: https://docs.astral.sh/uv/guides/install-python/
[syrupy]: https://github.com/syrupy-project/syrupy
[inline-snapshot]: https://15r10nk.github.io/inline-snapshot/
[just-install]: https://just.systems/man/en/installation.html
