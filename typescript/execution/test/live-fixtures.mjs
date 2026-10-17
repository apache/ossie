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

import { Message } from "apache-arrow";
import { ARROW_MEDIA_TYPE, LIVE_CONTRACT_VERSION } from "../dist/index.js";
import { MODEL, arrowFixture, execution } from "./fixtures.mjs";

export function liveFixture(options = {}) {
  const fixture = arrowFixture({
    batchRows: [[["West", 9007199254740993n]], [[null, null]]],
    metadata: {
      contract_version: LIVE_CONTRACT_VERSION, execution_id: "exec-1",
      result_id: "result-1", data_source_id: "sales", model: MODEL,
    },
    ...options,
  });
  const result = fixture.succeeded.result;
  const stream = {
    result_id: result.result_id, format: result.format, url: result.url,
    expires_at: result.expires_at, columns: result.columns, replayable: false,
  };
  const live = (state, extra = {}) => ({
    ...execution(state), contract_version: LIVE_CONTRACT_VERSION, delivery: "live",
    ...(state === "queued" ? {} : { stream }), ...extra,
  });
  const completion = {
    result_id: result.result_id, row_count: result.row_count, byte_count: result.byte_count,
    completeness: result.completeness,
    ...(result.truncation_reason === undefined ? {} : { truncation_reason: result.truncation_reason }),
  };
  return { ...fixture, stream, completion, live, running: live("running"),
    succeeded: live("succeeded", { completion }) };
}

export function firstBatchEnd(bytes) {
  let offset = 0;
  while (offset < bytes.length) {
    const view = new DataView(bytes.buffer, bytes.byteOffset + offset);
    const prefix = view.getInt32(0, true) === -1 ? 8 : 4;
    const length = view.getInt32(prefix - 4, true);
    if (length === 0) break;
    const message = Message.decode(bytes.subarray(offset + prefix, offset + prefix + length));
    offset += prefix + length + message.bodyLength;
    if (message.isRecordBatch()) return offset;
  }
  throw new Error("No record batch");
}

export function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

export function gatedBody(bytes, split = firstBatchEnd(bytes)) {
  let controller;
  let cancelled = false;
  const response = new Response(new ReadableStream({
    start(value) { controller = value; value.enqueue(bytes.slice(0, split)); },
    cancel() { cancelled = true; },
  }, { highWaterMark: 0 }), { headers: { "Content-Type": ARROW_MEDIA_TYPE } });
  return {
    response,
    finish() { controller.enqueue(bytes.slice(split)); controller.close(); },
    fail() { controller.error(new Error("SYNTHETIC_BODY_FAILURE")); },
    get cancelled() { return cancelled; },
  };
}
