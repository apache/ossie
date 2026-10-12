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

# ADBC SQLite materialization example

One backend-side reference adapter for the existing
[HTTP execution 0.1-draft](../../docs/proposals/execute-query-http.md) result
contract. This is **not a server, Foundation compiler, conformance certificate,
or evidence of vendor adoption**. The [backend mapping](../../docs/proposals/execution-http/backend-mapping.md)
explains the complementary roles of Flight/Flight SQL, ADBC, and the HTTP gateway.

The actual data path is:

```text
trusted backend SQL -> ADBC SQLite execute/fetch_record_batch
                   -> PyArrow IPC materialization -> existing TypeScript client
```

The CLI seeds a synthetic SQLite fixture, then opens it read-only through ADBC.
It queries deliberately unsorted input with `ORDER BY sequence`, preserving
`9007199254740993`, a null, and `-7` exactly. There is no public SQL submission
endpoint. The fixed SQL in `demo.py` is a fixture, **not** a compiler for the
structured HTTP query shape.

## Run from the repository root

Python 3.13, uv, and the existing Node/TypeScript client build are sufficient.
Dependencies are isolated in this example's ignored `.venv`; `uv.lock` pins
the packages and hashes. Nothing is installed globally.

```powershell
uv sync --project examples\execution-adbc --python 3.13 --locked
uv run --project examples\execution-adbc --python 3.13 --locked python -m pytest examples\execution-adbc\tests --basetemp=examples\execution-adbc\.test-output -q -W error
uv run --project examples\execution-adbc --python 3.13 --locked python examples\execution-adbc\demo.py --output examples\execution-adbc\.capture-paged
npm --prefix typescript\execution run build
node examples\execution-adbc\verify-client.mjs examples\execution-adbc\.capture-paged
```

Use a **new explicit directory** for each demo run; existing directories are
refused. Absolute output paths are also accepted. Add `--single-stream` to the
Python demo and use a different output directory to exercise the `url` form.
The default demo emits two pages (two rows, then one). Delete the generated
capture and test directories when finished; do not commit their contents.

Each capture has `execution` (the full succeeded execution) and `resources`
(objects with `url` and a generated relative `file` basename). The only durable
demo outputs are `capture.json` and `.arrow` streams. The fixture database is
closed and removed. URLs use `https://example.test` and are **not live links**.
The 2099 expiry is only a test value, not a retention commitment.

`verify-client.mjs` reads the Python-written bytes via synthetic Fetch, retaining
the existing TypeScript client's framing, FlatBuffer, metadata, count, expiry,
and physical-type validators. It verifies selected pages, full `readBatches`
exhaustion, and `execute`, including bigint/null/order. Malformed captures or
unsupported IPC fail nonzero. No Node Arrow writer creates the payload.
The synthetic request uses the captured binding id and column model references;
the client checks captured execution/model/page identities against native IPC metadata.
This proves native ADBC-to-IPC-to-client interoperability for this fixture,
**not live engine HTTP, browser deployment, or Flight SQL interoperability**.

## Integration boundary and ownership

`materialize_sqlite(database, trusted_sql, **options)` owns its read-only
connection, cursor, and result reader. It opens an existing database with
`mode=ro` and enables SQLite `query_only`. These backend settings are tested;
they are not a SQL sandbox or a substitute for authorization. There is no
regex SQL filtering.

Alternatively, an integration can call
`materialize(cursor.fetch_record_batch(), **options)` with an already-executed
ADBC cursor. `materialize` takes ownership of and closes the reader, including
on failure. The caller owns/closes that cursor and its connection. Both paths
use the same materializer, not two backend implementations.

The caller supplies:

- Trusted, already-authorized/compiled SQL **or** an existing Arrow reader.
  Native SQL never becomes an HTTP-profile `query`.
- `execution_id`, `result_id`, `data_source_id`, and `model={id, revision}`.
  A gateway/planner must ensure binding immutability and unique execution/result
  identity; this example has no registry enforcing lifetime uniqueness.
- `columns` in physical field order: `column_id`, `name`, `logical_type`,
  `nullable`, optionally `role`, `model_ref`, `label`, `description`, `unit`,
  and `currency`. Descriptor extensions are intentionally not supported here.
  Semantic descriptors come from the planner, not guesses from SQL aliases.
- An existing private `output` directory chosen by trusted application code,
  `expires_at`, and optional `page_rows`, `max_rows`, `max_bytes`.
  The row/decoded-IPC-byte defaults are 100,000/64 MiB; `page_rows=None` chooses
  a single stream. Pagination never re-executes SQL or adds query offsets.

## Guarantees and deliberate limits

- Arrow primitive physical types and values are retained without coercion.
  Unsupported types, schema drift, extension metadata, and descriptor
  mismatches fail closed. SQLite's dynamically inferred Arrow types are
  driver-specific; an empty query may not infer the type a planner expects.
  The adapter rejects mismatches rather than manufacturing a replacement type.
- Native PyArrow writes uncompressed little-endian V5 IPC. Schema and field
  metadata are rewritten with the required Ossie identities. Provider metadata
  is deliberately not forwarded. This requires IPC serialization and schema
  rewriting; there is **no zero-copy claim**.
- Pages preserve reader order, repeat the same field schema, and carry
  `page_index`/`row_offset`. Empty results still get one schema-bearing stream.
  Exact row and decoded-byte totals include repeated schemas and IPC framing.
- No success descriptor is returned until exhaustion, writer finalization,
  reader closure, and an expiry recheck. A bounded sink limits actual IPC bytes
  written, including metadata/framing. Limits fail the operation, never return
  success-shaped truncation. The final control JSON is capped at 1 MiB.
  Driver batches, Arrow allocations, metadata, and query-engine work are not
  bounded by the on-disk byte ceiling; no heap/deadline guarantee is claimed.
- Generated UUID basenames are opened exclusively; wire IDs never choose paths.
  A failed call removes only its own files, preserving unrelated caller files.
  Error-path SQLite statement preparation clears a retained failed-step status
  before close; it does not execute or retry the query.
- Local `MaterializationError.public_detail()` exposes a fixed category/message,
  never native SQL, backend diagnostics, credentials, or file paths. These
  categories are **not Foundation semantic error codes** or ready-made
  `QueryError` objects. A production gateway owns reviewed error translation
  and private diagnostics.

The caller must keep the spool private until success and preserve the published
bytes thereafter. There is no crash recovery, atomic durable publication,
job store, HTTP service, authorization/RLS, cancellation, renewal, expiry
deletion scheduler, provider-token forwarding, or general semantic planning.
Existing-query cancellation and durable job lifecycle belong to the gateway,
not to closing this synchronous reader.

Tests validate emitted objects against the existing JSON Schema, plus runtime
identity/count/order invariants and actual SQLite/ADBC values. Primitive-type
unit fixtures test the materializer independently; they do not claim SQLite
supports all those types. Further backend work should follow measured
integration evidence rather than relaxing the current contract in advance.
