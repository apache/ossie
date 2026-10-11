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

import { OssieError, ProtocolError, TimeoutError } from "./errors.js";
import { assert } from "./validation.js";

/** One deadline spans every phase of a public operation, including user callbacks. */
export class Operation {
  readonly controller = new AbortController();
  readonly signal = this.controller.signal;
  private readonly timer: ReturnType<typeof setTimeout>;
  private readonly deadline: number;
  private readonly parentAbort = (): void => {
    this.controller.abort(new OssieError("ABORTED", "The client operation was aborted."));
  };

  constructor(private readonly parent: AbortSignal | undefined, timeoutMs: number) {
    this.deadline = performance.now() + timeoutMs;
    this.timer = setTimeout(() => this.controller.abort(new TimeoutError()), timeoutMs);
    if (parent?.aborted) this.parentAbort();
    else parent?.addEventListener("abort", this.parentAbort, { once: true });
  }

  check(): void {
    if (!this.signal.aborted && performance.now() >= this.deadline) {
      this.controller.abort(new TimeoutError());
    }
    if (this.signal.aborted) throw this.signal.reason as OssieError;
  }

  async race<T>(operation: () => T | PromiseLike<T>): Promise<T> {
    this.check();
    return new Promise<T>((resolve, reject) => {
      const abort = (): void => reject(this.signal.reason);
      this.signal.addEventListener("abort", abort, { once: true });
      Promise.resolve().then(() => {
        this.check();
        return operation();
      }).then(resolve, reject).finally(() => this.signal.removeEventListener("abort", abort));
    });
  }

  async sleep(milliseconds: number): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await this.race(() => new Promise<void>(resolve => { timer = setTimeout(resolve, milliseconds); }));
    } finally {
      clearTimeout(timer);
    }
  }

  close(): void {
    clearTimeout(this.timer);
    this.parent?.removeEventListener("abort", this.parentAbort);
  }
}

export function cancelBody(response: Response): void {
  if (response.body && !response.body.locked) void response.body.cancel().catch(() => {});
}

/** Reads decoded fetch bytes, never Content-Length (which can describe compressed HTTP data). */
export class BodySource {
  private readonly reader: ReadableStreamDefaultReader<Uint8Array>;
  private readonly abort = (): void => { void this.reader.cancel().catch(() => {}); };
  private closed = false;
  private pending: Uint8Array = new Uint8Array(0);
  private offset = 0;
  bytes = 0;

  constructor(response: Response, private readonly operation: Operation, private readonly maxBytes: number) {
    assert(response.body, "The HTTP response has no readable body.");
    this.reader = response.body.getReader();
    operation.signal.addEventListener("abort", this.abort, { once: true });
    if (operation.signal.aborted) this.abort();
  }

  async chunk(): Promise<Uint8Array | undefined> {
    let result: ReadableStreamReadResult<Uint8Array>;
    try {
      result = await this.operation.race(() => this.reader.read());
    } catch {
      this.operation.check();
      throw new OssieError("TRANSPORT_ERROR", "The HTTP response body could not be read.");
    }
    this.operation.check();
    if (result.done) return undefined;
    assert(result.value instanceof Uint8Array, "The transport did not provide bytes.");
    this.bytes += result.value.byteLength;
    if (!Number.isSafeInteger(this.bytes) || this.bytes > this.maxBytes) {
      throw new OssieError("RESULT_LIMIT_EXCEEDED", "The response exceeded the configured byte limit.");
    }
    return result.value;
  }

  async exact(length: number, allowEof = false): Promise<Uint8Array | undefined> {
    assert(Number.isSafeInteger(length) && length >= 0 && length <= this.maxBytes,
      "Invalid IPC buffer length.");
    const output = new Uint8Array(length);
    let filled = 0;
    while (filled < length) {
      if (this.offset === this.pending.byteLength) {
        const chunk = await this.chunk();
        if (chunk === undefined) {
          if (allowEof && filled === 0) return undefined;
          throw new ProtocolError("The IPC stream ended inside a message.");
        }
        this.pending = chunk;
        this.offset = 0;
        if (chunk.byteLength === 0) continue;
      }
      const amount = Math.min(length - filled, this.pending.byteLength - this.offset);
      output.set(this.pending.subarray(this.offset, this.offset + amount), filled);
      this.offset += amount;
      filled += amount;
    }
    return output;
  }

  async end(): Promise<void> {
    assert(this.offset === this.pending.byteLength, "Unexpected bytes after the IPC stream.");
    let chunk: Uint8Array | undefined;
    while ((chunk = await this.chunk()) !== undefined) {
      assert(chunk.byteLength === 0, "Unexpected bytes after the IPC stream.");
    }
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.operation.signal.removeEventListener("abort", this.abort);
    void this.reader.cancel().catch(() => {});
    this.reader.releaseLock();
  }
}

export function mediaType(response: Response): string {
  return response.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() ?? "";
}

export async function readJson(response: Response, operation: Operation): Promise<unknown> {
  const source = new BodySource(response, operation, 1024 * 1024);
  try {
    const chunks: Uint8Array[] = [];
    let chunk: Uint8Array | undefined;
    while ((chunk = await source.chunk()) !== undefined) if (chunk.byteLength) chunks.push(chunk);
    const bytes = new Uint8Array(source.bytes);
    let offset = 0;
    for (const part of chunks) { bytes.set(part, offset); offset += part.byteLength; }
    try {
      return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) as unknown;
    } catch {
      throw new ProtocolError("The server returned invalid JSON.");
    }
  } finally {
    source.close();
  }
}
