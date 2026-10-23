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
import { Decimal, Field, TimestampNanosecond, makeData } from "apache-arrow";
import {
  COLUMN_METADATA_KEY, LIVE_CONTRACT_VERSION, LIVE_FEATURE, MAX_TRACKED_EXECUTIONS,
  LiveExecutionCancelledError, LiveQueryExecutionError, OssieClient, ProtocolError,
} from "../dist/index.js";
import { CAPABILITIES, ERROR, MODEL, ORIGIN, REQUEST, arrowResponse, collect, json, mockFetch } from "./fixtures.mjs";
import { deferred, gatedBody, liveFixture } from "./live-fixtures.mjs";

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const code = expected => error => error.code === expected;
const capabilities = { ...CAPABILITIES, features: [LIVE_FEATURE] };

function setup({ fixture = liveFixture(), status, data, initial, options = {}, preflight } = {}) {
  const mock = mockFetch(call => {
    if (call.url.pathname === "/capabilities") return preflight?.(call) ?? json(capabilities);
    if (call.url.pathname === "/queries" && call.method === "POST") {
      const value = initial ?? fixture.running;
      return json(value, ["succeeded", "failed", "cancelled"].includes(value.state) ? 200 : 202);
    }
    if (call.url.pathname.startsWith("/results/")) return data?.(call) ?? arrowResponse(fixture.bytes);
    return status?.(call) ?? json(fixture.succeeded);
  });
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, pollIntervalMs: 1, timeoutMs: 1500, ...options });
  return { ...mock, api, fixture };
}

async function drain(iterator) {
  const batches = [];
  while (true) {
    const next = await iterator.next();
    if (next.done) return { batches, execution: next.value };
    batches.push(next.value);
  }
}

test("LIVE yields before backend completion and returns verified terminal success after exhaustion", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes);
  let observed = false;
  const { api, calls } = setup({
    fixture, data: () => body.response, status: () => json(observed ? fixture.succeeded : fixture.running),
  });
  const iterator = api.stream(REQUEST);
  const first = await iterator.next();
  assert.equal(first.done, false);
  assert.equal(first.value.getChildAt(1).get(0), 9007199254740993n);
  assert.equal(observed, false);
  observed = true;
  body.finish();
  const result = await drain(iterator);
  assert.equal(result.batches.length, 1);
  assert.deepEqual(result.execution, fixture.succeeded);
  assert.equal(calls.filter(call => call.method === "POST").length, 1);
  assert.equal(calls.filter(call => call.url.pathname.startsWith("/results/")).length, 1);
  assert.deepEqual(JSON.parse(calls.find(call => call.method === "POST").body),
    { ...REQUEST, contract_version: LIVE_CONTRACT_VERSION, delivery: "live" });
  assert.equal(calls[0].url.pathname, "/capabilities");
});

test("LIVE EOF while running awaits terminal status; EOS alone cannot succeed", async () => {
  const fixture = liveFixture();
  const polled = deferred();
  const terminal = deferred();
  const { api } = setup({ fixture, status: () => { polled.resolve(); return terminal.promise; } });
  let done = false;
  const result = api.executeLive(REQUEST).then(value => { done = true; return value; });
  await polled.promise;
  await delay(10);
  assert.equal(done, false);
  terminal.resolve(json(fixture.succeeded));
  assert.equal((await result).table.numRows, 2);
});

test("LIVE terminal success does not abort delayed IPC EOF", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes, fixture.bytes.length);
  const succeeded = deferred();
  const { api } = setup({ fixture, data: () => body.response });
  let done = false;
  const result = api.executeLive(REQUEST, { onProgress: e => { if (e.state === "succeeded") succeeded.resolve(); } })
    .then(value => { done = true; return value; });
  await succeeded.promise;
  assert.equal(body.cancelled, false);
  assert.equal(done, false);
  body.finish();
  assert.equal((await result).table.numRows, 2);
});

test("LIVE terminal success before the last batch preserves still-undelivered bytes", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes);
  const succeeded = deferred();
  const { api } = setup({ fixture, data: () => body.response });
  const iterator = api.stream(REQUEST, { onProgress: e => {
    if (e.state === "succeeded") succeeded.resolve();
  } });
  assert.equal((await iterator.next()).value.numRows, 1);
  await succeeded.promise;
  assert.equal(body.cancelled, false);
  body.finish();
  const result = await drain(iterator);
  assert.equal(result.batches.length, 1);
  assert.equal(result.batches[0].getChildAt(0).get(0), null);
  assert.deepEqual(result.execution, fixture.succeeded);
});

test("LIVE succeeded status and data headers alone cannot certify a result", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes, 0);
  const { api } = setup({ fixture, initial: fixture.succeeded, data: () => body.response,
    options: { timeoutMs: 40 },
  });
  await assert.rejects(api.executeLive(REQUEST), code("TIMEOUT"));
  assert.equal(body.cancelled, true);
});

test("LIVE schema-only zero-row stream preserves its schema", async () => {
  const fixture = liveFixture({ batchRows: [] });
  const { api } = setup({ fixture });
  const result = await api.executeLive(REQUEST);
  assert.equal(result.table.numRows, 0);
  assert.equal(result.table.numCols, 2);
  assert.equal(result.execution.completion.row_count, 0);
});

for (const state of ["failed", "cancelled"]) {
  test(`LIVE ${state} status promptly stops a stalled body and preserves typed terminal shape`, async () => {
    const fixture = liveFixture();
    const body = gatedBody(fixture.bytes);
    let failed = false;
    const terminal = fixture.live(state, state === "failed" ? { error: ERROR } : {});
    const { api, calls } = setup({
      fixture, data: () => body.response, status: () => json(failed ? terminal : fixture.running),
    });
    const iterator = api.stream(REQUEST);
    assert.equal((await iterator.next()).done, false);
    failed = true;
    await assert.rejects(iterator.next(), error => {
      assert.ok(error instanceof (state === "failed" ? LiveQueryExecutionError : LiveExecutionCancelledError));
      assert.deepEqual(error.execution, terminal);
      if (state === "failed") assert.deepEqual(error.error.replacement_query, ERROR.replacement_query);
      return true;
    });
    assert.equal(body.cancelled, true);
    assert.equal(body.response.body.locked, false);
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
  });
}

test("LIVE status failure while iterator is paused releases the body and has no unhandled rejection", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes);
  const statusFailed = deferred();
  let fail = false;
  const { api } = setup({ fixture, data: () => body.response, status: () => {
    if (!fail) return json(fixture.running);
    statusFailed.resolve();
    throw new Error("SYNTHETIC_STATUS_FAILURE");
  } });
  const iterator = api.stream(REQUEST);
  await iterator.next();
  fail = true;
  await statusFailed.promise;
  await delay(10);
  assert.equal(body.cancelled, true);
  assert.equal(body.response.body.locked, false);
  await assert.rejects(iterator.next(), code("TRANSPORT_ERROR"));
});

for (const final of ["failed", "succeeded", "unavailable"]) {
  test(`LIVE body failure with ${final} final inspection is never cured or retried`, async () => {
    const fixture = liveFixture();
    const body = gatedBody(fixture.bytes);
    let broken = false;
    let finalReads = 0;
    const { api, calls } = setup({ fixture, data: () => body.response, status: () => {
      if (!broken) return json(fixture.running);
      finalReads++;
      if (final === "unavailable") throw new Error("SYNTHETIC_STATUS_FAILURE");
      return json(final === "failed" ? fixture.live("failed", { error: ERROR }) : fixture.succeeded);
    } });
    const iterator = api.stream(REQUEST);
    await iterator.next();
    broken = true;
    body.fail();
    await assert.rejects(iterator.next(), final === "failed" ? LiveQueryExecutionError : code("TRANSPORT_ERROR"));
    assert.equal(finalReads, 1);
    assert.equal(calls.filter(call => call.url.pathname.startsWith("/results/")).length, 1);
    assert.equal(calls.filter(call => call.method === "POST").length, 1);
  });
}

test("LIVE status unavailable after EOF rejects rather than accepting a complete Arrow stream", async () => {
  const { api } = setup({ status: () => { throw new Error("SYNTHETIC_UNAVAILABLE"); } });
  await assert.rejects(api.executeLive(REQUEST), code("TRANSPORT_ERROR"));
});

test("LIVE body failure racing an in-flight failed status preserves the backend error", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes);
  const started = deferred(), pending = deferred();
  const failed = fixture.live("failed", { error: ERROR });
  let reads = 0;
  const { api, calls } = setup({ fixture, data: () => body.response, status: () => {
    if (++reads === 1) { started.resolve(); return pending.promise; }
    return json(failed);
  } });
  const iterator = api.stream(REQUEST);
  await iterator.next();
  await started.promise;
  const rejected = assert.rejects(iterator.next(), error => {
    assert.ok(error instanceof LiveQueryExecutionError);
    assert.equal(error.code, ERROR.code);
    assert.deepEqual(error.execution, failed);
    return true;
  });
  body.fail();
  pending.resolve(json(failed));
  await rejected;
  assert.ok(reads <= 2, "Only the in-flight poll and at most one recovery inspection");
  assert.equal(calls.filter(c => c.method === "POST").length, 1);
  assert.equal(calls.filter(c => c.url.pathname.startsWith("/results/")).length, 1);
});

for (const key of ["row_count", "byte_count", "result_id"]) {
  test(`LIVE terminal ${key} mismatch rejects`, async () => {
    const fixture = liveFixture();
    const wrong = structuredClone(fixture.succeeded);
    wrong.completion[key] = key === "result_id" ? "different" : wrong.completion[key] + 1;
    const { api } = setup({ fixture, status: () => json(wrong) });
    await assert.rejects(api.executeLive(REQUEST), ProtocolError);
  });
}

for (const mutate of [
  e => { e.stream.url += "?changed"; },
  e => { e.stream.result_id = "different"; },
  e => { e.stream.columns[0].name = "different"; },
  e => { delete e.stream; },
  e => { e.model.revision = "different"; },
  e => { e.data_source_id = "different"; },
  e => { e.contract_version = "0.1-draft"; },
  e => { e.delivery = "materialized"; },
  e => { e.state = "queued"; delete e.stream; },
]) {
  test(`LIVE rejects mutation: ${mutate}`, async () => {
    const fixture = liveFixture();
    const changed = structuredClone(fixture.running);
    mutate(changed);
    const { api } = setup({ fixture, status: () => json(changed) });
    const submitted = await api.submitLive(REQUEST);
    assert.equal(submitted.state, "running");
    await assert.rejects(api.getLiveExecution(submitted.execution_id), ProtocolError);
  });
}

test("LIVE descriptor must persist even on terminal failure; terminal states are immutable", async () => {
  const fixture = liveFixture();
  let value = fixture.live("failed", { error: ERROR, stream: undefined });
  const { api } = setup({ fixture, status: () => json(value) });
  await api.submitLive(REQUEST);
  await assert.rejects(api.getLiveExecution("exec-1"), ProtocolError);
  value = fixture.live("failed", { error: ERROR });
  await api.getLiveExecution("exec-1");
  value = { ...value, warnings: [{ code: "LATE", message: "Changed" }] };
  await assert.rejects(api.getLiveExecution("exec-1"), ProtocolError);
});

test("LIVE unsupported preflight rejects before POST; existing materialized remains selectable", async () => {
  const { api, calls } = setup({ preflight: () => json(CAPABILITIES) });
  await assert.rejects(api.submitLive(REQUEST), code("LIVE_UNSUPPORTED"));
  assert.deepEqual(calls.map(call => call.method), ["GET"]);
});

for (const maximum of [0, 30]) {
  test(`LIVE preflight rejects waitSeconds above advertised maximum ${maximum} before POST`, async () => {
    const { api, calls } = setup({ preflight: () => json({ ...capabilities, max_wait_seconds: maximum }) });
    await assert.rejects(api.submitLive(REQUEST, { waitSeconds: maximum + 1 }), ProtocolError);
    assert.deepEqual(calls.map(call => call.method), ["GET"]);
  });
}

test("LIVE preflight accepts waitSeconds zero when advertised maximum is zero", async () => {
  const { api, calls } = setup({ preflight: () => json({ ...capabilities, max_wait_seconds: 0 }) });
  assert.equal((await api.submitLive(REQUEST, { waitSeconds: 0 })).state, "running");
  assert.equal(calls.find(call => call.method === "POST").headers.get("Prefer"), "wait=0");
});

test("LIVE inspection/cancel remain explicit and do not preflight, submit, or open streams", async () => {
  const fixture = liveFixture();
  const { api, calls } = setup({ fixture, status: call => json(call.method === "POST"
    ? fixture.live("cancelled") : fixture.running) });
  assert.deepEqual(await api.getLiveExecution("exec-1"), fixture.running);
  assert.equal((await api.cancelLive("exec-1")).state, "cancelled");
  assert.deepEqual(calls.map(c => [c.method, c.url.pathname]),
    [["GET", "/queries/exec-1"], ["POST", "/queries/exec-1/cancel"]]);
});

test("LIVE readLiveBatches attaches to submission without a second POST or preflight", async () => {
  const { api, calls } = setup();
  const submitted = await api.submitLive(REQUEST);
  const result = await drain(api.readLiveBatches(submitted));
  assert.equal(result.execution.state, "succeeded");
  assert.equal(calls.filter(c => c.method === "POST").length, 1);
  assert.equal(calls.filter(c => c.url.pathname === "/capabilities").length, 1);
  await assert.rejects(collect(api.readLiveBatches(result.execution)), code("LIVE_STREAM_CONSUMED"));
  api.forgetExecution("exec-1");
  await assert.rejects(collect(api.readLiveBatches(result.execution)), code("LIVE_STREAM_CONSUMED"));
});

test("LIVE active concurrent readers cannot open a second stream", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes);
  const { api, calls } = setup({ fixture, data: () => body.response, status: () => json(fixture.running) });
  const iterator = api.readLiveBatches(fixture.running);
  await iterator.next();
  await assert.rejects(collect(api.readLiveBatches(fixture.running)), code("LIVE_STREAM_CONSUMED"));
  await iterator.return();
  assert.equal(calls.filter(c => c.url.pathname.startsWith("/results/")).length, 1);
});

test("LIVE consumption registry is bounded and fails closed without evicting a consumption fence", async () => {
  const fixture = liveFixture();
  const { api, calls } = setup({ fixture, initial: { ...fixture.running, execution_id: "submit-only" } });
  for (let i = 0; i < MAX_TRACKED_EXECUTIONS; i++) {
    const value = { ...fixture.succeeded, execution_id: `unique-${i}` };
    // The mismatching IPC identity fails after claiming the one-use fence.
    await assert.rejects(collect(api.readLiveBatches(value)), ProtocolError);
  }
  await assert.rejects(collect(api.readLiveBatches(fixture.succeeded)), code("LIVE_STREAM_LIMIT"));
  assert.equal(calls.filter(c => c.url.pathname.startsWith("/results/")).length, MAX_TRACKED_EXECUTIONS);
  const count = calls.length;
  await assert.rejects(api.executeLive(REQUEST), code("LIVE_STREAM_LIMIT"));
  await assert.rejects(collect(api.stream(REQUEST)), code("LIVE_STREAM_LIMIT"));
  assert.equal(calls.length, count, "Predictable saturation must not make capability or submission requests");
  assert.equal((await api.submitLive(REQUEST)).state, "running", "Submission-only remains a separate explicit operation");
  assert.equal(calls.filter(c => c.method === "POST").length, 1);
});

test("LIVE consuming submissions reserve remaining capacity before concurrent preflight", async () => {
  const ready = deferred(), preflight = deferred();
  const { api, calls } = setup({ preflight: () => { ready.resolve(); return preflight.promise; } });
  for (let index = 0; index < MAX_TRACKED_EXECUTIONS - 1; index++) api.consumedLive.add(`older-${index}`);
  const first = api.executeLive(REQUEST);
  await ready.promise;
  await assert.rejects(api.executeLive(REQUEST), code("LIVE_STREAM_LIMIT"));
  assert.equal(calls.length, 1, "Only the admitted call starts capability discovery");
  preflight.resolve(json(capabilities));
  assert.equal((await first).table.numRows, 2);
  assert.equal(api.reservedLive, 0);
  assert.equal(calls.filter(c => c.method === "POST").length, 1);
});

test("LIVE failed capability preflight releases its reserved reader capacity", async () => {
  let attempts = 0;
  const { api } = setup({ preflight: () => json(attempts++ === 0 ? CAPABILITIES : capabilities) });
  for (let index = 0; index < MAX_TRACKED_EXECUTIONS - 1; index++) api.consumedLive.add(`older-${index}`);
  await assert.rejects(api.executeLive(REQUEST), code("LIVE_UNSUPPORTED"));
  assert.equal(api.reservedLive, 0);
  assert.equal((await api.executeLive(REQUEST)).table.numRows, 2);
  assert.equal(api.reservedLive, 0);
});

test("LIVE terminal failure before acquisition releases capacity without clearing consumed ids", async () => {
  const fixture = liveFixture();
  const { api, calls } = setup({ fixture, initial: fixture.live("failed", { stream: undefined, error: ERROR }) });
  for (let index = 0; index < MAX_TRACKED_EXECUTIONS - 1; index++) api.consumedLive.add(`older-${index}`);
  await assert.rejects(api.executeLive(REQUEST), LiveQueryExecutionError);
  assert.equal(api.reservedLive, 0);
  assert.equal(api.consumedLive.size, MAX_TRACKED_EXECUTIONS - 1);
  assert.equal(calls.filter(c => c.url.pathname.startsWith("/results/")).length, 0);
});

test("LIVE observation history stays bounded independently of single-use consumption fences", async () => {
  const fixture = liveFixture();
  const { api, calls } = setup({ fixture, status: call => json(fixture.live("cancelled", {
    execution_id: decodeURIComponent(call.url.pathname.split("/").at(-1)),
  })) });
  for (let index = 0; index < MAX_TRACKED_EXECUTIONS * 2; index++) {
    await api.getLiveExecution(`history-${index}`);
  }
  assert.equal(api.liveExecutions.size, MAX_TRACKED_EXECUTIONS);
  assert.equal(api.liveExecutions.has("history-0"), false);
  assert.equal(api.consumedLive.size, 0);
  const count = calls.length;
  api.forgetExecution(`history-${MAX_TRACKED_EXECUTIONS * 2 - 1}`);
  assert.equal(api.liveExecutions.size, MAX_TRACKED_EXECUTIONS - 1);
  assert.equal(calls.length, count);
});

for (const [options, expected] of [
  [{ maxResultRows: 0 }, "RESULT_LIMIT_EXCEEDED"],
  [{ maxResultBytes: 32 }, "RESULT_LIMIT_EXCEEDED"],
]) {
  test(`LIVE incremental configured bounds: ${JSON.stringify(options)}`, async () => {
    const { api } = setup({ options });
    await assert.rejects(api.executeLive(REQUEST), code(expected));
  });
}

test("LIVE expiry is checked before obtaining result credentials", async () => {
  const fixture = liveFixture();
  fixture.stream.expires_at = "2000-01-01T00:00:00Z";
  let tokens = 0;
  const { api, calls } = setup({ fixture, options: { getAccessToken: () => { tokens++; return "SYNTHETIC"; } } });
  await assert.rejects(collect(api.readLiveBatches(fixture.running)), code("RESULT_EXPIRED"));
  assert.equal(tokens, 0);
  assert.equal(calls.length, 0);
});

test("LIVE already-open body may finish after stream-start expiry", async () => {
  const fixture = liveFixture();
  fixture.stream.expires_at = new Date(Date.now() + 200).toISOString();
  const body = gatedBody(fixture.bytes);
  const { api } = setup({ fixture, data: () => body.response });
  const iterator = api.stream(REQUEST);
  await iterator.next();
  await delay(210);
  body.finish();
  assert.equal((await drain(iterator)).execution.state, "succeeded");
});

for (const stage of ["preflight", "queued", "body"]) {
  test(`LIVE caller abort during ${stage} cancels locally without backend cancel`, async () => {
    const fixture = liveFixture();
    const gate = deferred();
    const abort = new AbortController();
    const body = gatedBody(fixture.bytes);
    const { api, calls } = setup({
      fixture, ...(stage === "queued" ? { initial: fixture.live("queued") } : {}),
      preflight: stage === "preflight" ? () => { gate.resolve(); return new Promise(() => {}); } : undefined,
      data: () => body.response, status: () => {
        gate.resolve();
        return json(stage === "queued" ? fixture.live("queued") : fixture.running);
      },
    });
    const iterator = api.stream(REQUEST, { signal: abort.signal });
    const first = iterator.next();
    const rejected = assert.rejects(stage === "body" ? first.then(() => iterator.next()) : first, code("ABORTED"));
    if (stage === "body") await first;
    else await gate.promise;
    abort.abort();
    await rejected;
    assert.ok(calls.every(c => !c.url.pathname.endsWith("/cancel")));
    if (stage === "body") assert.equal(body.cancelled, true);
  });
}

test("LIVE one overall deadline includes preflight, POST, and a stalled body", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes);
  const { api, calls } = setup({ fixture, data: () => body.response, options: { timeoutMs: 60 },
    preflight: async () => { await delay(30); return json(capabilities); },
    status: () => json(fixture.running),
  });
  await assert.rejects(api.executeLive(REQUEST), code("TIMEOUT"));
  assert.equal(body.cancelled, true);
  assert.equal(calls.filter(c => c.method === "POST").length, 1);
});

test("LIVE early return aborts in-flight status, releases body and does not cancel backend", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes);
  const polling = deferred();
  const { api, calls } = setup({ fixture, data: () => body.response, status: call => {
    polling.resolve(call.signal);
    return new Promise(() => {});
  } });
  const iterator = api.stream(REQUEST);
  await iterator.next();
  const statusSignal = await polling.promise;
  await iterator.return();
  assert.equal(statusSignal.aborted, true);
  assert.equal(body.cancelled, true);
  assert.equal(body.response.body.locked, false);
  const count = calls.length;
  await delay(10);
  assert.equal(calls.length, count);
  assert.ok(calls.every(c => !c.url.pathname.endsWith("/cancel")));
});

test("LIVE async progress mutations cannot rewrite identity; callback failure cleans up", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes);
  const { api } = setup({ fixture, data: () => body.response, status: () => json(fixture.running) });
  let progress = 0;
  const boom = new Error("SYNTHETIC_CALLBACK_FAILURE");
  const iterator = api.stream(REQUEST, { onProgress: async e => {
    e.model.revision = "caller mutation";
    await Promise.resolve();
    if (++progress > 1) throw boom;
  } });
  await iterator.next();
  await assert.rejects(iterator.next(), error => error === boom);
  assert.equal(body.cancelled, true);
  assert.equal((await api.getLiveExecution("exec-1")).model.revision, MODEL.revision);
});

test("LIVE concurrent cancel wins over older pending poll without regressing history", async () => {
  const fixture = liveFixture();
  const pending = deferred(), started = deferred();
  let delayed = true;
  const { api } = setup({ fixture, status: call => {
    if (call.method === "POST" || !delayed) return json(fixture.live("cancelled"));
    started.resolve();
    return pending.promise;
  } });
  await api.submitLive(REQUEST);
  const poll = api.getLiveExecution("exec-1");
  await started.promise;
  assert.equal((await api.cancelLive("exec-1")).state, "cancelled");
  delayed = false;
  pending.resolve(json(fixture.running));
  assert.equal((await poll).state, "cancelled");
  assert.equal((await api.getLiveExecution("exec-1")).state, "cancelled");
});

test("LIVE exact decimal and nanosecond buffers are preserved", async () => {
  const columns = [
    { column_id: "decimal", name: "decimal", logical_type: "Decimal", nullable: false },
    { column_id: "timestamp", name: "timestamp", logical_type: "DateTime", nullable: false },
  ];
  const fields = [new Decimal(9, 30, 128), new TimestampNanosecond()].map((type, i) =>
    new Field(columns[i].name, type, false, new Map([[COLUMN_METADATA_KEY, JSON.stringify({ column_id: columns[i].column_id })]])));
  const decimal = new Uint32Array([0xffffffff, 0x12345678, 0xabcdef12, 0]);
  const timestamp = new BigInt64Array([1700000000123456789n]);
  const fixture = liveFixture({ columns, fields, batchRows: [[[null, null]]], columnData: [
    makeData({ type: fields[0].type, length: 1, data: decimal }),
    makeData({ type: fields[1].type, length: 1, data: timestamp }),
  ] });
  const { api } = setup({ fixture });
  const { table } = await api.executeLive(REQUEST);
  assert.deepEqual(table.getChildAt(0).data[0].values, decimal);
  assert.deepEqual(table.getChildAt(1).data[0].values, timestamp);
});

for (const accepted of [false, true]) {
  test(`LIVE truncation is checked only after completion; acceptance=${accepted}`, async () => {
    const fixture = liveFixture({ manifest: { completeness: "truncated", truncation_reason: "SERVER_LIMIT" } });
    const { api } = setup({ fixture });
    const result = api.executeLive(REQUEST, { allowTruncated: accepted });
    if (accepted) assert.equal((await result).table.numRows, 2);
    else await assert.rejects(result, code("TRUNCATED_RESULT"));
  });
}

test("LIVE queued then running without a stream polls only until a descriptor is available", async () => {
  const fixture = liveFixture();
  const pending = [fixture.live("running", { stream: undefined }), fixture.running, fixture.succeeded];
  const { api, calls } = setup({ fixture, initial: fixture.live("queued"), status: () => json(pending.shift()) });
  const result = await api.executeLive(REQUEST);
  assert.equal(result.table.numRows, 2);
  const paths = calls.map(c => c.url.pathname);
  assert.ok(paths.indexOf("/results/result-1") < paths.lastIndexOf("/queries/exec-1"),
    "Download begins before the final status read");
});

for (const phase of ["submit", "download"]) {
  test(`LIVE ${phase} HTTP failure is not retried or silently materialized`, async () => {
    const problem = () => json({ type: "about:blank", title: "Unavailable", status: 503,
      error: { ...ERROR, code: "UNAVAILABLE", retryable: true } }, 503,
    { "Content-Type": "application/problem+json", "Retry-After": "0" });
    const fixture = liveFixture();
    const mock = mockFetch(call => {
      if (call.url.pathname === "/capabilities") return json(capabilities);
      if (call.url.pathname === "/queries") return phase === "submit" ? problem() : json(fixture.running, 202);
      if (call.url.pathname.startsWith("/results/")) return problem();
      return json(fixture.succeeded);
    });
    const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
    await assert.rejects(api.executeLive(REQUEST), code("UNAVAILABLE"));
    assert.equal(mock.calls.filter(c => c.method === "POST").length, 1);
    assert.equal(mock.calls.filter(c => c.url.pathname.startsWith("/results/")).length, phase === "submit" ? 0 : 1);
  });
}

test("LIVE malformed IPC cannot be cured by already succeeded status", async () => {
  const fixture = liveFixture();
  const { api } = setup({ fixture, initial: fixture.succeeded,
    data: () => arrowResponse(fixture.bytes.slice(0, -9)),
  });
  await assert.rejects(api.executeLive(REQUEST), error => {
    assert.ok(error instanceof ProtocolError);
    assert.match(error.message, /inside a message/);
    return true;
  });
});

for (const patch of [
  { contract_version: "0.1-draft" },
  { result_id: "wrong" },
  { model: { id: "sales-model", revision: "wrong" } },
  { delivery: "live" },
  { page_index: 0, row_offset: 0 },
]) {
  test(`LIVE rejects incorrect Arrow execution metadata: ${JSON.stringify(patch)}`, async () => {
    const fixture = liveFixture({ metadata: {
      contract_version: LIVE_CONTRACT_VERSION, execution_id: "exec-1", result_id: "result-1",
      data_source_id: "sales", model: MODEL, ...patch,
    } });
    const { api } = setup({ fixture });
    await assert.rejects(api.executeLive(REQUEST), ProtocolError);
  });
}

test("LIVE status failure stops a body that never supplies even a schema", async () => {
  const fixture = liveFixture();
  const body = gatedBody(fixture.bytes, 0);
  const { api } = setup({ fixture, data: () => body.response,
    status: () => { throw new Error("SYNTHETIC_STATUS_FAILURE"); },
  });
  await assert.rejects(api.executeLive(REQUEST), code("TRANSPORT_ERROR"));
  assert.equal(body.cancelled, true);
  assert.equal(body.response.body.locked, false);
});

test("LIVE timeout during preflight never POSTs", async () => {
  const { api, calls } = setup({ preflight: () => new Promise(() => {}), options: { timeoutMs: 20 } });
  await assert.rejects(api.executeLive(REQUEST), code("TIMEOUT"));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].signal.aborted, true);
});

test("LIVE original deadline remains TIMEOUT while verifying status after EOF", async () => {
  const { api } = setup({ status: () => new Promise(() => {}), options: { timeoutMs: 40 } });
  await assert.rejects(api.executeLive(REQUEST), code("TIMEOUT"));
});

test("LIVE original caller abort remains ABORTED while verifying status after EOF", async () => {
  const polled = deferred();
  const abort = new AbortController();
  const { api } = setup({ status: () => { polled.resolve(); return new Promise(() => {}); } });
  const result = api.executeLive(REQUEST, { signal: abort.signal });
  const rejected = assert.rejects(result, code("ABORTED"));
  await polled.promise;
  abort.abort();
  await rejected;
});

test("LIVE data protocol failure retains precise error when final status is unavailable", async () => {
  const fixture = liveFixture();
  const { api } = setup({ fixture, initial: fixture.succeeded,
    data: () => arrowResponse(fixture.bytes.slice(0, -9)),
    status: () => { throw new Error("SYNTHETIC_UNAVAILABLE"); },
  });
  await assert.rejects(api.executeLive(REQUEST), error => {
    assert.ok(error instanceof ProtocolError);
    assert.match(error.message, /inside a message/);
    return true;
  });
});

test("LIVE expiry is rechecked after delayed token acquisition without a result GET", async () => {
  const fixture = liveFixture();
  fixture.stream.expires_at = new Date(Date.now() + 50).toISOString();
  const { api, calls } = setup({ fixture, options: { getAccessToken: async () => {
    await delay(60);
    return "SYNTHETIC";
  } } });
  await assert.rejects(collect(api.readLiveBatches(fixture.succeeded)), code("RESULT_EXPIRED"));
  assert.equal(calls.filter(c => c.url.pathname.startsWith("/results/")).length, 0);
});

test("LIVE explicit cross-origin stream omits account tokens and cookies", async () => {
  const fixture = liveFixture();
  fixture.stream.url = "https://results.example/results/result-1";
  const { api, calls } = setup({ fixture, options: {
    allowedResultOrigins: ["https://results.example"], getAccessToken: () => "SYNTHETIC",
  } });
  await api.executeLive(REQUEST);
  const download = calls.find(c => c.url.origin === "https://results.example");
  assert.equal(download.headers.has("authorization"), false);
  assert.equal(download.credentials, "omit");
  assert.equal(download.redirect, "error");
  assert.equal(download.headers.has("range"), false);
});

test("LIVE IPC byte counts include HTTP-decoded bytes, not Content-Length", async () => {
  const fixture = liveFixture();
  const { api } = setup({ fixture, data: () => arrowResponse(fixture.bytes, {
    "Content-Length": "10", "Content-Encoding": "gzip",
  }) });
  assert.equal((await api.executeLive(REQUEST)).execution.completion.byte_count, fixture.bytes.length);
});

test("LIVE pending late poll cannot replace an advertised immutable descriptor", async () => {
  const fixture = liveFixture();
  const pending = deferred(), started = deferred();
  let reads = 0;
  const { api } = setup({ fixture, initial: fixture.live("queued"), status: () => {
    if (++reads === 1) { started.resolve(); return pending.promise; }
    return json(fixture.running);
  } });
  await api.submitLive(REQUEST);
  const stale = api.getLiveExecution("exec-1");
  await started.promise;
  await api.getLiveExecution("exec-1");
  pending.resolve(json(fixture.live("running", { stream: undefined })));
  assert.deepEqual((await stale).stream, fixture.stream);
});
