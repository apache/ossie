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

# Backend mapping: materialized HTTP, Flight SQL, and ADBC

**Design note for [HTTP execution 0.1-draft](../execute-query-http.md), not an
additional wire contract or adopted standard.** Flight/ADBC and a browser HTTP
binding are complementary, not replacements for each other. Reusing established
backend execution/data APIs can reduce gateway work without pretending that
they already provide Ossie semantic planning or the HTTP draft's guarantees.

## Concrete boundaries

```text
structured Ossie query + immutable binding
  -> authorization / semantic planning / backend compilation (external)
  -> Flight SQL or ADBC execution (backend-specific)
  -> validate, annotate, materialize immutable Arrow IPC (adapter)
  -> HTTP execution job + manifest + result delivery (gateway)
```

Native backend SQL exists only behind the planner boundary. It is not a new
HTTP `query` variant or a fallback after semantic validation fails. A fixed SQL
fixture is not a general Layer 3/Foundation compiler.

| Concern | Established backend API | Work still required by this HTTP draft |
| --- | --- | --- |
| Submission | [Flight SQL][flight-sql] packs `CommandStatementQuery` in Protobuf `Any` in a CMD `FlightDescriptor`, obtains `FlightInfo` through `GetFlightInfo`, then reads tickets with `DoGet`. [ADBC][adbc] executes a statement; Python DBAPI `execute` followed by `fetch_record_batch` exposes an Arrow reader. | Compile the authorized structured query for that backend. Assign and retain Ossie execution, result, binding, and model identities. Neither interface supplies those semantics automatically. |
| Pending execution | [Flight][flight] `PollFlightInfo`, when the server supports the applicable command, can yield evolving `FlightInfo` and a retry descriptor. An ADBC driver may expose incremental partition execution. | A durable job/id mapping and worker retain state across HTTP requests. The gateway bounds `Prefer: wait=N`, polls **the same** job, and publishes the whole immutable manifest only after materialization. An ordinary synchronous DBAPI call is not a durable asynchronous job service. |
| Data | `DoGet` streams Arrow batches; endpoint locations are alternatives for the same partition, not additional partitions. ADBC readers expose Arrow batches. Partition execution/read support is driver-specific; the [SQLite driver][sqlite] does not support partitioned result sets. | Fetch every required partition once, validate flat supported types and fixed schema, attach Ossie schema/column identities, and serialize self-contained IPC streams. ADBC/C data transfer or Flight transport is not itself the required HTTP IPC resource. |
| Counts | [FlightInfo][flight-proto] `total_records`/`total_bytes` may be unknown (`-1`, **not zero**). Backend counts need not describe the final reserialized HTTP IPC byte length. | Exact decoded IPC counts, including framing, rewritten metadata, and repeated page schemas, normally require spooling before success. Do not translate unknown into empty or equate compressed `Content-Length` with decoded bytes. |
| Ordering | `FlightInfo.ordered=true` defines concatenation in endpoint order. With `ordered=false`, inter-endpoint ordering is not promised; each endpoint retains its own row order. ADBC exposes the backend reader's order. | Preserve the planner's `ORDER BY` semantics and reader/partition order. Arbitrarily sorting endpoint descriptors cannot manufacture the missing global row order. Require a backend ordering guarantee or a planner-owned merge/sort, otherwise refuse that ordered execution. |
| Replay and expiry | A Flight endpoint with `expiration_time` permits repeated `DoGet` before expiry; absent expiry, retryability is application-defined. Optional `RenewFlightEndpoint` extends access. ADBC partition tokens have driver-specific lifetime/reuse semantics. | Pages must refer to one retained immutable materialization, with contiguous indices/offsets and one whole-result expiry. Snapshot/provider identity must survive mapping; a refetch must not silently re-execute against changed data. Renewal of a data reference is not a change to result data. This HTTP draft currently has **no renewal**. |
| Cancellation | Flight `DoAction(CancelFlightInfo)` reports cancelled, cancelling, not-cancellable, or unspecified/unknown outcomes. ADBC 1.1 introduced cancellation; Python exposes `adbc_cancel`, with actual support depending on the driver/backend. | Keep best-effort cancellation and completion races distinct. Accepted cancellation is not confirmed cancellation, and closing a local reader is not a durable backend cancel operation. The gateway owns the state machine. |
| Errors | Flight status codes and ADBC portable status categories can carry provider-specific details such as vendor codes/SQLSTATE. | Preserve genuine planner/Foundation semantic codes directly; do not invent an `E_*` semantic diagnosis from a generic backend status. Sanitize provider details, keep private diagnostics separate, and leave unknown codes inspectable. Retryability is a reviewed policy decision, not automatic re-execution. |

Raw Flight permits application-defined commands. A server accepting raw SQL
bytes in a custom Flight descriptor is **not automatically Flight SQL
compatible**: Flight SQL defines specific messages and their RPC behavior.
Likewise, merely returning Arrow does not establish either Ossie or Flight SQL
semantic conformance.

The gateway additionally owns authorization at submission/status/result access,
RLS-aware backend credentials and session selection, origin/redirect/CORS policy,
and isolation of provider credentials/tickets from browser tokens. None of the
adapter mechanics authorize a query. Durable queues, job retention, bounded
wait, identity mapping, and terminal publication remain gateway responsibilities.

## Can upstream HTTP locations be reused?

[Flight locations][flight-proto] can advertise HTTP URLs, including formats
other than IPC. Reuse is possible **only** when the gateway can establish all
of this draft's requirements: supported self-contained IPC/media type, Ossie
schema and column metadata, immutable binding/result/page identity, physical
schema/type compatibility, exact decoded byte/row counts, required ordering,
retention/replay and whole-result expiry, and applicable authorization/origin/
redirect/token-isolation protections.

An Arrow-looking content type or a presigned URL alone is insufficient. If any
guarantee is missing, consume and validate the upstream result, rewrite metadata,
and reframe/spool it into owned resources rather than passing the location
through. Even when some Arrow buffers can be shared in-process, metadata
rewriting and IPC serialization still incur work: no zero-copy, live-streaming,
or Flight performance-parity claim follows.

## Evidence first, then possible extensions

The [ADBC SQLite example](../../../examples/execution-adbc/README.md) implements
one narrow path today: trusted SQL or an existing ADBC reader into immutable,
single-stream/paged IPC using native PyArrow, with resource ceilings and exact
final counts. Its capture is consumed by the unchanged TypeScript client's
validators through synthetic Fetch. It does not run a Flight SQL service or a
live HTTP engine, implement a planner/job gateway, or prove multiple vendors
adopt the profile.

This makes the existing materialized contract testable before weakening it.
A future streaming-partition mode could expose evolving endpoints/unknown
totals; a future optional-metadata mode could reduce annotation requirements.
Both would change observable guarantees and need explicit negotiation,
completeness/ordering/expiry rules, and evidence from additional integrations.
They are follow-up questions, **not changes made by this mapping or example**.

[flight]: https://arrow.apache.org/docs/format/Flight.html
[flight-sql]: https://arrow.apache.org/docs/format/FlightSql.html
[flight-proto]: https://github.com/apache/arrow/blob/main/format/Flight.proto
[adbc]: https://arrow.apache.org/adbc/current/format/specification.html
[sqlite]: https://arrow.apache.org/adbc/current/driver/sqlite.html
