<!--
  Licensed to the Apache Software Foundation (ASF) under one
  or more contributor license agreements. See the NOTICE file
  distributed with this work for additional information
  regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the
  "License"); you may not use this file except in compliance
  with the License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing,
  software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
  KIND, either express or implied. See the License for the
  specific language governing permissions and limitations
  under the License.
-->

# MCP endpoint contract checks

Implementor tooling for the proposed
[`execute_query` profile](../../docs/proposals/execute-query-mcp.md), version
`0.4-draft`. It checks the **MCP tool declaration and captured observable
contract**, not a query compiler, an engine or an adopted Ossie standard.
An engine's integration tests can collect these captures without changing its
implementation language.

## Reusable schemas

| Artifact | Purpose |
| --- | --- |
| `execute-query-input.schema.json` | Publish as the tool's `inputSchema`; accepts `{data_source_id, query}`. |
| `execute-query-output.schema.json` | Publish as `outputSchema` for structured results, including semantic errors and object-valued repair queries. |
| `execute-query-query.schema.json` | Validate the Layer 3 object independently; uses the same definitions as requests and repairs. |
| `call-result.schema.json` | Ossie constraints on a final MCP result: text/resource content, explicit status and optional structured data. Not the complete MCP protocol schema. |
| `capture.schema.json` | Offline test-harness capture format; not a new endpoint or RPC. |

The first three schemas and `example-capture.json` are generated from the
profile's schema/example blocks, preventing documentation and implementation
contracts from drifting. Their `urn:` identifiers are offline schema identities,
not download URLs. Resolve the provided files locally; never fetch captured
schema references over the network.

```sh
python scripts/generate-mcp-profile.py          # refresh generated files
python scripts/generate-mcp-profile.py --check  # CI drift check
```

## Checking an endpoint

Copy its `execute_query` entry from `tools/list` and capture final results from
your SDK/client. Strip JSON-RPC wrappers: `arguments` is the `tools/call`
argument object; `result` is the final `CallToolResult`. For a completed Task,
capture the final nested tool result, not the initial Task handle.

```json
{
  "protocol_version": "2026-07-28",
  "tool": {
    "name": "execute_query",
    "inputSchema": {}
  },
  "cases": [{
    "name": "replace with a real captured exchange",
    "arguments": {
      "data_source_id": "sales-model",
      "query": {"measures": ["total_revenue"]}
    },
    "result": {}
  }]
}
```

The empty schemas/result above are placeholders, **not a conforming capture**.
Use `example-capture.json` for a complete generated illustration:

```sh
uv run validation/validate_mcp.py validation/mcp/example-capture.json
uv run validation/validate_mcp.py path/to/endpoint-capture.json
```

With `jsonschema` already installed, `python` works in place of `uv run`.
The CLI reads at most 16 MiB, rejects duplicate JSON keys/non-finite constants,
prints a JSON report, and performs no network requests or query execution.
Do not commit captures containing private query results or credentials.

| Exit / status | Meaning within the reported scope |
| --- | --- |
| `0` / `passed` | Recognized declarations and supplied exchanges passed the observable contract checks. |
| `1` / `failed` | A concrete violation, malformed capture or invalid schema was found; JSON Pointer findings identify the surface. |
| `2` / `unverified` | Declaration equivalence, Markdown-only interpretation or an opaque encoding could not be established. Never treat this as a pass. |

The library entry point is `validate_capture(capture)`. Its report includes
`profile`, `scope`, `cases_checked`, `findings`, `unverified` and **`unchecked`**.
CI should require exit zero and review unchecked obligations separately.

## What the checker establishes

- Tool name and valid schema declarations; recognizes the published input/output
  schemas including local reference expansion, reordered set-like keywords and
  annotation changes. It does **not** claim to prove arbitrary JSON Schema
  equivalence: other schemas are explicitly unverified.
- Requests outside the input contract cannot receive success; result objects
  match the published profile, repairs use the query schema, and `isError`,
  structured status, source and model/revision consistency agree.
- Success descriptors refer to actual embedded resources, not link-only
  substitutes; CSV headers, arity, materialized counts, completeness and metadata
  agree. Null/empty/string escaping, exact numeric strings and locale-neutral
  date/time encodings are checked.
- Preview rows/counts/types match embedded CSV, including sample membership,
  duplicate rows/names, and first-row ordering when declared. Omitted previews
  are valid. Binary payloads must be base64, with correct declared export MIME
  types; this does not prove the file is valid XLSX/Parquet.

The report does **not** certify Foundation query answers, semantic-code triggers,
authorization/RLS/OLS, read-only enforcement, invisible retries/probes, live
transport/discovery, Task lifecycle, server budgets, natural-language prose
meaning or binary file fidelity. A captured source's real model identity needs
independent discovery/engine evidence. Passing one fixture proves only that
fixture, not every behavior of an endpoint.

Recommended integration cases: aggregation/scalar success; header-only zero
rows; invalid shapes; semantic refusals; unsupported capabilities; null/empty,
large integer/decimal and Unicode values; optional previews; resource limits;
and the final output from synchronous and Task execution. Use the official MCP
conformance tooling for protocol lifecycle, and Foundation semantic fixtures
for engine answers; neither replaces these Ossie-specific output checks.
