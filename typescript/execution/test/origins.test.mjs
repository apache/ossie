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
import { OssieClient } from "../dist/index.js";
import {
  CAPABILITIES, ORIGIN, arrowFixture, arrowResponse, assertSafeError,
  collect, json, mockFetch,
} from "./fixtures.mjs";

test("same-origin requests use authentication but disable redirect following", async () => {
  const fixture = arrowFixture();
  const mock = mockFetch(call => call.url.pathname === "/capabilities" ? json(CAPABILITIES) : arrowResponse(fixture.bytes));
  const api = new OssieClient({
    baseUrl: ORIGIN, fetch: mock.fetch, getAccessToken: async () => "test-token-not-real",
  });
  await api.getCapabilities();
  await collect(api.readBatches(fixture.succeeded));
  assert.equal(mock.calls.length, 2);
  for (const call of mock.calls) {
    assert.equal(call.headers.get("Authorization"), "Bearer test-token-not-real");
    assert.ok(["same-origin", "omit"].includes(call.credentials));
    assert.ok(["manual", "error"].includes(call.redirect), "Redirects cannot forward authentication to another origin");
  }
});

test("explicit allowlisted cross-origin results omit Authorization and browser credentials", async () => {
  const fixture = arrowFixture({ manifest: { url: "https://results.example/result?sig=not-a-real-signature" } });
  let tokenRequests = 0;
  const mock = mockFetch(() => arrowResponse(fixture.bytes));
  const api = new OssieClient({
    baseUrl: ORIGIN, fetch: mock.fetch, allowedResultOrigins: ["https://results.example"],
    getAccessToken() { tokenRequests++; return "control-plane-token"; },
  });
  await collect(api.readBatches(fixture.succeeded));
  assert.equal(mock.calls[0].headers.get("Authorization"), null);
  assert.equal(mock.calls[0].credentials, "omit");
  assert.equal(tokenRequests, 0);
});

for (const url of [
  "https://untrusted.example/result",
  "https://results.example.attacker.example/result",
  "https://user:password@results.example/result",
  "https://results.example/result#fragment",
  "http://results.example/result",
  "file:///C:/private/file.arrow",
  "data:application/vnd.apache.arrow.stream;base64,AAAA",
  "/results/result-1",
]) {
  test(`unsafe result destination is rejected before fetch: ${new URL(url, ORIGIN).protocol} ${new URL(url, ORIGIN).hostname}`, async () => {
    const fixture = arrowFixture({ manifest: { url } });
    const mock = mockFetch(() => arrowResponse(fixture.bytes));
    const api = new OssieClient({
      baseUrl: ORIGIN, fetch: mock.fetch, allowedResultOrigins: ["https://results.example"],
      allowInsecureLocalhost: true,
    });
    await assert.rejects(collect(api.readBatches(fixture.succeeded)), assertSafeError);
    assert.equal(mock.calls.length, 0);
  });
}

for (const baseUrl of [
  "http://ossie.example", "http://127.0.0.1:8000",
  "https://user:password@ossie.example", "https://ossie.example/#private",
  "https://ossie.example/?secret=token",
]) {
  test(`unsafe control plane URL is rejected: ${baseUrl}`, () => {
    assert.throws(() => new OssieClient({ baseUrl }), assertSafeError);
  });
}

test("explicit local-development exception permits loopback HTTP, never arbitrary cleartext hosts", () => {
  for (const baseUrl of ["http://127.0.0.1:8000", "http://localhost:8000", "http://[::1]:8000"]) {
    assert.doesNotThrow(() => new OssieClient({ baseUrl, allowInsecureLocalhost: true }));
  }
  for (const baseUrl of ["http://ossie.example", "http://localhost.attacker.example", "http://10.0.0.1"]) {
    assert.throws(() => new OssieClient({ baseUrl, allowInsecureLocalhost: true }), assertSafeError);
  }
});

test("result redirects are not followed even to another allowlisted origin", async () => {
  const fixture = arrowFixture();
  const mock = mockFetch(() => new Response(null, { status: 302, headers: { Location: "https://results.example/next" } }));
  const api = new OssieClient({
    baseUrl: ORIGIN, fetch: mock.fetch, allowedResultOrigins: ["https://results.example"],
    getAccessToken: () => "test-token-not-real",
  });
  await assert.rejects(collect(api.readBatches(fixture.succeeded)), assertSafeError);
  assert.equal(mock.calls.length, 1);
  assert.ok(["manual", "error"].includes(mock.calls[0].redirect));
});

test("control-plane redirects are not followed", async () => {
  const mock = mockFetch(() => new Response(null, { status: 307, headers: { Location: "https://ossie.example/new" } }));
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch });
  await assert.rejects(api.getCapabilities(), assertSafeError);
  assert.equal(mock.calls.length, 1);
  assert.ok(["manual", "error"].includes(mock.calls[0].redirect));
});

test("signed result URL and token do not appear in result transport errors", async () => {
  const signature = "SYNTHETIC_SECRET_SIGNATURE";
  const token = "SYNTHETIC_SECRET_BEARER";
  const fixture = arrowFixture({ manifest: { url: `${ORIGIN}/result?sig=${signature}` } });
  const mock = mockFetch(call => { throw new Error(`fetch ${call.url.href} Authorization: Bearer ${token}`); });
  const api = new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch, getAccessToken: () => token });
  await assert.rejects(collect(api.readBatches(fixture.succeeded)), error =>
    assertSafeError(error, [signature, token, fixture.succeeded.result.url]));
});

test("expired result is rejected without fetching its signed URL", async () => {
  const fixture = arrowFixture({ manifest: { expires_at: "2000-01-01T00:00:00Z" } });
  const mock = mockFetch(() => arrowResponse(fixture.bytes));
  await assert.rejects(collect(new OssieClient({ baseUrl: ORIGIN, fetch: mock.fetch }).readBatches(fixture.succeeded)), assertSafeError);
  assert.equal(mock.calls.length, 0);
});
