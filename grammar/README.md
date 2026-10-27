<!--

Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# Ossie expression grammar

`OssieExpression.g4` is an [ANTLR 4](https://www.antlr.org/) grammar for the
`OSSIE_SQL_2026` expression language described in
[`core-spec/expression_language.md`](../core-spec/expression_language.md). It covers
expressions only (no `SELECT`, `FROM`, `WHERE`, subqueries). Function names and arities
are not checked: every function parses as `name(args)`, which functions exist is a
semantic question. Constructs with their own syntax (`CASE`, `CAST`, `EXTRACT`,
`POSITION`, `FILTER`, `WITHIN GROUP`, `OVER`, typed literals) have their own rules.

The entry rule is `expression`. The generated parser is not committed.

## Where the grammar goes beyond the spec text

The precedence levels follow the list in the spec's "Operator Precedence" section. Two points
are not decided there:

- `||` is accepted, at the level of `+` and `-`. The spec's portable form is `CONCAT(a, b)`;
  `a || b` only appears as a dialect form.
- Comparisons do not chain: `a = b = c` needs parentheses.

## Tests

The tests generate the Python parser with ANTLR 4.13.2 and need Java 11+. The first run downloads
the ANTLR jar from Maven Central into `dev/lib/` (git-ignored, like Apache RAT) and checks it
against a pinned SHA-256; ANTLR is BSD-3-Clause licensed.

```bash
uv run --with pytest --with pyyaml --with antlr4-python3-runtime==4.13.2 \
  -m pytest grammar/tests/
```

`tests/fixtures/*.txt` hold one expression per line (`#` starts a comment):
`valid.txt` must parse, `invalid.txt` must be rejected. `test_example_models_parse` also
parses every `ANSI_SQL` / `OSSIE_SQL_2026` expression and top-level `sql` in `examples/*.yaml`.

Python style is checked with `uv run --with ruff ruff check grammar/ && uv run --with ruff ruff format --check grammar/`
(settings in `ruff.toml`).
