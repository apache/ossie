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
import { once } from "node:events";
import { createServer } from "node:http";
import test from "node:test";
import {
  ARROW_MEDIA_TYPE, HttpProblemError, OssieClient, ProtocolError, QueryExecutionError,
} from "../dist/index.js";
import {
  BINDING, CAPABILITIES, ERROR, ORIGIN, REQUEST,
  arrowFixture, arrowResponse, assertSafeError, execution, json, mockFetch,
} from "./fixtures.mjs";

const client = fetch => new OssieClient({ baseUrl: ORIGIN, fetch, pollIntervalMs: 1, timeoutMs: 1000 });

test("capabilities and binding are explicit discovery operations", async () => {
  const mock = mockFetch(call => {
    if (call.url.pathname === "/capabilities") return json(CAPABILITIES);
    assert.equal(call.url.pathname, "/bindings/sales");
    return json(BINDING);
  });
  const api = client(mock.fetch);
  assert.deepEqual(await api.getCapabilities(), CAPABILITIES);
  assert.deepEqual(await api.getBinding("sales"), BINDING);
  assert.equal(mock.calls.length, 2);
});

test("submit preserves Layer 3 structure and limit zero; it never sends SQL", async () => {
  const mock = mockFetch(() => json(execution("queued"), 202));
  const api = client(mock.fetch);
  assert.equal((await api.submit(REQUEST, { waitSeconds: 2 })).state, "queued");
  assert.equal(mock.calls.length, 1);
  const [call] = mock.calls;
  assert.equal(call.url.pathname, "/queries");
  assert.equal(call.method, "POST");
  assert.deepEqual(JSON.parse(call.body), REQUEST);
  assert.equal(call.headers.get("Content-Type"), "application/json");
  assert.match(call.headers.get("Prefer") ?? "", /wait=2/);
});

test("scalar query is preserved without aggregation clauses", async () => {
  const request = { data_source_id: "sales", query: { fields: ["region"], limit: 0 } };
  const mock = mockFetch(() => json(execution("queued"), 202));
  await client(mock.fetch).submit(request);
  assert.deepEqual(JSON.parse(mock.calls[0].body), request);
});

test("immediate success executes exactly one submit and one materialized result fetch", async () => {
  const fixture = arrowFixture();
  const mock = mockFetch(call => call.method === "POST" ? json(fixture.succeeded) : arrowResponse(fixture.bytes));
  const result = await client(mock.fetch).execute(REQUEST);
  assert.equal(result.execution.execution_id, "exec-1");
  assert.equal(result.table.numRows, 2);
  assert.equal(result.table.getChildAt(1).get(0), 9007199254740993n);
  assert.equal(result.table.getChildAt(0).get(1), null);
  assert.deepEqual(mock.calls.map(call => call.method), ["POST", "GET"]);
});

test("queued/running/progress/succeeded poll loop preserves execution identity", async () => {
  const fixture = arrowFixture();
  const states = [
    execution("queued"),
    execution("running", { progress: { fraction: 0.25, phase: "scan" } }),
    fixture.succeeded,
  ];
  const progress = [];
  const mock = mockFetch(call => json(states.shift(), call.method === "POST" ? 202 : 200));
  const api = client(mock.fetch);
  const initial = await api.submit(REQUEST);
  const completed = await api.wait(initial, { onProgress: value => progress.push(value) });
  assert.equal(completed.state, "succeeded");
  assert.ok(progress.some(value => value.state === "running" && value.progress?.fraction === 0.25));
  assert.equal(mock.calls.filter(call => call.method === "POST").length, 1);
  for (const call of mock.calls.slice(1)) assert.equal(call.url.pathname, "/queries/exec-1");
});

test("HTTP 200 failed execution preserves replacement query without automatic execution", async () => {
  const failed = execution("failed", { error: ERROR });
  const mock = mockFetch(() => json(failed));
  const api = client(mock.fetch);
  assert.deepEqual(await api.submit(REQUEST), failed);
  await assert.rejects(api.execute(REQUEST), error => {
    assert.ok(error instanceof QueryExecutionError);
    const serialized = JSON.stringify(error);
    assert.ok(serialized.includes("replacement_query"), "Query error retains structured replacement query");
    assert.ok(serialized.includes("revenue"));
    return true;
  });
  assert.equal(mock.calls.length, 2, "One submit per explicit request, no replacement execution");
});

test("poll failure is a query failure, not an HTTP transport failure", async () => {
  const mock = mockFetch(() => json(execution("failed", { error: ERROR })));
  await assert.rejects(client(mock.fetch).wait(execution()), QueryExecutionError);
  assert.equal(mock.calls.length, 1);
});

test("cancellation acknowledges cancelling then observes cancelled", async () => {
  const mock = mockFetch(call => json(execution(call.method === "POST" ? "cancelling" : "cancelled"),
    call.method === "POST" ? 202 : 200));
  const api = client(mock.fetch);
  const cancelled = await api.cancel("exec-1");
  assert.equal(cancelled.state, "cancelling");
  assert.equal(mock.calls[0].url.pathname, "/queries/exec-1/cancel");
  await assert.rejects(api.wait(cancelled), assertSafeError);
  assert.equal(mock.calls.length, 2);
});

test("cancellation racing completion preserves succeeded state and result", async () => {
  const fixture = arrowFixture();
  const mock = mockFetch(() => json(fixture.succeeded));
  assert.deepEqual(await client(mock.fetch).cancel("exec-1"), fixture.succeeded);
  assert.equal(mock.calls.length, 1);
});

for (const status of [429, 500, 502, 503]) {
  test(`submit is not retried on transient HTTP ${status}`, async () => {
    const mock = mockFetch(() => json({
      type: "about:blank", title: "Unavailable", status,
      error: { code: "UNAVAILABLE", message: "Please retry", retryable: true },
    }, status, { "Retry-After": "0", "Content-Type": "application/problem+json" }));
    await assert.rejects(client(mock.fetch).submit(REQUEST), error => {
      assert.ok(error instanceof HttpProblemError);
      assert.equal(error.code, "UNAVAILABLE");
      assert.equal(error.status, status);
      return true;
    });
    assert.equal(mock.calls.length, 1);
  });
}

test("HTTP problem details retain query replacement without running it", async () => {
  const problem = { type: "about:blank", title: "Invalid query", status: 400, error: ERROR };
  const mock = mockFetch(() => json(problem, 400, { "Content-Type": "application/problem+json" }));
  await assert.rejects(client(mock.fetch).execute(REQUEST), error => {
    assert.ok(error instanceof HttpProblemError);
    assert.deepEqual(error.problem, problem);
    return true;
  });
  assert.equal(mock.calls.length, 1);
});

test("a timeout also bounds an unresponsive credential callback, before fetch", { timeout: 2000 }, async () => {
  const mock = mockFetch(() => json(CAPABILITIES));
  const api = new OssieClient({
    baseUrl: ORIGIN, fetch: mock.fetch, timeoutMs: 20,
    getAccessToken: () => new Promise(() => {}),
  });
  await assert.rejects(api.getCapabilities(), assertSafeError);
  assert.equal(mock.calls.length, 0);
});

test("wait uses an overall deadline rather than resetting it on every successful poll", { timeout: 2000 }, async () => {
  const mock = mockFetch(() => json(execution("running")));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, timeoutMs: 200, pollIntervalMs: 5 });
  await assert.rejects(api.wait(execution("queued")), error => {
    assert.equal(error.code, "TIMEOUT");
    return true;
  });
  assert.ok(mock.calls.length >= 1);
  assert.ok(mock.calls.every(call => call.method === "GET"), "Timeout is local; it does not issue backend cancellation");
});

test("caller progress callback cannot mutate the execution used for polling", async () => {
  const fixture = arrowFixture();
  const mock = mockFetch(() => json(fixture.succeeded));
  const completed = await client(mock.fetch).wait(execution("queued"), {
    onProgress(value) { value.execution_id = "malicious-change"; value.model.revision = "different"; },
  });
  assert.equal(completed.execution_id, "exec-1");
  assert.equal(completed.model.revision, "revision-1");
  assert.equal(mock.calls[0].url.pathname, "/queries/exec-1");
});

test("submit transport failure is sanitized and never retried", async () => {
  const secret = "transport-bearer-secret";
  const mock = mockFetch(() => { throw new TypeError(`fetch failed https://secret.invalid/?token=${secret}`); });
  await assert.rejects(client(mock.fetch).submit(REQUEST), error => assertSafeError(error, [secret]));
  assert.equal(mock.calls.length, 1);
});

test("JSON body transport failures are typed, sanitized and never cause a submit retry", async () => {
  const secret = "BODY_STREAM_SYNTHETIC_SECRET";
  const mock = mockFetch(() => new Response(new ReadableStream({
    start(controller) { controller.error(new Error(`socket failed https://host.invalid/?token=${secret}`)); },
  }), { headers: { "Content-Type": "application/json" } }));
  await assert.rejects(client(mock.fetch).submit(REQUEST), error => assertSafeError(error, [secret]));
  assert.equal(mock.calls.length, 1);
});

test("AbortSignal stops a request and an already-aborted call makes no network request", async () => {
  const controller = new AbortController();
  controller.abort();
  const mock = mockFetch(() => json(execution()));
  await assert.rejects(client(mock.fetch).submit(REQUEST, { signal: controller.signal }), assertSafeError);
  assert.equal(mock.calls.length, 0);
});

test("request timeout includes a stalled JSON response body", { timeout: 2000 }, async () => {
  let cancelled = false;
  const mock = mockFetch(() => new Response(new ReadableStream({
    start(controller) { controller.enqueue(new TextEncoder().encode("{")); },
    cancel() { cancelled = true; },
  }), { headers: { "Content-Type": "application/json" } }));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, timeoutMs: 30 });
  await assert.rejects(api.submit(REQUEST), assertSafeError);
  assert.ok(cancelled, "Timed out JSON stream is cancelled");
});

test("abort includes a stalled JSON response body", { timeout: 2000 }, async () => {
  const controller = new AbortController();
  const mock = mockFetch(() => new Response(new ReadableStream({
    start(stream) {
      stream.enqueue(new TextEncoder().encode("{"));
      setTimeout(() => controller.abort(), 20);
    },
  }), { headers: { "Content-Type": "application/json" } }));
  await assert.rejects(client(mock.fetch).submit(REQUEST, { signal: controller.signal }), assertSafeError);
});

test("two synthetic HTTP behaviours work over real loopback fetch, not vendor engines", async t => {
  for (const behaviour of ["immediate", "asynchronous"]) {
    await t.test(behaviour, { timeout: 5000 }, async () => {
      const fixture = arrowFixture({ rows: behaviour === "immediate" ? [["Now", 3n]] : [["Later", 8n], ["Later", 9n]] });
      const requests = [];
      let polls = 0;
      const server = createServer(async (request, response) => {
        requests.push({ method: request.method, url: request.url });
        if (request.url === "/results/result-1") {
          response.writeHead(200, { "Content-Type": ARROW_MEDIA_TYPE });
          response.end(fixture.bytes);
          return;
        }
        const body = [];
        for await (const chunk of request) body.push(chunk);
        if (request.method === "POST") assert.deepEqual(JSON.parse(Buffer.concat(body).toString()), REQUEST);
        response.writeHead(behaviour === "asynchronous" && request.method === "POST" ? 202 : 200,
          { "Content-Type": "application/json" });
        if (behaviour === "immediate" || (request.method === "GET" && ++polls >= 2)) {
          response.end(JSON.stringify(fixture.succeeded));
        } else {
          response.end(JSON.stringify(execution(request.method === "POST" ? "queued" : "running")));
        }
      });
      server.listen(0, "127.0.0.1");
      await once(server, "listening");
      const baseUrl = `http://127.0.0.1:${server.address().port}`;
      fixture.succeeded.result.url = `${baseUrl}/results/result-1`;
      try {
        const api = new OssieClient({ baseUrl, allowInsecureLocalhost: true, pollIntervalMs: 1, timeoutMs: 2000 });
        const result = await api.execute(REQUEST);
        assert.equal(result.table.numRows, behaviour === "immediate" ? 1 : 2);
        assert.equal(result.table.getChildAt(0).get(0), behaviour === "immediate" ? "Now" : "Later");
        assert.equal(requests.filter(request => request.method === "POST").length, 1);
        assert.equal(polls, behaviour === "immediate" ? 0 : 2);
      } finally {
        server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
      }
    });
  }
});

test("malformed JSON is a protocol error, not a query failure", async () => {
  const mock = mockFetch(() => new Response("{broken", { headers: { "Content-Type": "application/json" } }));
  await assert.rejects(client(mock.fetch).submit(REQUEST), ProtocolError);
});
