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

import type { Execution, LiveExecution, ProblemDetails, QueryError } from "./types.js";

/** Base error. Messages deliberately exclude URLs, credentials and server bodies.
 * Structured server details are untrusted data and must not be logged blindly.
 */
export class OssieError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
    this.name = new.target.name;
  }
}

export class ProtocolError extends OssieError {
  constructor(message = "The server returned an invalid execution protocol response.") {
    super("PROTOCOL_ERROR", message);
  }
}

export class QueryExecutionError extends OssieError {
  readonly error: QueryError;
  constructor(public readonly execution: Extract<Execution, { state: "failed" }>) {
    super(execution.error.code, "The query execution failed.");
    this.error = execution.error;
  }
}

export class ExecutionCancelledError extends OssieError {
  constructor(public readonly execution: Extract<Execution, { state: "cancelled" }>) {
    super("EXECUTION_CANCELLED", "The query execution was cancelled.");
  }
}

export class LiveQueryExecutionError extends OssieError {
  readonly error: QueryError;
  constructor(public readonly execution: Extract<LiveExecution, { state: "failed" }>) {
    super(execution.error.code, "The live query execution failed.");
    this.error = execution.error;
  }
}

export class LiveExecutionCancelledError extends OssieError {
  constructor(public readonly execution: Extract<LiveExecution, { state: "cancelled" }>) {
    super("EXECUTION_CANCELLED", "The live query execution was cancelled.");
  }
}

export class HttpProblemError extends OssieError {
  readonly status: number;
  constructor(public readonly problem: ProblemDetails) {
    super(problem.error.code, "The server rejected the HTTP request.");
    this.status = problem.status;
  }
}

export class TimeoutError extends OssieError {
  constructor() {
    super("TIMEOUT", "The client operation exceeded its deadline.");
  }
}
