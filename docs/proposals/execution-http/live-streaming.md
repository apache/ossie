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

# Live execution extension - DRAFT 0.2

**Proposed, opt-in, and not an adopted standard.** This extension adds early
result delivery to the [materialized HTTP base](../execute-query-http.md).
It does not change the meaning or representation of existing `0.1-draft`
materialized requests, results, or immutable pages. The capitalized requirement
words have the same BCP 14 meaning as in that document.

## 1. Delivery mode and negotiation

Materialization is a delivery choice, not a requirement of Arrow IPC and not
a requirement that an engine buffer the whole result in RAM. The base adapter
spools completed results to disk. Live delivery instead permits a consumer to
read final-valued result batches while the backend is still producing them,
without first retaining the complete result.

The endpoint MUST advertise `live-stream-v0.2` in the base capabilities
`features` array before a client selects live delivery. The client MUST request
it explicitly using `LiveRequest`, defined by [live.schema.json](live.schema.json):

```json
{
  "contract_version": "0.2-draft",
  "delivery": "live",
  "data_source_id": "sales-v7",
  "query": {
    "fields": ["orders.order_id", "orders.amount"],
    "limit": 100
  }
}
```

`POST /queries` otherwise follows the existing media type, authorization,
bounded-wait, and `200` terminal / `202` pending rules. The response MUST be a
`LiveExecution` with matching version, delivery mode, and data source. A server
unable to implement live delivery MUST refuse it; neither side may silently
downgrade to materialized delivery or resubmit the query. The Layer 3 query,
Foundation revision, fixed model binding, and semantic refusal codes are
unchanged. A capability advertisement is not proof of supported query semantics.

Legacy materialized submission remains the default. An old client cannot
consume this new schema by treating a running execution as a completed result.
The TypeScript live methods perform capability discovery before their one
submission; ordinary materialized methods do not gain automatic preflight.

## 2. Execution state and stream readiness

`LiveExecution` carries `contract_version`, `delivery`, `execution_id`,
`data_source_id`, `model`, `state`, `warnings`, and optional `extensions`.
It follows the base execution-state transitions with these payload rules:

| State | Stream / completion |
| --- | --- |
| `queued` | No stream or completion; optional progress |
| `running`, `cancelling` | Optional stream once ready; optional progress; no completion |
| `succeeded` | Stream and final completion required; no progress or error |
| `failed` | Error required; stream retained if it was previously advertised; no completion or progress |
| `cancelled` | Stream retained if previously advertised; no completion, error or progress |

Readiness is independent of completion. Once the schema is fixed and a result
can be streamed, the server publishes a `LiveStream` descriptor:

```json
{
  "contract_version": "0.2-draft",
  "delivery": "live",
  "execution_id": "q-live",
  "data_source_id": "sales-v7",
  "model": { "id": "sales", "revision": "7" },
  "state": "running",
  "warnings": [],
  "stream": {
    "result_id": "r-live",
    "format": "arrow-stream",
    "url": "https://api.example.test/streams/r-live",
    "expires_at": "2099-01-01T00:00:00Z",
    "replayable": false,
    "columns": [
      { "column_id": "id", "name": "order_id", "logical_type": "Integer", "nullable": false },
      { "column_id": "amount", "name": "amount", "logical_type": "Decimal", "nullable": true }
    ]
  }
}
```

The consumer MUST start the stream without waiting for `succeeded`. A pending
POST MAY return as soon as the stream is ready even when its wait preference
has time remaining. Servers MUST NOT make completion a precondition of live
stream access. A blocking aggregation, sort, or other physical operator may
still delay the first valid batch: this extension does not override execution
semantics or require speculative values. Already emitted values MUST NOT
require later corrections or retractions.

Execution/model identity is fixed. After first advertisement the stream
descriptor MUST NOT disappear or change, including on terminal failure.
Terminal representations are immutable. Final values describe the same
`result_id`; a new execution or replacement result cannot stand in for it.

## 3. Plain IPC data, separate status

`GET stream.url` returns `application/vnd.apache.arrow.stream`. It contains
only ordinary Arrow IPC messages, not JSON events, sentinel rows, or errors
disguised as data. The base flat physical-type subset, predecode safety,
schema stability, logical-type mapping, and column metadata requirements apply.

The schema's `org.apache.ossie.execution` value is `LiveExecutionMetadata`:
`contract_version: "0.2-draft"`, `execution_id`, `result_id`, `data_source_id`,
and `model`. Page coordinates are forbidden. Column identifiers and semantic
descriptors remain identical to those of the stream descriptor.

Counts are deliberately absent while running; missing means unknown, not zero.
Consumers MUST enforce actual received-byte and row limits during streaming,
not rely on a promised future total. Produced bytes are counted after HTTP
decompression and include every IPC message, padding, and end marker.
Client read buffering, network backpressure and engine buffering remain
distinct; no end-to-end zero-copy or heap-size guarantee is introduced.

### Mid-stream failure

Before the data response starts, failures can use HTTP Problem Details.
After headers have been sent, a server cannot change that HTTP response code.
On execution or serialization failure it MUST:

1. Publish terminal `failed` with the structured `QueryError` on the execution
   status resource, preserving the advertised stream and result identity.
2. Stop producing data and terminate or abort the data response.

For confirmed cancellation the same rule applies with terminal `cancelled`.
`GET /queries/{id}` remains authoritative for execution outcome. The client
monitors it while reading and stops local consumption on failure/cancellation.
After a stream error the client can inspect status, within its existing
deadline, to obtain a recorded engine error. A data transport or validation
failure MUST NOT be converted into success if the status says `succeeded`.
Neither HTTP trailers nor a nonstandard Arrow error message are required.

### Final verification

The server MUST NOT mark an execution `succeeded` until both evaluation and
production/IPC serialization have completed successfully. It publishes a
`LiveCompletion` in that terminal record:

```json
{
  "result_id": "r-live",
  "row_count": 100,
  "byte_count": 8192,
  "completeness": "complete"
}
```

This example uses hypothetical counts. Exact terminal counts MUST match the
same stream the client consumed. `completeness: "truncated"` additionally
requires a nonblank `truncation_reason`; accepting it is an explicit client
choice, not an excuse for a broken stream or backend failure.

Verified success requires **both** successful full IPC consumption and a
successful terminal record with matching identity, schema and final counts.
HTTP `200`, EOF, an IPC end marker, or a progress fraction alone is insufficient.
If EOF arrives while execution is still pending, the client waits for terminal
status within its original deadline. If terminal status is unavailable,
completion remains unverified and the operation fails. An unavailable status
channel cannot be silently treated as an empty or successful result.

Batches exposed before final verification are provisional. On any later
failure the application MUST discard them or clearly label them incomplete.
The `executeLive` convenience operation returns a table only after verification;
the streaming iterator yields incrementally and validates before successful
exhaustion. A later downstream network failure after server production succeeded
does not retroactively mutate a successful execution: that is a delivery failure.

## 4. Lifetime, cancellation and compatibility

`replayable: false` means a forward-only, single-consumer stream. The server
MUST reject repeated stream acquisition; the client MUST NOT automatically
retry, resume, reread, or resubmit. A fresh client instance is not a way to
claim replay support. Retrying a lost live download requires a new explicit
user decision, not a hidden query retry.

`expires_at` is the latest time to **start** the live data request. An already
opened stream may finish after that time subject to its operation deadline;
expiry is not an instruction to discard an active stream or invent success.
The server MUST retain status while a stream is active and for at least 60
seconds after termination, and at least until `expires_at`. Operational
retention beyond this minimum remains a deployment choice. Clients need a
bounded final verification, not indefinite job retention.

The existing explicit cancellation endpoint still distinguishes request
acceptance from confirmed backend cancellation. Local abort, deadline or early
iterator return stops local data/status work and releases readers; it does not
claim backend cancellation. A client may separately call `cancelLive`.

HTTPS, caller authorization, exact model binding, origin allowlisting,
credential isolation, redirect refusal, and CORS rules apply to data and status
requests. Structured backend errors remain untrusted reference data and must
not leak private credentials or paths.

Materialized single-stream and immutable-page results retain their original
replay, upfront count, expiry, and completeness rules. Live mode does not offer
indexed pagination, live partition discovery, renewable links, or replay.
Supporting early delivery, arbitrary page rereads and no retained result
storage simultaneously is not a promise of this extension.

## 5. Evidence and backend alignment

Flight/ADBC readers can feed IPC production without spooling solely to discover
final counts. A gateway still owns execution identity, lifecycle/error
publication, authorization, and final byte accounting. Those interfaces do not
provide Ossie semantic planning automatically.

The existing ADBC SQLite example remains a materialized adapter. Controlled
HTTP producers exercise the live client and browser flow; they do not establish
live AS/Flight SQL interoperability or a general engine implementation.
