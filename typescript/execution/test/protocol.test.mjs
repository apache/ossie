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
import { OssieClient, ProtocolError } from "../dist/index.js";
import {
  BINDING, CAPABILITIES, ERROR, ORIGIN, REQUEST,
  arrowFixture, execution, json, mockFetch,
} from "./fixtures.mjs";

const client = fetch => new OssieClient({ baseUrl: ORIGIN, fetch, pollIntervalMs: 1, timeoutMs: 1000 });

const badQueries = [
  ["mixed scalar and aggregation", { fields: ["region"], measures: ["revenue"] }],
  ["scalar having", { fields: ["region"], having: "revenue > 0" }],
  ["empty scalar", { fields: [] }],
  ["empty aggregation", {}],
  ["empty dimensions and measures", { dimensions: [], measures: [] }],
  ["negative limit", { measures: ["revenue"], limit: -1 }],
  ["fractional limit", { measures: ["revenue"], limit: 0.5 }],
  ["unsafe integer limit", { measures: ["revenue"], limit: Number.MAX_SAFE_INTEGER + 1 }],
  ["nonfinite limit", { measures: ["revenue"], limit: Infinity }],
  ["unknown clause", { measures: ["revenue"], sql: "select * from sales" }],
  ["empty measure", { measures: [""] }],
  ["invalid ordering", { measures: ["revenue"], order_by: [{ field: "revenue", direction: "SIDEWAYS" }] }],
  ["empty ordering", { measures: ["revenue"], order_by: [] }],
];

for (const [name, query] of badQueries) {
  test(`rejects ${name} before submission`, async () => {
    const mock = mockFetch(() => json(execution()));
    await assert.rejects(async () => client(mock.fetch).submit({ ...REQUEST, query }), ProtocolError);
    assert.equal(mock.calls.length, 0);
  });
}

for (const [name, extra] of [
  ["unknown contract version", { contract_version: "future-1" }],
  ["unknown state", { state: "done" }],
  ["unknown property", { surprising: true }],
  ["running execution with error", { error: ERROR }],
  ["running execution with result", { result: arrowFixture().succeeded.result }],
  ["invalid progress", { progress: { fraction: 1.1 } }],
  ["invalid warning", { warnings: [{ message: "No code" }] }],
  ["nonobject extension", { extensions: { "com.example.custom": true } }],
]) {
  test(`rejects ${name} in execution envelope`, async () => {
    const mock = mockFetch(() => json(execution("running", extra)));
    await assert.rejects(client(mock.fetch).getExecution("exec-1"), ProtocolError);
  });
}

for (const state of ["failed", "succeeded"]) {
  test(`rejects ${state} missing its mandatory payload`, async () => {
    const mock = mockFetch(() => json(execution(state)));
    await assert.rejects(client(mock.fetch).getExecution("exec-1"), ProtocolError);
  });
}

for (const [name, mutation] of [
  ["execution id", value => { value.execution_id = "exec-2"; }],
  ["data source", value => { value.data_source_id = "other-source"; }],
  ["model id", value => { value.model.id = "other-model"; }],
  ["model revision", value => { value.model.revision = "revision-2"; }],
]) {
  test(`poll rejects mutable ${name}`, async () => {
    const changed = execution();
    mutation(changed);
    const mock = mockFetch(() => json(changed));
    await assert.rejects(client(mock.fetch).wait(execution()), ProtocolError);
    assert.equal(mock.calls.length, 1);
  });
}

test("getExecution rejects a response for another execution id", async () => {
  const mock = mockFetch(() => json(execution("running", { execution_id: "exec-other" })));
  await assert.rejects(client(mock.fetch).getExecution("exec-1"), ProtocolError);
});

test("terminal execution state and immutable result cannot change on later reads", async () => {
  for (const mutation of [
    value => { value.state = "running"; delete value.result; },
    value => { value.result.result_id = "replacement"; },
    value => { value.result.url = `${ORIGIN}/replacement`; },
    value => { value.result.row_count++; },
  ]) {
    const { succeeded } = arrowFixture();
    const altered = structuredClone(succeeded);
    mutation(altered);
    const responses = [succeeded, altered];
    const mock = mockFetch(() => json(responses.shift()));
    const api = client(mock.fetch);
    await api.getExecution("exec-1");
    await assert.rejects(api.getExecution("exec-1"), ProtocolError);
  }
});

test("client snapshots execution history instead of trusting caller-mutated responses", async () => {
  const mock = mockFetch(() => json(execution()));
  const api = client(mock.fetch);
  const returned = await api.getExecution("exec-1");
  returned.model.revision = "mutated";
  assert.equal((await api.getExecution("exec-1")).model.revision, "revision-1");
});

test("submit rejects a response for another data source", async () => {
  const mock = mockFetch(() => json(execution("queued", { data_source_id: "other-source" })));
  await assert.rejects(client(mock.fetch).submit(REQUEST), ProtocolError);
});

for (const operation of ["submit", "cancel"]) {
  test(`${operation} enforces HTTP 200 terminal and HTTP 202 pending state consistency`, async () => {
    const { succeeded } = arrowFixture();
    for (const [value, status] of [[execution("queued"), 200], [succeeded, 202]]) {
      const mock = mockFetch(() => json(value, status));
      const api = client(mock.fetch);
      await assert.rejects(operation === "submit" ? api.submit(REQUEST) : api.cancel("exec-1"), ProtocolError);
      assert.equal(mock.calls.length, 1);
    }
  });
}

for (const [name, change] of [
  ["unsafe row count", value => { value.row_count = Number.MAX_SAFE_INTEGER + 1; }],
  ["unsafe byte count", value => { value.byte_count = Number.MAX_SAFE_INTEGER + 1; }],
  ["negative count", value => { value.row_count = -1; }],
  ["fractional count", value => { value.byte_count = 0.5; }],
  ["duplicate column id", value => { value.columns[1].column_id = value.columns[0].column_id; }],
  ["unknown logical type", value => { value.columns[0].logical_type = "GuessFromJSON"; }],
  ["unknown column attribute", value => { value.columns[0].guess = true; }],
  ["missing truncation reason", value => { value.completeness = "truncated"; }],
  ["complete with truncation reason", value => { value.truncation_reason = "unneeded"; }],
  ["invalid expiry", value => { value.expires_at = "never"; }],
  ["relative result URL", value => { value.url = "/results/result-1"; }],
]) {
  test(`rejects malformed result manifest: ${name}`, async () => {
    const { succeeded } = arrowFixture();
    change(succeeded.result);
    const mock = mockFetch(() => json(succeeded));
    await assert.rejects(client(mock.fetch).getExecution("exec-1"), ProtocolError);
  });
}

test("capabilities revision mismatches and unknown fields fail closed", async () => {
  for (const mutation of [
    { contract_version: "0.2" }, { foundation_revision: "old" },
    { query_schema_version: "invented" }, { result_formats: ["json"] },
    { silent_feature: true }, { max_wait_seconds: Number.MAX_SAFE_INTEGER + 1 },
  ]) {
    const mock = mockFetch(() => json({ ...CAPABILITIES, ...mutation }));
    await assert.rejects(client(mock.fetch).getCapabilities(), ProtocolError);
  }
});

test("binding identity is checked and extensions remain namespaced", async () => {
  const mock = mockFetch(() => json({ ...BINDING, data_source_id: "wrong" }));
  await assert.rejects(client(mock.fetch).getBinding("sales"), ProtocolError);
  const extension = { "com.example.metrics": { region: "west", enabled: true } };
  const valid = mockFetch(() => json(execution("running", { extensions: extension })));
  assert.deepEqual((await client(valid.fetch).getExecution("exec-1")).extensions, extension);
});

test("binding path values cannot escape the configured control-plane base path", async () => {
  const id = "sales/../../private?token=x#fragment";
  const mock = mockFetch(() => json({ ...BINDING, data_source_id: id }));
  const api = new OssieClient({ baseUrl: `${ORIGIN}/api/v1`, fetch: mock.fetch });
  await api.getBinding(id);
  assert.equal(mock.calls.length, 1);
  assert.ok(mock.calls[0].url.pathname.startsWith("/api/v1/bindings/"));
  assert.equal(mock.calls[0].url.search, "");
  assert.equal(mock.calls[0].url.hash, "");
});

test("literal dot-segment opaque IDs are rejected before any network request", async () => {
  for (const method of ["getBinding", "getExecution", "cancel"]) {
    for (const id of [".", ".."]) {
      assert.equal(encodeURIComponent(id), id, "URI encoding alone does not protect dot segments");
      const mock = mockFetch(() => json(execution()));
      const api = new OssieClient({ baseUrl: `${ORIGIN}/api/v1`, fetch: mock.fetch });
      await assert.rejects(async () => api[method](id), ProtocolError);
      assert.equal(mock.calls.length, 0, `${method} must reject ${id} before URL normalization or fetch`);
    }
  }
});

test("encoded dots and slashes remain opaque IDs instead of becoming traversal segments", async () => {
  for (const id of ["%2e", "%2e%2e", "../admin", "a/../admin"]) {
    const mock = mockFetch(() => json(execution("running", { execution_id: id })));
    const api = new OssieClient({ baseUrl: `${ORIGIN}/api/v1`, fetch: mock.fetch });
    assert.equal((await api.getExecution(id)).execution_id, id);
    assert.equal(mock.calls.length, 1);
    assert.equal(mock.calls[0].url.pathname, `/api/v1/queries/${encodeURIComponent(id)}`);
    assert.equal(mock.calls[0].url.search, "");
    assert.equal(mock.calls[0].url.hash, "");
  }
});

test("waitSeconds is a bounded nonnegative safe integer", async () => {
  for (const waitSeconds of [-1, 0.5, Infinity, Number.MAX_SAFE_INTEGER + 1]) {
    const mock = mockFetch(() => json(execution()));
    await assert.rejects(async () => client(mock.fetch).submit(REQUEST, { waitSeconds }), ProtocolError);
    assert.equal(mock.calls.length, 0);
  }
});
