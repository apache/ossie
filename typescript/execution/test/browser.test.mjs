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
import { fileURLToPath } from "node:url";
import test from "node:test";
import { build } from "esbuild";

test("public package bundles for a browser without Node builtins, externals or polyfills", async () => {
  const packageDirectory = fileURLToPath(new URL("../", import.meta.url));
  const result = await build({
    absWorkingDir: packageDirectory,
    stdin: {
      contents: "import * as Ossie from './dist/index.js'; globalThis.Ossie = Ossie;",
      resolveDir: packageDirectory, sourcefile: "browser-validation.mjs", loader: "js",
    },
    bundle: true, platform: "browser", format: "iife", target: "es2022",
    write: false, metafile: true, logLevel: "silent",
  });
  assert.equal(result.outputFiles.length, 1);
  for (const output of Object.values(result.metafile.outputs)) assert.deepEqual(output.imports, []);
  assert.ok(result.outputFiles[0].text.includes("OssieClient"));
  assert.ok(!result.warnings.length);
});
