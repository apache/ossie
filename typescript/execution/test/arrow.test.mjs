/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements. See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership. The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License. You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied. See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

import assert from "node:assert/strict";
import test from "node:test";
import {
  Bool, DateDay, Decimal, Dictionary, Field, Float64, Int32, Int64, List,
  TimeMicrosecond, TimestampMicrosecond, TimestampNanosecond, Utf8,
  Table, makeData, tableToIPC,
} from "apache-arrow";
import {
  ARROW_MEDIA_TYPE, COLUMN_METADATA_KEY, OssieClient, ProtocolError,
} from "../dist/index.js";
import {
  MODEL, ORIGIN, arrowFixture, arrowResponse, assertSafeError,
  chunksResponse, collect, mockFetch, rewriteFirstBatchHeader,
} from "./fixtures.mjs";

const client = (fetch, options = {}) => new OssieClient({
  baseUrl: ORIGIN, fetch, timeoutMs: 1000, ...options,
});
const read = (fixture, options = {}, response) => {
  const mock = mockFetch(() => response ?? arrowResponse(fixture.bytes));
  return collect(client(mock.fetch, options).readBatches(fixture.succeeded));
};

test("real Arrow IPC survives arbitrary network chunk boundaries and retains schema metadata", async () => {
  const fixture = arrowFixture();
  const batches = await read(fixture, {}, chunksResponse(fixture.bytes, 1));
  assert.equal(batches.reduce((n, batch) => n + batch.numRows, 0), 2);
  assert.equal(batches[0].getChildAt(1).get(0), 9007199254740993n);
  assert.equal(batches[0].getChildAt(1).get(1), null);
  assert.deepEqual(batches[0].schema.metadata, fixture.schema.metadata);
});

test("zero-row result is a valid typed table, not absent data", async () => {
  const fixture = arrowFixture({ rows: [] });
  const batches = await read(fixture);
  assert.equal(batches.reduce((count, batch) => count + batch.numRows, 0), 0);
});

test("schema-only zero-row stream preserves its typed columns during execute", async () => {
  const fixture = arrowFixture({ rows: [] });
  fixture.bytes = tableToIPC(new Table(fixture.schema, []), "stream");
  fixture.succeeded.result.byte_count = fixture.bytes.length;
  const mock = mockFetch(call => call.method === "POST"
    ? new Response(JSON.stringify(fixture.succeeded), { headers: { "Content-Type": "application/json" } })
    : arrowResponse(fixture.bytes));
  const result = await client(mock.fetch).execute({ data_source_id: "sales", query: { fields: ["region"], limit: 0 } });
  assert.equal(result.table.numRows, 0);
  assert.equal(result.table.numCols, 2);
  assert.deepEqual(result.table.schema.metadata, fixture.schema.metadata);
});

test("duplicate display names and labels are allowed when column ids are stable and unique", async () => {
  const fixture = arrowFixture({ columns: [
    { column_id: "first", name: "value", label: "Value", logical_type: "String", nullable: true },
    { column_id: "second", name: "value", label: "Value", logical_type: "Integer", nullable: true },
  ] });
  const batches = await read(fixture);
  assert.equal(batches[0].schema.fields[0].name, "value");
  assert.equal(batches[0].schema.fields[1].name, "value");
  assert.equal(batches[0].getChildAt(1).get(0), 9007199254740993n);
});

test("precision and literal nulls remain Arrow data, not guessed JSON/JavaScript scalars", async () => {
  const columns = [
    { column_id: "integer", name: "integer", logical_type: "Integer", nullable: true },
    { column_id: "decimal", name: "decimal", logical_type: "Decimal", nullable: true },
    { column_id: "date", name: "date", logical_type: "Date", nullable: true },
    { column_id: "timestamp", name: "timestamp", logical_type: "DateTime", nullable: true },
    { column_id: "literal", name: "literal", logical_type: "String", nullable: true },
  ];
  const types = [new Int64(), new Decimal(2, 38, 128), new DateDay(), new TimestampMicrosecond(), new Utf8()];
  const fields = columns.map((column, index) => new Field(column.name, types[index], true,
    new Map([[COLUMN_METADATA_KEY, JSON.stringify({ column_id: column.column_id })]])));
  const decimal = new Uint32Array([0xffffffff, 0x00200000, 0, 0]);
  const fixture = arrowFixture({
    columns, fields,
    rows: [
      [9007199254740993n, decimal, new Date("2024-02-29T00:00:00Z"), 1709164800123.456, "null"],
      [null, null, null, null, null],
    ],
  });

  const [batch] = await read(fixture);
  assert.equal(batch.getChildAt(0).get(0), 9007199254740993n);
  assert.deepEqual(Array.from(batch.getChildAt(1).get(0)), Array.from(decimal));
  assert.equal(batch.schema.fields[1].type.scale, 2);
  assert.equal(batch.schema.fields[1].type.precision, 38);
  for (const index of [2, 3]) {
    assert.equal(batch.schema.fields[index].type.toString(), fixture.schema.fields[index].type.toString());
    const actual = batch.getChildAt(index).data[0].values;
    const original = fixture.batches[0].getChildAt(index).data[0].values;
    assert.deepEqual(actual, original.subarray(0, actual.length));
  }
  assert.equal(batch.getChildAt(4).get(0), "null");
  for (let index = 0; index < columns.length; index++) assert.equal(batch.getChildAt(index).get(1), null);
});

test("Boolean, Float, Time and timezone-bearing timestamps keep their physical Arrow types", async () => {
  const definitions = [
    ["Boolean", new Bool(), true],
    ["Float", new Float64(), 1.125],
    ["Time", new TimeMicrosecond(), 43200123456n],
    ["DateTimeTz", new TimestampMicrosecond("UTC"), 1709164800123.456],
  ];
  const columns = definitions.map(([logical_type], index) => ({
    column_id: `typed-${index}`, name: `typed-${index}`, logical_type, nullable: true,
  }));
  const fields = definitions.map(([, type], index) => new Field(columns[index].name, type, true,
    new Map([[COLUMN_METADATA_KEY, JSON.stringify({ column_id: columns[index].column_id })]])));
  const fixture = arrowFixture({
    columns, fields,
    rows: [definitions.map(([, , value]) => value), definitions.map(() => null)],
  });
  const [batch] = await read(fixture);
  assert.equal(batch.getChildAt(0).get(0), true);
  assert.equal(batch.getChildAt(1).get(0), 1.125);
  for (let index = 0; index < columns.length; index++) {
    assert.equal(batch.schema.fields[index].type.toString(), fields[index].type.toString());
    assert.equal(batch.getChildAt(index).get(1), null);
  }
  assert.equal(batch.getChildAt(2).data[0].values[0], 43200123456n);
  assert.equal(batch.schema.fields[3].type.timezone, "UTC");
});

test("nanosecond timestamps preserve exact Arrow buffers without promising lossless convenience accessors", async () => {
  const nanoseconds = 1709164800123456789n;
  const integer = 9007199254740993n;
  const types = [new TimestampNanosecond("UTC"), new Int64()];
  const columns = [
    { column_id: "instant", name: "instant", logical_type: "DateTimeTz", nullable: true },
    { column_id: "integer", name: "integer", logical_type: "Integer", nullable: true },
  ];
  const fields = columns.map((column, index) => new Field(column.name, types[index], true,
    new Map([[COLUMN_METADATA_KEY, JSON.stringify({ column_id: column.column_id })]])));
  const columnData = [nanoseconds, integer].map((value, index) => makeData({
    type: types[index], length: 2, data: BigInt64Array.of(value, 0n),
    nullBitmap: Uint8Array.of(1), nullCount: 1,
  }));
  const fixture = arrowFixture({
    fields, columns, columnData, rows: [[null, null], [null, null]],
  });
  const [batch] = await read(fixture);
  // Timestamp get() may convert to a millisecond Number; inspect the exact native buffer.
  assert.equal(batch.getChildAt(0).data[0].values[0], nanoseconds);
  assert.equal(batch.getChildAt(0).type.unit, types[0].unit);
  assert.equal(batch.getChildAt(0).get(1), null);
  assert.equal(batch.getChildAt(1).get(0), integer);
  assert.equal(batch.getChildAt(1).get(1), null);
});

for (const [name, metadata] of [
  ["execution id", { execution_id: "another-execution" }],
  ["result id", { result_id: "another-result" }],
  ["data source", { data_source_id: "another-source" }],
  ["model revision", { model: { ...MODEL, revision: "another-revision" } }],
  ["contract version", { contract_version: "unknown-version" }],
  ["unknown metadata field", { extra: true }],
]) {
  test(`Arrow schema metadata must match envelope: ${name}`, async () => {
    const good = arrowFixture();
    const value = JSON.parse(good.schema.metadata.values().next().value);
    await assert.rejects(read(arrowFixture({ metadata: { ...value, ...metadata } })), ProtocolError);
  });
}

test("Arrow schema metadata must be valid JSON", async () => {
  await assert.rejects(read(arrowFixture({ metadata: "{bad" })), ProtocolError);
});

test("Arrow column metadata id must match its manifest column", async () => {
  await assert.rejects(read(arrowFixture({ columnMetadata: [{ column_id: "wrong-column" }] })), ProtocolError);
});

test("Arrow column metadata rejects unknown core properties", async () => {
  await assert.rejects(read(arrowFixture({
    columnMetadata: [{ column_id: "col-region", inferred: true }],
  })), ProtocolError);
});

test("missing required schema or field metadata is rejected", async () => {
  for (const missing of ["schema", "field"]) {
    const fixture = arrowFixture();
    if (missing === "schema") fixture.schema.metadata.clear();
    else fixture.schema.fields[0].metadata.clear();
    fixture.bytes = tableToIPC(fixture.table, "stream");
    fixture.succeeded.result.byte_count = fixture.bytes.length;
    await assert.rejects(read(fixture), ProtocolError);
  }
});

test("dictionary and nested Arrow types are outside the flat result profile", async () => {
  for (const type of [
    new Dictionary(new Utf8(), new Int32(), 0, false),
    new List(new Field("item", new Utf8(), true)),
  ]) {
    const normal = arrowFixture();
    const first = new Field("region", type, true, normal.schema.fields[0].metadata);
    const value = type instanceof List ? ["West"] : "West";
    const fixture = arrowFixture({
      fields: [first, normal.schema.fields[1]], rows: [[value, 1n]],
    });
    await assert.rejects(read(fixture), ProtocolError);
  }
});

test("Arrow extension types cannot masquerade as supported primitive data", async () => {
  const normal = arrowFixture();
  normal.schema.fields[0].metadata.set("ARROW:extension:name", "example.opaque");
  const fixture = arrowFixture({ fields: normal.schema.fields });
  await assert.rejects(read(fixture), ProtocolError);
});

for (const [name, mutate] of [
  ["name", columns => { columns[0].name = "different"; }],
  ["logical type", columns => { columns[0].logical_type = "Integer"; }],
  ["nullability", columns => { columns[0].nullable = false; }],
  ["column count", columns => { columns.pop(); }],
]) {
  test(`Arrow field ${name} must match result columns`, async () => {
    const fixture = arrowFixture();
    mutate(fixture.succeeded.result.columns);
    await assert.rejects(read(fixture), ProtocolError);
  });
}

for (const [name, headers] of [
  ["JSON MIME", { "Content-Type": "application/json" }],
  ["file IPC MIME", { "Content-Type": "application/vnd.apache.arrow.file" }],
]) {
  test(`result refuses ${name}`, async () => {
    const fixture = arrowFixture();
    await assert.rejects(read(fixture, {}, arrowResponse(fixture.bytes, headers)), ProtocolError);
  });
}

test("HTTP content decoding precedes byte_count checks; HTTP compression is not IPC compression", async () => {
  const fixture = arrowFixture();
  const batches = await read(fixture, {}, arrowResponse(fixture.bytes, {
    "Content-Encoding": "gzip", "Content-Length": "100",
  }));
  assert.equal(batches[0].numRows, 2);
});

for (const [name, change] of [
  ["too few rows", result => { result.row_count--; }],
  ["too many rows", result => { result.row_count++; }],
  ["too few bytes", result => { result.byte_count--; }],
  ["too many bytes", result => { result.byte_count++; }],
]) {
  test(`exact result counts reject ${name}`, async () => {
    const fixture = arrowFixture();
    change(fixture.succeeded.result);
    await assert.rejects(read(fixture), ProtocolError);
  });
}

test("malformed Arrow bytes are rejected even when byte count matches", async () => {
  const fixture = arrowFixture();
  fixture.bytes = new TextEncoder().encode("not an Arrow stream");
  fixture.succeeded.result.byte_count = fixture.bytes.length;
  await assert.rejects(read(fixture), ProtocolError);
});

for (const [name, mutate] of [
  ["IPC compression declaration", header => { header._compression = { type: 0, method: 0 }; }],
  ["field length mismatch", header => { header.nodes[0].length++; }],
  ["null count mismatch", header => { header.nodes[0].nullCount = 0; }],
  ["missing validity bytes", header => { header.buffers[0].length = 0; }],
  ["out-of-bounds buffer", header => { header.buffers[1].length = 1_000_000; }],
  ["unaligned buffer offset", header => { header.buffers[1].offset++; }],
]) {
  test(`malformed or unsupported Arrow batch rejects ${name}`, async () => {
    const fixture = rewriteFirstBatchHeader(arrowFixture(), mutate);
    await assert.rejects(read(fixture), ProtocolError);
  });
}

test("Arrow file encoding is rejected even with stream MIME", async () => {
  const fixture = arrowFixture();
  fixture.bytes = tableToIPC(fixture.table, "file");
  fixture.succeeded.result.byte_count = fixture.bytes.length;
  await assert.rejects(read(fixture), ProtocolError);
});

test("a second schema cannot replace metadata mid-result", async () => {
  const fixture = arrowFixture();
  const bytes = new Uint8Array(fixture.bytes.length * 2 - 8);
  bytes.set(fixture.bytes.subarray(0, -8));
  bytes.set(fixture.bytes, fixture.bytes.length - 8);
  fixture.bytes = bytes;
  fixture.succeeded.result.byte_count = bytes.length;
  fixture.succeeded.result.row_count *= 2;
  await assert.rejects(read(fixture), ProtocolError);
});

test("trailing bytes after the IPC end marker are rejected", async () => {
  const fixture = arrowFixture();
  const bytes = new Uint8Array(fixture.bytes.length + 8);
  bytes.set(fixture.bytes);
  bytes.fill(42, fixture.bytes.length);
  fixture.bytes = bytes;
  fixture.succeeded.result.byte_count = bytes.length;
  await assert.rejects(read(fixture), ProtocolError);
});

for (const removeBytes of [12, 40]) {
  test(`truncated IPC missing ${removeBytes} final record bytes is not accepted as a complete result`, async () => {
    const fixture = arrowFixture();
    fixture.bytes = fixture.bytes.slice(0, -removeBytes);
    fixture.succeeded.result.byte_count = fixture.bytes.length;
    await assert.rejects(read(fixture), ProtocolError);
  });
}

test("truncated query result requires explicit opt-in independent of well-formed IPC", async () => {
  const fixture = arrowFixture({ manifest: { completeness: "truncated", truncation_reason: "server-row-limit" } });
  const mock = mockFetch(() => arrowResponse(fixture.bytes));
  const api = client(mock.fetch);
  await assert.rejects(collect(api.readBatches(fixture.succeeded)), assertSafeError);
  assert.equal(mock.calls.length, 0);
  const batches = await collect(api.readBatches(fixture.succeeded, { allowTruncated: true }));
  assert.equal(batches[0].numRows, 2);
});

test("configured result size limits reject manifests before fetching", async () => {
  const fixture = arrowFixture();
  for (const limits of [{ maxResultBytes: fixture.bytes.length - 1 }, { maxResultRows: 1 }]) {
    const mock = mockFetch(() => arrowResponse(fixture.bytes));
    await assert.rejects(collect(client(mock.fetch, limits).readBatches(fixture.succeeded)), assertSafeError);
    assert.equal(mock.calls.length, 0);
  }
});

test("default byte/row limits reject oversized manifests before fetching", async () => {
  for (const oversized of [{ byte_count: 64 * 1024 * 1024 + 1 }, { row_count: 1_000_001 }]) {
    const fixture = arrowFixture({ manifest: oversized });
    const mock = mockFetch(() => arrowResponse(fixture.bytes));
    await assert.rejects(collect(client(mock.fetch).readBatches(fixture.succeeded)), assertSafeError);
    assert.equal(mock.calls.length, 0);
  }
});

test("actual streamed bytes cannot evade limits through an underreported manifest", async () => {
  const fixture = arrowFixture();
  const limit = fixture.bytes.length - 16;
  fixture.succeeded.result.byte_count = limit;
  await assert.rejects(read(fixture, { maxResultBytes: limit }, chunksResponse(fixture.bytes)), assertSafeError);
});

test("readBatches yields the first batch before consuming the complete result; early break cancels body", async () => {
  const fixture = arrowFixture({
    batchRows: Array.from({ length: 20 }, (_, index) =>
      Array.from({ length: 128 }, () => [`batch-${index}-${"x".repeat(64)}`, BigInt(index)])),
  });
  let delivered = 0;
  let cancelled = false;
  const mock = mockFetch(() => chunksResponse(fixture.bytes, 97, {
    onPull(offset) { delivered = Math.min(offset + 97, fixture.bytes.length); },
    onCancel() { cancelled = true; },
  }));
  for await (const batch of client(mock.fetch).readBatches(fixture.succeeded)) {
    assert.equal(batch.numRows, 128);
    assert.ok(delivered < fixture.bytes.length / 2, "Result bytes are not fully accumulated before first yield");
    break;
  }
  assert.ok(cancelled, "Early iterator return releases/cancels the underlying response body");
});

test("result timeout covers a stalled Arrow response body", { timeout: 2000 }, async () => {
  const fixture = arrowFixture();
  let cancelled = false;
  const mock = mockFetch(() => new Response(new ReadableStream({
    start(controller) { controller.enqueue(fixture.bytes.slice(0, 12)); },
    cancel() { cancelled = true; },
  }), { headers: { "Content-Type": ARROW_MEDIA_TYPE } }));
  await assert.rejects(collect(client(mock.fetch, { timeoutMs: 30 }).readBatches(fixture.succeeded)), assertSafeError);
  assert.ok(cancelled);
});

test("Arrow body transport failures are typed and do not expose signed result details", async () => {
  const fixture = arrowFixture();
  const secret = "ARROW_STREAM_SYNTHETIC_SECRET";
  const response = new Response(new ReadableStream({
    start(controller) { controller.error(new Error(`socket failed https://host.invalid/?token=${secret}`)); },
  }), { headers: { "Content-Type": ARROW_MEDIA_TYPE } });
  await assert.rejects(read(fixture, {}, response), error => assertSafeError(error, [secret]));
});

test("result abort covers a stalled Arrow response body", { timeout: 2000 }, async () => {
  const fixture = arrowFixture();
  const controller = new AbortController();
  let cancelled = false;
  const mock = mockFetch(() => new Response(new ReadableStream({
    start(stream) {
      stream.enqueue(fixture.bytes.slice(0, 12));
      setTimeout(() => controller.abort(), 20);
    },
    cancel() { cancelled = true; },
  }), { headers: { "Content-Type": ARROW_MEDIA_TYPE } }));
  await assert.rejects(collect(client(mock.fetch).readBatches(fixture.succeeded, { signal: controller.signal })), assertSafeError);
  assert.ok(cancelled);
});
