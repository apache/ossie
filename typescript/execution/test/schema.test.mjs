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
import { readFile } from "node:fs/promises";
import test from "node:test";
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import {
  COLUMN_METADATA_KEY, EXECUTION_METADATA_KEY,
} from "../dist/index.js";
import {
  BINDING, CAPABILITIES, ERROR, REQUEST, arrowFixture, execution, pagedFixture,
} from "./fixtures.mjs";

const schema = JSON.parse(await readFile(new URL("../../../docs/proposals/execution-http/execution.schema.json", import.meta.url), "utf8"));
const openapi = JSON.parse(await readFile(new URL("../../../docs/proposals/execution-http/openapi.json", import.meta.url), "utf8"));
const ajv = new Ajv2020({ allErrors: true, strict: false });
addFormats(ajv);
ajv.addSchema(schema);
const validate = name => ajv.getSchema(`${schema.$id}#/$defs/${name}`);

test("independent JSON Schema validators accept every fixture envelope and Arrow metadata document", () => {
  const fixture = arrowFixture();
  const paged = pagedFixture();
  const examples = [
    ["Request", REQUEST],
    ["Request", { data_source_id: "sales", query: { fields: ["region"], limit: 0 } }],
    ["Capabilities", CAPABILITIES], ["Binding", BINDING],
    ...["queued", "running", "cancelling", "cancelled"].map(state => ["Execution", execution(state)]),
    ["Execution", fixture.succeeded], ["Execution", execution("failed", { error: ERROR })],
    ["Execution", paged.succeeded],
    ...paged.streams.flatMap(page => [
      ["ResultPage", page.descriptor],
      ["ExecutionMetadata", JSON.parse(page.schema.metadata.get(EXECUTION_METADATA_KEY))],
    ]),
    ["QueryError", ERROR],
    ["ExecutionMetadata", JSON.parse(fixture.schema.metadata.get(EXECUTION_METADATA_KEY))],
    ...fixture.schema.fields.map(field => ["ColumnMetadata", JSON.parse(field.metadata.get(COLUMN_METADATA_KEY))]),
  ];
  for (const [name, value] of examples) {
    const validator = validate(name);
    assert.ok(validator, `Definition ${name} exists`);
    assert.ok(validator(value), `${name}: ${ajv.errorsText(validator.errors)}`);
  }
});

test("JSON Schema rejects malformed query shapes, unsafe counters and contradictory states", () => {
  const fixture = arrowFixture();
  const paged = pagedFixture();
  const examples = [
    ["Request", { ...REQUEST, query: { fields: ["region"], measures: ["revenue"] } }],
    ["Request", { ...REQUEST, query: { fields: ["region"], having: "revenue > 1" } }],
    ["Request", { ...REQUEST, query: {} }],
    ["Request", { ...REQUEST, query: { fields: [] } }],
    ["Request", { ...REQUEST, query: { measures: ["revenue"], limit: Number.MAX_SAFE_INTEGER + 1 } }],
    ["Request", { ...REQUEST, query: { measures: ["revenue"], order_by: [] } }],
    ["Request", { ...REQUEST, query: { measures: ["revenue"], sql: "SELECT 1" } }],
    ["Capabilities", { ...CAPABILITIES, contract_version: "2.0" }],
    ["Execution", execution("running", { result: fixture.succeeded.result })],
    ["Execution", execution("running", { error: ERROR })],
    ["Execution", execution("failed")],
    ["Execution", execution("succeeded")],
    ["Execution", execution("cancelled", { progress: { fraction: 1 } })],
    ["Execution", { ...fixture.succeeded, result: { ...fixture.succeeded.result, byte_count: Number.MAX_SAFE_INTEGER + 1 } }],
    ["Execution", { ...fixture.succeeded, result: { ...fixture.succeeded.result, completeness: "truncated" } }],
    ["Execution", { ...fixture.succeeded, result: { ...fixture.succeeded.result, truncation_reason: "contradiction" } }],
    ["Execution", { ...fixture.succeeded, unexpected: true }],
    ["ExecutionMetadata", { contract_version: "0.1-draft", execution_id: "exec-1" }],
    ["ColumnMetadata", { column_id: "id", unexpected: true }],
    ["ResultManifest", { ...paged.succeeded.result, url: "https://example.test/result" }],
    ["ResultManifest", { ...paged.succeeded.result, pages: [] }],
    ["ResultManifest", { ...fixture.succeeded.result, url: undefined }],
    ["ResultPage", { ...paged.streams[0].descriptor, row_offset: -1 }],
    ["ResultPage", { ...paged.streams[0].descriptor, byte_count: 0 }],
    ["ResultPage", { ...paged.streams[0].descriptor, page_index: Number.MAX_SAFE_INTEGER + 1 }],
    ["ResultPage", { ...paged.streams[0].descriptor, unexpected: true }],
    ["ExecutionMetadata", { ...JSON.parse(paged.streams[0].schema.metadata.get(EXECUTION_METADATA_KEY)), row_offset: undefined }],
  ];
  for (const [name, value] of examples) {
    assert.equal(validate(name)(value), false, `${name} unexpectedly accepted ${JSON.stringify(value)}`);
  }
});

test("OpenAPI defines lifecycle and references only available execution schema definitions", () => {
  assert.match(openapi.openapi, /^3\.1\./);
  for (const path of ["/capabilities", "/bindings/{id}", "/queries", "/queries/{id}", "/queries/{id}/cancel"]) {
    assert.ok(openapi.paths[path], `Missing lifecycle path ${path}`);
  }
  let references = 0;
  function walk(value) {
    if (!value || typeof value !== "object") return;
    if (typeof value.$ref === "string" && value.$ref.includes("execution.schema.json#/$defs/")) {
      const name = value.$ref.split("#/$defs/")[1];
      assert.ok(schema.$defs[name], `Unresolved schema reference ${name}`);
      references++;
    }
    for (const item of Object.values(value)) walk(item);
  }
  walk(openapi);
  assert.ok(references > 0, "OpenAPI must use the shared schema, not a divergent inline model");
});

test("OpenAPI media-type examples validate against their referenced contract definitions", () => {
  let examples = 0;
  function walk(value) {
    if (!value || typeof value !== "object") return;
    const reference = value.schema?.$ref;
    if (typeof reference === "string" && reference.includes("execution.schema.json#/$defs/")) {
      const validator = validate(reference.split("#/$defs/")[1]);
      const values = [
        ...(Object.hasOwn(value, "example") ? [value.example] : []),
        ...Object.values(value.examples ?? {}).filter(example => Object.hasOwn(example, "value")).map(example => example.value),
      ];
      for (const example of values) {
        assert.ok(validator(example), ajv.errorsText(validator.errors));
        examples++;
      }
    }
    for (const child of Object.values(value)) walk(child);
  }
  walk(openapi);
  assert.ok(examples > 0, "At least one executable request example must be present");
});

test("normative draft JSON and HTTP-body examples conform structurally to the shared schema", async () => {
  const document = await readFile(new URL("../../../docs/proposals/execute-query-http.md", import.meta.url), "utf8");
  const definitions = new Set();
  for (const [, language, contents] of document.matchAll(/```(json|http)\r?\n([\s\S]*?)\r?\n```/g)) {
    const body = language === "http" ? contents.replace(/^[\s\S]*?\r?\n\r?\n/, "") : contents;
    const value = JSON.parse(body);
    const name = "foundation_revision" in value ? "Capabilities"
      : "state" in value ? "Execution"
      : "result_id" in value && "execution_id" in value ? "ExecutionMetadata"
      : "query" in value ? "Request"
      : "status" in value && "error" in value ? "ProblemDetails"
      : "model" in value && "query_shapes" in value ? "Binding"
      : undefined;
    assert.ok(name, "Every documented JSON object must identify a known contract definition");
    const validator = validate(name);
    assert.ok(validator(value), `${name}: ${ajv.errorsText(validator.errors)}`);
    if (language === "http" && name === "ProblemDetails") {
      assert.equal(value.status, Number(/^HTTP\/1\.1 (\d+)/.exec(contents)?.[1]));
    }
    definitions.add(name);
  }
  assert.deepEqual([...definitions].sort(),
    ["Binding", "Capabilities", "Execution", "ExecutionMetadata", "ProblemDetails", "Request"]);
});
