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
 * node_modules with dist/). The tests behind `cube_gate` skip when there isn't one, so
 * this is a local and release-time gate rather than a CI one.
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

/* The deepest directory containing every input, which is what the relative keys are
 * relative to. One file has no shared prefix to find, so its own directory is the root. */
function commonRoot(paths) {
  const dirs = paths.map((p) => path.dirname(path.resolve(p)).split(path.sep));
  let shared = dirs[0];
  for (const parts of dirs.slice(1)) {
    let i = 0;
    while (i < shared.length && i < parts.length && shared[i] === parts[i]) i += 1;
    shared = shared.slice(0, i);
  }
  return shared.join(path.sep) || path.sep;
}

// Cube keys model files by their path *relative to the model root* -- its own
// FileRepository walks the tree and joins the directory back on, so a cube at
// `cubes/orders.yml` is keyed by exactly that. Using the basename instead let two files
// of one name collide, and the loser was dropped without a word: `cubes/orders.yml` plus
// an invalid `views/orders.yml` reported COMPILED OK, while the same two files under
// distinct names failed as they should. A gate that quietly drops half its input is
// worse than no gate, and the Databricks fixture emits that exact pair.
const root = commonRoot(files);
const dataSchemaFiles = files.map((p) => ({
  fileName: path.relative(root, path.resolve(p)),
  content: fs.readFileSync(p, 'utf8'),
}));

// Nothing may share a key, or Cube silently sees fewer files than we passed.
const seen = new Map();
for (const f of dataSchemaFiles) {
  if (seen.has(f.fileName)) {
    console.log(`COMPILE FAILED\nduplicate model key '${f.fileName}'`);
    process.exit(1);
  }
  seen.set(f.fileName, true);
}

const { compiler } = prepareCompiler(
  { localPath: () => root, dataSchemaFiles: () => Promise.resolve(dataSchemaFiles) },
  { adapter: 'postgres' });

compiler.compile()
  .then(() => console.log('COMPILED OK'))
  .catch((e) => {
    // Cube's compile errors are the useful part; the stack is noise here.
    console.log(`COMPILE FAILED\n${String((e && e.message) || e)}`);
    process.exit(1);
  });
