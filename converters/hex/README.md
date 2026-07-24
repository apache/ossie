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

# Ossie ↔ Hex converter

Bidirectional, offline conversion between [Apache Ossie](https://ossie.apache.org/) and [Hex](https://learn.hex.tech/docs/connect-to-data/semantic-models/semantic-authoring/modeling-specification).

- **Export** (`ossie-hex export`): Ossie → Hex
- **Import** (`ossie-hex import`): Hex → Ossie

**Hex → Ossie → Hex is lossless for the supported Hex surface.**

## Installation

This converter is distributed as a Python package. Install it with `uv` or `pip`:

```bash
uv tool install ossie-hex

# Alternatively
pip install ossie-hex
```

Requires Python 3.11 or newer.
