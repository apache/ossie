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

import { Schema, Table } from "apache-arrow";
import type { RecordBatch } from "apache-arrow";
import { arrowBatches } from "./arrow.js";
import { ExecutionCancelledError, HttpProblemError, LiveExecutionCancelledError, LiveQueryExecutionError,
  OssieError, ProtocolError, QueryExecutionError } from "./errors.js";
import { ARROW_MEDIA_TYPE, EXECUTION_METADATA_KEY, LIVE_CONTRACT_VERSION, LIVE_FEATURE } from "./types.js";
import type { Binding, Capabilities, ClientOptions, ExecuteOptions, ExecuteRequest, Execution,
  LiveExecution, LiveOptions, LiveReadOptions, LiveStream, LiveSuccessfulExecution,
  RequestOptions, ResultPage, SubmitOptions, SuccessfulExecution, WaitOptions } from "./types.js";
import { Operation, cancelBody, mediaType, readJson } from "./transport.js";
import { assert, count, equivalent, terminal, text, transition, validateBinding, validateCapabilities,
  validateExecution, validateLiveExecution, validateProblem, validateRequest } from "./validation.js";

export const DEFAULT_TIMEOUT_MS = 60_000;
export const DEFAULT_POLL_INTERVAL_MS = 250;
export const DEFAULT_MAX_RESULT_BYTES = 64 * 1024 * 1024;
export const DEFAULT_MAX_RESULT_ROWS = 1_000_000;
export const MAX_TRACKED_EXECUTIONS = 64;

interface Observation {
  previous: Execution | undefined;
  fallback?: Execution;
}
interface LiveObservation {
  previous: LiveExecution | undefined;
  fallback?: LiveExecution;
}
interface LiveConsumption {
  acquire(id: string): void;
  release(): void;
}

function liveFailure(execution: LiveExecution): void {
  if (execution.state === "failed") throw new LiveQueryExecutionError(execution);
  if (execution.state === "cancelled") throw new LiveExecutionCancelledError(execution);
}

function integerOption(value: unknown, fallback: number, minimum: number, maximum = Number.MAX_SAFE_INTEGER): number {
  if (value === undefined) return fallback;
  count(value);
  assert(value >= minimum && value <= maximum, "Invalid numeric client option.");
  return value;
}

function url(value: string): URL {
  text(value);
  assert(value === value.trim() && !/[\u0000-\u0020\u007f\\]/.test(value), "Invalid URL.");
  const authority = /^(?:[a-z][a-z0-9+.-]*:)?\/{2,}([^/?#]*)/i.exec(value)?.[1];
  assert(!authority?.includes("@"), "URL user information is forbidden.");
  try {
    const parsed = new URL(value);
    assert(!parsed.username && !parsed.password && !parsed.href.includes("#"), "URL credentials and fragments are forbidden.");
    return parsed;
  } catch {
    throw new ProtocolError("Invalid or unsafe URL.");
  }
}

function local(url: URL): boolean {
  return ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
}

function idPath(id: string): string {
  text(id);
  assert(id !== "." && id !== "..", "Dot-segment identifiers are unsupported.");
  try { return encodeURIComponent(id); }
  catch { throw new ProtocolError("The identifier is not valid Unicode."); }
}

function waitPreference(options: SubmitOptions): string | undefined {
  if (options.waitSeconds === undefined) return undefined;
  count(options.waitSeconds);
  assert(options.waitSeconds <= 60, "waitSeconds must be an integer between zero and sixty.");
  return `wait=${options.waitSeconds}`;
}

function successful(execution: Execution): SuccessfulExecution {
  if (execution.state === "failed") throw new QueryExecutionError(execution);
  if (execution.state === "cancelled") throw new ExecutionCancelledError(execution);
  assert(execution.state === "succeeded", "The execution has not succeeded.");
  return execution;
}

function schemaIdentity(schema: Schema): unknown {
  const metadata: Record<string, unknown> = Object.fromEntries(schema.metadata);
  const identity = JSON.parse(schema.metadata.get(EXECUTION_METADATA_KEY)!) as Record<string, unknown>;
  delete identity.page_index;
  delete identity.row_offset;
  metadata[EXECUTION_METADATA_KEY] = identity;
  return {
    metadata,
    fields: schema.fields.map(field => ({
      name: field.name, nullable: field.nullable, type: field.type,
      metadata: Object.fromEntries(field.metadata),
    })),
  };
}

/** Experimental browser-first client. It never retries or repairs a submitted query.
 * Aborting a local operation does not cancel the backend execution; use cancel().
 */
export class OssieClient {
  private readonly base: URL;
  private readonly fetcher: typeof globalThis.fetch;
  private readonly getAccessToken: ClientOptions["getAccessToken"];
  private readonly origins = new Set<string>();
  private readonly timeoutMs: number;
  private readonly pollIntervalMs: number;
  private readonly maxResultBytes: number;
  private readonly maxResultRows: number;
  private readonly executions = new Map<string, Execution>();
  private readonly liveExecutions = new Map<string, LiveExecution>();
  // Fail closed at the bound rather than evicting a single-use consumption fence.
  private readonly consumedLive = new Set<string>();
  private reservedLive = 0;

  constructor(options: ClientOptions) {
    assert(options !== null && typeof options === "object", "Client options are required.");
    if (options.allowInsecureLocalhost !== undefined) {
      assert(typeof options.allowInsecureLocalhost === "boolean", "allowInsecureLocalhost must be a boolean.");
    }
    this.base = url(options.baseUrl);
    assert(!this.base.href.includes("?"), "The base URL cannot contain a query.");
    assert(this.base.protocol === "https:" || (options.allowInsecureLocalhost === true
      && this.base.protocol === "http:" && local(this.base)), "The base URL must use HTTPS.");
    if (!this.base.pathname.endsWith("/")) this.base.pathname += "/";
    this.fetcher = options.fetch ?? globalThis.fetch?.bind(globalThis);
    assert(typeof this.fetcher === "function", "A Fetch implementation is required.");
    this.getAccessToken = options.getAccessToken;
    assert(this.getAccessToken === undefined || typeof this.getAccessToken === "function",
      "getAccessToken must be a function.");
    this.timeoutMs = integerOption(options.timeoutMs, DEFAULT_TIMEOUT_MS, 1, 2_147_483_647);
    this.pollIntervalMs = integerOption(options.pollIntervalMs, DEFAULT_POLL_INTERVAL_MS, 1, 2_147_483_647);
    this.maxResultBytes = integerOption(options.maxResultBytes, DEFAULT_MAX_RESULT_BYTES, 1);
    this.maxResultRows = integerOption(options.maxResultRows, DEFAULT_MAX_RESULT_ROWS, 0);
    if (options.allowedResultOrigins !== undefined) {
      assert(Array.isArray(options.allowedResultOrigins), "allowedResultOrigins must be an array.");
      for (const origin of options.allowedResultOrigins) {
        const parsed = url(origin);
        assert(parsed.protocol === "https:" && parsed.pathname === "/" && !parsed.href.includes("?"),
          "Allowed result origins must be HTTPS origins, without paths or queries.");
        this.origins.add(parsed.origin);
      }
    }
  }

  /** Reads only the configured endpoint; does not enumerate vendors or data sources. */
  async getCapabilities(options: RequestOptions = {}): Promise<Capabilities> {
    return this.run(options, async operation =>
      validateCapabilities(await this.control("capabilities", "GET", operation, [200])));
  }

  async getBinding(id: string, options: RequestOptions = {}): Promise<Binding> {
    const path = `bindings/${idPath(id)}`;
    return this.run(options, async operation => {
      const binding = validateBinding(await this.control(path, "GET", operation, [200]));
      assert(binding.data_source_id === id, "Binding identity mismatch.");
      return binding;
    });
  }

  /** Sends exactly one POST. Shape validation is not an expression compiler. */
  async submit(request: ExecuteRequest, options: SubmitOptions = {}): Promise<Execution> {
    const body = this.requestBody(request);
    const prefer = waitPreference(options);
    return this.run(options, operation => this.submitInternal(body, prefer, operation));
  }

  /** Returns failed/cancelled states as data, while enforcing identity and lifecycle. */
  async getExecution(id: string, options: RequestOptions = {}): Promise<Execution> {
    idPath(id);
    return this.run(options, operation => this.getInternal(id, operation));
  }

  /** Polls a known execution. Progress fractions may change in either direction. */
  async wait(execution: Execution, options: WaitOptions = {}): Promise<SuccessfulExecution> {
    validateExecution(execution);
    const known = this.executions.get(execution.execution_id);
    if (known) this.sameIdentity(known, execution);
    const snapshot = this.remember(known && !terminal(execution) ? known : execution);
    this.validateProgressCallback(options);
    return this.run(options, operation => this.waitInternal(snapshot, options, operation));
  }

  /** Explicit backend cancellation; races can still return a succeeded execution. */
  async cancel(executionId: string, options: RequestOptions = {}): Promise<Execution> {
    const path = `queries/${idPath(executionId)}/cancel`;
    const observation: Observation = { previous: this.executions.get(executionId) };
    return this.run(options, async operation => this.remember(validateExecution(
      await this.control(path, "POST", operation, [200, 202], undefined, undefined, true)),
    executionId, undefined, observation));
  }

  /** Explicit opt-in: preflight 0.1 capabilities, then exactly one 0.2 live POST. */
  async submitLive(request: ExecuteRequest, options: SubmitOptions = {}): Promise<LiveExecution> {
    const body = this.liveRequestBody(request);
    const prefer = waitPreference(options);
    return this.run(options, operation => this.submitLiveInternal(body, prefer, operation));
  }

  async getLiveExecution(id: string, options: RequestOptions = {}): Promise<LiveExecution> {
    idPath(id);
    return this.run(options, operation => this.getLiveInternal(id, operation));
  }

  async cancelLive(id: string, options: RequestOptions = {}): Promise<LiveExecution> {
    const path = `queries/${idPath(id)}/cancel`;
    const observation: LiveObservation = { previous: this.liveExecutions.get(id) };
    return this.run(options, async operation => this.rememberLive(validateLiveExecution(
      await this.control(path, "POST", operation, [200, 202], undefined, undefined, "live")),
    id, undefined, observation));
  }

  /** Provisional batches from one execution. Only the generator's return value
   * certifies success, after both complete IPC and terminal count verification.
   */
  async *stream(request: ExecuteRequest, options: LiveOptions = {}):
    AsyncGenerator<RecordBatch, LiveSuccessfulExecution> {
    const body = this.liveRequestBody(request);
    const prefer = waitPreference(options);
    this.validateLiveOptions(options);
    const operation = new Operation(options.signal, this.timeoutMs);
    let consumption: LiveConsumption | undefined;
    try {
      operation.check();
      consumption = this.reserveLiveConsumption();
      const execution = await this.submitLiveInternal(body, prefer, operation);
      return yield* this.readLiveInternal(execution, options, operation, consumption);
    } finally {
      consumption?.release();
      operation.controller.abort(new OssieError("ABORTED", "The live reader was closed."));
      operation.close();
    }
  }

  /** Attach without POST or preflight. Never wait for success before opening an
   * advertised stream. Early return closes local resources, not the backend job.
   */
  async *readLiveBatches(execution: LiveExecution, options: LiveReadOptions = {}):
    AsyncGenerator<RecordBatch, LiveSuccessfulExecution> {
    validateLiveExecution(execution);
    const known = this.liveExecutions.get(execution.execution_id);
    if (known) this.sameLiveIdentity(known, execution);
    const snapshot = this.rememberLive(known && !terminal(execution) ? known : execution);
    this.validateLiveOptions(options);
    const operation = new Operation(options.signal, this.timeoutMs);
    let consumption: LiveConsumption | undefined;
    try {
      operation.check();
      consumption = this.reserveLiveConsumption(snapshot.execution_id);
      return yield* this.readLiveInternal(snapshot, options, operation, consumption);
    } finally {
      consumption?.release();
      operation.controller.abort(new OssieError("ABORTED", "The live reader was closed."));
      operation.close();
    }
  }

  /** Bounded materialization through exactly the same live streaming workflow. */
  async executeLive(request: ExecuteRequest, options: LiveOptions = {}):
    Promise<{ execution: LiveSuccessfulExecution; table: Table }> {
    const iterator = this.stream(request, options);
    const batches: RecordBatch[] = [];
    try {
      while (true) {
        const next = await iterator.next();
        if (next.done) {
          assert(batches.length > 0, "The Arrow reader did not preserve the result schema.");
          return { execution: next.value, table: new Table(batches[0]!.schema, batches) };
        }
        batches.push(next.value);
      }
    } finally {
      // A completed generator ignores the return argument.
      await iterator.return(undefined as never);
    }
  }

  /** Drops local lifecycle history only; does not cancel/delete server data or
   * clear single-use live consumption fences (bounded to 64 per client).
   */
  forgetExecution(executionId: string): void {
    idPath(executionId);
    this.executions.delete(executionId);
    this.liveExecutions.delete(executionId);
  }

  /** Streams batches from one completed immutable result. Full counts are only
   * verified after exhaustion; early return cancels/releases the response reader.
   */
  async *readBatches(execution: SuccessfulExecution,
    options: RequestOptions & { allowTruncated?: boolean } = {}): AsyncGenerator<RecordBatch> {
    const snapshot = successful(this.remember(validateExecution(execution)));
    this.validateTruncated(options);
    const operation = new Operation(options.signal, this.timeoutMs);
    try {
      yield* this.readInternal(snapshot, options.allowTruncated === true, operation);
    } finally {
      operation.close();
    }
  }

  /** Reads one immutable page without fetching others. Index zero also works
   * for an unpaged result. Verifying a page does not verify the whole result.
   */
  async readPage(execution: SuccessfulExecution, pageIndex: number,
    options: RequestOptions & { allowTruncated?: boolean } = {}): Promise<Table> {
    const snapshot = successful(this.remember(validateExecution(execution)));
    this.validateTruncated(options);
    count(pageIndex);
    const pages = snapshot.result.pages;
    assert(pageIndex < (pages?.length ?? 1), "Result page index is out of range.");
    const page = pages?.[pageIndex];
    this.admitResult(snapshot, options.allowTruncated === true, page);
    return this.run(options, async operation => {
      const batches: RecordBatch[] = [];
      for await (const batch of this.readStream(snapshot, operation, page)) batches.push(batch);
      operation.check();
      assert(batches.length > 0, "The Arrow reader did not preserve the result schema.");
      return new Table(batches[0]!.schema, batches);
    });
  }

  /** Bounded materialization preserving Arrow's exact types and metadata. */
  async execute(request: ExecuteRequest, options: ExecuteOptions = {}):
    Promise<{ execution: SuccessfulExecution; table: Table }> {
    const body = this.requestBody(request);
    const prefer = waitPreference(options);
    this.validateProgressCallback(options);
    this.validateTruncated(options);
    return this.run(options, async operation => {
      const submitted = await this.submitInternal(body, prefer, operation);
      const execution = await this.waitInternal(submitted, options, operation);
      const batches: RecordBatch[] = [];
      for await (const batch of this.readInternal(execution, options.allowTruncated === true, operation)) batches.push(batch);
      operation.check();
      assert(batches.length > 0, "The Arrow reader did not preserve the result schema.");
      let schema = batches[0]!.schema;
      if (execution.result.pages) {
        const metadata = new Map(schema.metadata);
        metadata.set(EXECUTION_METADATA_KEY, JSON.stringify({
          contract_version: execution.contract_version, execution_id: execution.execution_id,
          result_id: execution.result.result_id, data_source_id: execution.data_source_id, model: execution.model,
        }));
        schema = new Schema(schema.fields, metadata);
      }
      return { execution, table: new Table(schema, batches) };
    });
  }

  private requestBody(request: ExecuteRequest): string {
    validateRequest(request);
    try {
      const body = JSON.stringify(request);
      // The serialized shape is the actual contract (including unusual JS inputs).
      validateRequest(JSON.parse(body) as unknown);
      return body;
    } catch (error) {
      if (error instanceof OssieError) throw error;
      throw new ProtocolError("The query request is not serializable.");
    }

  }

  private liveRequestBody(request: ExecuteRequest): string {
    return JSON.stringify({ ...(JSON.parse(this.requestBody(request)) as ExecuteRequest),
      contract_version: LIVE_CONTRACT_VERSION, delivery: "live" });
  }

  private validateLiveOptions(options: LiveReadOptions): void {
    assert(options.onProgress === undefined || typeof options.onProgress === "function",
      "onProgress must be a function.");
    this.validateTruncated(options);
  }

  private validateProgressCallback(options: WaitOptions): void {
    assert(options.onProgress === undefined || typeof options.onProgress === "function",
      "onProgress must be a function.");
  }

  private validateTruncated(options: { allowTruncated?: boolean }): void {
    assert(options.allowTruncated === undefined || typeof options.allowTruncated === "boolean",
      "allowTruncated must be a boolean.");
  }

  private async run<T>(options: RequestOptions, callback: (operation: Operation) => Promise<T>): Promise<T> {
    const operation = new Operation(options.signal, this.timeoutMs);
    try { operation.check(); return await callback(operation); }
    finally { operation.close(); }
  }

  private sameIdentity(previous: Execution, next: Execution): void {
    assert(previous.execution_id === next.execution_id && previous.data_source_id === next.data_source_id
      && equivalent(previous.model, next.model), "Execution identity changed.");
  }

  private remember(execution: Execution, id?: string, source?: string, observation?: Observation): Execution {
    assert(!this.liveExecutions.has(execution.execution_id), "Execution delivery changed.");
    if (id !== undefined) assert(execution.execution_id === id, "Execution identifier mismatch.");
    if (source !== undefined) assert(execution.data_source_id === source, "Execution data-source mismatch.");
    const previous = this.executions.get(execution.execution_id);
    if (observation?.previous) transition(observation.previous, execution);
    if (observation?.fallback) transition(observation.fallback, execution);
    if (previous) this.sameIdentity(previous, execution);
    // Overlapping requests can deliver an older pending observation after a
    // cancellation response. Never let it overwrite a newer observed state.
    if (observation && previous && previous !== observation.previous && !terminal(execution)) {
      this.executions.delete(previous.execution_id);
      this.executions.set(previous.execution_id, previous);
      return structuredClone(previous);
    }
    if (previous) transition(previous, execution);
    // A caller mutating a returned object cannot rewrite the client's history.
    const snapshot = structuredClone(execution);
    this.executions.delete(execution.execution_id);
    this.executions.set(execution.execution_id, structuredClone(snapshot));
    if (this.executions.size > MAX_TRACKED_EXECUTIONS) {
      this.executions.delete(this.executions.keys().next().value!);
    }
    return snapshot;
  }

  private sameLiveIdentity(previous: LiveExecution, next: LiveExecution): void {
    assert(previous.contract_version === next.contract_version && previous.delivery === next.delivery
      && previous.execution_id === next.execution_id && previous.data_source_id === next.data_source_id
      && equivalent(previous.model, next.model), "Execution identity changed.");
    if (previous.stream && next.stream) {
      assert(equivalent(previous.stream, next.stream), "The live stream descriptor changed.");
    }
  }

  private rememberLive(execution: LiveExecution, id?: string, source?: string,
    observation?: LiveObservation): LiveExecution {
    assert(!this.executions.has(execution.execution_id), "Execution delivery changed.");
    if (id !== undefined) assert(execution.execution_id === id, "Execution identifier mismatch.");
    if (source !== undefined) assert(execution.data_source_id === source, "Execution data-source mismatch.");
    const previous = this.liveExecutions.get(execution.execution_id);
    if (observation?.previous) transition(observation.previous, execution);
    if (observation?.fallback) transition(observation.fallback, execution);
    if (previous) this.sameLiveIdentity(previous, execution);
    if (observation && previous && previous !== observation.previous && !terminal(execution)) {
      this.liveExecutions.delete(previous.execution_id);
      this.liveExecutions.set(previous.execution_id, previous);
      return structuredClone(previous);
    }
    if (previous) transition(previous, execution);
    const snapshot = structuredClone(execution);
    this.liveExecutions.delete(execution.execution_id);
    this.liveExecutions.set(execution.execution_id, structuredClone(snapshot));
    if (this.liveExecutions.size > MAX_TRACKED_EXECUTIONS) {
      this.liveExecutions.delete(this.liveExecutions.keys().next().value!);
    }
    return snapshot;
  }

  private async submitLiveInternal(body: string, prefer: string | undefined, operation: Operation): Promise<LiveExecution> {
    const capabilities = validateCapabilities(await this.control("capabilities", "GET", operation, [200]));
    if (!capabilities.features.includes(LIVE_FEATURE)) {
      throw new OssieError("LIVE_UNSUPPORTED", "The endpoint does not advertise live execution support.");
    }
    assert(prefer === undefined || Number(prefer.slice("wait=".length)) <= capabilities.max_wait_seconds,
      "waitSeconds exceeds the endpoint's advertised maximum wait.");
    const request = JSON.parse(body) as ExecuteRequest;
    return this.rememberLive(validateLiveExecution(
      await this.control("queries", "POST", operation, [200, 202], body, prefer, "live")),
    undefined, request.data_source_id);
  }

  private async getLiveInternal(id: string, operation: Operation, fallback?: LiveExecution): Promise<LiveExecution> {
    const observation: LiveObservation = {
      previous: this.liveExecutions.get(id), ...(fallback === undefined ? {} : { fallback }),
    };
    return this.rememberLive(validateLiveExecution(
      await this.control(`queries/${idPath(id)}`, "GET", operation, [200])), id, undefined, observation);
  }

  private reserveLiveConsumption(id?: string): LiveConsumption {
    if (id !== undefined && this.consumedLive.has(id)) {
      throw new OssieError("LIVE_STREAM_CONSUMED", "This client already attempted to consume the live execution.");
    }
    if (this.consumedLive.size + this.reservedLive >= MAX_TRACKED_EXECUTIONS) {
      throw new OssieError("LIVE_STREAM_LIMIT", "This client's bounded live consumption registry is full.");
    }
    this.reservedLive++;
    let reserved = true;
    const release = (): void => {
      if (reserved) {
        reserved = false;
        this.reservedLive--;
      }
    };
    return {
      acquire: executionId => {
        assert(reserved, "Live consumption reservation was already released.");
        if (this.consumedLive.has(executionId)) {
          throw new OssieError("LIVE_STREAM_CONSUMED", "This client already attempted to consume the live execution.");
        }
        this.consumedLive.add(executionId);
        release();
      },
      release,
    };
  }

  private async *readLiveInternal(initial: LiveExecution, options: LiveReadOptions, operation: Operation,
    consumption: LiveConsumption):
    AsyncGenerator<RecordBatch, LiveSuccessfulExecution> {
    let execution = initial;
    while (true) {
      operation.check();
      liveFailure(execution);
      if (options.onProgress) await operation.race(() => options.onProgress!(structuredClone(execution)));
      if (execution.stream) break;
      await operation.sleep(this.pollIntervalMs);
      execution = await this.getLiveInternal(execution.execution_id, operation, execution);
    }
    const opened = execution as LiveExecution & { stream: LiveStream };
    const stream = opened.stream;
    if (Date.parse(stream.expires_at) <= Date.now()) {
      throw new OssieError("RESULT_EXPIRED", "The live stream start window has expired.");
    }
    const target = this.resultUrl(stream.url);
    consumption.acquire(execution.execution_id);
    const dataOperation = new Operation(operation.signal, this.timeoutMs);
    const monitorOperation = new Operation(operation.signal, this.timeoutMs);
    let monitorFailed = false;
    let monitorError: unknown;
    const monitor = (async (): Promise<LiveSuccessfulExecution> => {
      try {
        let current = opened as LiveExecution;
        while (!terminal(current)) {
          await monitorOperation.sleep(this.pollIntervalMs);
          current = await this.getLiveInternal(current.execution_id, monitorOperation, current);
          liveFailure(current);
          if (options.onProgress) await monitorOperation.race(() => options.onProgress!(structuredClone(current)));
        }
        assert(current.state === "succeeded", "Live execution did not succeed.");
        return current;
      } catch (error) {
        if (!monitorOperation.signal.aborted) {
          monitorFailed = true;
          monitorError = error;
          dataOperation.controller.abort(error);
        }
        throw error;
      } finally {
        monitorOperation.close();
      }
    })();
    // Monitoring must remain active even while the consumer is paused at yield.
    // Attach a rejection handler immediately; consumption may end early.
    void monitor.catch(() => {});
    try {
      const stats = { rows: 0, bytes: 0 };
      try {
        const response = await this.fetchResponse(target, "GET", dataOperation, ARROW_MEDIA_TYPE,
          undefined, undefined, Date.parse(stream.expires_at));
        try {
          if (response.status >= 400) await this.problem(response, dataOperation);
          assert(response.status === 200 && mediaType(response) === ARROW_MEDIA_TYPE,
            "Expected a successful Arrow IPC stream response.");
          yield* arrowBatches(response, opened, dataOperation, this.maxResultBytes,
            this.maxResultRows, undefined, this.maxResultBytes, stats);
        } finally {
          cancelBody(response);
        }
      } catch (error) {
        operation.check();
        if (monitorFailed) throw monitorError;
        monitorOperation.controller.abort(new OssieError("ABORTED", "The live data reader failed."));
        await monitor.catch(() => {});
        const known = this.liveExecutions.get(execution.execution_id);
        if (known) liveFailure(known);
        // One bounded status inspection can preserve a published engine failure.
        // Neither success nor unavailable status can repair invalid/missing IPC.
        let final: LiveExecution | undefined;
        try { final = await this.getLiveInternal(execution.execution_id, operation, execution); }
        catch { /* Keep the precise original data-plane failure. */ }
        if (final) liveFailure(final);
        throw error;
      }
      const completed = await operation.race(() => monitor);
      operation.check();
      assert(completed.completion.result_id === stream.result_id, "Live completion result identity mismatch.");
      assert(completed.completion.row_count === stats.rows, "Live Arrow row count mismatch.");
      assert(completed.completion.byte_count === stats.bytes, "Live decoded IPC byte count mismatch.");
      if (completed.completion.completeness === "truncated" && options.allowTruncated !== true) {
        throw new OssieError("TRUNCATED_RESULT", "The result is truncated; explicit acceptance is required.");
      }
      return completed;
    } finally {
      const closed = new OssieError("ABORTED", "The live reader was closed.");
      dataOperation.controller.abort(closed);
      monitorOperation.controller.abort(closed);
      dataOperation.close();
      monitorOperation.close();
    }
  }

  private async submitInternal(body: string, prefer: string | undefined, operation: Operation): Promise<Execution> {
    const request = JSON.parse(body) as ExecuteRequest;
    const result = await this.control("queries", "POST", operation, [200, 202], body, prefer, true);
    const execution = validateExecution(result);
    return this.remember(execution, undefined, request.data_source_id);
  }

  private async getInternal(id: string, operation: Operation, fallback?: Execution): Promise<Execution> {
    const observation: Observation = {
      previous: this.executions.get(id), ...(fallback === undefined ? {} : { fallback }),
    };
    return this.remember(validateExecution(
      await this.control(`queries/${idPath(id)}`, "GET", operation, [200])), id, undefined, observation);
  }

  private async waitInternal(initial: Execution, options: WaitOptions, operation: Operation): Promise<SuccessfulExecution> {
    let execution = initial;
    while (true) {
      operation.check();
      if (options.onProgress) {
        await operation.race(() => options.onProgress!(structuredClone(execution)));
      }
      if (terminal(execution)) return successful(execution);
      await operation.sleep(this.pollIntervalMs);
      execution = await this.getInternal(execution.execution_id, operation, execution);
    }
  }

  private resultUrl(value: string): URL {
    const result = url(value);
    if (result.origin === this.base.origin) {
      assert(result.protocol === this.base.protocol, "Unsafe result URL.");
    } else {
      assert(result.protocol === "https:" && this.origins.has(result.origin),
        "The result origin is not explicitly allowed.");
    }
    return result;
  }

  private async *readInternal(execution: SuccessfulExecution, allowTruncated: boolean,
    operation: Operation): AsyncGenerator<RecordBatch> {
    operation.check();
    this.admitResult(execution, allowTruncated);
    let schema: unknown;
    let remainingBytes = this.maxResultBytes;
    for (const page of execution.result.pages ?? [undefined]) {
      for await (const batch of this.readStream(execution, operation, page, remainingBytes)) {
        const current = schemaIdentity(batch.schema);
        assert(schema === undefined || equivalent(schema, current), "Arrow schema or metadata changed between result pages.");
        schema ??= structuredClone(current);
        yield batch;
      }
      remainingBytes -= (page ?? execution.result).byte_count;
    }
  }

  private admitResult(execution: SuccessfulExecution, allowTruncated: boolean, page?: ResultPage): void {
    const manifest = execution.result;
    if (manifest.completeness === "truncated" && !allowTruncated) {
      throw new OssieError("TRUNCATED_RESULT", "The result is truncated; explicit acceptance is required.");
    }
    const counts = page ?? manifest;
    if (counts.byte_count > this.maxResultBytes || counts.row_count > this.maxResultRows) {
      throw new OssieError("RESULT_LIMIT_EXCEEDED", "The result exceeds configured materialization limits.");
    }
    if (Date.parse(manifest.expires_at) <= Date.now()) {
      throw new OssieError("RESULT_EXPIRED", "The materialized result has expired.");
    }
  }

  private async *readStream(execution: SuccessfulExecution, operation: Operation,
    page?: ResultPage, remainingBytes = this.maxResultBytes): AsyncGenerator<RecordBatch> {
    operation.check();
    const target = page?.url ?? execution.result.url;
    assert(target !== undefined, "The result has no stream URL.");
    const response = await this.fetchResponse(this.resultUrl(target), "GET", operation, ARROW_MEDIA_TYPE,
      undefined, undefined, Date.parse(execution.result.expires_at));
    try {
      if (response.status >= 400) await this.problem(response, operation);
      assert(response.status === 200 && mediaType(response) === ARROW_MEDIA_TYPE,
        "Expected a successful Arrow IPC stream response.");
      yield* arrowBatches(response, execution, operation, this.maxResultBytes, this.maxResultRows, page, remainingBytes);
    } finally {
      cancelBody(response);
    }
  }

  private async control(path: string, method: string, operation: Operation, statuses: readonly number[],
    body?: string, prefer?: string, lifecyclePost: boolean | "live" = false): Promise<unknown> {
    const response = await this.fetchResponse(new URL(path, this.base), method, operation, "application/json", body, prefer);
    try {
      if (response.status >= 400) await this.problem(response, operation);
      assert(statuses.includes(response.status), "Unexpected HTTP success status.");
      assert(mediaType(response) === "application/json", "Expected an application/json response.");
      const result = await readJson(response, operation);
      if (lifecyclePost) {
        const execution = lifecyclePost === "live" ? validateLiveExecution(result) : validateExecution(result);
        assert((response.status === 200) === terminal(execution), "HTTP status does not match execution state.");
      }
      return result;
    } finally {
      cancelBody(response);
    }
  }

  private async problem(response: Response, operation: Operation): Promise<never> {
    assert(mediaType(response) === "application/problem+json", "Expected an application/problem+json error.");
    throw new HttpProblemError(validateProblem(await readJson(response, operation), response.status));
  }

  private async fetchResponse(target: URL, method: string, operation: Operation, accept: string,
    body?: string, prefer?: string, expiresAt?: number): Promise<Response> {
    const headers = new Headers({ Accept: accept });
    if (body !== undefined) headers.set("Content-Type", "application/json");
    if (prefer !== undefined) headers.set("Prefer", prefer);
    if (target.origin === this.base.origin && this.getAccessToken) {
      let token: string;
      try {
        token = await operation.race(() => this.getAccessToken!());
        assert(typeof token === "string" && token.trim().length > 0 && !/[\r\n]/.test(token),
          "The access-token callback returned an invalid token.");
        headers.set("Authorization", `Bearer ${token}`);
      } catch {
        operation.check();
        throw new OssieError("AUTHENTICATION_ERROR", "An access token could not be obtained.");
      }
    }
    const expired = new OssieError("RESULT_EXPIRED", "The materialized result has expired.");
    let response: Response;
    try {
      response = await operation.race(async () => {
        if (expiresAt !== undefined && expiresAt <= Date.now()) throw expired;
        const received = await this.fetcher(target.href, {
          method, headers, ...(body === undefined ? {} : { body }), signal: operation.signal,
          redirect: "error", credentials: "omit", referrerPolicy: "no-referrer", cache: "no-store",
        });
        // A custom Fetch implementation may ignore AbortSignal and resolve late.
        if (operation.signal.aborted) {
          cancelBody(received);
          operation.check();
        }
        return received;
      });
    } catch (error) {
      if (error === expired) throw expired;
      operation.check();
      throw new OssieError("TRANSPORT_ERROR", "The HTTP request could not be completed.");
    }
    if (operation.signal.aborted) {
      cancelBody(response);
      operation.check();
    }
    if (response.redirected) {
      cancelBody(response);
      throw new ProtocolError("HTTP redirects are forbidden.");
    }
    return response;
  }
}
