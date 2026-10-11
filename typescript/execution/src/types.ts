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

export const CONTRACT_VERSION = "0.1-draft" as const;
export const FOUNDATION_REVISION = "cc0d07099a3ef31e85af1fd93458a52186fae2c6" as const;
export const QUERY_SCHEMA_VERSION = "mcp-0.4-draft" as const;
export const ARROW_MEDIA_TYPE = "application/vnd.apache.arrow.stream" as const;
export const EXECUTION_METADATA_KEY = "org.apache.ossie.execution" as const;
export const COLUMN_METADATA_KEY = "org.apache.ossie.column" as const;

export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type Extensions = Record<string, { [key: string]: JsonValue }>;
export type Predicate = string | readonly string[];
export interface OrderBy {
  field: string;
  direction?: "ASC" | "DESC";
  nulls?: "FIRST" | "LAST";
}
interface QueryClauses {
  where?: Predicate;
  order_by?: readonly OrderBy[];
  limit?: number;
}
export interface AggregationQuery extends QueryClauses {
  dimensions?: readonly string[];
  measures?: readonly string[];
  having?: Predicate;
  fields?: never;
}
export interface ScalarQuery extends QueryClauses {
  fields: readonly string[];
  dimensions?: never;
  measures?: never;
  having?: never;
}
export type Query = AggregationQuery | ScalarQuery;
export type QueryShape = "aggregation" | "scalar";
export interface ExecuteRequest {
  data_source_id: string;
  query: Query;
}
export interface ModelIdentity {
  id: string;
  revision: string;
}
export interface Capabilities {
  contract_version: typeof CONTRACT_VERSION;
  foundation_revision: typeof FOUNDATION_REVISION;
  query_schema_version: typeof QUERY_SCHEMA_VERSION;
  result_formats: readonly ["arrow-stream"];
  query_shapes: readonly QueryShape[];
  features: readonly string[];
  max_wait_seconds: number;
  extensions?: Extensions;
}
export interface Binding {
  data_source_id: string;
  model: ModelIdentity;
  query_shapes: readonly QueryShape[];
  features: readonly string[];
  extensions?: Extensions;
}
export interface QueryError {
  code: string;
  message: string;
  retryable: boolean;
  replacement_query?: Query;
  extensions?: Extensions;
}
export interface ProblemDetails {
  type: string;
  title: string;
  status: number;
  detail?: string;
  error: QueryError;
}
export interface Progress {
  fraction?: number;
  phase?: string;
}
export interface Warning {
  code: string;
  message: string;
}
export type LogicalType = "String" | "Integer" | "Decimal" | "Float" | "Boolean"
  | "Date" | "Time" | "DateTime" | "DateTimeTz";
export interface Column {
  column_id: string;
  name: string;
  logical_type: LogicalType;
  nullable: boolean;
  role?: "dimension" | "measure" | "field";
  model_ref?: string;
  label?: string;
  description?: string;
  unit?: string;
  currency?: string;
  extensions?: Extensions;
}
export interface ResultPage {
  page_index: number;
  row_offset: number;
  row_count: number;
  byte_count: number;
  url: string;
}
interface ResultManifestBase {
  result_id: string;
  format: "arrow-stream";
  row_count: number;
  byte_count: number;
  completeness: "complete" | "truncated";
  truncation_reason?: string;
  expires_at: string;
  columns: readonly Column[];
  extensions?: Extensions;
}
export type ResultManifest = ResultManifestBase & (
  | { url: string; pages?: never }
  | { url?: never; pages: readonly ResultPage[] }
);
interface ExecutionBase {
  contract_version: typeof CONTRACT_VERSION;
  execution_id: string;
  data_source_id: string;
  model: ModelIdentity;
  warnings: readonly Warning[];
  extensions?: Extensions;
}
export type Execution = ExecutionBase & (
  | { state: "queued" | "running" | "cancelling"; progress?: Progress; result?: never; error?: never }
  | { state: "succeeded"; result: ResultManifest; progress?: never; error?: never }
  | { state: "failed"; error: QueryError; progress?: never; result?: never }
  | { state: "cancelled"; progress?: never; result?: never; error?: never }
);
export type SuccessfulExecution = Extract<Execution, { state: "succeeded" }>;
export interface ExecutionMetadata {
  contract_version: typeof CONTRACT_VERSION;
  execution_id: string;
  result_id: string;
  data_source_id: string;
  model: ModelIdentity;
  page_index?: number;
  row_offset?: number;
}
export interface ClientOptions {
  baseUrl: string;
  fetch?: typeof globalThis.fetch;
  getAccessToken?: () => string | Promise<string>;
  allowedResultOrigins?: readonly string[];
  allowInsecureLocalhost?: boolean;
  pollIntervalMs?: number;
  timeoutMs?: number;
  maxResultBytes?: number;
  maxResultRows?: number;
}
export interface RequestOptions {
  signal?: AbortSignal;
}
export interface SubmitOptions extends RequestOptions {
  waitSeconds?: number;
}
export interface WaitOptions extends RequestOptions {
  onProgress?: (execution: Execution) => void;
}
export interface ExecuteOptions extends SubmitOptions, WaitOptions {
  allowTruncated?: boolean;
}
