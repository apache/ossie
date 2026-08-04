/*
 * Licensed to the Apache Software Foundation (ASF) under one
 * or more contributor license agreements.  See the NOTICE file
 * distributed with this work for additional information
 * regarding copyright ownership.  The ASF licenses this file
 * to you under the Apache License, Version 2.0 (the
 * "License"); you may not use this file except in compliance
 * with the License.  You may obtain a copy of the License at
 *
 *   http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 */

/*
 * Does Cube accept this model? -- the one question a YAML round trip cannot answer.
 *
 *   OSSIE_CUBE_REPO=~/src/cube node tools/cube_compile.js model/cubes/*.yml
 *
 * Prints `COMPILED OK`, or Cube's own errors, and exits 1 on failure. Wanted because
 * Cube compiles every string in a model as a Python f-string, resolves every member
 * reference, and enforces one member namespace per cube -- so a model can round-trip
 * through Ossie byte-for-byte and still be one Cube refuses to load. Three defects of
 * exactly that kind were found by running this.
 *
 * Needs a built Cube checkout (`yarn build` in the monorepo, or an installed
 * node_modules with dist/). `tests/test_cube_compiles.py` skips when there isn't one,
 * so this is a local and release-time gate rather than a CI one.
 */

const fs = require('fs');
const path = require('path');

const repo = process.env.OSSIE_CUBE_REPO;
if (!repo) {
  console.log('SKIP OSSIE_CUBE_REPO is not set (point it at a built Cube checkout)');
  process.exit(2);
}

const compilerDist = path.join(
  repo, 'packages/cubejs-schema-compiler/dist/src');
if (!fs.existsSync(compilerDist)) {
  console.log(`SKIP no built schema compiler at ${compilerDist} (run yarn build)`);
  process.exit(2);
}

// The monorepo's packages are built independently, so a schema-compiler build can ask
// `getEnv` for a variable an older cubejs-backend-shared build does not know, which
// throws. Unknown keys fall back to undefined rather than taking the run down: this
// script is asking about *model* validity, not about environment configuration.
try {
  const shared = require(
    path.join(repo, 'packages/cubejs-backend-shared/dist/src/env'));
  const realGetEnv = shared.getEnv;
  shared.getEnv = (key, ...rest) => {
    try {
      return realGetEnv(key, ...rest);
    } catch (e) {
      return undefined;
    }
  };
} catch (e) {
  // Older or differently-laid-out checkout: carry on and let compile() report.
}

const { prepareCompiler } = require(path.join(compilerDist, 'compiler/PrepareCompiler'));

const files = process.argv.slice(2);
if (!files.length) {
  console.log('usage: cube_compile.js <model file> [...]');
  process.exit(2);
}

// Cube keys models by file name, and its loader does not care about directories, so a
// flat list is enough -- `cubes/orders.yml` and `views/sales.yml` compile together.
const dataSchemaFiles = files.map((p) => ({
  fileName: path.basename(p),
  content: fs.readFileSync(p, 'utf8'),
}));

const { compiler } = prepareCompiler(
  { localPath: () => path.dirname(files[0]), dataSchemaFiles: () => Promise.resolve(dataSchemaFiles) },
  { adapter: 'postgres' });

compiler.compile()
  .then(() => console.log('COMPILED OK'))
  .catch((e) => {
    // Cube's compile errors are the useful part; the stack is noise here.
    console.log(`COMPILE FAILED\n${String((e && e.message) || e)}`);
    process.exit(1);
  });
