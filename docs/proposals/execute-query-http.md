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

# Ossie HTTP execution — DRAFT 0.1

**Status:** proposal `0.1-draft`, not an adopted Apache Ossie standard.
The associated TypeScript package is a client and local protocol-validation
prototype, not a server, semantic engine, or vendor adapter.

This proposal binds structured Ossie query submission and status to HTTP/JSON,
and completed tabular results to Apache Arrow IPC streams. It targets browser
applications without requiring gRPC. It does not define a new query language.
It is a complementary access binding, not a replacement for Flight SQL or
ADBC. Engines may retain those interfaces behind an HTTP adapter; Layer 3
interpretation remains a separate semantic responsibility.

## 1. Normative basis and scope

The words **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** have the
meaning given in [BCP 14](https://www.rfc-editor.org/rfc/rfc8174).

The following proposal revisions are pinned; both upstream pull requests remain
open, and their inclusion here does not imply adoption:

| Dependency | Pinned revision |
| --- | --- |
| [MCP proposal #529](https://github.com/apache/ossie/pull/529), query profile `0.4-draft` | `b5418ee3aa19f117a1c464733de21f5a0dc0326b` |
| [Foundation proposal #246](https://github.com/apache/ossie/pull/246) | `cc0d07099a3ef31e85af1fd93458a52186fae2c6` |

[execution.schema.json](execution-http/execution.schema.json) is the normative
JSON Schema (draft 2020-12).
[openapi.json](execution-http/openapi.json) describes the HTTP operations using
references to those definitions, not a second set of object schemas. These
documents specify structure; the rules below additionally specify behavior.

The lowercase `expression`, `projection`, `predicate`, and `query` definitions
are copied unchanged from the [pinned MCP query schema][query-schema].
The public `Query` definition adds only a portable integer bound to `limit`.
All wire property names are case-sensitive lower snake case; schema definition
names such as `Execution` are not wire keys.

In scope: one read-only query, immutable model binding, bounded synchronous wait,
polling, cancellation, errors, and one materialized result delivered as a single
stream or immutable pages. Out of scope:
model/catalog CRUD, native-query escape, query compilation, semantic evaluation,
sessions, transactions, mutations, SSE/progress subscriptions, multi-result or
live-result delivery, cursor APIs, result-link renewal, and automatic repair or
re-execution.
Flight is a possible future mapping, not implemented or negotiated by this draft.

### HTTP versions and transport behavior

This is a binding to [HTTP semantics](https://www.rfc-editor.org/rfc/rfc9110),
not an HTTP/1.1-only protocol. The same methods, status codes, headers, JSON
objects, and Arrow IPC payloads can be carried over HTTP/1.1, HTTP/2, or
HTTP/3 where the client, server, and intermediaries support them. The profile
does not require every deployment to support every HTTP version.
The `HTTP/1.1` request/response examples below use readable textual notation;
they do not require that wire framing for HTTP/2 or HTTP/3.

Transport-version negotiation belongs to the HTTP stack and deployment, not
to the Ossie query or capability schema. The browser Fetch client does not
select or force a version. HTTPS and the authorization, origin, redirect,
expiry, and completeness rules apply regardless of the negotiated version.

HTTP/2 and HTTP/3 support multiplexed streams, but multiplexing alone does not
supply Flight's endpoint/ticket conventions, distributed-result coordination,
or application-level flow control. The prototype traverses result pages
sequentially without prefetching. Pull-based consumption through Web Streams
does not guarantee end-to-end backpressure or bounded buffering in browsers,
proxies, servers, or query engines. No Flight performance-parity claim is made.
Current integration fixtures use loopback HTTP/1.1; they do not establish
HTTP/2 or HTTP/3 deployment interoperability.

## 2. JSON objects and discovery

JSON messages MUST conform to their named schema definitions. Unknown core
properties MUST be rejected. Optional `extensions` values are namespaced JSON
objects; clients MAY ignore unfamiliar namespaces, which MUST NOT change core
meaning or relax required validation. Unknown feature identifiers confer no
capability. Core text fields and extension namespace keys MUST be nonblank when
present; arbitrary text inside opaque extensions is not subject to that rule.
Extension keys SHOULD use reverse-DNS naming to avoid collisions; domain-style
syntax is a naming convention, not an additional schema/client validity check.

Identifiers are opaque, nonblank strings. Clients MUST encode identifiers as a
single URL path segment, preserving an embedded slash as an encoded component.
Clients MUST reject `.` and `..` where URL normalization would change the path.
Model identity consists of `id` and `revision`;
`revision` is an opaque immutable revision, not necessarily a content hash.
JSON counters (`limit`, `page_index`, `row_offset`, `row_count`, `byte_count`) MUST be integers in
`0..9007199254740991`, inclusive. JavaScript clients MUST reject values outside
that range rather than round them. This binding restriction is intentionally
stricter than the pinned MCP schema's unbounded nonnegative `limit`.

`GET /capabilities` returns `Capabilities` for the current caller:

```json
{
  "contract_version": "0.1-draft",
  "foundation_revision": "cc0d07099a3ef31e85af1fd93458a52186fae2c6",
  "query_schema_version": "mcp-0.4-draft",
  "result_formats": ["arrow-stream"],
  "query_shapes": ["aggregation", "scalar"],
  "features": [],
  "max_wait_seconds": 10
}
```

The three version values MUST match this draft exactly. `query_shapes` is a
nonempty unique list; `features` is a possibly empty unique list. These advertise
support, not semantic-conformance certificates. `max_wait_seconds` MUST be an
integer from 0 through 60.

`GET /bindings/{id}` returns a `Binding`; `{id}` is its `data_source_id`:

```json
{
  "data_source_id": "sales-v7",
  "model": { "id": "sales", "revision": "7" },
  "query_shapes": ["aggregation", "scalar"],
  "features": []
}
```

A binding id MUST identify exactly one model revision throughout its lifetime
and MUST NOT be reassigned. Publishing a new revision requires a new binding
id. Executions and results MUST retain that identity. Binding support MUST NOT
exceed service capabilities; servers MUST reject shapes unsupported by either
the service or binding. Clients SHOULD discover both before selecting a query
shape. The binding is not the model document: models are obtained separately.

## 3. HTTP operations

Paths are relative to a deployment-selected base URL. Control requests and
successful responses use `application/json`. Servers MUST NOT redirect these
operations. A non-2xx response MUST use `application/problem+json` with
`ProblemDetails`; this draft uses 4xx/5xx for failures, not redirects.

| Operation | Successful response |
| --- | --- |
| `GET /capabilities` | `200 Capabilities` |
| `GET /bindings/{id}` | `200 Binding` |
| `POST /queries` with `Request` | `200 Execution` if terminal; `202 Execution` if pending |
| `GET /queries/{id}` | `200 Execution` in any state |
| `POST /queries/{id}/cancel`, no body | `200 Execution` if terminal; `202 Execution` if pending |
| `GET` the successful manifest's `url` or a `pages[i].url` | `200 application/vnd.apache.arrow.stream` |

### Submission

`Request` contains only `data_source_id` and `query`. An aggregation query has
at least one nonempty `dimensions` or `measures` projection and no `fields`.
A scalar query has nonempty `fields` and no `dimensions`, `measures`, or
`having`. Both support `where`, `order_by`, and `limit`; only aggregation
supports `having`. Predicates are a nonblank expression or a nonempty list of
nonblank expressions. Order direction is `ASC`/`DESC`; null ordering is
`FIRST`/`LAST`. Interpretation and omitted-clause defaults belong to the pinned
query proposals, not to HTTP or this client's shape validator.

```http
POST /queries HTTP/1.1
Content-Type: application/json
Accept: application/json
Prefer: wait=5

{"data_source_id":"sales-v7","query":{"dimensions":["customers.region"],"measures":["revenue"],"order_by":[{"field":"revenue","direction":"DESC","nulls":"LAST"}],"limit":100}}
```

Scalar request:

```json
{
  "data_source_id": "sales-v7",
  "query": {
    "fields": ["orders.order_id", "orders.amount"],
    "where": ["orders.amount > 0"],
    "limit": 100
  }
}
```

The optional preference is `Prefer: wait=N`, where `N` is an integer from 0
through 60, no greater than `max_wait_seconds`. An omitted preference means
`wait=0`. Unsupported or malformed values MUST be rejected, not silently
treated as a longer wait. The server MAY return earlier. It MUST NOT hold the
POST beyond `N` seconds waiting for execution completion. It returns `200` if
terminal or `202` if pending, without cancelling the execution at wait expiry.
Clients MAY discover/cache the advertised bound or validate only the protocol
range locally and rely on the server to enforce its advertised maximum.
The TypeScript prototype does not automatically preflight capabilities or
bindings and does not cache discovery results for submission-time enforcement.
Its explicit getters return validated data; applications can use that data to
choose requests, while the server enforces its advertised restrictions.
`200` does not mean the query succeeded: inspect `state`.
No submission idempotency key or automatic retry is defined. An interrupted
submission can leave an execution running even if the client did not receive
its id; blindly resubmitting can create a second execution.

### Bounded wait versus synchronous and asynchronous execution

There is always **one execution job**, not separate synchronous and asynchronous
query modes. `Prefer: wait=0` returns immediately with `202` unless the execution
is already terminal, in which case it returns `200`. `Prefer: wait=N` holds the
POST for at most `N` seconds: fast completion returns terminal `200`; otherwise
the server returns pending `202` and the client polls **the same execution id**.
Terminal `200` can mean `succeeded`, `failed`, or `cancelled`.

Unbounded synchronous waiting risks long-held connections and intermediary
timeouts; pure asynchronous submission typically needs extra polling round trips.
Bounded wait optimizes the fast path without requiring a long-lived POST.
The wait preference is neither an execution deadline nor a result-download
timeout, and its expiry does not cancel the job. A JavaScript `execute()` promise
is asynchronous independently of this HTTP choice: it can hide polling until
that same job finishes, then download and validate its result.

### Lifecycle and cancellation

`Execution` always carries `contract_version`, `execution_id`,
`data_source_id`, `model`, `state`, and `warnings` (an array, possibly empty).
Its identity MUST remain unchanged across observations.

| Current state | Permitted next states |
| --- | --- |
| `queued` | `running`, `cancelling`, `succeeded`, `failed`, `cancelled` |
| `running` | `cancelling`, `succeeded`, `failed`, `cancelled` |
| `cancelling` | `succeeded`, `failed`, `cancelled` |
| `succeeded`, `failed`, `cancelled` | No different state |

Repeated observations of the same state are allowed. Terminal representations,
including result manifests, MUST be immutable. Only pending states MAY carry
`progress`, with optional `fraction` in `[0,1]` and optional `phase`.
Progress is advisory, not evidence of result completeness.

```json
{
  "contract_version": "0.1-draft",
  "execution_id": "q-42",
  "data_source_id": "sales-v7",
  "model": { "id": "sales", "revision": "7" },
  "state": "running",
  "warnings": [],
  "progress": { "fraction": 0.25, "phase": "executing" }
}
```

Only `succeeded` carries `result`; only `failed` carries `error`. Neither field
is allowed while pending or cancelled. Terminal responses MUST NOT carry
`progress`. Clients MUST poll with `GET /queries/{id}` to observe completion;
they SHOULD space requests and impose an application-selected deadline.

Cancellation is best-effort and idempotent for a given execution.
`POST /queries/q-42/cancel` MAY return a terminal result if completion wins
the race, or `202` with `cancelling` or another observed pending state.
A client MUST NOT interpret acceptance as confirmed cancellation.
Aborting a request, stopping an iterator, or reaching a client timeout MUST
NOT be represented as a server-side cancellation; cancellation requires the
explicit endpoint.

### Errors

`QueryError` contains `code`, `message`, `retryable`, and optional
`replacement_query` and `extensions`. A [Foundation error code][foundation-semantics] MUST appear
directly in `error.code`, without a transport-specific prefix or replacement
taxonomy. Unknown codes MUST remain inspectable. This draft does not pretend
the Foundation enumerates transport, authorization, or local client errors.

```http
HTTP/1.1 422 Unprocessable Content
Content-Type: application/problem+json

{"type":"about:blank","title":"Query cannot be resolved","status":422,"error":{"code":"E_AMBIGUOUS_PATH","message":"More than one model relationship path applies.","retryable":false}}
```

`ProblemDetails.status` MUST equal the HTTP status. A failed execution returned
in a successful HTTP response carries the same `QueryError` shape in `error`.
`retryable` and `replacement_query` are advisory: clients MUST NOT automatically
repair, retry, or re-execute queries. They MUST NOT fall back to SQL, DAX, or
another native query. The package's low-level methods return failed/cancelled
executions for inspection; `wait` and `execute` throw for those states.

## 4. Results and Arrow contract

A successful result is one immutable materialization, available only after
execution succeeds. Its `ResultManifest` MUST provide **exactly one** delivery
form: `url` for one self-contained Arrow IPC stream, or a nonempty `pages` array
of `ResultPage` descriptors, each pointing to a self-contained Arrow IPC stream.
Downloading batches or pages is not streaming live rows from a running query.
No Arrow file container, base64 JSON, range retrieval, multi-stream concatenation,
or external dictionary dependency is permitted.

Illustrative successful `Execution` (the counts describe a hypothetical
payload, not an executable semantic fixture):

```json
{
  "contract_version": "0.1-draft",
  "execution_id": "q-42",
  "data_source_id": "sales-v7",
  "model": { "id": "sales", "revision": "7" },
  "state": "succeeded",
  "warnings": [],
  "result": {
    "result_id": "r-42",
    "format": "arrow-stream",
    "url": "https://api.example.test/results/r-42",
    "row_count": 2,
    "byte_count": 1536,
    "completeness": "complete",
    "expires_at": "2026-10-12T00:00:00Z",
    "columns": [
      {"column_id":"c-1","name":"region","logical_type":"String","nullable":true,"role":"dimension","model_ref":"customers.region"},
      {"column_id":"c-2","name":"revenue","logical_type":"Decimal","nullable":true,"role":"measure","model_ref":"revenue","label":"Revenue","unit":"currency","currency":"USD"}
    ]
  }
}
```

`row_count` and `byte_count` are required exact counts of the delivered
materialization, including when truncated. `byte_count` includes all IPC
framing, metadata, padding, and record batches **after HTTP decompression**.
For paged delivery these are whole-result totals: bytes are summed across pages,
including every repeated schema and each page's framing.
HTTP `Content-Length` may describe compressed transfer bytes and is not a
substitute. Counts detect length/count mismatches; they are not checksums or
cryptographic integrity guarantees.

`completeness` MUST be `complete` or `truncated`. A truncated result MUST include
a nonblank `truncation_reason`; a complete result MUST omit it. A server-imposed
row/byte/time cap MUST NOT be disguised as a complete result. An explicit query
`limit` defines the requested result, so satisfying it is not by itself
truncation. Partial rows MUST NOT be emitted. `expires_at` is RFC 3339 and applies
to the whole result; result URLs, including every page URL, MUST NOT be used at
or after expiry. This draft offers no renewal or automatic re-execution. Expiry or
authorization failure does not change terminal state.

### Immutable result pagination

The server chooses page sizes and a fixed partition of the completed result.
There is no new query clause, requested page size, or query offset: `limit`
remains a semantic cap on the requested result, not a page size. Pagination
does not re-execute a query with arbitrary SQL `OFFSET`, define a cursor API,
or supply a server-side query engine.

The entire immutable page manifest MUST be returned with the successful
execution. A client can select a descriptor by index or traverse the array in
order; fetching pages MUST NOT create new executions or change that partition.
The complete manifest remains subject to the client's 1 MiB control-JSON limit;
pagination does not provide a cursor-based fallback for an oversized manifest.

Each `ResultPage` is a closed object with exactly `page_index`, `row_offset`,
`row_count`, `byte_count`, and `url`. All counters are safe nonnegative integers.
For a manifest with `N` pages:

- `page_index` MUST equal the descriptor's array position, from `0` to `N-1`.
- The first `row_offset` MUST be `0`; every later offset MUST equal the preceding
  offset plus its row count. Offsets describe this materialization, not a query
  clause or re-execution instruction.
- Descriptor row and decoded-byte counts MUST sum exactly to the root
  `row_count` and `byte_count`, respectively.
- A zero-row page is forbidden except as the sole page of a zero-row result.
  Every page MUST have a positive `byte_count`, including that empty result's
  self-contained schema.

These contiguity, sum, and empty-page rules are normative runtime invariants,
not expressible by the descriptor's structural JSON Schema alone.

Illustrative paged `Execution` (hypothetical payload counts):

```json
{
  "contract_version": "0.1-draft",
  "execution_id": "q-43",
  "data_source_id": "sales-v7",
  "model": { "id": "sales", "revision": "7" },
  "state": "succeeded",
  "warnings": [],
  "result": {
    "result_id": "r-43",
    "format": "arrow-stream",
    "pages": [
      {"page_index":0,"row_offset":0,"row_count":1,"byte_count":1024,"url":"https://api.example.test/results/r-43/pages/0"},
      {"page_index":1,"row_offset":1,"row_count":1,"byte_count":1024,"url":"https://api.example.test/results/r-43/pages/1"}
    ],
    "row_count": 2,
    "byte_count": 2048,
    "completeness": "complete",
    "expires_at": "2026-10-12T00:00:00Z",
    "columns": [
      {"column_id":"c-1","name":"region","logical_type":"String","nullable":true,"role":"dimension","model_ref":"customers.region"},
      {"column_id":"c-2","name":"revenue","logical_type":"Decimal","nullable":true,"role":"measure","model_ref":"revenue","label":"Revenue","unit":"currency","currency":"USD"}
    ]
  }
}
```

`complete` or `truncated` describes the entire materialization, not an individual
page or the subset a user has browsed. Browsing selected pages is not verified
complete-result consumption, even when the manifest says `complete`.
Applications MAY explicitly read an immutable page again before expiry; this
is not an automatic network retry. A failed download MUST NOT automatically
resubmit the query, renew links, or retry the network operation.

The existing single-stream `url` form remains supported unchanged alongside
pages in this unreleased `0.1-draft` profile. Clients implementing this draft
MUST support both forms without an additional capability feature token.
Servers can retain the `url` form for interoperability with older
single-stream-only clients; those older clients must not be assumed to support
a paged manifest.

### Identity and semantic descriptors

The Arrow schema MUST contain UTF-8 JSON metadata at
`org.apache.ossie.execution`, conforming to `ExecutionMetadata`:

```json
{
  "contract_version": "0.1-draft",
  "execution_id": "q-42",
  "result_id": "r-42",
  "data_source_id": "sales-v7",
  "model": { "id": "sales", "revision": "7" }
}
```

For paged delivery, `ExecutionMetadata` MUST additionally contain `page_index`
and `row_offset` together, matching the selected descriptor. Both fields MUST
be absent for single-stream `url` delivery. For example, page 1 above carries:

```json
{
  "contract_version": "0.1-draft",
  "execution_id": "q-43",
  "result_id": "r-43",
  "data_source_id": "sales-v7",
  "model": { "id": "sales", "revision": "7" },
  "page_index": 1,
  "row_offset": 1
}
```

The paired fields bind an Arrow stream to its page and reject a response that
otherwise identifies the right result but contains the wrong page.

Each field MUST contain UTF-8 JSON metadata at `org.apache.ossie.column`,
conforming to `ColumnMetadata`, for example `{"column_id":"c-2"}`.
These identities MUST equal the execution and manifest identities.
`column_id` values MUST be unique within the result. Display/physical names
MAY repeat, so clients MUST NOT key results only by name.

The manifest is authoritative for semantic descriptors: `logical_type`,
optional `role`, `model_ref`, `label`, `description`, `unit`, `currency`, and
`extensions`. Its nonempty columns array MUST match Arrow field count, order, names,
nullability, ids, and the physical/logical mapping below. Labels or duplicate
names MUST NOT change field identity. Clients MUST reject mismatches rather
than infer a replacement schema.

Every page MUST repeat the same self-contained schema: identical field names,
order, ids, nullability, actual physical types, and field metadata. Schema
metadata MUST also remain identical except for the two page coordinates in
`org.apache.ossie.execution`. Choosing another permitted physical type for the
same logical type on a later page is forbidden: integer widths, decimal
precision/scale, and timestamp unit/timezone, for example, MUST remain fixed.
Consumers traversing pages MUST reject physical-schema drift.

### Baseline physical types

| Manifest `logical_type` | Permitted Arrow physical types |
| --- | --- |
| `String` | `Utf8` |
| `Integer` | Signed or unsigned `Int8`, `Int16`, `Int32`, `Int64` |
| `Float` | `Float32`, `Float64` |
| `Boolean` | `Bool` |
| `Decimal` | `Decimal128`, `Decimal256`, retaining declared precision and scale |
| `Date` | `Date32`, day unit |
| `Time` | `Time32` second/millisecond; `Time64` microsecond/nanosecond |
| `DateTime` | Timestamp without a timezone: civil date-time, not an inferred UTC instant |
| `DateTimeTz` | Timestamp with a nonempty timezone: instant, preserving timezone metadata |

Timestamp units MAY be second, millisecond, microsecond, or nanosecond.
The baseline is flat, nondictionary, uncompressed little-endian IPC with V4 or
V5 metadata. A stream MAY terminate at a valid message boundary by EOF or use
the Arrow end-of-stream marker. Nested, opaque/extension,
dictionary-encoded, compressed-buffer, or other unlisted physical types MUST be
refused rather than guessed or converted. Ordinary HTTP content compression
MAY be used independently of the prohibited Arrow IPC buffer compression.

Consumers MUST preserve Arrow values and physical type information, including
64-bit integers, decimal precision/scale, timestamp units, and nulls.
The TypeScript package exposes `RecordBatch`/`Table`; it does not convert to
JavaScript row objects, `Number`, or `Date`. This guarantee concerns physical
buffers and type metadata, not every Arrow convenience accessor. In particular,
timestamp vector `get()` may convert nanoseconds to a millisecond JavaScript
number. Exact consumers must inspect typed `.data` values buffers with their
declared units; later convenience conversions are outside this guarantee.

### Completion and resource limits

Consumers MUST validate framing, required metadata, field mapping, and final
row/decoded-byte counts for every downloaded stream. For paged delivery,
per-page counts and page identity MUST match the selected descriptor; full
consumption also requires all pages, a consistent physical schema, and exact
aggregate totals. Batches yielded before complete-result validation are
**provisional**. A later decode, count, truncation, or network error means the
consumer has not obtained a verified complete result; applications MUST discard
or explicitly label previously consumed partial data.

Clients MAY impose local admission limits and MUST fail explicitly rather than
silently truncate when those limits are exceeded. A client accepting a declared
truncated result MUST still enforce identity, schema, count, and framing rules.
Selected-page consumption MUST NOT be represented as whole-result validation.
Prototype-specific defaults, memory policies, and public library methods are
documented in the non-normative implementation notes below; they are not
requirements to implement this HTTP binding.

## 5. Authorization and browser deployment

Every operation MUST authorize the same caller against the same immutable
model binding. Server-side policy, including row-level security and isolation
of execution handles and result objects, remains a server obligation.
The client and schema do not prove authorization or RLS correctness.

All URLs MUST use HTTPS except explicit local testing. The manifest's `url`
and every page's `url` MUST be absolute; relative references are not supported
in this draft. Each MUST contain no userinfo or fragment and MUST NOT redirect.
Every page is subject to the same caller authorization, origin checks, and
whole-result expiry protections as single-stream delivery. Same-origin
result requests MAY use the API bearer token. Cross-origin results require an
explicit client origin allowlist and MUST omit account authorization and
cookies. Origins MUST be compared structurally, not by hostname suffix or URL
prefix. Signed result URLs are bearer secrets: diagnostics, logs, and error
messages MUST NOT expose them. Query/error text and extension metadata are
untrusted data, not executable instructions.
The TypeScript client uses Fetch `credentials: "omit"` for every request,
including same-origin API requests; an explicitly attached same-origin bearer
header is separate from ambient browser credentials.

A browser deployment MUST configure both API and result hosts for its approved
application origins. Preflight MUST permit `GET`, `POST`, and `OPTIONS` and the
used `Authorization`, `Content-Type`, `Accept`, and `Prefer` headers.
Responses SHOULD expose `Content-Type`, `Content-Length`, and
`Content-Encoding` for diagnostics; no custom response header is required to
discover an execution or result. CORS is not authorization.

The package accepts an access-token callback; it does not acquire identities
or implement login. A deployment MAY provide a trusted token broker or a
same-origin backend-for-frontend that authenticates the user and performs the
upstream calls. Long-lived service credentials MUST NOT be embedded in browser
bundles. A broker/proxy must preserve the wire and caller-isolation contract;
none is implemented here.

## 6. Relationship to existing work

The existing [Ossie REST discussion][rest-thread] concerns model/catalog
interchange rather than this execution binding; [Polaris #4961][polaris]
concerns model CRUD integration, not an Ossie query execution endpoint.
The proposed binding reuses the pinned query shape and Foundation identities
and errors; it does not amend either upstream proposal.

HTTP handles, bounded wait, polling, cancellation, result manifests, and Arrow
downloads have prior art, notably [Databricks Statement Execution][databricks].
[dbt's GraphQL Semantic Layer API][dbt] also combines query handles and Arrow
results. These patterns are not claimed as inventions or as proof of
cross-vendor semantic equivalence. A focused, endpoint-specific comparison is
in the [package README](../../typescript/execution/README.md#feature-comparison).

[Arrow Flight][flight] is an Arrow-oriented RPC transport; Flight SQL adds a
database service protocol. [ADBC][adbc] is a database driver API, not an HTTP
wire format. The [backend mapping](execution-http/backend-mapping.md) describes
corresponding operations and obligations that cannot be delegated automatically.
In particular, existing Flight SQL and ADBC endpoints do not gain Ossie query
semantics merely by receiving a new request envelope.

A separate [ADBC materialization example](../../examples/execution-adbc/README.md)
demonstrates reusing a real ADBC SQLite result reader behind the existing
materialized-result contract. It is not an HTTP service, general Layer 3
compiler, Flight implementation, or adapter certification for other drivers.
The TypeScript client itself implements neither Flight nor an ADBC driver.

Local fixtures and JSON Schema validation establish only exercised transport
behavior. They do not establish expression-language conformance, correct
joins/aggregation, production authorization, or native vendor interoperability.

## 7. Experimental status and implementation evidence (non-normative)

The proposed progression criterion is two independently implemented backends
demonstrating the same declared Layer 3 subset through a common client. Evidence
should cover expected answers for grain-sensitive queries, unsupported-query
refusals, caller/model isolation, cancellation races, paging, and partial or
failed result delivery. The scope and capability differences must be explicit;
passing a finite corpus is not proof of general semantic equivalence.

The local TypeScript fixtures and the single ADBC SQLite example do not satisfy
that criterion. There are no confirmed independent engine commitments recorded
by this proposal. Keeping the PR experimental avoids treating a prototype as
community adoption; adoption remains subject to the project's normal process.

A future streaming-partition mode, different metadata attachment rules, or
renewable access references should be motivated by adapter evidence and reviewed
as protocol changes. They are not implicit relaxations of this draft's exact
counts, immutable manifests, page identity, or expiry requirements.

## 8. Prototype implementation notes (non-normative)

The TypeScript package's `readBatches` yields provisional batches in page order;
`execute` returns a fully validated table; `readPage` validates only a selected
page (index zero for unpaged delivery). Whole-result operations use aggregate
limits, while `readPage` uses selected-page limits. Defaults are 64 MiB decoded
IPC bytes and 1,000,000 rows. Declared truncation requires explicit caller
acceptance. These are configurable admission policies, not protocol defaults.
Arrow decoding and application allocations may exceed IPC byte size.

The client limits control JSON and each IPC metadata message to 1 MiB. Its
predecode FlatBuffer guard checks bounds and schema-vector counts, with repeated
metadata references charged against `maxResultBytes`. That is prototype
hardening, not a new Arrow format or a prescribed verifier implementation.
Replacing generic binary verification with maintained upstream/library support
is preferable if equivalent pre-allocation safety and resource limits can be
preserved; Ossie identity/count/type checks would still be needed.

Cross-call history retains 64 recently observed executions and can be released
with `forgetExecution`, without changing server state. Active waits preserve
their own identity baseline. Reordered concurrent pending responses cannot
replace newer observations; terminal and identity checks still apply.
The client cannot compare forgotten history. Full implementation details and
security limitations are in the [client README](../../typescript/execution/README.md).

[query-schema]: https://raw.githubusercontent.com/apache/ossie/b5418ee3aa19f117a1c464733de21f5a0dc0326b/validation/mcp/execute-query-query.schema.json
[foundation-semantics]: https://github.com/apache/ossie/blob/cc0d07099a3ef31e85af1fd93458a52186fae2c6/core-spec/foundational_semantics.md
[rest-thread]: https://lists.apache.org/thread/xvpomkdvmgv1m5krprq590l8mzccks5n
[polaris]: https://github.com/apache/polaris/pull/4961
[databricks]: https://docs.databricks.com/api/statement-execution/v1/execute-statement
[dbt]: https://docs.getdbt.com/docs/dbt-apis/sl-graphql
[flight]: https://arrow.apache.org/docs/format/Flight.html
[adbc]: https://arrow.apache.org/adbc/current/format/specification.html
