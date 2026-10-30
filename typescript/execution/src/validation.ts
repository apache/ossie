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

import { ProtocolError } from "./errors.js";
import { CONTRACT_VERSION, FOUNDATION_REVISION, LIVE_CONTRACT_VERSION, QUERY_SCHEMA_VERSION } from "./types.js";
import type { Binding, Capabilities, ExecuteRequest, Execution, ExecutionMetadata,
  LiveCompletion, LiveExecution, LiveExecutionMetadata, LiveStream,
  ProblemDetails, Query, QueryError, ResultManifest } from "./types.js";

type ObjectValue = Record<string, unknown>;
const own = (value: ObjectValue, key: string): boolean => Object.hasOwn(value, key);

export function assert(condition: unknown, message: string): asserts condition {
  if (!condition) throw new ProtocolError(message);
}

export function object(value: unknown, allowed: readonly string[], required: readonly string[]): ObjectValue {
  assert(value !== null && typeof value === "object" && !Array.isArray(value),
    "Expected a protocol object.");
  const record = value as ObjectValue;
  assert(Object.getPrototypeOf(record) === Object.prototype || Object.getPrototypeOf(record) === null,
    "Expected a plain protocol object.");
  assert(Object.keys(record).every(key => allowed.includes(key)), "Unknown core protocol field.");
  assert(required.every(key => own(record, key)), "Missing required protocol field.");
  return record;
}

export function text(value: unknown): asserts value is string {
  assert(typeof value === "string" && value.trim().length > 0, "Expected a nonempty string.");
}

export function count(value: unknown): asserts value is number {
  assert(typeof value === "number" && Number.isSafeInteger(value) && value >= 0,
    "Expected a nonnegative safe integer.");
}

function strings(value: unknown, nonempty = false): asserts value is string[] {
  assert(Array.isArray(value) && (!nonempty || value.length > 0), "Expected a string array.");
  for (const item of value) text(item);
}

function enumeration(value: unknown, choices: readonly unknown[]): void {
  assert(choices.includes(value), "Unsupported protocol value.");
}

function boolean(value: unknown): void {
  assert(typeof value === "boolean", "Expected a boolean.");
}

function json(value: unknown, depth = 0, seen = new Set<object>()): void {
  assert(depth <= 32, "Extension nesting exceeds the supported limit.");
  if (value === null || typeof value === "string" || typeof value === "boolean") return;
  if (typeof value === "number") {
    assert(Number.isFinite(value), "Extension numbers must be finite.");
    return;
  }
  assert(typeof value === "object" && !seen.has(value), "Invalid JSON extension value.");
  seen.add(value);
  for (const item of Object.values(value)) json(item, depth + 1, seen);
  seen.delete(value);
}

function extensions(value: ObjectValue): void {
  if (!own(value, "extensions")) return;
  assert(value.extensions !== null && typeof value.extensions === "object" && !Array.isArray(value.extensions),
    "Extensions must be a namespaced object.");
  for (const [key, extension] of Object.entries(value.extensions)) {
    text(key);
    assert(extension !== null && typeof extension === "object" && !Array.isArray(extension),
      "Each extension must be an object.");
    json(extension);
  }
}

function predicate(value: unknown): void {
  if (Array.isArray(value)) strings(value, true);
  else text(value);
}

export function validateQuery(value: unknown): Query {
  const query = object(value, ["fields", "dimensions", "measures", "where", "having", "order_by", "limit"], []);
  if (own(query, "fields")) {
    assert(!own(query, "dimensions") && !own(query, "measures") && !own(query, "having"),
      "Scalar fields cannot be combined with aggregation clauses.");
    strings(query.fields, true);
  } else {
    if (own(query, "dimensions")) strings(query.dimensions);
    if (own(query, "measures")) strings(query.measures);
    assert((Array.isArray(query.dimensions) && query.dimensions.length > 0)
      || (Array.isArray(query.measures) && query.measures.length > 0),
    "An aggregation needs at least one dimension or measure.");
    if (own(query, "having")) predicate(query.having);
  }
  if (own(query, "where")) predicate(query.where);
  if (own(query, "limit")) count(query.limit);
  if (own(query, "order_by")) {
    assert(Array.isArray(query.order_by) && query.order_by.length > 0, "order_by must be a nonempty array.");
    for (const item of query.order_by) {
      const order = object(item, ["field", "direction", "nulls"], ["field"]);
      text(order.field);
      if (own(order, "direction")) enumeration(order.direction, ["ASC", "DESC"]);
      if (own(order, "nulls")) enumeration(order.nulls, ["FIRST", "LAST"]);
    }
  }
  return value as Query;
}

export function validateRequest(value: unknown): ExecuteRequest {
  const request = object(value, ["data_source_id", "query"], ["data_source_id", "query"]);
  text(request.data_source_id);
  validateQuery(request.query);
  return value as ExecuteRequest;
}

function model(value: unknown): void {
  const identity = object(value, ["id", "revision"], ["id", "revision"]);
  text(identity.id);
  text(identity.revision);
}

function shapes(value: unknown): void {
  strings(value, true);
  value.forEach(item => enumeration(item, ["scalar", "aggregation"]));
  assert(new Set(value).size === value.length, "Duplicate query shapes.");
}

export function validateCapabilities(value: unknown): Capabilities {
  const result = object(value, ["contract_version", "foundation_revision", "query_schema_version",
    "result_formats", "query_shapes", "features", "max_wait_seconds", "extensions"],
  ["contract_version", "foundation_revision", "query_schema_version", "result_formats",
    "query_shapes", "features", "max_wait_seconds"]);
  enumeration(result.contract_version, [CONTRACT_VERSION]);
  enumeration(result.foundation_revision, [FOUNDATION_REVISION]);
  enumeration(result.query_schema_version, [QUERY_SCHEMA_VERSION]);
  assert(Array.isArray(result.result_formats) && result.result_formats.length === 1
    && result.result_formats[0] === "arrow-stream", "Unsupported result formats.");
  shapes(result.query_shapes);
  strings(result.features);
  assert(new Set(result.features).size === result.features.length, "Duplicate features.");
  count(result.max_wait_seconds);
  assert(result.max_wait_seconds <= 60, "Unsupported maximum wait.");
  extensions(result);
  return value as Capabilities;
}

export function validateBinding(value: unknown): Binding {
  const result = object(value, ["data_source_id", "model", "query_shapes", "features", "extensions"],
    ["data_source_id", "model", "query_shapes", "features"]);
  text(result.data_source_id);
  model(result.model);
  shapes(result.query_shapes);
  strings(result.features);
  assert(new Set(result.features).size === result.features.length, "Duplicate features.");
  extensions(result);
  return value as Binding;
}

export function validateQueryError(value: unknown): QueryError {
  const error = object(value, ["code", "message", "retryable", "replacement_query", "extensions"],
    ["code", "message", "retryable"]);
  text(error.code);
  text(error.message);
  boolean(error.retryable);
  if (own(error, "replacement_query")) validateQuery(error.replacement_query);
  extensions(error);
  return value as QueryError;
}

export function validateProblem(value: unknown, status: number): ProblemDetails {
  const problem = object(value, ["type", "title", "status", "detail", "error"],
    ["type", "title", "status", "error"]);
  text(problem.type);
  text(problem.title);
  count(problem.status);
  assert(problem.status === status && status >= 400 && status <= 599, "Problem status mismatch.");
  if (own(problem, "detail")) text(problem.detail);
  validateQueryError(problem.error);
  return value as ProblemDetails;
}

function timestamp(value: unknown): void {
  text(value);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(?:Z|([+-])(\d{2}):(\d{2}))$/i.exec(value);
  assert(match, "Expected an RFC3339 timestamp.");
  const [, y, m, d, h, minute, second, , oh = "0", om = "0"] = match;
  const year = Number(y), month = Number(m), day = Number(d);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  assert(month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1]!
    && Number(h) <= 23 && Number(minute) <= 59 && Number(second) <= 59
    && Number(oh) <= 23 && Number(om) <= 59 && Number.isFinite(Date.parse(value)),
  "Invalid RFC3339 timestamp.");
}

function resultUrl(value: unknown): void {
  text(value);
  assert(/^https?:\/\//i.test(value) && !/[\u0000-\u0020\u007f\\]/.test(value),
    "Result URLs must be absolute HTTP(S) URLs.");
  try {
    const parsed = new URL(value);
    const authority = /^https?:\/\/([^/?#]*)/i.exec(value)?.[1];
    assert(!parsed.username && !parsed.password && !authority?.includes("@")
      && !parsed.href.includes("#"), "Result URL credentials and fragments are forbidden.");
  } catch {
    throw new ProtocolError("Invalid or unsafe result URL.");
  }
}

export function validateManifest(value: unknown): ResultManifest {
  const manifest = object(value, ["result_id", "format", "url", "pages", "row_count", "byte_count",
    "completeness", "truncation_reason", "expires_at", "columns", "extensions"],
  ["result_id", "format", "row_count", "byte_count", "completeness", "expires_at", "columns"]);
  text(manifest.result_id);
  enumeration(manifest.format, ["arrow-stream"]);
  count(manifest.row_count);
  count(manifest.byte_count);
  assert(own(manifest, "url") !== own(manifest, "pages"), "Expected exactly one result delivery mode.");
  if (own(manifest, "url")) resultUrl(manifest.url);
  else {
    assert(Array.isArray(manifest.pages) && manifest.pages.length > 0, "Expected nonempty result pages.");
    let rows = 0, bytes = 0;
    for (let index = 0; index < manifest.pages.length; index++) {
      const page = object(manifest.pages[index], ["page_index", "row_offset", "row_count", "byte_count", "url"],
        ["page_index", "row_offset", "row_count", "byte_count", "url"]);
      count(page.page_index);
      count(page.row_offset);
      count(page.row_count);
      count(page.byte_count);
      resultUrl(page.url);
      assert(page.page_index === index && page.row_offset === rows, "Result pages are not contiguous and ordered.");
      assert(page.byte_count > 0, "Every page must include a self-contained Arrow schema.");
      assert(page.row_count > 0 || manifest.pages.length === 1, "Only a single empty-result page may have zero rows.");
      rows += page.row_count;
      bytes += page.byte_count;
      count(rows);
      count(bytes);
    }
    assert(rows === manifest.row_count && bytes === manifest.byte_count, "Result page totals do not match the manifest.");
  }
  enumeration(manifest.completeness, ["complete", "truncated"]);
  if (manifest.completeness === "truncated") {
    assert(own(manifest, "truncation_reason"), "A truncated result must give a truncation reason.");
  }
  if (own(manifest, "truncation_reason")) {
    text(manifest.truncation_reason);
    assert(manifest.completeness === "truncated", "A complete result cannot have a truncation reason.");
  }
  timestamp(manifest.expires_at);
  columns(manifest.columns);
  extensions(manifest);
  return value as ResultManifest;
}

function columns(value: unknown): void {
  assert(Array.isArray(value) && value.length > 0, "Expected result columns.");
  const ids = new Set<string>();
  for (const item of value) {
    const column = object(item, ["column_id", "name", "logical_type", "nullable", "role", "model_ref",
      "label", "description", "unit", "currency", "extensions"], ["column_id", "name", "logical_type", "nullable"]);
    text(column.column_id);
    assert(!ids.has(column.column_id), "Duplicate result column identifiers.");
    ids.add(column.column_id);
    text(column.name);
    enumeration(column.logical_type, ["String", "Integer", "Decimal", "Float", "Boolean",
      "Date", "Time", "DateTime", "DateTimeTz"]);
    boolean(column.nullable);
    if (own(column, "role")) enumeration(column.role, ["dimension", "measure", "field"]);
    for (const key of ["model_ref", "label", "description", "unit", "currency"]) {
      if (own(column, key)) text(column[key]);
    }
    extensions(column);
  }
}

export function validateExecution(value: unknown): Execution {
  const execution = object(value, ["contract_version", "execution_id", "data_source_id", "model",
    "warnings", "extensions", "state", "progress", "result", "error"],
  ["contract_version", "execution_id", "data_source_id", "model", "warnings", "state"]);
  enumeration(execution.contract_version, [CONTRACT_VERSION]);
  text(execution.execution_id);
  text(execution.data_source_id);
  model(execution.model);
  assert(Array.isArray(execution.warnings), "Expected execution warnings.");
  for (const item of execution.warnings) {
    const warning = object(item, ["code", "message"], ["code", "message"]);
    text(warning.code);
    text(warning.message);
  }
  enumeration(execution.state, ["queued", "running", "cancelling", "succeeded", "failed", "cancelled"]);
  if (execution.state === "succeeded") validateManifest(execution.result);
  else assert(!own(execution, "result"), "Only succeeded executions can have a result.");
  if (execution.state === "failed") validateQueryError(execution.error);
  else assert(!own(execution, "error"), "Only failed executions can have an error.");
  if (["queued", "running", "cancelling"].includes(execution.state as string)) {
    if (own(execution, "progress")) {
      const progress = object(execution.progress, ["fraction", "phase"], []);
      if (own(progress, "fraction")) {
        assert(typeof progress.fraction === "number" && Number.isFinite(progress.fraction)
          && progress.fraction >= 0 && progress.fraction <= 1, "Invalid progress fraction.");
      }
      if (own(progress, "phase")) text(progress.phase);
    }
  } else assert(!own(execution, "progress"), "Terminal executions cannot have progress.");
  extensions(execution);
  return value as Execution;
}

export function validateMetadata(value: unknown): ExecutionMetadata {
  const metadata = object(value, ["contract_version", "execution_id", "result_id", "data_source_id", "model",
    "page_index", "row_offset"],
    ["contract_version", "execution_id", "result_id", "data_source_id", "model"]);
  enumeration(metadata.contract_version, [CONTRACT_VERSION]);
  text(metadata.execution_id);
  text(metadata.result_id);
  text(metadata.data_source_id);
  model(metadata.model);
  assert(own(metadata, "page_index") === own(metadata, "row_offset"), "Page metadata requires both index and offset.");
  if (own(metadata, "page_index")) {
    count(metadata.page_index);
    count(metadata.row_offset);
  }
  return value as ExecutionMetadata;
}

export function terminal(execution: Execution | LiveExecution): boolean {
  return ["succeeded", "failed", "cancelled"].includes(execution.state);
}

/** Canonical comparison is independent of JSON object key order. */
export function equivalent(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (left === null || right === null || typeof left !== "object" || typeof right !== "object") return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const a = left as ObjectValue, b = right as ObjectValue;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every(key => own(b, key) && equivalent(a[key], b[key]));
}

export function transition(previous: Execution | LiveExecution, next: Execution | LiveExecution): void {
  assert(previous.contract_version === next.contract_version
    && previous.execution_id === next.execution_id && previous.data_source_id === next.data_source_id
    && equivalent(previous.model, next.model), "Execution identity changed.");
  if ("delivery" in previous) {
    assert("delivery" in next && next.delivery === previous.delivery, "Execution delivery changed.");
    if (previous.stream) assert(equivalent(previous.stream, next.stream), "The live stream descriptor changed.");
  }
  const allowed: Record<Execution["state"], readonly Execution["state"][]> = {
    queued: ["queued", "running", "cancelling", "succeeded", "failed", "cancelled"],
    running: ["running", "cancelling", "succeeded", "failed", "cancelled"],
    cancelling: ["cancelling", "succeeded", "failed", "cancelled"],
    succeeded: ["succeeded"], failed: ["failed"], cancelled: ["cancelled"],
  };
  assert(allowed[previous.state].includes(next.state), "Execution lifecycle regressed.");
  if (terminal(previous)) {
    assert(equivalent(previous, next), "An immutable terminal execution changed.");
  }
}

export function validateLiveStream(value: unknown): LiveStream {
  const stream = object(value, ["result_id", "format", "url", "expires_at", "columns", "replayable"],
    ["result_id", "format", "url", "expires_at", "columns", "replayable"]);
  text(stream.result_id);
  enumeration(stream.format, ["arrow-stream"]);
  resultUrl(stream.url);
  timestamp(stream.expires_at);
  columns(stream.columns);
  assert(stream.replayable === false, "A live stream must be single-use.");
  return value as LiveStream;
}

export function validateLiveCompletion(value: unknown): LiveCompletion {
  const completion = object(value, ["result_id", "row_count", "byte_count", "completeness", "truncation_reason"],
    ["result_id", "row_count", "byte_count", "completeness"]);
  text(completion.result_id);
  count(completion.row_count);
  count(completion.byte_count);
  enumeration(completion.completeness, ["complete", "truncated"]);
  assert(own(completion, "truncation_reason") === (completion.completeness === "truncated"),
    "Only a truncated live completion must include a truncation reason.");
  if (own(completion, "truncation_reason")) text(completion.truncation_reason);
  return value as LiveCompletion;
}

export function validateLiveExecution(value: unknown): LiveExecution {
  const execution = object(value, ["contract_version", "delivery", "execution_id", "data_source_id",
    "model", "warnings", "extensions", "state", "progress", "stream", "completion", "error"],
  ["contract_version", "delivery", "execution_id", "data_source_id", "model", "warnings", "state"]);
  enumeration(execution.contract_version, [LIVE_CONTRACT_VERSION]);
  enumeration(execution.delivery, ["live"]);
  // Reuse the unchanged 0.1 common-field/state validator, not its result contract.
  const { delivery: _delivery, stream: _stream, completion: _completion, ...common } = execution;
  validateExecution({ ...common, contract_version: CONTRACT_VERSION,
    ...(execution.state === "succeeded" ? { state: "cancelled" } : {}) });
  if (own(execution, "stream")) {
    assert(execution.state !== "queued", "Queued live executions cannot advertise a stream.");
    validateLiveStream(execution.stream);
  }
  if (execution.state === "succeeded") {
    assert(own(execution, "stream"), "Live success requires a stream.");
    const completion = validateLiveCompletion(execution.completion);
    assert(completion.result_id === (execution.stream as LiveStream).result_id,
      "Live completion result identity mismatch.");
  } else assert(!own(execution, "completion"), "Only live success can include completion.");
  return value as LiveExecution;
}

export function validateLiveMetadata(value: unknown): LiveExecutionMetadata {
  const metadata = object(value, ["contract_version", "execution_id", "result_id", "data_source_id", "model"],
    ["contract_version", "execution_id", "result_id", "data_source_id", "model"]);
  enumeration(metadata.contract_version, [LIVE_CONTRACT_VERSION]);
  validateMetadata({ ...metadata, contract_version: CONTRACT_VERSION });
  return value as LiveExecutionMetadata;
}
