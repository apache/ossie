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
import { Table, tableToIPC, vectorFromArray, Int32 } from "apache-arrow";
import { OssieClient, ProtocolError, EXECUTION_METADATA_KEY } from "../dist/index.js";
import {
  ORIGIN, REQUEST, arrowFixture, arrowResponse, chunksResponse, collect, json, mockFetch, pagedFixture,
} from "./fixtures.mjs";

function clientFor(fixture, options = {}, handler) {
  const mock = mockFetch(call => {
    if (handler) return handler(call);
    if (call.method === "POST") return json(fixture.succeeded);
    const stream = fixture.streams.find(item => item.descriptor.url === call.url.href);
    assert.ok(stream, "Fetch only one of the advertised pages");
    return arrowResponse(stream.bytes);
  });
  return { api: new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, ...options }), mock };
}

function changePage(fixture, index, transform) {
  const previous = fixture.streams[index];
  transform(previous);
  previous.bytes = tableToIPC(new Table(previous.schema, previous.batches), "stream");
  previous.descriptor.byte_count = previous.bytes.length;
  fixture.succeeded.result.byte_count = fixture.streams.reduce((sum, page) => sum + page.bytes.length, 0);
}

test("execute traverses ordered result pages once without resubmitting or modifying limit", async () => {
  const fixture = pagedFixture();
  const { api, mock } = clientFor(fixture);
  const { table, execution } = await api.execute(REQUEST, { waitSeconds: 5 });
  assert.equal(table.numRows, 2);
  assert.equal(table.getChildAt(1).get(0), 9007199254740993n);
  assert.equal(table.getChildAt(0).get(1), null);
  assert.deepEqual(execution, fixture.succeeded);
  assert.equal(Object.hasOwn(JSON.parse(table.schema.metadata.get(EXECUTION_METADATA_KEY)), "page_index"), false);
  assert.equal(JSON.parse(table.batches[1].schema.metadata.get(EXECUTION_METADATA_KEY)).page_index, 1);
  assert.deepEqual(mock.calls.map(call => call.method), ["POST", "GET", "GET"]);
  assert.deepEqual(JSON.parse(mock.calls[0].body), REQUEST);
  assert.equal(mock.calls[0].headers.get("Prefer"), "wait=5");
  assert.deepEqual(mock.calls.slice(1).map(call => call.url.href), fixture.succeeded.result.pages.map(page => page.url));
});

test("readPage allows indexed access and explicit re-reading of an immutable page", async () => {
  const fixture = pagedFixture();
  const { api, mock } = clientFor(fixture);
  const second = await api.readPage(fixture.succeeded, 1);
  assert.equal(second.numRows, 1);
  assert.equal(second.getChildAt(0).get(0), null);
  await api.readPage(fixture.succeeded, 1);
  assert.deepEqual(mock.calls.map(call => call.url.href), [fixture.streams[1].descriptor.url, fixture.streams[1].descriptor.url]);
});

test("readPage zero also works for legacy single-stream results", async () => {
  const fixture = arrowFixture();
  const mock = mockFetch(() => arrowResponse(fixture.bytes));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
  assert.equal((await api.readPage(fixture.succeeded, 0)).numRows, 2);
  await assert.rejects(api.readPage(fixture.succeeded, 1), ProtocolError);
  assert.equal(mock.calls.length, 1);
});

test("page reads enforce per-page limits, while full traversal enforces aggregate limits", async () => {
  const fixture = pagedFixture();
  const pageBytes = Math.max(...fixture.streams.map(page => page.bytes.length));
  const { api, mock } = clientFor(fixture, { maxResultRows: 1, maxResultBytes: pageBytes + 100 });
  assert.equal((await api.readPage(fixture.succeeded, 1)).numRows, 1);
  await assert.rejects(collect(api.readBatches(fixture.succeeded)), error => error.code === "RESULT_LIMIT_EXCEEDED");
  assert.equal(mock.calls.length, 1, "Full-result admission rejects before fetching pages");
});

test("oversized selected page rejects before download", async () => {
  const fixture = pagedFixture();
  const { api, mock } = clientFor(fixture, { maxResultBytes: 10 });
  await assert.rejects(api.readPage(fixture.succeeded, 0), error => error.code === "RESULT_LIMIT_EXCEEDED");
  assert.equal(mock.calls.length, 0);
});

for (const index of [-1, 0.5, Number.MAX_SAFE_INTEGER + 1, NaN, Infinity, 2]) {
  test(`invalid page index ${index} is rejected without a download`, async () => {
    const fixture = pagedFixture();
    const { api, mock } = clientFor(fixture);
    await assert.rejects(api.readPage(fixture.succeeded, index), ProtocolError);
    assert.equal(mock.calls.length, 0);
  });
}

for (const [name, mutate] of [
  ["both delivery modes", m => { m.url = `${ORIGIN}/other`; }],
  ["neither delivery mode", m => { delete m.pages; }],
  ["empty page list", m => { m.pages = []; }],
  ["unknown page field", m => { m.pages[0].unexpected = true; }],
  ["index gap", m => { m.pages[1].page_index = 2; }],
  ["offset gap", m => { m.pages[1].row_offset = 2; }],
  ["offset overlap", m => { m.pages[1].row_offset = 0; }],
  ["missing total rows", m => { m.row_count++; }],
  ["missing total bytes", m => { m.byte_count++; }],
  ["unsafe page count", m => { m.pages[1].row_count = Number.MAX_SAFE_INTEGER + 1; }],
  ["byte sum overflow", m => { m.pages[1].byte_count = Number.MAX_SAFE_INTEGER; }],
  ["zero-byte page", m => { m.pages[1].byte_count = 0; }],
  ["empty page amongst others", m => { m.pages[1].row_count = 0; }],
  ["relative page URL", m => { m.pages[1].url = "/page"; }],
]) {
  test(`invalid pagination manifest rejects ${name}`, async () => {
    const fixture = pagedFixture();
    mutate(fixture.succeeded.result);
    const mock = mockFetch(() => json(fixture.succeeded));
    const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
    await assert.rejects(api.getExecution("exec-1"), ProtocolError);
  });
}

for (const [name, mutate] of [
  ["swapped page index", m => { m.page_index = 1; }],
  ["wrong row offset", m => { m.row_offset = 5; }],
  ["missing coordinates", m => { delete m.page_index; delete m.row_offset; }],
  ["half coordinates", m => { delete m.row_offset; }],
  ["wrong result", m => { m.result_id = "another-result"; }],
]) {
  test(`Arrow page identity rejects ${name}`, async () => {
    const fixture = pagedFixture();
    changePage(fixture, 0, page => {
      const metadata = JSON.parse(page.schema.metadata.get(EXECUTION_METADATA_KEY));
      mutate(metadata);
      page.schema.metadata.set(EXECUTION_METADATA_KEY, JSON.stringify(metadata));
    });
    const { api } = clientFor(fixture);
    await assert.rejects(api.readPage(fixture.succeeded, 0), ProtocolError);
  });
}

test("identical page payloads cannot be swapped unnoticed", async () => {
  const fixture = pagedFixture({ pageRows: [[["same", 1n]], [["same", 1n]]] });
  const { api } = clientFor(fixture, {}, () => arrowResponse(fixture.streams[1].bytes));
  await assert.rejects(api.readPage(fixture.succeeded, 0), ProtocolError);
});

test("unpaged results reject paged Arrow metadata", async () => {
  const fixture = pagedFixture();
  const unpaged = fixture.streams[0].succeeded;
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: async () => arrowResponse(fixture.streams[0].bytes) });
  await assert.rejects(api.readPage(unpaged, 0), ProtocolError);
});

test("physical integer widths cannot change between otherwise valid logical pages", async () => {
  const fixture = pagedFixture();
  changePage(fixture, 1, page => {
    page.schema.fields[1].type = new Int32();
    page.batches[0].data.children[1] = vectorFromArray([null], new Int32()).data[0];
  });
  const { api } = clientFor(fixture);
  await assert.rejects(collect(api.readBatches(fixture.succeeded)), ProtocolError);
});

test("caller mutation of a yielded batch cannot change the cross-page schema baseline", async () => {
  const fixture = pagedFixture();
  const { api } = clientFor(fixture);
  const reader = api.readBatches(fixture.succeeded);
  const first = await reader.next();
  first.value.schema.fields[1].type.bitWidth = 32;
  assert.equal((await reader.next()).value.numRows, 1);
  assert.equal((await reader.next()).done, true);
});

for (const level of ["field", "schema"]) {
  test(`additional ${level} metadata cannot drift between pages`, async () => {
    const fixture = pagedFixture();
    changePage(fixture, 1, page => {
      const metadata = level === "field" ? page.schema.fields[0].metadata : page.schema.metadata;
      metadata.set("example.test.label", "different");
    });
    const { api } = clientFor(fixture);
    await assert.rejects(collect(api.readBatches(fixture.succeeded)), ProtocolError);
  });
}

test("schema metadata compares by keys and execution identity rather than JSON serialization order", async () => {
  const fixture = pagedFixture();
  changePage(fixture, 1, page => {
    const identity = JSON.parse(page.schema.metadata.get(EXECUTION_METADATA_KEY));
    page.schema.metadata.set(EXECUTION_METADATA_KEY, JSON.stringify(Object.fromEntries(Object.entries(identity).reverse()), null, 2));
  });
  const { api } = clientFor(fixture);
  assert.equal((await api.execute(REQUEST)).table.numRows, 2);
});

test("underreported later pages cannot reset the aggregate decoded-byte budget", async () => {
  const fixture = pagedFixture({
    pageRows: [[["large-".repeat(1024), 1n]], [["large-".repeat(1024), 2n]]],
  });
  const firstBytes = fixture.streams[0].bytes.length;
  const budget = firstBytes + 1024;
  fixture.succeeded.result.pages[1].byte_count = 1024;
  fixture.succeeded.result.byte_count = budget;
  const { api } = clientFor(fixture, { maxResultBytes: budget });
  let rows = 0;
  await assert.rejects(async () => {
    for await (const batch of api.readBatches(fixture.succeeded)) rows += batch.numRows;
  }, error => error.code === "RESULT_LIMIT_EXCEEDED");
  assert.equal(rows, 1, "Do not yield data from a page that exceeds the remaining aggregate budget");
});

test("one schema-only page represents a zero-row result", async () => {
  const fixture = pagedFixture({ pageRows: [[]] });
  const page = fixture.streams[0];
  page.bytes = tableToIPC(new Table(page.schema, []), "stream");
  page.descriptor.byte_count = page.bytes.length;
  fixture.succeeded.result.byte_count = page.bytes.length;
  const { api } = clientFor(fixture);
  assert.equal((await api.readPage(fixture.succeeded, 0)).numCols, 2);
  assert.equal((await api.execute(REQUEST)).table.numRows, 0);
});

test("breaking batch traversal does not prefetch another page", async () => {
  const batchRows = Array.from({ length: 10 }, (_, index) =>
    Array.from({ length: 128 }, () => [`batch-${index}`, BigInt(index)]));
  const fixture = pagedFixture({
    pageRows: [batchRows.flat(), [["last", 1n]]], pageOptions: [{ batchRows }],
  });
  let cancelled = false;
  const { api, mock } = clientFor(fixture, {}, () =>
    chunksResponse(fixture.streams[0].bytes, 97, { onCancel: () => { cancelled = true; } }));
  for await (const batch of api.readBatches(fixture.succeeded)) {
    assert.equal(batch.numRows, 128);
    break;
  }
  assert.equal(mock.calls.length, 1);
  assert.equal(cancelled, true);
});

test("a late page error rejects full materialization without rerunning the query", async () => {
  const fixture = pagedFixture();
  const { api, mock } = clientFor(fixture, {}, call => {
    if (call.method === "POST") return json(fixture.succeeded);
    if (call.url.href === fixture.streams[0].descriptor.url) return arrowResponse(fixture.streams[0].bytes);
    throw new Error("synthetic download failure");
  });
  await assert.rejects(api.execute(REQUEST), error => error.code === "TRANSPORT_ERROR");
  assert.equal(mock.calls.filter(call => call.method === "POST").length, 1);
  assert.equal(mock.calls.length, 3);
});

test("page byte and row counts are checked independently of the manifest totals", async () => {
  const fixture = pagedFixture();
  fixture.succeeded.result.pages[0].byte_count += 8;
  fixture.succeeded.result.pages[1].byte_count -= 8;
  const { api } = clientFor(fixture);
  await assert.rejects(api.readPage(fixture.succeeded, 0), ProtocolError);
});

test("page-level origin policy never forwards account credentials to an external host", async () => {
  const fixture = pagedFixture();
  fixture.streams[1].descriptor.url = "https://download.example/page1?signature=secret";
  let tokens = 0;
  const { api, mock } = clientFor(fixture, {
    allowedResultOrigins: ["https://download.example"],
    getAccessToken: () => { tokens++; return "ACCOUNT_TOKEN"; },
  });
  await api.readPage(fixture.succeeded, 1);
  assert.equal(mock.calls[0].headers.has("Authorization"), false);
  assert.equal(mock.calls[0].credentials, "omit");
  assert.equal(mock.calls[0].redirect, "error");
  assert.equal(tokens, 0);
});

test("unallowlisted page origin is refused before download", async () => {
  const fixture = pagedFixture();
  fixture.streams[1].descriptor.url = "https://untrusted.example/page";
  const { api, mock } = clientFor(fixture);
  await assert.rejects(api.readPage(fixture.succeeded, 1), ProtocolError);
  assert.equal(mock.calls.length, 0);
});

test("page expiry is rechecked after token refresh", async context => {
  let now = Date.now();
  context.mock.method(Date, "now", () => now);
  const fixture = pagedFixture({ manifest: { expires_at: new Date(now + 1000).toISOString() } });
  const { api, mock } = clientFor(fixture, { getAccessToken: async () => { now += 2000; return "TOKEN"; } });
  await assert.rejects(api.readPage(fixture.succeeded, 1), error => error.code === "RESULT_EXPIRED");
  assert.equal(mock.calls.length, 0);
});

test("truncation opt-in does not confuse an individual page with a complete result", async () => {
  const fixture = pagedFixture({ manifest: { completeness: "truncated", truncation_reason: "server limit" } });
  const { api, mock } = clientFor(fixture);
  await assert.rejects(api.readPage(fixture.succeeded, 0), error => error.code === "TRUNCATED_RESULT");
  assert.equal(mock.calls.length, 0);
  assert.equal((await api.readPage(fixture.succeeded, 0, { allowTruncated: true })).numRows, 1);
  assert.equal(fixture.succeeded.result.completeness, "truncated");
});

test("terminal page descriptors remain immutable across observations", async () => {
  const fixture = pagedFixture();
  const changed = structuredClone(fixture.succeeded);
  changed.result.pages[1].url = `${ORIGIN}/different`;
  const responses = [fixture.succeeded, changed];
  const mock = mockFetch(() => json(responses.shift()));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
  await api.getExecution("exec-1");
  await assert.rejects(api.getExecution("exec-1"), ProtocolError);
});
