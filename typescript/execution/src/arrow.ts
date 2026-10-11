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

import { DataType, DateUnit, Message, MessageHeader, MetadataVersion, Precision, RecordBatchReader, TimeUnit, Type } from "apache-arrow";
import type { RecordBatch, Schema } from "apache-arrow";
import { OssieError, ProtocolError } from "./errors.js";
import { BodySource, Operation } from "./transport.js";
import { COLUMN_METADATA_KEY, EXECUTION_METADATA_KEY } from "./types.js";
import type { Column, ResultPage, SuccessfulExecution } from "./types.js";
import { assert, count, equivalent, object, validateMetadata } from "./validation.js";

const timeUnits = [TimeUnit.SECOND, TimeUnit.MILLISECOND, TimeUnit.MICROSECOND, TimeUnit.NANOSECOND];

function supported(type: DataType, column: Column): void {
  let matches = false;
  switch (column.logical_type) {
    case "String": matches = DataType.isUtf8(type); break;
    case "Integer":
      matches = DataType.isInt(type) && [8, 16, 32, 64].includes(type.bitWidth)
        && typeof type.isSigned === "boolean";
      break;
    case "Float":
      matches = DataType.isFloat(type) && [Precision.SINGLE, Precision.DOUBLE].includes(type.precision);
      break;
    case "Boolean": matches = DataType.isBool(type); break;
    case "Decimal":
      matches = DataType.isDecimal(type) && [128, 256].includes(type.bitWidth)
        && Number.isInteger(type.precision) && type.precision >= 1
        && type.precision <= (type.bitWidth === 128 ? 38 : 76) && Number.isInteger(type.scale);
      break;
    case "Date": matches = DataType.isDate(type) && type.unit === DateUnit.DAY; break;
    case "Time":
      matches = DataType.isTime(type) && ((type.bitWidth === 32
        && [TimeUnit.SECOND, TimeUnit.MILLISECOND].includes(type.unit))
        || (type.bitWidth === 64 && [TimeUnit.MICROSECOND, TimeUnit.NANOSECOND].includes(type.unit)));
      break;
    case "DateTime":
      matches = DataType.isTimestamp(type) && timeUnits.includes(type.unit)
        && (type.timezone === undefined || type.timezone === null || type.timezone === "");
      break;
    case "DateTimeTz":
      matches = DataType.isTimestamp(type) && timeUnits.includes(type.unit)
        && typeof type.timezone === "string" && type.timezone.trim().length > 0;
      break;
  }
  assert(matches && (!type.children || type.children.length === 0),
    "An Arrow physical type is unsupported or does not match its logical type.");
}

function metadataJson(value: string | undefined): unknown {
  assert(typeof value === "string", "Required Ossie Arrow metadata is missing.");
  try { return JSON.parse(value) as unknown; }
  catch { throw new ProtocolError("Ossie Arrow metadata is not valid JSON."); }
}

function schemaMatches(schema: Schema, execution: SuccessfulExecution, page?: ResultPage): void {
  const metadata = validateMetadata(metadataJson(schema.metadata.get(EXECUTION_METADATA_KEY)));
  assert(equivalent(metadata, {
    contract_version: execution.contract_version,
    execution_id: execution.execution_id,
    result_id: execution.result.result_id,
    data_source_id: execution.data_source_id,
    model: execution.model,
    ...(page === undefined ? {} : { page_index: page.page_index, row_offset: page.row_offset }),
  }), "Arrow execution metadata does not match the execution.");
  assert(schema.fields.length === execution.result.columns.length, "Arrow column count mismatch.");
  schema.fields.forEach((field, index) => {
    const column = execution.result.columns[index]!;
    assert(field.name === column.name && field.nullable === column.nullable,
      "Arrow column name, order or nullability mismatch.");
    const metadata = object(metadataJson(field.metadata.get(COLUMN_METADATA_KEY)), ["column_id"], ["column_id"]);
    assert(metadata.column_id === column.column_id, "Arrow column identity mismatch.");
    assert(![...field.metadata.keys()].some(key => key.startsWith("ARROW:extension:")),
      "Arrow extension types, including Opaque, are unsupported.");
    supported(field.type, column);
  });
}

type FlatTable = readonly (number | undefined)[];
interface FlatVector { start: number; length: number }

/** Arrow's generated FlatBuffer accessors do not check bounds. Verify every
 * reachable flat-profile table, vector and string before either decoder sees it.
 */
class MetadataVerifier {
  private readonly view: DataView;
  private remaining: number;
  private visits = 0;

  constructor(private readonly bytes: Uint8Array, private readonly operation: Operation, maxBytes: number) {
    this.view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    this.remaining = maxBytes;
  }

  private range(offset: number, length: number, alignment = 1): void {
    assert(Number.isSafeInteger(offset) && Number.isSafeInteger(length) && offset >= 0 && length >= 0
      && offset % alignment === 0 && offset <= this.bytes.length - length,
    "Arrow metadata reference is unaligned or out of bounds.");
  }

  private consume(length: number): void {
    // Count repeated references too: individually valid shared vectors/strings
    // must not expand into more metadata than the configured result byte budget.
    this.remaining -= length;
    if (this.remaining < 0) {
      throw new OssieError("RESULT_LIMIT_EXCEEDED", "Expanded Arrow metadata exceeds the configured byte limit.");
    }
    if ((this.visits++ & 255) === 0) this.operation.check();
  }

  private reference(location: number): number {
    this.range(location, 4, 4);
    const distance = this.view.getUint32(location, true);
    assert(distance > 0, "Arrow metadata contains a null or cyclic reference.");
    const target = location + distance;
    this.range(target, 4, 4);
    return target;
  }

  private table(position: number, widths: readonly number[]): FlatTable {
    this.range(position, 4, 4);
    const distance = this.view.getInt32(position, true);
    assert(distance !== 0, "Arrow metadata has an invalid vtable reference.");
    const vtable = position - distance;
    this.range(vtable, 4, 2);
    const vtableSize = this.view.getUint16(vtable, true);
    const objectSize = this.view.getUint16(vtable + 2, true);
    assert(vtableSize >= 4 && vtableSize % 2 === 0 && objectSize >= 4,
      "Arrow metadata has an invalid table size.");
    this.range(vtable, vtableSize, 2);
    this.range(position, objectSize, 4);
    this.consume(vtableSize + objectSize);
    const fields: (number | undefined)[] = new Array(widths.length);
    for (let index = 0; index < (vtableSize - 4) / 2; index++) {
      const offset = this.view.getUint16(vtable + 4 + index * 2, true);
      if (offset === 0) continue;
      const width = widths[index];
      assert(width !== undefined && offset >= 4 && offset <= objectSize - width,
        "Arrow metadata field is outside its table or unsupported.");
      this.range(position + offset, width, width);
      fields[index] = position + offset;
    }
    return fields;
  }

  private vector(location: number | undefined, stride: number, alignment: number,
    expected?: number): FlatVector {
    if (location === undefined) {
      assert(expected === undefined || expected === 0, "Required Arrow metadata vector is missing.");
      return { start: 0, length: 0 };
    }
    const position = this.reference(location);
    const length = this.view.getUint32(position, true);
    const start = position + 4;
    assert(length <= Math.floor((this.bytes.length - start) / stride),
      "Arrow metadata vector exceeds its actual buffer.");
    assert(expected === undefined || length === expected, "Arrow metadata vector does not match the flat schema.");
    this.range(start, length * stride, alignment);
    this.consume(4 + length * stride);
    return { start, length };
  }

  private string(location: number | undefined, required = false): void {
    if (location === undefined) {
      assert(!required, "Required Arrow metadata string is missing.");
      return;
    }
    const position = this.reference(location);
    const length = this.view.getUint32(position, true);
    this.range(position + 4, length + 1);
    assert(this.bytes[position + 4 + length] === 0, "Arrow metadata string is not terminated.");
    this.consume(5 + length);
  }

  private customMetadata(location: number | undefined): void {
    const vector = this.vector(location, 4, 4);
    for (let index = 0; index < vector.length; index++) {
      const entry = this.table(this.reference(vector.start + index * 4), [4, 4]);
      this.string(entry[0]);
      this.string(entry[1]);
    }
  }

  private signed16(location: number | undefined, fallback = 0): number {
    return location === undefined ? fallback : this.view.getInt16(location, true);
  }

  private unsigned8(location: number | undefined, fallback = 0): number {
    return location === undefined ? fallback : this.view.getUint8(location);
  }

  private counter(location: number | undefined): number {
    if (location === undefined) return 0;
    const value = this.view.getBigInt64(location, true);
    assert(value >= 0n && value <= BigInt(Number.MAX_SAFE_INTEGER), "Invalid Arrow metadata counter.");
    return Number(value);
  }

  private field(position: number): void {
    const field = this.table(position, [4, 1, 1, 4, 4, 4, 4]);
    this.string(field[0], true);
    assert(this.unsigned8(field[1]) <= 1, "Invalid Arrow nullable flag.");
    assert(field[4] === undefined, "Dictionary-encoded Arrow fields are unsupported.");
    // Children are forbidden by this flat profile, so no recursive decoder
    // traversal is allowed, including children hidden on primitive fields.
    this.vector(field[5], 4, 4, 0);
    this.customMetadata(field[6]);
    const typeId = this.unsigned8(field[2]);
    const layouts: Partial<Record<Type, readonly number[]>> = {
      [Type.Int]: [4, 1], [Type.Float]: [2], [Type.Utf8]: [], [Type.Bool]: [],
      [Type.Decimal]: [4, 4, 4], [Type.Date]: [2], [Type.Time]: [2, 4], [Type.Timestamp]: [2, 4],
    };
    const widths = layouts[typeId as Type];
    assert(widths !== undefined && field[3] !== undefined, "Unsupported or missing Arrow physical type.");
    const physical = this.table(this.reference(field[3]), widths);
    if (typeId === Type.Int) assert(this.unsigned8(physical[1]) <= 1, "Invalid Arrow signedness flag.");
    if (typeId === Type.Timestamp) this.string(physical[1]);
  }

  verify(schema: Schema | undefined, columnCount: number): void {
    const message = this.table(this.reference(0), [2, 1, 4, 8, 4]);
    assert([MetadataVersion.V4, MetadataVersion.V5].includes(this.signed16(message[0])),
      "Unsupported Arrow metadata version.");
    const type = this.unsigned8(message[1]);
    assert(message[2] !== undefined, "Required Arrow message header is missing.");
    this.customMetadata(message[4]);
    const bodyLength = this.counter(message[3]);
    if (type === MessageHeader.Schema) {
      assert(schema === undefined && bodyLength === 0, "The IPC stream must have exactly one schema.");
      const header = this.table(this.reference(message[2]), [2, 4, 4, 4]);
      // Arrow JS discards endianness when decoding.
      assert(this.signed16(header[0]) === 0, "Big-endian Arrow streams are unsupported.");
      const fields = this.vector(header[1], 4, 4, columnCount);
      this.customMetadata(header[2]);
      this.vector(header[3], 8, 8);
      for (let index = 0; index < fields.length; index++) this.field(this.reference(fields.start + index * 4));
    } else {
      assert(type === MessageHeader.RecordBatch && schema !== undefined,
        "Only uncompressed record batches may follow the schema.");
      const header = this.table(this.reference(message[2]), [8, 4, 4, 4, 4]);
      this.counter(header[0]);
      this.vector(header[1], 16, 8, schema.fields.length);
      const buffers = schema.fields.reduce((sum, field) => sum + (DataType.isUtf8(field.type) ? 3 : 2), 0);
      this.vector(header[2], 16, 8, buffers);
      assert(header[3] === undefined, "Compressed Arrow IPC is unsupported.");
      this.vector(header[4], 8, 8, 0);
    }
    this.operation.check();
  }
}

function valueBytes(type: DataType, rows: number): number {
  if (DataType.isBool(type)) return Math.ceil(rows / 8);
  if (DataType.isInt(type) || DataType.isTime(type) || DataType.isDecimal(type)) return rows * type.bitWidth / 8;
  if (DataType.isFloat(type)) return rows * (type.precision === Precision.SINGLE ? 4 : 8);
  if (DataType.isDate(type)) return rows * 4;
  if (DataType.isTimestamp(type)) return rows * 8;
  throw new ProtocolError("Unsupported Arrow value buffer.");
}

type BatchHeader = ReturnType<Message<import("apache-arrow").MessageHeader.RecordBatch>["header"]>;

function validateBatchHeader(header: BatchHeader, schema: Schema, bodyLength: number): void {
  count(header.length);
  assert(header.compression === null, "Compressed Arrow IPC is unsupported.");
  assert(header.variadicBufferCounts.length === 0, "Variadic Arrow buffers are unsupported.");
  assert(header.nodes.length === schema.fields.length, "Arrow field-node count mismatch.");
  const bufferCount = schema.fields.reduce((sum, field) => sum + (DataType.isUtf8(field.type) ? 3 : 2), 0);
  assert(header.buffers.length === bufferCount, "Arrow buffer count mismatch.");
  let end = 0;
  for (const buffer of header.buffers) {
    count(buffer.offset);
    count(buffer.length);
    assert(buffer.offset % 8 === 0 && buffer.offset >= end && buffer.offset + buffer.length <= bodyLength,
      "Arrow buffer is unaligned, overlapping or out of bounds.");
    end = buffer.offset + buffer.length;
  }
  for (let i = 0; i < header.nodes.length; i++) {
    const node = header.nodes[i]!;
    count(node.length);
    count(node.nullCount);
    assert(node.length === header.length && node.nullCount <= node.length, "Arrow field length mismatch.");
    assert(schema.fields[i]!.nullable || node.nullCount === 0, "Nonnullable Arrow field contains nulls.");
  }
}

function validateBatchBody(header: BatchHeader, schema: Schema, body: Uint8Array): void {
  let bufferIndex = 0;
  const next = (): Uint8Array => {
    const region = header.buffers[bufferIndex++]!;
    return body.subarray(region.offset, region.offset + region.length);
  };
  for (let i = 0; i < schema.fields.length; i++) {
    const field = schema.fields[i]!, node = header.nodes[i]!;
    const validity = next();
    assert(validity.byteLength === 0 || validity.byteLength >= Math.ceil(node.length / 8),
      "Arrow validity buffer is too short.");
    assert(node.nullCount === 0 || validity.byteLength > 0, "Arrow null bitmap is missing.");
    let nulls = 0;
    if (validity.byteLength > 0) {
      for (let row = 0; row < node.length; row++) {
        if ((validity[row >> 3]! & (1 << (row & 7))) === 0) nulls++;
      }
    }
    assert(nulls === node.nullCount, "Arrow null count does not match the validity bitmap.");
    if (DataType.isUtf8(field.type)) {
      const offsets = next(), values = next();
      assert((node.length === 0 && offsets.byteLength === 0)
        || offsets.byteLength >= (node.length + 1) * 4, "Arrow string offsets are too short.");
      if (offsets.byteLength === 0) continue;
      const view = new DataView(offsets.buffer, offsets.byteOffset, offsets.byteLength);
      let previous = view.getInt32(0, true);
      assert(previous >= 0 && previous <= values.byteLength, "Invalid Arrow string offset.");
      const decoder = new TextDecoder("utf-8", { fatal: true });
      for (let row = 0; row < node.length; row++) {
        const offset = view.getInt32((row + 1) * 4, true);
        assert(offset >= previous && offset <= values.byteLength, "Invalid Arrow string offsets.");
        if (validity.byteLength === 0 || (validity[row >> 3]! & (1 << (row & 7))) !== 0) {
          decoder.decode(values.subarray(previous, offset));
        }
        previous = offset;
      }
    } else {
      assert(next().byteLength >= valueBytes(field.type, node.length), "Arrow value buffer is too short.");
    }
  }
}

/** Validates IPC framing before Arrow allocates or silently accepts schema replacement. */
async function* frames(source: BodySource, operation: Operation, execution: SuccessfulExecution,
  maxRows: number, maxBytes: number, page?: ResultPage): AsyncGenerator<Uint8Array> {
  let schema: Schema | undefined;
  let rows = 0;
  const counts = page ?? execution.result;
  const finish = (): void => {
    assert(schema !== undefined, "The IPC stream has no schema.");
    assert(source.bytes === counts.byte_count, "Decoded IPC byte count mismatch.");
    assert(rows === counts.row_count, "Arrow row count mismatch.");
  };
  while (true) {
    operation.check();
    const first = await source.exact(4, true);
    if (first === undefined) { finish(); return; }
    let prefix = first;
    let length = new DataView(first.buffer).getInt32(0, true);
    if (length === -1) {
      const second = (await source.exact(4))!;
      prefix = new Uint8Array(8);
      prefix.set(first);
      prefix.set(second, 4);
      length = new DataView(second.buffer).getInt32(0, true);
    }
    if (length === 0) {
      await source.end();
      finish();
      yield prefix;
      return;
    }
    assert(length > 0 && length <= Math.min(maxBytes, 1024 * 1024), "Invalid Arrow metadata length.");
    assert((length + prefix.byteLength) % 8 === 0, "Unaligned Arrow metadata.");
    const bytes = (await source.exact(length))!;
    new MetadataVerifier(bytes, operation, maxBytes).verify(schema, execution.result.columns.length);
    const message = Message.decode(bytes);
    assert([MetadataVersion.V4, MetadataVersion.V5].includes(message.version), "Unsupported Arrow metadata version.");
    count(message.bodyLength);
    assert(message.bodyLength <= maxBytes && message.bodyLength % 8 === 0, "Invalid Arrow body length.");
    let header: BatchHeader | undefined;
    if (message.isSchema()) {
      assert(schema === undefined && message.bodyLength === 0, "The IPC stream must have exactly one schema.");
      schema = message.header();
      schemaMatches(schema, execution, page);
    } else {
      assert(schema !== undefined && message.isRecordBatch(), "Only uncompressed record batches may follow the schema.");
      header = message.header();
      validateBatchHeader(header, schema, message.bodyLength);
      rows += header.length;
      if (!Number.isSafeInteger(rows) || rows > maxRows) {
        throw new OssieError("RESULT_LIMIT_EXCEEDED", "The result exceeded the configured row limit.");
      }
      assert(rows <= counts.row_count, "Arrow row count exceeds the manifest.");
    }
    // Retain at most one complete IPC message, not the whole result.
    const body = (await source.exact(message.bodyLength))!;
    if (header) validateBatchBody(header, schema!, body);
    operation.check();
    yield prefix;
    yield bytes;
    if (body.byteLength) yield body;
  }
}

export async function* arrowBatches(response: Response, execution: SuccessfulExecution,
  operation: Operation, maxBytes: number, maxRows: number, page?: ResultPage,
  remainingBytes = maxBytes): AsyncGenerator<RecordBatch> {
  const source = new BodySource(response, operation, Math.min(maxBytes, remainingBytes));
  const input = frames(source, operation, execution, maxRows, maxBytes, page);
  let reader: RecordBatchReader | undefined;
  try {
    reader = await operation.race(() => RecordBatchReader.from(input));
    await operation.race(() => reader!.open({ autoDestroy: false }));
    assert(reader.isStream() && reader.schema, "Expected an Arrow IPC stream with a schema.");
    schemaMatches(reader.schema, execution, page);
    while (true) {
      const next = await operation.race(() => reader!.next());
      if (next.done) break;
      const batch = next.value as RecordBatch;
      if (new Set(reader.schema.fields.map(field => field.name)).size !== reader.schema.fields.length) {
        // Arrow 21's RecordBatch constructor merges field metadata by display
        // name. Restore the already-validated positional IPC schema; keep the
        // exact decoded batch and all its column buffers untouched.
        Object.defineProperty(batch, "schema", { value: reader.schema });
      }
      schemaMatches(batch.schema, execution, page);
      operation.check();
      yield batch;
    }
  } catch (error) {
    operation.check();
    if (error instanceof OssieError) throw error;
    throw new ProtocolError("The result is not a valid supported Arrow IPC stream.");
  } finally {
    source.close();
    // Closing the source first also unblocks an outstanding Arrow read on abort.
    if (reader) void Promise.resolve(reader.cancel()).catch(() => {});
    void input.return(undefined).catch(() => {});
  }
}
