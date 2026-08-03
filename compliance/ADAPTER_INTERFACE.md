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

# Adapter Interface Contract

**Version**: 1.0

---

## Purpose

This document defines the boundary between the **test harness** and an **OSI implementation adapter**. It is the authoritative reference for what adapters may and must not do.

## CLI Contract

```bash
<adapter> sql --model <model.yaml> --query-file <query.json> --dialect <dialect>
```

| Stream | Content |
|--------|---------|
| **stdout** | Generated SQL string only — no headers, no decoration |
| **stderr** | Error messages on failure |
| **exit code** | 0 on success, non-zero on error |

## Adapter Boundary Rules

An adapter is a **thin translator** between the harness CLI contract and the implementation's programmatic API. It performs **format conversion only**.

### An adapter MUST:

1. Parse CLI arguments (`--model`, `--query-file`, `--dialect`)
2. Translate the query JSON into the implementation's query object (e.g., `LODQuery`)
3. Invoke the implementation's planner and transpiler
4. Print the resulting SQL to stdout
5. Print errors to stderr and exit non-zero on failure

### An adapter MUST NOT:

1. **Validate query semantics** — duplicate dimensions, ambiguous fields, ORDER BY validity, EXCLUDE dimension membership. These are planner responsibilities. If the implementation doesn't validate them, **file a bug on the implementation**.

2. **Rewrite SQL expressions** — dialect function translation (IFF→CASE, ZEROIFNULL→COALESCE), expression normalization. These are transpiler responsibilities. If the transpiler mishandles a function, **fix the transpiler**.

3. **Substitute parameters** — resolving `:param`/`$param` placeholders, type-aware quoting. These are parsing-layer responsibilities. If the parser doesn't handle parameters, **extend the parser**.

4. **Handle special query shapes** — dimensions-only queries, empty-measure queries. These are planner responsibilities. If the planner can't handle a valid query shape, **fix the planner**.

5. **Work around implementation bugs** — injecting synthetic measures, monkey-patching frozen models, generating SQL directly. If the implementation produces wrong SQL, **fix the implementation**.

6. **Contain business logic** — aggregation classification, grain resolution, safety checks, filter routing. All BI semantics belong in the implementation.

### An adapter MAY:

1. Set up import paths to locate the implementation (`sys.path` manipulation)
2. Register dialect plugins (e.g., importing `osi.frontend.dialect` to register with sqlglot)
3. Translate between the JSON query format and the implementation's API types (this is format conversion, not business logic)

## Size Guidance

A compliant adapter should be about a page of code (or less). If an adapter exceeds this, it is almost certainly doing work that belongs in the implementation. The reference Python adapter is ~160 lines.

## For AI Agents

When working on adapters for the OSI compliance suite, you MUST follow these rules:

1. **Never add business logic to an adapter.** If a test fails because the implementation doesn't handle something, fix the implementation — not the adapter.
2. **Never add more than 10 lines to an adapter without checking this document.** If you're adding validation, rewriting, parameter handling, or SQL generation, you're in the wrong file.
3. **The adapter line count is a hard signal.** If it exceeds 200 lines, something is wrong.
4. **When in doubt, the implementation is the right place.** The adapter exists only because different implementations have different API shapes. Everything else is implementation work.
