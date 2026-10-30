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
import { ExecutionCancelledError, MAX_TRACKED_EXECUTIONS, OssieClient, ProtocolError } from "../dist/index.js";
import { ERROR, ORIGIN, arrowFixture, collect, execution, json, mockFetch } from "./fixtures.mjs";

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

test("a pending poll arriving after confirmed cancellation preserves cancellation", async () => {
  const started = deferred(), held = deferred();
  const mock = mockFetch(call => {
    if (call.method === "POST") return json(execution("cancelled"));
    started.resolve();
    return held.promise;
  });
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, pollIntervalMs: 1 });
  const waiting = assert.rejects(api.wait(execution("queued")), ExecutionCancelledError);
  await started.promise;
  assert.equal((await api.cancel("exec-1")).state, "cancelled");
  held.resolve(json(execution("running")));
  await waiting;
  assert.equal(mock.calls.length, 2);
});

test("a stale concurrent poll still cannot change execution identity", async () => {
  const started = deferred(), held = deferred();
  const mock = mockFetch(call => {
    if (call.method === "POST") return json(execution("cancelled"));
    started.resolve();
    return held.promise;
  });
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, pollIntervalMs: 1 });
  const waiting = assert.rejects(api.wait(execution("queued")), ProtocolError);
  await started.promise;
  await api.cancel("exec-1");
  held.resolve(json(execution("running", { data_source_id: "different-source" })));
  await waiting;
});

test("waiting with an older submitted object observes an already confirmed cancellation", async () => {
  const mock = mockFetch(() => json(execution("cancelled")));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
  await api.cancel("exec-1");
  await assert.rejects(api.wait(execution("queued")), ExecutionCancelledError);
  assert.equal(mock.calls.length, 1);
});

test("sequential lifecycle regressions remain protocol errors", async () => {
  let calls = 0;
  const mock = mockFetch(() => json(execution(calls++ === 0 ? "running" : "queued")));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
  await api.getExecution("exec-1");
  await assert.rejects(api.getExecution("exec-1"), ProtocolError);
});

for (const [name, mutate] of [
  ["error code", state => { state.error.code = "CHANGED"; }],
  ["replacement query", state => { state.error.replacement_query = { fields: ["other"] }; }],
  ["terminal warnings", state => { state.warnings = [{ code: "CHANGED", message: "changed" }]; }],
  ["terminal extensions", state => { state.extensions = { "example.test": { changed: true } }; }],
]) {
  test(`terminal immutability includes ${name}`, async () => {
    const original = execution("failed", { error: structuredClone(ERROR) });
    const changed = structuredClone(original);
    mutate(changed);
    const responses = [original, changed];
    const mock = mockFetch(() => json(responses.shift()));
    const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
    await api.getExecution("exec-1");
    await assert.rejects(api.getExecution("exec-1"), ProtocolError);
  });
}

test("terminal comparison ignores JSON object property order", async () => {
  const original = execution("failed", { error: structuredClone(ERROR) });
  const reordered = Object.fromEntries(Object.entries(original).reverse());
  const responses = [original, reordered];
  const mock = mockFetch(() => json(responses.shift()));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
  await api.getExecution("exec-1");
  assert.deepEqual(await api.getExecution("exec-1"), original);
});

test("execution history is bounded and can be explicitly released without network calls", async () => {
  const mock = mockFetch(call => json(execution("cancelled", {
    execution_id: decodeURIComponent(call.url.pathname.split("/").at(-1)),
    extensions: { "example.test": { padding: "x".repeat(128 * 1024) } },
  })));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
  for (let index = 0; index < MAX_TRACKED_EXECUTIONS * 2; index++) {
    await api.getExecution(`exec-${index}`);
  }
  assert.equal(api.executions.size, MAX_TRACKED_EXECUTIONS);
  assert.equal(api.executions.has("exec-0"), false);
  const count = mock.calls.length;
  api.forgetExecution(`exec-${MAX_TRACKED_EXECUTIONS * 2 - 1}`);
  assert.equal(api.executions.size, MAX_TRACKED_EXECUTIONS - 1);
  api.forgetExecution("never-seen");
  assert.equal(mock.calls.length, count);
});

test("an active wait validates its previous state even after history eviction", async () => {
  const started = deferred(), held = deferred();
  const mock = mockFetch(call => {
    const id = decodeURIComponent(call.url.pathname.split("/").at(-1));
    if (id !== "exec-1") return json(execution("cancelled", { execution_id: id }));
    started.resolve();
    return held.promise;
  });
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, pollIntervalMs: 1 });
  const waiting = assert.rejects(api.wait(execution("running")), ProtocolError);
  await started.promise;
  for (let index = 0; index < MAX_TRACKED_EXECUTIONS; index++) {
    await api.getExecution(`other-${index}`);
  }
  assert.equal(api.executions.has("exec-1"), false);
  held.resolve(json(execution("queued")));
  await waiting;
});

test("result expiry is rechecked after asynchronous token refresh before issuing GET", async context => {
  let now = Date.now();
  context.mock.method(Date, "now", () => now);
  const expires = now + 1000;
  const fixture = arrowFixture({ manifest: { expires_at: new Date(expires).toISOString() } });
  let fetches = 0;
  const api = new OssieClient({
    baseUrl: ORIGIN,
    getAccessToken: async () => {
      now = expires + 1;
      return "SYNTHETIC_TOKEN";
    },
    fetch: async () => {
      fetches++;
      throw new Error("Expired URL must never be fetched");
    },
  });
  await assert.rejects(collect(api.readBatches(fixture.succeeded)), error => error.code === "RESULT_EXPIRED");
  assert.equal(fetches, 0);
});

test("repopulated history cannot replace an active wait's own model identity", async () => {
  const started = deferred(), resume = deferred();
  const fixture = arrowFixture();
  fixture.succeeded.model = { id: "sales-model", revision: "different-revision" };
  const mock = mockFetch(call => {
    const id = decodeURIComponent(call.url.pathname.split("/").at(-1));
    return json(id === "exec-1" ? fixture.succeeded : execution("cancelled", { execution_id: id }));
  });
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, pollIntervalMs: 1 });
  const waiting = assert.rejects(api.wait(execution("running"), {
    onProgress: async () => {
      started.resolve();
      await resume.promise;
    },
  }), ProtocolError);
  await started.promise;
  for (let index = 0; index < MAX_TRACKED_EXECUTIONS; index++) {
    await api.getExecution(`other-${index}`);
  }
  await api.getExecution("exec-1");
  resume.resolve();
  await waiting;
});
