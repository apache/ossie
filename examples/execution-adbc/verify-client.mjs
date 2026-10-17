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
import { readFile, realpath } from "node:fs/promises";
import path from "node:path";
import { OssieClient, ARROW_MEDIA_TYPE } from "../../typescript/execution/dist/index.js";

assert.equal(process.argv.length, 3, "Usage: node verify-client.mjs <output-directory>");
const root = await realpath(process.argv[2]);
const capture = JSON.parse(await readFile(path.join(root, "capture.json"), "utf8"));
assert.deepEqual(Object.keys(capture).sort(), ["execution", "resources"]);
assert.equal(capture.execution.state, "succeeded");
assert.ok(Array.isArray(capture.resources) && capture.resources.length > 0);
const resources = new Map();
for (const resource of capture.resources) {
  assert.deepEqual(Object.keys(resource).sort(), ["file", "url"]);
  assert.match(resource.file, /^page-[a-f0-9]{32}\.arrow$/);
  assert.equal(resource.url, `https://example.test/results/${resource.file}`);
  assert.ok(!resources.has(resource.url));
  const file = await realpath(path.join(root, resource.file));
  assert.equal(path.dirname(file), root, "Capture resources must remain inside the output directory");
  resources.set(resource.url, await readFile(file));
}
const execution = capture.execution;
const descriptors = execution.result.pages ?? [{
  url: execution.result.url, row_offset: 0, row_count: execution.result.row_count,
}];
assert.deepEqual([...resources.keys()], descriptors.map(page => page.url));
const expected = [[1n, 9007199254740993n], [2n, null], [3n, -7n]];
const rows = table => Array.from({ length: table.numRows }, (_, i) =>
  [table.getChildAt(0).get(i), table.getChildAt(1).get(i)]);
const fields = execution.result.columns.map(column => column.model_ref);
assert.equal(fields.length, 2, "The demo capture must have two columns");
assert.ok(fields.every(field => typeof field === "string" && field.trim().length > 0));
const request = {
  data_source_id: execution.data_source_id,
  query: {
    fields,
    order_by: [{ field: fields[0], direction: "ASC" }],
  },
};
let submissions = 0;
const api = new OssieClient({
  baseUrl: "https://example.test/",
  fetch: async (input, init) => {
    const url = String(input);
    if (init.method === "POST") {
      assert.equal(url, "https://example.test/queries");
      assert.deepEqual(JSON.parse(init.body), request);
      submissions++;
      return new Response(JSON.stringify(execution), { headers: { "Content-Type": "application/json" } });
    }
    assert.equal(init.method, "GET");
    assert.ok(resources.has(url), "Fetch must only read the captured IPC resources");
    return new Response(resources.get(url), { headers: { "Content-Type": ARROW_MEDIA_TYPE } });
  },
});

// Exercise selected-page validation, then full exhaustion and materialization.
for (let i = descriptors.length - 1; i >= 0; i--) {
  const page = descriptors[i];
  assert.deepEqual(rows(await api.readPage(execution, i)),
    expected.slice(page.row_offset, page.row_offset + page.row_count));
}
const consumed = [];
for await (const batch of api.readBatches(execution)) consumed.push(...rows(batch));
assert.deepEqual(consumed, expected);
const result = await api.execute(request);
assert.deepEqual(rows(result.table), expected);
assert.deepEqual(result.execution, execution);
assert.equal(submissions, 1);
console.log("ADBC SQLite -> PyArrow IPC -> existing TS client verified (synthetic Fetch, not live HTTP).");
