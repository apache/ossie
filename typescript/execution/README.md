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

# Ossie execution client — DRAFT

A browser-oriented TypeScript client prototype for
[Ossie HTTP execution `0.1-draft`](../../docs/proposals/execute-query-http.md):
HTTP/JSON submission, status and cancellation, followed by materialized
Apache Arrow IPC results, either in one stream or immutable pages.

**This is a proposal, not an adopted standard or a published-service promise.**
The package does not implement an execution server, semantic evaluator,
authentication system, Flight transport, or vendor adapters. Local tests
exercise the protocol against fixtures; they do not prove production vendor
interoperability, Foundation expression semantics, or row-level security.

## HTTP versions

The binding is **not HTTP/1.1-only**: its HTTP semantics and payloads can run
over HTTP/1.1, HTTP/2, or HTTP/3 where supported. Fetch and the deployment
negotiate the transport; the client exposes no version-selection option.
Textual `HTTP/1.1` examples in the spec are illustrative, not a restriction.
No deployment is required to support all three versions.

HTTP/2 or HTTP/3 multiplexing does not turn this into Flight or introduce
parallel page downloads: full-result traversal remains sequential.
Web Streams provide pull-based consumption, not a guarantee of end-to-end
backpressure or bounded intermediary buffering. The existing integration
fixtures use loopback HTTP/1.1; HTTP/2/3 interoperability and performance parity
with Flight have not been demonstrated.

## Contract

- [Normative draft](../../docs/proposals/execute-query-http.md).
- [JSON Schema, draft 2020-12](../../docs/proposals/execution-http/execution.schema.json),
  including `Request`, `Query`, `Capabilities`, `Binding`, `Execution`,
  `ResultManifest`, `ResultPage`, `Column`, `QueryError`, and `ProblemDetails`.
- [OpenAPI 3.1](../../docs/proposals/execution-http/openapi.json), referencing
  those same definitions.
- [Public TypeScript types](src/types.ts).

The draft pins the still-open [MCP #529][mcp] at
`b5418ee3aa19f117a1c464733de21f5a0dc0326b` (query profile `0.4-draft`) and
[Foundation #246][foundation] at
`cc0d07099a3ef31e85af1fd93458a52186fae2c6`.
The MCP query definitions are copied unchanged into the HTTP schema; its
`Query` wrapper additionally restricts `limit` to a nonnegative safe integer.
This is shape reuse, not a second expression language or a semantic validator.

## Client surface

`OssieClient` accepts a base URL, an optional `fetch` implementation, and an
optional access-token callback. The package is intended for a browser bundler
or Node.js 22+ with Fetch, Web Streams, and `AbortController`.
It is a repository-local package; the examples do not imply npm publication.

| Method (optional options argument omitted below) | Purpose / return type |
| --- | --- |
| `getCapabilities()` | `Promise<Capabilities>`; validate versions, result format, wait bound, shapes, and features. |
| `getBinding(dataSourceId)` | `Promise<Binding>`; resolve the model id/revision and supported shapes. |
| `submit(request)` | `Promise<Execution>`; submit once and return any execution state. Accepts `SubmitOptions`. |
| `getExecution(executionId)` | `Promise<Execution>`; read state, including failed or cancelled. |
| `wait(execution)` | `Promise<SuccessfulExecution>`; poll to success, otherwise throw. Accepts `WaitOptions`. |
| `cancel(executionId)` | `Promise<Execution>`; request best-effort cancellation and return the observed state. |
| `forgetExecution(executionId)` | Release local lifecycle history; no network request, cancellation, or server-side deletion. |
| `readBatches(successfulExecution)` | `AsyncGenerator<RecordBatch>`; provisional batches until successful exhaustion. Accepts `RequestOptions` plus `allowTruncated`. |
| `readPage(successfulExecution, pageIndex)` | `Promise<Table>` for one zero-based page; accepts `RequestOptions` plus `allowTruncated`. Only that page is fetched and validated. |
| `execute(request)` | `Promise<{ execution: SuccessfulExecution; table: Table }>`; submit, wait, and return verified materialization. Accepts `ExecuteOptions`. |

Unless noted otherwise, methods accept `RequestOptions` with an optional
`signal`. Types, `OssieClient`, default-limit constants, and error classes are
exported from the package entry point.

### Example

Once built and linked as a local dependency, the package imports as
`@apache-ossie/execution-client`. The host application supplies a short-lived
token; the example does not implement or prescribe a token broker.

```ts
import { OssieClient } from "@apache-ossie/execution-client";

export async function loadRevenue(
  getAccessToken: () => Promise<string>,
  signal?: AbortSignal,
) {
  const client = new OssieClient({
    baseUrl: "https://api.example.test/ossie",
    getAccessToken,
    maxResultBytes: 64 * 1024 * 1024,
    maxResultRows: 1_000_000,
  });
  const { execution, table } = await client.execute(
    {
      data_source_id: "sales-v7",
      query: {
        dimensions: ["customers.region"],
        measures: ["revenue"],
        limit: 100,
      },
    },
    signal ? { signal } : {},
  );
  // Keep Arrow values; do not turn exact integers/decimals into Number.
  return { table, columns: execution.result.columns };
}
```

The URL and model are hypothetical. No included server can execute this
example. Low-level consumers may instead call `submit`, `wait` with the returned
execution object, and `readBatches` with the successful execution.

### Bounded wait is a synchronous fast path for one asynchronous job

`waitSeconds` controls how long the **initial submission response** may wait,
not how long the query may run. The server may return earlier.

| Pattern | Submission response |
| --- | --- |
| Immediate asynchronous (`waitSeconds: 0`, also the default) | Return the handle promptly; `202` while pending, or `200` if already terminal. |
| Bounded wait (`waitSeconds: 5`) | Wait up to five seconds. Return `200` if terminal, otherwise `202` with the **same running execution**. |
| Unbounded synchronous | Hold the connection until completion regardless of duration. This draft does not offer that submission mode. |

For a 200 ms query with `waitSeconds: 5`, the submission can return success
without polling. For a 20 s query, the submission returns pending at or before
five seconds and the client polls the same id. It does not cancel, restart or
submit another query. Results are still downloaded separately after success.
HTTP `200` means terminal, which may also be failed or cancelled.
JavaScript methods always return promises; this is separate from the server's
sync/async response policy. `execute()` hides polling and resolves only after
the result is fully read. `timeoutMs` instead bounds the whole client operation,
including polling/download, and stopping it does not cancel server execution.

### Pagination

The server partitions one immutable materialized result and returns **either**
the existing `result.url` **or** a `result.pages` manifest. Each descriptor has
`page_index`, `row_offset`, `row_count`, `byte_count`, and `url`; the root counts
describe all pages. Pages are zero-based, ordered and contiguous. Their sizes
are server-selected, not extra query clauses. The same query `limit` can span
many pages: a page boundary is neither query truncation nor a new execution.

```ts
const submitted = await client.submit(
  { data_source_id: "sales-v7", query: { measures: ["revenue"], limit: 1000 } },
  { waitSeconds: 5 },
);
const completed = await client.wait(submitted);
const firstPage = await client.readPage(completed, 0);
const pageCount = completed.result.pages?.length ?? 1;
// Read another page only when the application requests it.
if (pageCount > 1) {
  const secondPage = await client.readPage(completed, 1);
}
```

`readPage` checks its selected page against configured row/byte limits, even
when the full result exceeds them. It returns only after validating that
page's Arrow schema, identity and counts. A single-stream result is page zero.
Explicitly rereading a page before expiry is allowed; automatic retries,
offset-based query reruns, cursor APIs and link renewal are not implemented.
The complete page manifest must fit the existing 1 MiB control-response limit.

`readBatches` traverses every page in order without prefetching, while `execute`
returns one combined table. Both enforce aggregate limits and physical-schema
consistency across pages. Reading only selected pages proves nothing about
unread pages or cross-page schema consistency. Each page's Arrow metadata
includes `page_index` and `row_offset`; individual batches preserve those
coordinates. The combined table's schema metadata describes the full result
and omits page coordinates. Original batch buffers are not converted.

### Configuration

| `ClientOptions` | Meaning |
| --- | --- |
| `baseUrl` | HTTP API base URL; HTTPS required except explicit localhost testing. |
| `fetch` | Optional Fetch-compatible transport. It must preserve redirect refusal, cancellation, decoded response bytes, and header/credential behavior. |
| `getAccessToken` | Optional `() => string \| Promise<string>`; supplies a bearer token, not a login flow. |
| `allowedResultOrigins` | Explicit cross-origin result allowlist. No account token or cookies are sent to those origins. |
| `allowInsecureLocalhost` | Explicit local-test exception; not a production TLS bypass. |
| `pollIntervalMs`, `timeoutMs` | Defaults: 250 ms poll spacing and 60,000 ms operation deadline. `execute` shares one deadline across submission, polling, and download. A local deadline is not server cancellation. |
| `maxResultBytes` | Decoded IPC admission limit, default 64 MiB; whole-result operations use totals, `readPage` uses its selected page. Not a JavaScript heap-size guarantee. |
| `maxResultRows` | Row limit, default 1,000,000; whole result or selected page as above. Exceeding it is an error, not silent truncation. |

Per-request `signal` supports local abort. `SubmitOptions.waitSeconds` controls
the submission's `Prefer: wait=N`. The client checks the protocol range
`0..60`; the server enforces its advertised lower `max_wait_seconds` and
binding support. `getCapabilities` and `getBinding` are explicit validated
reads, not a cached preflight policy. Submission performs no automatic discovery
or cached capability/binding enforcement. Applications can use explicit
discovery results to choose a supported shape and wait preference.
`WaitOptions.onProgress` receives execution observations.
`ExecuteOptions.allowTruncated` explicitly accepts a server-declared truncated
materialization without waiving count, identity, framing, or resource checks.
Control-plane JSON responses and individual IPC metadata messages have separate
fixed 1 MiB admission caps; increasing `maxResultBytes` does not raise those caps.
Before Arrow decoding, the client checks FlatBuffer table, vector and string
bounds, flat-schema node/buffer counts, and unsupported nested encodings.
Repeated metadata references also count against the expanded-metadata budget
of `maxResultBytes` per message; valid but unusually expanded metadata can
therefore be rejected with `RESULT_LIMIT_EXCEEDED`.
The client retains at most 64 most-recently observed executions
(`MAX_TRACKED_EXECUTIONS`) for cross-call lifecycle validation. Older history is
evicted; `forgetExecution` can release it sooner. An active `wait` keeps its own
previous observation even after eviction. Server immutability is still required,
but the client cannot compare forgotten history. Overlapping polls cannot
replace a newer cancellation observation with an older pending state; identity
checks and immutable terminal-response checks still apply.

The wire `Query` is either:

```ts
// Aggregation shape: at least one nonempty dimensions/measures projection.
const aggregate = {
  data_source_id: "sales-v7",
  query: {
    dimensions: ["customers.region"],
    measures: ["revenue"],
    limit: 100,
  },
};

// Scalar shape: fields, without dimensions, measures, or having.
const scalar = {
  data_source_id: "sales-v7",
  query: {
    fields: ["orders.order_id", "orders.amount"],
    where: ["orders.amount > 0"],
    limit: 100,
  },
};
```

These are structural examples against a hypothetical binding, not executable
semantic fixtures. The server must authorize and evaluate the query correctly.
Both shapes support `order_by` entries with `field`, optional `ASC`/`DESC`
`direction`, and optional `FIRST`/`LAST` `nulls`. Structurally invalid queries
are refused locally; the server enforces advertised shape support.
The client does not repair the query or substitute a native query.

## What result validation means

- Result URLs exist only on `succeeded`. Each identifies one immutable,
  self-contained Arrow IPC **stream**, not live rows produced during execution.
  A result uses one URL or an ordered page manifest, never both.
- The stream's `org.apache.ossie.execution` JSON metadata binds contract version,
  execution, result, data source, and model revision. Each field's
  `org.apache.ossie.column` JSON metadata binds its `column_id`.
  Paged streams also bind the page index and row offset to prevent unnoticed
  page substitution; unpaged streams omit both.
- The manifest supplies authoritative semantic descriptors. Field ids are
  unique; names may duplicate. Field order, names, nullability, ids, and physical
  versus logical types must agree.
- Supported physical types are flat, nondictionary `Utf8`; signed/unsigned
  integers 8–64 bit; `Float32/64`; `Bool`; `Decimal128/256`; `Date32` days;
  `Time32` seconds/milliseconds; `Time64` microseconds/nanoseconds; and timestamps
  with or without timezone. No-timezone timestamps are civil date-times, not
  inferred UTC instants. Unsupported, nested, opaque, dictionary, or
  Arrow-buffer-compressed payloads are refused. Streams use little-endian V4/V5
  metadata and terminate at a valid EOF boundary or Arrow end-of-stream marker.
- Arrow `RecordBatch`/`Table` objects preserve exact physical buffers and type
  metadata. The package performs no row-object, `Number`, or `Date` conversion.
  This is not a guarantee about every Arrow convenience accessor:
  a timestamp vector's `get()` may convert nanoseconds to a millisecond
  JavaScript number and lose precision. Use the typed `.data` values buffers
  with their declared units when exact temporal values are required.
  Convenience conversions to `Number`/`Date` are the caller's choice and can
  also lose 64-bit integer or decimal precision.
- `row_count` and `byte_count` are exact counts of the delivered materialization.
  Bytes are counted **after HTTP decompression**, including IPC framing.
  HTTP compression is distinct from unsupported Arrow buffer compression.
  Counts are not cryptographic checksums.
- `readBatches` can yield before a late framing/count/network failure. Only
  successful exhaustion of all pages validates the whole result. Early
  termination leaves the result unverified. Do not present a consumed prefix
  or selected page as the complete result.
- `execute` buffers within configured limits and returns only after validation.
  A complete result differs from a declared truncated result; the latter must
  include `truncation_reason`, even when explicitly accepted by the caller.

## Errors and operational boundaries

HTTP success is not query success. `submit`, `getExecution`, and `cancel` expose
terminal failures and cancellation for inspection. `wait` and `execute` turn
them into errors. Foundation codes are retained directly in `error.code`;
non-2xx responses carry RFC 9457 `ProblemDetails`.
Server error details and replacement queries are untrusted advisory data.
There is no automatic repair, retry, resubmission, or native-query fallback.
`QueryExecutionError.error` retains the server query error;
`HttpProblemError.problem` retains the problem document. Other exported classes
include `ExecutionCancelledError`, `ProtocolError`, `TimeoutError`, and their
base `OssieError`. Local abort uses `OssieError` code `ABORTED`; result-bound
failures use `RESULT_LIMIT_EXCEEDED` or `TRUNCATED_RESULT`.
Other local codes include `RESULT_EXPIRED`, `AUTHENTICATION_ERROR`, and
`TRANSPORT_ERROR`.
Structured error properties may contain sensitive server
data even though the client-generated messages avoid result URLs.

Cancellation can race completion: `202` means pending, not confirmed cancelled.
Request abort and timeout stop local work; use `cancel` to request server-side
cancellation. An aborted submission may have executed without returning its id.
Do not resubmit blindly.

Result URLs must be absolute; relative references are not supported.
The target is subject to the same HTTPS and origin policy.
All page URLs share RFC 3339 `expires_at`; there is no link renewal, byte-range
resume, or re-execution fallback. Explicit page rereads are allowed until
expiry. Redirects are refused. Signed URLs
are credentials: do not log complete executions, manifests, raw error bodies,
or access tokens. Cross-origin result access requires an explicit origin
allowlist and omits API account credentials.
All client Fetch calls use `credentials: "omit"`, including same-origin API
calls. The optional bearer `Authorization` header is attached explicitly to
same-origin requests; it is not an ambient cookie credential.

For browser use, configure API and result-host CORS for approved app origins:
allow the used `GET`/`POST`/`OPTIONS` methods and
`Authorization`/`Content-Type`/`Accept`/`Prefer` request headers. Expose
`Content-Type`, `Content-Length`, and `Content-Encoding` when needed for
diagnostics. CORS is not authorization. A separately deployed token broker or
backend-for-frontend can keep long-lived credentials off the browser; neither
is supplied here. All operations and result delivery remain subject to the
same caller's authorized model and server-enforced RLS.

## Local development

From the repository root, using PowerShell:

```powershell
Set-Location .\typescript\execution
npm ci
npm run build
npm test
```

`npm run check` runs the same build-and-test path as `npm test`. The package is
private and uses Apache Arrow JavaScript `21.2.0`. `npm test` includes the
esbuild browser-bundle check but does not require a browser executable.
Tests and a successful bundle validate only exercised client/protocol behavior,
not a real service integration or query engine.

`npm run test:browser` runs the separate real-browser smoke, which bundles the public entry point with esbuild
and exercises synthetic loopback HTTP in an installed Chromium, Chrome, or
Edge browser. From the package directory, after building:

```powershell
npm run test:browser
```

The harness uses Node.js 22+'s built-in Fetch/WebSocket support, discovers common
browser installations, and fails rather than skips if none is found.
Set `OSSIE_BROWSER_BINARY` to an installed executable when discovery is
insufficient; an invalid explicit value also fails. For example:

```powershell
$env:OSSIE_BROWSER_BINARY = "C:\Program Files\Google\Chrome\Application\chrome.exe"
npm run test:browser
```

This test installs no browser or browser framework. Its fixture server is
test infrastructure, not a delivered execution server or vendor adapter.

## Feature comparison

This table compares **named interfaces**, not entire products. Documentation
was checked on 2026-10-11. “Arrow” does not imply that a product's REST endpoint
uses Arrow, or that two systems implement the same query semantics.
**This package contains no adapters for any endpoint below.**

| Interface | Submission/lifecycle | Result representation | Difference from this package |
| --- | --- | --- | --- |
| **This draft/client prototype** | Structured Ossie JSON; bounded wait, handle/poll/cancel | Single stream or indexed immutable Arrow pages; per-page identity/count checks, full-result schema/total checks | Local protocol implementation only; no query server or vendor semantic proof. |
| [Databricks Statement Execution][databricks] | SQL over HTTP; bounded synchronous wait, handles, polling, cancellation, manifests | `ARROW_STREAM` **only with `EXTERNAL_LINKS`**; inline supports `JSON_ARRAY`, not Arrow | Closest lifecycle/download prior art; additional formats and fresh link resolution remain beyond this client. External downloads must omit account authorization. |
| [dbt Semantic Layer GraphQL][dbt-graphql] | `createQuery`, then poll `query` with the id | Paginated base64-encoded Arrow in `arrowResult`; JSON is also available | Semantic query API, but different request schema and delivery encoding. [Its JDBC interface][dbt-jdbc] separately uses Arrow Flight SQL. |
| [Snowflake SQL API][snowflake-sql] | SQL HTTP submission, status handles, cancellation | Partitioned JSON `ResultSet`/`jsonv2` | Not an Arrow REST result API in the cited contract. [Python connector Arrow fetch APIs][snowflake-python] are a separate connector surface. |
| [Power BI REST Execute Queries][powerbi-rest] | DAX request against a semantic model | JSON; limits include 100,000 rows or 1,000,000 values, 15 MB; some limited-data/error responses are HTTP `200` | No Arrow result in this REST contract. Applications must inspect the body, not just status. |
| [Power BI/Analysis Services XMLA with ADOMD][xmla] | A separate Analysis Services client/protocol surface | XMLA text or binary XML, with transport compression options | Binary XML is not evidence of Arrow. Arrow on this path has not been verified. |
| [Cube REST API][cube] | Cube JSON semantic query, including `/v1/load` | JSON data plus annotations such as title, short title, and type | Prior art for semantic result descriptors; its JSON “columnar” layout is not Arrow IPC. |
| [Arrow Flight / Flight SQL][flight] | Arrow-oriented RPC; Flight SQL adds a SQL service protocol | Arrow record batches with tickets/endpoints | A distinct transport, not implemented by this browser HTTP client. A future mapping requires separate design. |
| [ADBC][adbc] | Database driver API with statements and driver-dependent capabilities | Arrow-oriented result consumption | An API abstraction, not an HTTP wire protocol or implemented adapter here. |

The [existing Ossie REST discussion][rest-thread] addresses model/catalog
interchange; [Polaris #4961][polaris] implements semantic-model CRUD.
Neither establishes this execution endpoint. The draft narrows familiar
patterns into a proposed browser contract; it does not claim novel transport
mechanisms or parity with a native vendor client.

[mcp]: https://github.com/apache/ossie/pull/529
[foundation]: https://github.com/apache/ossie/pull/246
[databricks]: https://docs.databricks.com/api/statement-execution/v1/execute-statement
[dbt-graphql]: https://docs.getdbt.com/docs/dbt-apis/sl-graphql
[dbt-jdbc]: https://docs.getdbt.com/docs/dbt-apis/sl-jdbc
[snowflake-sql]: https://docs.snowflake.com/en/developer-guide/sql-api/handling-responses
[snowflake-python]: https://docs.snowflake.com/en/developer-guide/python-connector/python-connector-api
[powerbi-rest]: https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries
[xmla]: https://learn.microsoft.com/en-us/analysis-services/instances/connection-string-properties-analysis-services
[cube]: https://docs.cube.dev/reference/core-data-apis/rest-api/reference
[flight]: https://arrow.apache.org/docs/format/Flight.html
[adbc]: https://arrow.apache.org/adbc/current/format/specification.html
[rest-thread]: https://lists.apache.org/thread/xvpomkdvmgv1m5krprq590l8mzccks5n
[polaris]: https://github.com/apache/polaris/pull/4961
