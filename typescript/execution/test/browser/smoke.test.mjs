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
import { spawn } from "node:child_process";
import { once } from "node:events";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { delimiter, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { build } from "esbuild";
import { ARROW_MEDIA_TYPE, LIVE_CONTRACT_VERSION, LIVE_FEATURE } from "../../dist/index.js";
import { BINDING, CAPABILITIES, ERROR, REQUEST, arrowFixture, execution, pagedFixture } from "../fixtures.mjs";
import { deferred, firstBatchEnd, liveFixture } from "../live-fixtures.mjs";

const packageDirectory = fileURLToPath(new URL("../../", import.meta.url));
const browserCandidates = process.env.OSSIE_BROWSER_BINARY ? [process.env.OSSIE_BROWSER_BINARY] : [
  process.env.PROGRAMFILES && join(process.env.PROGRAMFILES, "Microsoft", "Edge", "Application", "msedge.exe"),
  process.env["PROGRAMFILES(X86)"] && join(process.env["PROGRAMFILES(X86)"], "Microsoft", "Edge", "Application", "msedge.exe"),
  process.env.PROGRAMFILES && join(process.env.PROGRAMFILES, "Google", "Chrome", "Application", "chrome.exe"),
  "google-chrome", "chromium", "chromium-browser",
].filter(Boolean);
function resolveBrowser(candidate) {
  if (existsSync(candidate)) return candidate;
  if (/[\\/]/.test(candidate)) return undefined;
  for (const directory of (process.env.PATH ?? "").split(delimiter)) {
    for (const suffix of process.platform === "win32" ? ["", ".exe"] : [""]) {
      const path = join(directory, candidate + suffix);
      if (existsSync(path)) return path;
    }
  }
  return undefined;
}
const browser = browserCandidates.map(resolveBrowser).find(Boolean);

const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function devtools(endpoint) {
  const socket = new WebSocket(endpoint);
  await once(socket, "open");
  let nextId = 0;
  const pending = new Map();
  socket.addEventListener("message", event => {
    const message = JSON.parse(String(event.data));
    const request = pending.get(message.id);
    if (!request) return;
    pending.delete(message.id);
    clearTimeout(request.timer);
    if (message.error) request.reject(new Error(JSON.stringify(message.error)));
    else request.resolve(message.result);
  });
  return {
    close: () => socket.close(),
    send(method, params = {}, sessionId) {
      return new Promise((resolve, reject) => {
        const id = ++nextId;
        const timer = setTimeout(() => {
          pending.delete(id);
          reject(new Error(`DevTools command timed out: ${method}`));
        }, 5000);
        pending.set(id, { resolve, reject, timer });
        socket.send(JSON.stringify({ id, method, params, sessionId }));
      });
    },
  };
}

async function bundle(contents) {
  return build({
    absWorkingDir: packageDirectory,
    stdin: { contents, resolveDir: packageDirectory, sourcefile: "browser-validation.mjs", loader: "js" },
    bundle: true, platform: "browser", format: "iife", target: "es2022",
    write: false, metafile: true, logLevel: "silent",
  });
}

test("real browser validates materialized and LIVE HTTP Arrow, observation gating, CORS and late failure", {
  timeout: 40_000,
}, async () => {
  assert.ok(browser, "Set OSSIE_BROWSER_BINARY to an installed Chromium/Chrome/Edge executable");
  const fixture = arrowFixture();
  const paginated = pagedFixture();
  const requests = [];
  const polls = { asynchronous: 0, "late-failure": 0 };
  const live = Object.fromEntries(["live", "live-failure"].map(mode => [mode, {
    fixture: liveFixture(), observed: false, body: undefined, closed: deferred(), polls: 0,
  }]));
  let pageOrigin;
  let bundleText = "";
  const pageServer = createServer((request, response) => {
    if (request.url === "/") {
      response.writeHead(200, { "Content-Type": "text/html" });
      response.end('<!doctype html><html><body data-result="running">Running<script src="/client.js"></script></body></html>');
    } else if (request.url === "/client.js") {
      response.writeHead(200, { "Content-Type": "text/javascript" });
      response.end(bundleText);
    } else {
      response.writeHead(204);
      response.end();
    }
  });
  const apiServer = createServer(async (request, response) => {
    const observation = {
      path: request.url, method: request.method, origin: request.headers.origin,
      authorization: request.headers.authorization,
      requestedHeaders: request.headers["access-control-request-headers"],
      httpVersion: request.httpVersion,
    };
    requests.push(observation);
    const denied = request.url.startsWith("/cors-denied/");
    if (!denied) {
      response.setHeader("Access-Control-Allow-Origin", pageOrigin);
      response.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
      response.setHeader("Access-Control-Allow-Headers", "Authorization, Content-Type, Prefer");
    }
    if (request.method === "OPTIONS") {
      response.writeHead(204);
      response.end();
      return;
    }
    const liveMode = request.url.split("/")[1];
    const liveState = live[liveMode];
    if (liveState && request.url.endsWith("/results/result-1")) {
      assert.equal(liveState.body, undefined, "The browser must not retry the single-use stream");
      assert.equal(liveState.observed, false, "No terminal state before first-batch observation");
      liveState.body = response;
      response.writeHead(200, { "Content-Type": ARROW_MEDIA_TYPE });
      response.write(liveState.fixture.bytes.slice(0, firstBatchEnd(liveState.fixture.bytes)));
      response.on("close", () => liveState.closed.resolve());
      return;
    }
    if (liveState && request.url.endsWith("/observed")) {
      assert.ok(liveState.body, "Browser acknowledges only after opening and decoding the stream");
      assert.equal(liveState.observed, false);
      liveState.observed = true;
      if (liveMode === "live") {
        liveState.body.end(liveState.fixture.bytes.slice(firstBatchEnd(liveState.fixture.bytes)));
      }
      // Failure deliberately leaves the body stalled; only status can stop it.
      response.writeHead(204);
      response.end();
      return;
    }
    if (request.url === "/results/result-1") {
      response.writeHead(200, { "Content-Type": ARROW_MEDIA_TYPE });
      response.end(fixture.bytes);
      return;
    }
    const page = paginated.streams.find(stream =>
      new URL(stream.descriptor.url).pathname === request.url);
    if (page) {
      response.writeHead(200, { "Content-Type": ARROW_MEDIA_TYPE });
      response.end(page.bytes);
      return;
    }
    const bodyParts = [];
    for await (const chunk of request) bodyParts.push(chunk);
    observation.body = Buffer.concat(bodyParts).toString("utf8");
    const mode = request.url.split("/")[1];
    if (liveState) {
      response.writeHead(request.method === "POST" ? 202 : 200, { "Content-Type": "application/json" });
      if (request.url.endsWith("/capabilities")) {
        response.end(JSON.stringify({ ...CAPABILITIES, features: [LIVE_FEATURE] }));
      } else if (request.method === "POST") {
        response.end(JSON.stringify(liveState.fixture.running));
      } else {
        liveState.polls++;
        response.end(JSON.stringify(!liveState.observed ? liveState.fixture.running
          : mode === "live" ? liveState.fixture.succeeded : liveState.fixture.live("failed", { error: ERROR })));
      }
      return;
    }
    const pendingSubmission = request.method === "POST" && !["immediate", "paginated"].includes(mode);
    response.writeHead(pendingSubmission ? 202 : 200, { "Content-Type": "application/json" });
    if (request.url.endsWith("/capabilities")) response.end(JSON.stringify(CAPABILITIES));
    else if (request.url.endsWith("/bindings/sales")) response.end(JSON.stringify(BINDING));
    else if (mode === "paginated") response.end(JSON.stringify(paginated.succeeded));
    else if (mode === "immediate") response.end(JSON.stringify(fixture.succeeded));
    else if (request.method === "POST") response.end(JSON.stringify(execution("queued")));
    else {
      const finished = ++polls[mode] >= 2;
      response.end(JSON.stringify(!finished ? execution("running", { progress: { fraction: 0.5 } })
        : mode === "late-failure" ? execution("failed", { error: ERROR }) : fixture.succeeded));
    }
  });
  for (const server of [pageServer, apiServer]) {
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
  }
  pageOrigin = `http://127.0.0.1:${pageServer.address().port}`;
  const apiOrigin = `http://127.0.0.1:${apiServer.address().port}`;
  for (const [mode, state] of Object.entries(live)) {
    state.fixture.stream.url = `${apiOrigin}/${mode}/results/result-1`;
  }
  fixture.succeeded.result.url = `${apiOrigin}/results/result-1`;
  for (const stream of paginated.streams) {
    stream.descriptor.url = `${apiOrigin}/results/result-1/pages/${stream.descriptor.page_index}`;
  }
  const profile = await mkdtemp(join(packageDirectory, "test", "browser", ".browser-profile-"));
  try {
    const result = await bundle(`
    import { OssieClient, QueryExecutionError, LiveQueryExecutionError } from './dist/index.js';
    const check = (condition, label) => { if (!condition) throw new Error(label); };
    (async () => {
      const createClient = mode => new OssieClient({
        baseUrl: ${JSON.stringify(apiOrigin)} + '/' + mode,
        allowInsecureLocalhost: true, pollIntervalMs: 1, timeoutMs: 10000,
        getAccessToken: () => 'SYNTHETIC_BROWSER_TOKEN'
      });
      for (const mode of ['immediate', 'asynchronous']) {
        const api = createClient(mode);
        check((await api.getCapabilities()).result_formats[0] === 'arrow-stream', 'capabilities');
        check((await api.getBinding('sales')).model.id === 'sales-model', 'binding');
        const progress = [];
        const { table, execution } = await api.execute(${JSON.stringify(REQUEST)}, {
          waitSeconds: 0, onProgress: value => progress.push(value.state)
        });
        check(execution.state === 'succeeded', 'success state');
        check(table.numRows === 2, 'row count');
        check(table.getChildAt(1).get(0) === 9007199254740993n, 'int64 precision');
        check(table.getChildAt(0).get(1) === null, 'null');
        if (mode === 'asynchronous') {
          check(progress.includes('queued') && progress.includes('running'), 'queued/running progress');
        }
      }
      const pagedApi = createClient('paginated');
      const pagedExecution = await pagedApi.submit(${JSON.stringify(REQUEST)}, { waitSeconds: 5 });
      const selectedPage = await pagedApi.readPage(pagedExecution, 1);
      check(selectedPage.numRows === 1 && selectedPage.getChildAt(0).get(0) === null, 'selected page');
      const allRows = [];
      for await (const batch of pagedApi.readBatches(pagedExecution)) {
        for (let row = 0; row < batch.numRows; row++) allRows.push(batch.getChildAt(1).get(row));
      }
      check(allRows.length === 2 && allRows[0] === 9007199254740993n && allRows[1] === null, 'ordered page traversal');
      let failure;
      try { await createClient('late-failure').execute(${JSON.stringify(REQUEST)}); }
      catch (error) { failure = error; }
      check(failure instanceof QueryExecutionError, 'late failure classification');
      check(failure.code === 'INVALID_QUERY', 'late failure error code');
      check(failure.error.replacement_query.measures[0] === 'revenue', 'replacement query retained');
      for (const mode of ['live', 'live-failure']) {
        const iterator = createClient(mode).stream(${JSON.stringify(REQUEST)});
        const first = await iterator.next();
        check(!first.done && first.value.numRows === 1, 'LIVE first batch before terminal status');
        check(first.value.getChildAt(1).get(0) === 9007199254740993n, 'LIVE exact int64');
        // This acknowledgement is the ONLY gate that releases the last batch or
        // publishes terminal status. Timer-only chunking cannot satisfy this test.
        const acknowledged = await fetch(${JSON.stringify(apiOrigin)} + '/' + mode + '/observed', {
          headers: { Authorization: 'Bearer SYNTHETIC_BROWSER_TOKEN' }
        });
        check(acknowledged.ok, 'first batch observation gate');
        if (mode === 'live') {
          const second = await iterator.next();
          check(!second.done && second.value.getChildAt(0).get(0) === null, 'LIVE last batch');
          const end = await iterator.next();
          check(end.done && end.value.state === 'succeeded' && end.value.completion.row_count === 2,
            'LIVE verified generator return');
        } else {
          let liveFailure;
          try { await iterator.next(); } catch (error) { liveFailure = error; }
          check(liveFailure instanceof LiveQueryExecutionError, 'LIVE status interrupts stalled body');
          check(liveFailure.error.code === 'INVALID_QUERY' && liveFailure.execution.stream.replayable === false,
            'LIVE terminal failure retains result identity');
        }
      }
      const denied = new OssieClient({
        baseUrl: ${JSON.stringify(apiOrigin)} + '/cors-denied',
        allowInsecureLocalhost: true, timeoutMs: 10000
      });
      let corsFailure;
      try { await denied.getCapabilities(); } catch (error) { corsFailure = error; }
      check(corsFailure?.code === 'TRANSPORT_ERROR', 'browser enforces missing CORS permission');
      document.body.dataset.result = 'pass';
      document.body.textContent = 'OSSIE_BROWSER_PASS';
    })().catch(error => {
      document.body.dataset.result = 'fail';
      document.body.textContent = 'OSSIE_BROWSER_FAIL ' + error.name + ': ' + error.message;
    });
  `);
    bundleText = result.outputFiles[0].text;
    const child = spawn(browser, [
      "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
      "--disable-background-networking", "--disable-extensions", "--disable-component-update",
      "--disable-dev-shm-usage", "--no-sandbox",
      `--user-data-dir=${profile}`, "--remote-debugging-port=0",
      "--remote-debugging-address=127.0.0.1", "about:blank",
    ], { stdio: ["ignore", "pipe", "pipe"], windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", chunk => { stdout += chunk; });
    child.stderr.on("data", chunk => { stderr += chunk; });
    let cdp;
    try {
      const startDeadline = Date.now() + 10_000;
      let endpoint;
      while (Date.now() < startDeadline && child.exitCode === null) {
        endpoint = /DevTools listening on (ws:\/\/[^\s]+)/.exec(stderr)?.[1];
        if (endpoint) break;
        await delay(50);
      }
      assert.ok(endpoint, `Browser did not expose DevTools: ${stderr}`);
      cdp = await devtools(endpoint);
      const { targetId } = await cdp.send("Target.createTarget", { url: pageOrigin });
      const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
      const deadline = Date.now() + 15_000;
      let state;
      while (Date.now() < deadline) {
        const result = await cdp.send("Runtime.evaluate", {
          expression: "JSON.stringify({status:document.body?.dataset.result,text:document.body?.textContent})",
          returnByValue: true,
        }, sessionId);
        state = JSON.parse(result.result.value);
        if (state.status === "pass" || state.status === "fail") break;
        await delay(50);
      }
      assert.equal(state?.status, "pass", JSON.stringify({ state, requests, stdout, stderr }));
      assert.match(state.text, /OSSIE_BROWSER_PASS/);
      assert.deepEqual(polls, { asynchronous: 2, "late-failure": 2 });
      assert.ok(requests.some(request => request.method === "OPTIONS"
        && request.requestedHeaders.includes("authorization")), "Real authorization preflight is exercised");
      assert.equal(requests.filter(request => request.method === "GET" && request.path === "/results/result-1").length, 2);
      assert.equal(requests.filter(request => request.method === "GET" && request.path === "/results/result-1/pages/0").length, 1);
      assert.equal(requests.filter(request => request.method === "GET" && request.path === "/results/result-1/pages/1").length, 2);
      for (const mode of ["immediate", "asynchronous", "paginated", "late-failure"]) {
        const submissions = requests.filter(request => request.method === "POST" && request.path === `/${mode}/queries`);
        assert.equal(submissions.length, 1, "Neither polling nor replacement queries create additional submissions");
        assert.deepEqual(JSON.parse(submissions[0].body), REQUEST);
      }
      for (const request of requests.filter(value => value.method !== "OPTIONS" && !value.path.startsWith("/cors-denied/"))) {
        assert.equal(request.origin, pageOrigin);
        assert.equal(request.authorization, "Bearer SYNTHETIC_BROWSER_TOKEN");
      }
      assert.equal(requests.filter(request => request.path === "/cors-denied/capabilities").length, 1);
      for (const [mode, state] of Object.entries(live)) {
        assert.equal(state.observed, true, "Real browser decoded a provisional first batch");
        assert.ok(state.polls > 0, "Live status polling accompanied streaming");
        await Promise.race([state.closed.promise, delay(3000).then(() => { throw new Error("Live body was not closed"); })]);
        const submissions = requests.filter(r => r.method === "POST" && r.path === `/${mode}/queries`);
        assert.equal(submissions.length, 1);
        assert.deepEqual(JSON.parse(submissions[0].body),
          { ...REQUEST, contract_version: LIVE_CONTRACT_VERSION, delivery: "live" });
        assert.equal(requests.filter(r => r.method === "GET" && r.path === `/${mode}/results/result-1`).length, 1);
      }
      assert.ok(requests.every(r => r.httpVersion === "1.1"), "Synthetic harness verifies loopback HTTP/1.1 only");
    } finally {
      if (cdp) {
        await cdp.send("Browser.close").catch(() => {});
        cdp.close();
      }
      if (child.exitCode === null) {
        await Promise.race([once(child, "close"), delay(1000)]);
        if (child.exitCode === null) child.kill();
      }
    }
  } finally {
    for (const server of [pageServer, apiServer]) {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    }
    await rm(profile, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  }
});
