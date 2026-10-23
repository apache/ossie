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

# Proposal: `execute_query` MCP profile

**Status:** Proposed, `0.4-draft`. An optional execution contract for engines and
catalogs, not an Ossie server implementation or a condition of model conformance.
MUST, SHOULD and MAY describe requirements of this proposed profile.

## Scope

Execute an agent-authored, read-only **Layer 3 query object resolved against an
Ossie model**; return embedded data, optional previews and actionable diagnostics.
The engine owns model resolution, safe planning and authorization; the calling
agent owns analytical intent and correction. Native SQL/DAX/KQL execution,
Layer 2 queries, query generation, writes, batches, federation, model
authoring/conversion and client-input round trips are outside this profile.
Adoption does not change the core model schema, converters, or API/CLI access.

This complements model management/discovery rather than prescribing it:
[MCP discussion #510](https://github.com/apache/ossie/discussions/510),
[execution-contract proposal on dev@](https://lists.apache.org/thread/olc629g9zmqgx66bo2hdl9hg0on6jtlg),
[specification versus implementation](https://lists.apache.org/thread/wpj5nnpodysds1vqd2s1vxhzw0vkcndy),
and the [REST proposal](https://lists.apache.org/thread/hf71m6g1g1bnhqj5v0f4kc0rf4h8g4kg).
Query semantics are those of the [Layer 3 Foundation draft][layer3], pinned to
`cc0d0709` from [#246](https://github.com/apache/ossie/pull/246). This profile
defines its JSON/MCP binding, not new join, grain or filter semantics.
The dependency remains proposed, not an adopted standard. Layer 2's relational
interface ([#354](https://github.com/apache/ossie/pull/354)) is separate.
The [declarative-scope discussion](https://lists.apache.org/thread/9007r91vf33y04grtk8yn58nqc4oyco)
motivates model-resolved input and direct semantic error codes.

## Binding and request

A catalog, REST API, MCP resource/tool or deployment configuration MUST expose
the source's opaque `data_source_id`, bound **model identity and revision**,
profile and Foundation revisions, supported query shapes/capabilities, output
formats, diagnostic capabilities and execution/materialization ceilings.
Each call MUST resolve against one fixed authorized model revision; successful
results identify it. No mandatory discovery endpoint or core model connection
property is introduced.
The server MUST resolve and authorize the binding for the caller; model names
and dataset sources are not credentials or globally unique execution IDs.
An execution binding ID MUST NOT be reassigned to a different model or revision;
updating the model publishes a new binding, and retired IDs return
`SOURCE_UNAVAILABLE`. This prevents an agent's discovered model from being
silently replaced between discovery and execution.

Tool name: `execute_query`. `inputSchema`:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["data_source_id", "query"],
  "properties": {
    "data_source_id": { "type": "string", "minLength": 1, "pattern": "\\S" },
    "query": { "$ref": "#/$defs/query" }
  },
  "$defs": {
    "expression": { "type": "string", "minLength": 1, "pattern": "\\S" },
    "projection": { "type": "array", "items": { "$ref": "#/$defs/expression" } },
    "predicate": {
      "oneOf": [
        { "$ref": "#/$defs/expression" },
        { "type": "array", "minItems": 1, "items": { "$ref": "#/$defs/expression" } }
      ]
    },
    "query": {
      "type": "object", "additionalProperties": false,
      "properties": {
        "dimensions": { "$ref": "#/$defs/projection" },
        "measures": { "$ref": "#/$defs/projection" },
        "fields": { "allOf": [{ "$ref": "#/$defs/projection" }], "minItems": 1 },
        "where": { "$ref": "#/$defs/predicate" },
        "having": { "$ref": "#/$defs/predicate" },
        "order_by": {
          "type": "array", "minItems": 1,
          "items": {
            "type": "object", "additionalProperties": false, "required": ["field"],
            "properties": {
              "field": { "$ref": "#/$defs/expression" },
              "direction": { "enum": ["ASC", "DESC"] },
              "nulls": { "enum": ["FIRST", "LAST"] }
            }
          }
        },
        "limit": { "type": "integer", "minimum": 0 }
      },
      "oneOf": [
        {
          "not": { "required": ["fields"] },
          "anyOf": [
            { "required": ["dimensions"], "properties": { "dimensions": { "minItems": 1 } } },
            { "required": ["measures"], "properties": { "measures": { "minItems": 1 } } }
          ]
        },
        {
          "required": ["fields"],
          "not": { "anyOf": [{ "required": ["dimensions"] }, { "required": ["measures"] }, { "required": ["having"] }] }
        }
      ]
    }
  }
}
```

### Layer 3 surface and interpretation

This schema serializes Foundation section 5, not a native query inside JSON.
Property names are lowercase; `query` contains the clauses directly,
without another `query` wrapper. All expression strings use the portable
`OSSIE_SQL_2026` expression language and resolve only against the bound model.
Statement text, native dialects, connection properties, query parameters, joins,
grain overrides and undeclared clauses are outside the surface.

| Clause / shape | Contract |
| --- | --- |
| Aggregation | `dimensions` and/or `measures`, at least one nonempty. Omitted arrays mean empty. No `fields`; empty dimensions request totals. |
| Scalar | Nonempty `fields`; no `dimensions`, `measures` or `having`, even if empty. Foundation table-grain and fan-out rules apply. |
| Projections | Dimensions/fields/metrics, ad-hoc expressions and aliases follow Foundation section 5 and its expression subset. Schema validity alone does not establish name, type, grain or expression validity. |
| `where` / `having` | One predicate string or a nonempty list interpreted as AND. Row-level versus aggregate/window predicates follow Foundation sections 6.3/6.10; OR is inside an expression. |
| `order_by` | Objects containing `field` (in-scope name or projection-valid expression), optional `direction` (default `ASC`) and `nulls`. `nulls: FIRST/LAST` explicitly serializes section 5's NULL placement; defaults are ASC/LAST, DESC/FIRST. Positional ordinals are not allowed. |
| `limit` | Nonnegative integer bounding final query rows, including zero; absent means no query-authored cap. Accepted without ordering, but no stable row selection is implied. Server ceilings are independent and any resulting truncation is disclosed. |

The engine resolves names, relationships, join paths and grain-safe aggregation
according to the pinned Foundation, not backend-native semantics. Implementation
may compile to SQL/DAX or another engine representation internally; that does
not change the accepted interface or its meaning. Model expressions without a
supported semantics-preserving evaluation MUST be refused, not interpreted as
native text or silently dropped.

Bindings MUST advertise supported shapes and capability restrictions (such as
M:N traversal, windows, multi-fact queries or path length). Partial implementations
MUST refuse unsupported cases explicitly; exposing this MCP profile does not
claim full Foundation conformance. Reuse an applicable Foundation refusal code
where defined; otherwise return `UNSUPPORTED_QUERY` with the missing capability.
Unsettled Foundation semantics are resolved there, not by inventing backend
defaults here; implementations unable to guarantee the declared semantics MUST
refuse the case. Shape-only tests do not establish equivalent answers.

Each call returns one table. Semantics-preserving compilation is expected;
semantic query mutation, automatic repair/retry, broadened filters, source
fallback and discarded result tables are prohibited. No `language`, connection,
impersonation, external limits or diagnostic arguments are accepted.
Suggested annotations are `readOnlyHint: true`,
`destructiveHint: false` and an `openWorldHint` reflecting the actual deployment;
annotations are not enforcement or an idempotence guarantee.

## Results

A final `CallToolResult` MUST contain concise Markdown-readable status,
completeness, attachment names and applicable guidance. Successful calls,
including zero rows, use `isError: false`; tool failures use `isError: true`.
Malformed MCP envelopes, unknown tools and protocol-capability failures use
protocol errors instead. No failure may masquerade as successful empty data.

Structured output is RECOMMENDED. A tool advertising `outputSchema` MUST supply
conforming `structuredContent` on success and tool errors; Markdown-only
compatibility MAY omit both. `outputSchema` constrains that object, not content
blocks or Task handles. Text, structured fields and embedded resources MUST
agree. MCP's recommended serialized-JSON text fallback SHOULD be provided
where needed, without duplicating the full dataset in prose.
Every successful result MUST identify the evaluated model and revision;
Markdown-only results carry that identity in text or resource metadata.

### Data and previews

Success MUST include CSV as an embedded MCP resource: `type: "resource"` with
`resource.uri`, `mimeType: "text/csv"` and `text`. Known-schema zero-row results
produce header-only CSV; missing column schema is an explicit unsupported-result
error. Optional XLSX/Parquet use separate base64 `resource.blob` blocks with
`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` or
`application/vnd.apache.parquet`. All formats represent the same materialized
rows, columns and values, without re-execution.

Use safe, distinct filename-like URI labels (`file:///sales.csv`), not private
paths or credentials. Embedded bytes are already in the response; the URI
implies neither a physical file nor `resources/read`. Hosts determine how to
save/expose attachments. Resource links do not replace embedded CSV in this
profile. If delivery exceeds policy, return `RESOURCE_LIMIT` with narrowing
guidance, not preview-only success. Base64 overhead counts toward ceilings;
Tasks solve latency, not payload size. Servers embedding resources SHOULD
implement the resources capability, without promising retrieval of every label.

Preview selection is non-normative guidance; no row count or first-row rule is
required:

| Selection | Recommended use |
| --- | --- |
| First few rows, often 5-10 | Economical default; without ordering these are not ranked "top" rows. |
| All rows | Small results within a cell/byte budget. |
| Labeled sample | Exploration; no implied order or statistical representativeness. |
| No preview | Wide/large values; retain column metadata and embedded data. |

An optional structured preview MUST preserve exact cells, positional column
arity and JSON nulls; it MUST NOT synthesize statistics as query rows. Shortened
Markdown cells MUST be labeled and retain originals in CSV. Display selection
does not change execution. `preview.row_count` equals displayed rows;
`result.row_count` equals materialized rows excluding the header;
`preview.has_more` equals `result.row_count > preview.row_count`.
Independently, `result.completeness` is `complete` only with evidence of query
exhaustion, `truncated` with evidence of omitted rows, otherwise `unknown`.
Query-authored `limit` is query semantics, not export truncation. Capped or
sampled materializations MUST NOT be described as complete.

### Diagnostics

Every final result includes diagnostic availability, suggestions and filter
alternatives; empty arrays are valid. Applicable enrichment SHOULD be produced
under server policy, not an input flag. Supplemental probes MUST share caller
authorization, RLS/OLS and aggregate budgets. Partial/unavailable enrichment
requires an explanation, not an implied exhaustive search or a changed primary
query outcome. Zero rows remain success.

Suggestions are advice: `replacement_query` is a complete **Layer 3 object
matching the same query schema**, not native text, a JSON-encoded string or a
patch. It remains scoped to the same source/model and is never automatically
executed. Static examples are not verified query results.
Alternatives identify candidates for the original authorized filter field;
cross-field hints belong in suggestions. Their `complete` flag concerns
candidate search, not query completeness. Unauthorized names/values, connection
strings, credentials and stack traces MUST NOT appear in diagnostics.

The structured core is extensible through namespaced object-valued `extensions`;
clients MUST tolerate unfamiliar extension keys, error codes and guidance kinds,
but extensions cannot override core status, access or execution semantics.
Common codes: `INVALID_ARGUMENT`, `SOURCE_UNAVAILABLE`, `UNSUPPORTED_QUERY`,
`QUERY_REJECTED`, `QUERY_INVALID`, `PERMISSION_DENIED`, `TIMEOUT`, `CANCELLED`,
`RESOURCE_LIMIT`, `BACKEND_ERROR`. `SOURCE_UNAVAILABLE` may conceal inaccessible
bindings; error source IDs echo supplied values, not privileged resolved IDs.
Guidance kinds include `name`, `query`, `filter`, `limit`, `diagnostic`.

Foundation semantic refusals MUST appear unchanged in **`error.code`**, not
only in `extensions` or flattened to `QUERY_INVALID`. For example,
`E_NAME_NOT_FOUND`, `E_NO_PATH`, `E_AMBIGUOUS_PATH` and
`E3013_NO_STITCHING_DIMENSION` retain their defined triggers.
Mixed/empty query shapes use `E_MIXED_QUERY_SHAPE`,
`E_EMPTY_AGGREGATION_QUERY` or `E_EMPTY_SCALAR_QUERY` when applicable.
Expression parsing failures without a Foundation code use `QUERY_INVALID`;
malformed clause values use `INVALID_ARGUMENT`; valid but unsupported evaluation
uses `UNSUPPORTED_QUERY` unless Foundation defines a capability code.
Do not assign a semantic-invalid code merely because an engine lacks a planner
feature. Provider diagnostics MAY extend the error without overriding its code.

## Encoding and presentation

| Concern | Requirement / guidance |
| --- | --- |
| CSV syntax | UTF-8, header, comma delimiter, RFC 4180 quoting of commas/quotes/newlines. Preserve column positions and duplicate names. |
| Null versus string | Null is unquoted `\N`; non-null strings beginning with `\` gain one leading `\` before CSV quoting. Decode null first, then remove the escape prefix. Empty strings remain empty; literal `\N` becomes `\\N`. Headers are not null/escape encoded. |
| Independent interpretation | Every CSV resource MUST carry `_meta["org.apache.ossie/execute_query"]` with ordered column descriptors, row count, completeness, `csv_null_value` and `csv_escape_prefix`. Structured descriptors/preview MUST agree; this is profile metadata, not an MCP-defined key. |
| Logical types | Reuse core types; omit unknown types. Integer/Decimal use exact decimal strings; Float uses finite JSON numbers/invariant CSV text; Boolean uses JSON booleans/CSV `true` or `false`. Opaque values require a declared encoding. Never repair unknown arity/types by dropping cells. |
| Locale | Machine values MUST be locale-neutral: `.` decimals, no grouping/currency/percent suffixes, invariant signs/exponents and preserved scale. `0.125` is not changed to display `12.5%`. |
| Date/time | ISO 8601 with precision preserved. Distinguish civil `DateTime` from offset-bearing `DateTimeTz`; do not invent a timezone. Offsets identify instants, not necessarily named zones. Available zone/unit/currency/collation detail belongs in namespaced metadata. |
| Strings and UI | Preserve identifiers, Unicode, case, whitespace and leading zeros. UI prose MAY be localized independently; disclose display rounding. Readers SHOULD disable automatic locale/type guessing. |
| Spreadsheet safety | Do not silently prefix formulas or alter analytical CSV values. Warn about spreadsheet interpretation; optional XLSX should use appropriately typed, safe cells. |

### Token economy

Hosts SHOULD expose full attachments to file/data tooling rather than inject
CSV/base64 into model context; audience annotations are hints, not isolation or
confidentiality. LLM-facing output SHOULD contain one short outcome, optional
economical preview and actionable guidance, avoiding repeated query/schema/data
or error text. Candidate lists may be bounded if incompleteness is disclosed;
exact structured values MUST NOT be replaced by ellipses. Future additions
SHOULD justify decision value and token cost across tiny, wide, large, empty and
error results; additional formats are optional, not mandatory duplication.

## Structured output schema

The following schema applies only to final `structuredContent`. Requirements
outside JSON Schema include preview/resource consistency and encoding rules above.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "additionalProperties": false,
  "required": ["contract_version", "status", "diagnostics", "suggestions", "filter_value_alternatives"],
  "properties": {
    "contract_version": { "const": "0.4-draft" },
    "status": { "enum": ["success", "error"] },
    "data_source_id": { "type": "string", "minLength": 1 },
    "model": {
      "type": "object", "additionalProperties": false, "required": ["id", "revision"],
      "properties": {
        "id": { "type": "string", "minLength": 1 },
        "revision": { "type": "string", "minLength": 1 }
      }
    },
    "preview": {
      "type": "object", "additionalProperties": false,
      "required": ["columns", "rows", "row_count", "has_more"],
      "properties": {
        "columns": {
          "type": "array", "minItems": 1,
          "items": {
            "type": "object", "additionalProperties": false, "required": ["name"],
            "properties": {
              "name": { "type": "string" },
              "datatype": { "enum": ["String", "Integer", "Decimal", "Float", "Boolean", "Date", "Time", "DateTime", "DateTimeTz", "Opaque"] }
            }
          }
        },
        "rows": { "type": "array", "items": { "type": "array", "items": {} } },
        "row_count": { "type": "integer", "minimum": 0 },
        "has_more": { "type": "boolean" },
        "selection": { "enum": ["first_rows", "sample"] }
      }
    },
    "result": {
      "type": "object", "additionalProperties": false,
      "required": ["row_count", "completeness", "resources"],
      "properties": {
        "row_count": { "type": "integer", "minimum": 0 },
        "completeness": { "enum": ["complete", "truncated", "unknown"] },
        "resources": {
          "type": "array", "minItems": 1, "items": { "$ref": "#/$defs/resource" },
          "contains": { "type": "object", "required": ["format"], "properties": { "format": { "const": "csv" } } }
        },
        "extensions": { "$ref": "#/$defs/extensions" }
      }
    },
    "error": {
      "type": "object", "additionalProperties": false, "required": ["code", "message", "retryable"],
      "properties": {
        "code": { "type": "string", "minLength": 1 },
        "message": { "type": "string", "minLength": 1 },
        "retryable": { "type": "boolean" },
        "extensions": { "$ref": "#/$defs/extensions" }
      }
    },
    "diagnostics": {
      "type": "object", "additionalProperties": false, "required": ["state"],
      "properties": {
        "state": { "enum": ["completed", "partial", "unavailable", "not_applicable"] },
        "message": { "type": "string", "minLength": 1 },
        "extensions": { "$ref": "#/$defs/extensions" }
      },
      "if": { "properties": { "state": { "enum": ["partial", "unavailable"] } } },
      "then": { "required": ["message"] }
    },
    "suggestions": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false, "required": ["kind", "message"],
        "properties": {
          "kind": { "type": "string", "minLength": 1 },
          "message": { "type": "string", "minLength": 1 },
          "replacement_query": { "$ref": "#/$defs/query" },
          "extensions": { "$ref": "#/$defs/extensions" }
        }
      }
    },
    "filter_value_alternatives": {
      "type": "array",
      "items": {
        "type": "object", "additionalProperties": false,
        "required": ["field", "requested_value", "values", "match", "complete"],
        "properties": {
          "field": { "type": "string", "minLength": 1 },
          "requested_value": {},
          "values": { "type": "array", "items": {} },
          "match": { "enum": ["exact", "fuzzy", "unknown"] },
          "complete": { "type": "boolean" },
          "extensions": { "$ref": "#/$defs/extensions" }
        }
      }
    },
    "extensions": { "$ref": "#/$defs/extensions" }
  },
  "$defs": {
    "expression": { "type": "string", "minLength": 1, "pattern": "\\S" },
    "projection": { "type": "array", "items": { "$ref": "#/$defs/expression" } },
    "predicate": {
      "oneOf": [
        { "$ref": "#/$defs/expression" },
        { "type": "array", "minItems": 1, "items": { "$ref": "#/$defs/expression" } }
      ]
    },
    "query": {
      "type": "object", "additionalProperties": false,
      "properties": {
        "dimensions": { "$ref": "#/$defs/projection" },
        "measures": { "$ref": "#/$defs/projection" },
        "fields": { "allOf": [{ "$ref": "#/$defs/projection" }], "minItems": 1 },
        "where": { "$ref": "#/$defs/predicate" },
        "having": { "$ref": "#/$defs/predicate" },
        "order_by": {
          "type": "array", "minItems": 1,
          "items": {
            "type": "object", "additionalProperties": false, "required": ["field"],
            "properties": {
              "field": { "$ref": "#/$defs/expression" },
              "direction": { "enum": ["ASC", "DESC"] },
              "nulls": { "enum": ["FIRST", "LAST"] }
            }
          }
        },
        "limit": { "type": "integer", "minimum": 0 }
      },
      "oneOf": [
        {
          "not": { "required": ["fields"] },
          "anyOf": [
            { "required": ["dimensions"], "properties": { "dimensions": { "minItems": 1 } } },
            { "required": ["measures"], "properties": { "measures": { "minItems": 1 } } }
          ]
        },
        {
          "required": ["fields"],
          "not": { "anyOf": [{ "required": ["dimensions"] }, { "required": ["measures"] }, { "required": ["having"] }] }
        }
      ]
    },
    "extensions": { "type": "object", "additionalProperties": { "type": "object" } },
    "resource": {
      "type": "object", "additionalProperties": false,
      "required": ["uri", "name", "format", "mime_type"],
      "properties": {
        "uri": { "type": "string", "format": "uri" },
        "name": { "type": "string", "minLength": 1 },
        "format": { "type": "string", "minLength": 1 },
        "mime_type": { "type": "string", "minLength": 1 },
        "csv_null_value": { "const": "\\N" },
        "csv_escape_prefix": { "const": "\\" },
        "extensions": { "$ref": "#/$defs/extensions" }
      },
      "if": { "properties": { "format": { "const": "csv" } } },
      "then": {
        "required": ["csv_null_value", "csv_escape_prefix"],
        "properties": { "mime_type": { "const": "text/csv" } }
      }
    }
  },
  "oneOf": [
    { "properties": { "status": { "const": "success" } }, "required": ["data_source_id", "model", "result"], "not": { "required": ["error"] } },
    { "properties": { "status": { "const": "error" } }, "required": ["error"], "not": { "anyOf": [{ "required": ["preview"] }, { "required": ["result"] }] } }
  ]
}
```

## MCP execution and safety

This profile uses [MCP 2026-07-28 tools][mcp-tools] and [resources][mcp-resources],
with the optional [Tasks extension][mcp-tasks]. Implementations MUST follow the
selected MCP version; the application schema does not replace protocol schemas.

| Mechanism | Profile application |
| --- | --- |
| Discovery | Modern `server/discover` and authorized `tools/list` advertise only implemented capabilities. Source bindings remain catalog data, not connection state. Supported cacheable discover/list/read RPCs include `ttlMs`/`cacheScope`; private metadata is authorization-isolated. List cursors do not paginate CSV/query rows. |
| Request envelope | Per-request protocol version/client capabilities belong in `params._meta`. HTTP uses matching `MCP-Protocol-Version`, `Mcp-Method` and `Mcp-Name` (`execute_query` for calls, Task ID for Task RPCs), never query text in headers. |
| Progress/cancellation | Synchronous `_meta.progressToken` may report throttled phases without invented percentages. HTTP response-stream closure or stdio `notifications/cancelled` signals request cancellation; propagate backend cleanup where possible. |
| Durable Tasks | Servers SHOULD support long-running query/materialization through negotiated `io.modelcontextprotocol/tasks`. Create a durable flat `resultType: "task"` handle only for a declaring client; poll `tasks/get`, cancel with `tasks/cancel`. Completed Task `result` is the final `CallToolResult`, including tool errors; `failed` is for JSON-RPC faults. |
| Notifications/recovery | Optional Task subscriptions supplement polling, not repeated execution. Task cancellation is cooperative. Initial response loss gives no exactly-once guarantee; once a Task ID is known, resume polling it. Cache freshness, retention TTL and runtime deadlines are independent. |
| Compatibility | 2025-11-25 deployments use their own initialization, experimental task capability/request fields and `tasks/result`; do not mix them into the modern protocol. Latest/draft core schemas were identical at the pinned review commit. |

Server policy MUST bound request validation, planning, execution,
materialization and probes; Tasks do not relax ceilings. Authorize model
elements, sources, resources and Tasks for the effective caller, preserving
RLS/OLS. Parse/resolve expression fragments before safe compilation; do not
concatenate unchecked text into native statements. Enforce
read-only through engine permissions/compiler controls, not annotation or
string-prefix tests. Protected remote endpoints follow MCP authorization;
upstream credentials have separate audiences, with no arbitrary token
passthrough. Prefer bounded self-contained schemas/local `$defs`, not automatic
network `$ref` fetching. Optional trace context is envelope metadata, not source
authority; `_meta` is not confidential. Data and guidance are untrusted content.

The profile introduces no client-input round trips, model sampling, filesystem
Roots, query-authoring prompts or Apps/Skills dependency. Deprecated
Roots/Sampling/Logging are not adopted. Optional server onboarding/UI features
are separate concerns, not execution requirements.

## Examples

Aggregation query against the bound model:

```json
{
  "data_source_id": "sales-model",
  "query": {
    "dimensions": ["customers.region"],
    "measures": ["total_revenue"],
    "where": "orders.status = 'completed'",
    "order_by": [{ "field": "total_revenue", "direction": "DESC", "nulls": "LAST" }],
    "limit": 20
  }
}
```

Scalar query; predicate lists are AND-conjoined and omitted ordering options
use the profile defaults:

```json
{
  "data_source_id": "sales-model",
  "query": {
    "fields": ["orders.order_id", "orders.amount", "customers.region"],
    "where": ["orders.status = 'completed'", "orders.amount > 0"],
    "order_by": [{ "field": "orders.order_id" }],
    "limit": 100
  }
}
```

Modern request; Task support is declared, not demanded:

```json
{
  "jsonrpc": "2.0", "id": 1, "method": "tools/call",
  "params": {
    "name": "execute_query",
    "arguments": {
      "data_source_id": "sales-model",
      "query": {
        "dimensions": ["customers.region"],
        "measures": ["total_revenue"],
        "where": "orders.status = 'completed'",
        "order_by": [{ "field": "total_revenue", "direction": "DESC", "nulls": "LAST" }],
        "limit": 20
      }
    },
    "_meta": {
      "io.modelcontextprotocol/protocolVersion": "2026-07-28",
      "io.modelcontextprotocol/clientCapabilities": {
        "extensions": { "io.modelcontextprotocol/tasks": {} }
      }
    }
  }
}
```

Successful `CallToolResult` body; preview and CSV metadata correlate:

```json
{
  "resultType": "complete", "isError": false,
  "content": [
    { "type": "text", "text": "1 row; complete. Data: sales.csv. No applicable filter guidance." },
    {
      "type": "resource",
      "resource": {
        "uri": "file:///sales.csv", "mimeType": "text/csv",
        "text": "region,total_revenue\r\nWest,12345.67\r\n",
        "_meta": {
          "org.apache.ossie/execute_query": {
            "columns": [
              { "name": "region", "datatype": "String" },
              { "name": "total_revenue", "datatype": "Decimal" }
            ],
            "row_count": 1, "completeness": "complete",
            "csv_null_value": "\\N", "csv_escape_prefix": "\\"
          }
        }
      },
      "annotations": { "audience": ["user", "assistant"] }
    }
  ],
  "structuredContent": {
    "contract_version": "0.4-draft", "status": "success",
    "data_source_id": "sales-model",
    "model": { "id": "sales", "revision": "r1" },
    "preview": {
      "columns": [
        { "name": "region", "datatype": "String" },
        { "name": "total_revenue", "datatype": "Decimal" }
      ],
      "rows": [["West", "12345.67"]], "row_count": 1, "has_more": false
    },
    "result": {
      "row_count": 1, "completeness": "complete",
      "resources": [{
        "uri": "file:///sales.csv", "name": "sales.csv",
        "format": "csv", "mime_type": "text/csv",
        "csv_null_value": "\\N", "csv_escape_prefix": "\\"
      }]
    },
    "diagnostics": { "state": "not_applicable" },
    "suggestions": [], "filter_value_alternatives": []
  }
}
```

Tool error with extensible, advisory repair guidance:

```json
{
  "resultType": "complete", "isError": true,
  "content": [{
    "type": "text",
    "text": "Unknown metric `total_reveneu`; use `total_revenue`. No replacement query executed."
  }],
  "structuredContent": {
    "contract_version": "0.4-draft", "status": "error",
    "data_source_id": "sales-model",
    "model": { "id": "sales", "revision": "r1" },
    "error": {
      "code": "E_NAME_NOT_FOUND",
      "message": "Unknown metric 'total_reveneu'; did you mean 'total_revenue'?",
      "retryable": false,
      "extensions": { "example.org/parser": { "line": 1 } }
    },
    "diagnostics": { "state": "completed" },
    "suggestions": [{
      "kind": "query", "message": "Use the known total_revenue metric.",
      "replacement_query": {
        "dimensions": ["customers.region"],
        "measures": ["total_revenue"],
        "where": "orders.status = 'completed'",
        "order_by": [{ "field": "total_revenue", "direction": "DESC", "nulls": "LAST" }],
        "limit": 20
      }
    }],
    "filter_value_alternatives": []
  }
}
```

Deferred result body, only with negotiated Task support:

```json
{
  "resultType": "task", "taskId": "query-task-example", "status": "working",
  "createdAt": "2026-10-06T13:30:00Z", "lastUpdatedAt": "2026-10-06T13:30:00Z",
  "ttlMs": 3600000, "pollIntervalMs": 1000
}
```

`tasks/get` returns `resultType: "complete"` with Task state; on `completed`,
its nested `result` is the same final tool result. A Task handle is not a CSV URI
and is not validated by the application `outputSchema`.

## Conformance

Adapters MUST resolve the bound model and Layer 3 semantics and preserve typed
pre-formatting results, nulls/precision, completeness and embedded bytes rather
than reverse-parse lossy
previews or claim conformance by tool name. Relevant cases include aggregation,
scalar, mixed/empty shapes; name/path/grain/filter validation; valid but
unsupported capabilities; two-fact queries and Having; zero rows;
absent/first-row/sample previews; row-arity and
duplicate-name fidelity; CSV escapes, locale and exact numerics; partial
materialization/enrichment; resource budgets; permission refusals; extensible
tool errors; cancellation/reconnect; and Task tool-error versus protocol-fault
states. No reference engine or adapter implementation is included. `0.4-draft`
replaces native strings/language selection with Layer 3 objects and uses the
same object for repairs; it is not wire-compatible with `0.3-draft`.

### Implementor contract checks

[Reusable schemas and an offline checker](../../validation/mcp/README.md)
validate endpoint implementors' tool declarations and captured final results:
arguments/repair objects, status/source/model consistency, embedded CSV,
encoding metadata and preview fidelity. The published schemas are generated
from this profile; no semantic planner or endpoint is introduced.
Reports distinguish passed, failed and unverified captures and list unchecked
obligations. Passing captured contract tests does not certify authorization,
read-only execution, Foundation query answers or the MCP transport/Task lifecycle.

[layer3]: https://github.com/apache/ossie/blob/cc0d07099a3ef31e85af1fd93458a52186fae2c6/core-spec/foundational_semantics.md
[mcp-tools]: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/0a11bf68c7ec4473526ec15589f592afcd12d1e8/docs/specification/2026-07-28/server/tools.mdx
[mcp-resources]: https://github.com/modelcontextprotocol/modelcontextprotocol/blob/0a11bf68c7ec4473526ec15589f592afcd12d1e8/docs/specification/2026-07-28/server/resources.mdx
[mcp-tasks]: https://github.com/modelcontextprotocol/ext-tasks/blob/93a4915aadf714f87ece5cd40c317bce24779cf5/specification/2026-07-28/tasks.md
