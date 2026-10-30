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
import { Message, MessageHeader, Table, tableToIPC } from "apache-arrow";
import { OssieClient, ProtocolError } from "../dist/index.js";
import { ORIGIN, arrowFixture, arrowResponse, collect } from "./fixtures.mjs";

function metadataFrame(fixture, type) {
  let offset = 0;
  while (offset + 8 <= fixture.bytes.length) {
    const prefix = new DataView(fixture.bytes.buffer, fixture.bytes.byteOffset + offset);
    const prefixSize = prefix.getInt32(0, true) === -1 ? 8 : 4;
    const size = prefix.getInt32(prefixSize - 4, true);
    if (size === 0) break;
    const bytes = fixture.bytes.subarray(offset + prefixSize, offset + prefixSize + size);
    const message = Message.decode(bytes);
    if ((type === MessageHeader.Schema && message.isSchema())
      || (type === MessageHeader.RecordBatch && message.isRecordBatch())) return bytes;
    offset += prefixSize + size + message.bodyLength;
  }
  throw new Error("Fixture has no requested metadata frame");
}

function flatAccess(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const reference = location => location + view.getUint32(location, true);
  const field = (table, index) => {
    const vtable = table - view.getInt32(table, true);
    assert.ok(4 + index * 2 < view.getUint16(vtable, true));
    const offset = view.getUint16(vtable + 4 + index * 2, true);
    assert.ok(offset > 0, `Fixture field ${index} must be present`);
    return table + offset;
  };
  const root = view.getUint32(0, true);
  const header = reference(field(root, 2));
  return { view, reference, field, root, header };
}

const mutations = [
  ["record-batch node vector allocation", MessageHeader.RecordBatch, a =>
    a.view.setUint32(a.reference(a.field(a.header, 1)), 200_000, true)],
  ["record-batch buffer vector allocation", MessageHeader.RecordBatch, a =>
    a.view.setUint32(a.reference(a.field(a.header, 2)), 200_000, true)],
  ["in-bounds node count inconsistent with the flat schema", MessageHeader.RecordBatch, a =>
    a.view.setUint32(a.reference(a.field(a.header, 1)), 1, true)],
  ["schema fields vector allocation", MessageHeader.Schema, a =>
    a.view.setUint32(a.reference(a.field(a.header, 1)), 200_000, true)],
  ["custom metadata vector allocation", MessageHeader.Schema, a =>
    a.view.setUint32(a.reference(a.field(a.header, 2)), 200_000, true)],
  ["metadata string allocation", MessageHeader.Schema, a => {
    const vector = a.reference(a.field(a.header, 2));
    const entry = a.reference(vector + 4);
    a.view.setUint32(a.reference(a.field(entry, 1)), 200_000, true);
  }],
  ["unterminated metadata string", MessageHeader.Schema, a => {
    const vector = a.reference(a.field(a.header, 2));
    const entry = a.reference(vector + 4);
    const value = a.reference(a.field(entry, 1));
    a.view.setUint8(value + 4 + a.view.getUint32(value, true), 1);
  }],
  ["schema field reference outside metadata", MessageHeader.Schema, a => {
    const vector = a.reference(a.field(a.header, 1));
    a.view.setUint32(vector + 4, 0xffff_fffc, true);
  }],
  ["null message header reference", MessageHeader.Schema, a =>
    a.view.setUint32(a.field(a.root, 2), 0, true)],
  ["vtable outside metadata", MessageHeader.Schema, a =>
    a.view.setInt32(a.root, 0x7fff_fffc, true)],
];

for (const [name, type, mutate] of mutations) {
  test(`predecode bounds reject ${name} before Arrow allocation`, async context => {
    const fixture = arrowFixture();
    const metadata = metadataFrame(fixture, type);
    mutate(flatAccess(metadata));
    const originalDecode = Message.decode;
    let unsafeDecode = false;
    context.mock.method(Message, "decode", bytes => {
      if (bytes.length === metadata.length && bytes.every((value, index) => value === metadata[index])) {
        unsafeDecode = true;
        throw new Error("Unsafe metadata reached Arrow decoder");
      }
      return originalDecode.call(Message, bytes);
    });
    const client = new OssieClient({
      baseUrl: ORIGIN, fetch: async () => arrowResponse(fixture.bytes),
    });
    await assert.rejects(collect(client.readBatches(fixture.succeeded)), ProtocolError);
    assert.equal(unsafeDecode, false, "Reject malformed lengths before allocating decoded vectors");
  });
}

test("shared metadata strings cannot amplify decoding beyond the configured budget", async context => {
  const fixture = arrowFixture();
  fixture.schema.metadata.set("example.test.large", "x".repeat(4096));
  for (let index = 0; index < 32; index++) fixture.schema.metadata.set(`example.test.${index}`, "small");
  fixture.bytes = tableToIPC(new Table(fixture.schema, fixture.batches), "stream");
  fixture.succeeded.result.byte_count = fixture.bytes.length;
  const metadata = metadataFrame(fixture, MessageHeader.Schema);
  const a = flatAccess(metadata);
  const vector = a.reference(a.field(a.header, 2));
  const length = a.view.getUint32(vector, true);
  const entries = Array.from({ length }, (_, index) => a.reference(vector + 4 + index * 4));
  const values = entries.map(entry => a.reference(a.field(entry, 1)));
  const largest = values.reduce((best, value) =>
    a.view.getUint32(value, true) > a.view.getUint32(best, true) ? value : best);
  let aliases = 0;
  for (const entry of entries) {
    const pointer = a.field(entry, 1);
    if (pointer < largest && a.reference(pointer) !== largest) {
      a.view.setUint32(pointer, largest - pointer, true);
      aliases++;
    }
  }
  assert.ok(aliases >= 8, "Fixture must exercise repeated valid forward string references");
  const originalDecode = Message.decode;
  let unsafeDecode = false;
  context.mock.method(Message, "decode", bytes => {
    if (bytes.length === metadata.length && bytes.every((value, index) => value === metadata[index])) {
      unsafeDecode = true;
      throw new Error("Expanded metadata reached Arrow decoder");
    }
    return originalDecode.call(Message, bytes);
  });
  const client = new OssieClient({
    baseUrl: ORIGIN, fetch: async () => arrowResponse(fixture.bytes), maxResultBytes: fixture.bytes.length * 2,
  });
  await assert.rejects(collect(client.readBatches(fixture.succeeded)), error => error.code === "RESULT_LIMIT_EXCEEDED");
  assert.equal(unsafeDecode, false);
});
