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
import {
  Field, Int64, Message, RecordBatch, Schema, Struct, Table, Utf8,
  makeData, tableToIPC, vectorFromArray,
} from "apache-arrow";
import {
  ARROW_MEDIA_TYPE, COLUMN_METADATA_KEY, CONTRACT_VERSION,
  EXECUTION_METADATA_KEY, FOUNDATION_REVISION, QUERY_SCHEMA_VERSION,
} from "../dist/index.js";

export const ORIGIN = "https://ossie.example";
export const MODEL = { id: "sales-model", revision: "revision-1" };
export const REQUEST = {
  data_source_id: "sales",
  query: { dimensions: ["region"], measures: ["revenue"], limit: 0 },
};
export const CAPABILITIES = {
  contract_version: CONTRACT_VERSION,
  foundation_revision: FOUNDATION_REVISION,
  query_schema_version: QUERY_SCHEMA_VERSION,
  result_formats: ["arrow-stream"],
  query_shapes: ["aggregation", "scalar"],
  features: [],
  max_wait_seconds: 30,
};
export const BINDING = {
  data_source_id: "sales", model: MODEL,
  query_shapes: ["aggregation", "scalar"], features: [],
};
export const ERROR = {
  code: "INVALID_QUERY", message: "Choose a known measure", retryable: false,
  replacement_query: { measures: ["revenue"] },
};

export function execution(state = "running", extra = {}) {
  return {
    contract_version: CONTRACT_VERSION,
    execution_id: "exec-1", data_source_id: "sales", model: { ...MODEL },
    warnings: [], state, ...extra,
  };
}

export function json(value, status = 200, headers = {}) {
  return new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json", ...headers },
  });
}

export function arrowResponse(bytes, headers = {}) {
  return new Response(bytes, {
    headers: { "Content-Type": ARROW_MEDIA_TYPE, ...headers },
  });
}

export function mockFetch(handler) {
  const calls = [];
  const fetch = async (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : input);
    const call = {
      url, method: init.method ?? "GET",
      headers: new Headers(init.headers), body: init.body, signal: init.signal,
      credentials: init.credentials, redirect: init.redirect,
    };
    calls.push(call);
    return handler(call, calls.length - 1);
  };
  return { fetch, calls };
}

export async function collect(iterable) {
  const batches = [];
  for await (const batch of iterable) batches.push(batch);
  return batches;
}

export function assertSafeError(error, secrets = []) {
  assert.ok(error instanceof Error);
  assert.ok(typeof error.code === "string" && error.code.length > 0);
  const publicText = `${error.name} ${error.message} ${error.stack} ${JSON.stringify(error)}`;
  for (const secret of secrets) assert.ok(!publicText.includes(secret), `Error exposed ${secret}`);
  return true;
}

export function arrowFixture({
  rows = [["West", 9007199254740993n], [null, null]],
  batchRows,
  columns,
  fields,
  metadata,
  columnMetadata,
  columnData,
  manifest = {},
} = {}) {
  const defaultColumns = [
    { column_id: "col-region", name: "region", logical_type: "String", nullable: true, role: "dimension" },
    { column_id: "col-count", name: "count", logical_type: "Integer", nullable: true, role: "measure" },
  ];
  const resultColumns = columns ?? defaultColumns;
  const arrowFields = fields ?? [
    new Field(resultColumns[0].name, new Utf8(), true,
      new Map([[COLUMN_METADATA_KEY, JSON.stringify(columnMetadata?.[0] ?? { column_id: resultColumns[0].column_id })]])),
    new Field(resultColumns[1].name, new Int64(), true,
      new Map([[COLUMN_METADATA_KEY, JSON.stringify(columnMetadata?.[1] ?? { column_id: resultColumns[1].column_id })]])),
  ];
  const metadataValue = metadata ?? {
    contract_version: CONTRACT_VERSION, execution_id: "exec-1",
    result_id: "result-1", data_source_id: "sales", model: MODEL,
  };
  const schema = new Schema(arrowFields, new Map([
    [EXECUTION_METADATA_KEY, typeof metadataValue === "string" ? metadataValue : JSON.stringify(metadataValue)],
  ]));
  const chunks = batchRows ?? [rows];
  const batches = chunks.map(chunk => {
    const children = arrowFields.map((field, index) => columnData?.[index]
      ?? vectorFromArray(chunk.map(row => row[index]), field.type).data[0]);
    const batch = new RecordBatch(schema, makeData({
      type: new Struct(arrowFields), length: chunk.length,
      children,
    }));
    // Arrow JS Schema.assign merges by name inside RecordBatch construction.
    // Restore the position-preserving schema so this fixture can exercise duplicate names.
    batch.schema = schema;
    return batch;
  });
  const table = new Table(schema, batches);
  const bytes = tableToIPC(table, "stream");
  const succeeded = execution("succeeded", {
    result: {
      result_id: "result-1", format: "arrow-stream",
      url: `${ORIGIN}/results/result-1`,
      row_count: chunks.reduce((count, chunk) => count + chunk.length, 0),
      byte_count: bytes.byteLength,
      completeness: "complete", expires_at: "2099-01-01T00:00:00Z",
      columns: resultColumns, ...manifest,
    },
  });
  return { bytes, table, schema, batches, succeeded };
}

export function chunksResponse(bytes, chunkSize = 17, { onCancel, onPull } = {}) {
  let offset = 0;
  return new Response(new ReadableStream({
    pull(controller) {
      onPull?.(offset);
      if (offset === bytes.length) return controller.close();
      const end = Math.min(offset + chunkSize, bytes.length);
      controller.enqueue(bytes.slice(offset, end));
      offset = end;
    },
    cancel(reason) { onCancel?.(reason); },
  }, { highWaterMark: 0 }), { headers: { "Content-Type": ARROW_MEDIA_TYPE } });
}

export function pagedFixture({
  pageRows = [[["West", 9007199254740993n]], [[null, null]]],
  pageOptions = [],
  manifest = {},
} = {}) {
  let offset = 0;
  const streams = pageRows.map((rows, index) => {
    const fixture = arrowFixture({
      rows,
      metadata: {
        contract_version: CONTRACT_VERSION, execution_id: "exec-1",
        result_id: "result-1", data_source_id: "sales", model: MODEL,
        page_index: index, row_offset: offset,
      },
      ...pageOptions[index],
    });
    const descriptor = {
      page_index: index, row_offset: offset, row_count: rows.length,
      byte_count: fixture.bytes.length, url: `${ORIGIN}/results/result-1/pages/${index}`,
    };
    offset += rows.length;
    return { ...fixture, descriptor };
  });
  const succeeded = structuredClone(streams[0].succeeded);
  delete succeeded.result.url;
  Object.assign(succeeded.result, {
    pages: streams.map(stream => stream.descriptor), row_count: offset,
    byte_count: streams.reduce((total, stream) => total + stream.bytes.length, 0),
    ...manifest,
  });
  return { streams, succeeded };
}

export function rewriteFirstBatchHeader(fixture, mutate) {
  let offset = 0;
  while (offset < fixture.bytes.length) {
    const prefix = new DataView(fixture.bytes.buffer, fixture.bytes.byteOffset + offset);
    const prefixSize = prefix.getInt32(0, true) === -1 ? 8 : 4;
    const metadataSize = prefix.getInt32(prefixSize - 4, true);
    if (metadataSize === 0) break;
    const bodyStart = offset + prefixSize + metadataSize;
    const message = Message.decode(fixture.bytes.subarray(offset + prefixSize, bodyStart));
    if (message.isRecordBatch()) {
      const header = message.header();
      mutate(header);
      const metadata = Message.encode(Message.from(header, message.bodyLength));
      const paddedLength = Math.ceil(metadata.length / 8) * 8;
      const bytes = new Uint8Array(fixture.bytes.length - prefixSize - metadataSize + 8 + paddedLength);
      bytes.set(fixture.bytes.subarray(0, offset));
      const view = new DataView(bytes.buffer, offset);
      view.setInt32(0, -1, true);
      view.setInt32(4, paddedLength, true);
      bytes.set(metadata, offset + 8);
      bytes.set(fixture.bytes.subarray(bodyStart), offset + 8 + paddedLength);
      fixture.bytes = bytes;
      fixture.succeeded.result.byte_count = bytes.length;
      return fixture;
    }
    offset = bodyStart + message.bodyLength;
  }
  throw new Error("Fixture has no record batch to rewrite");
}
