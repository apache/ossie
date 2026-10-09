<!-- Licensed to the Apache Software Foundation (ASF) under one or more contributor license agreements. See the NOTICE
  file distributed with this work for additional information regarding copyright ownership. The ASF licenses this file
  to you under the Apache License, Version 2.0 (the "License"); you may not use this file except in compliance with the
  License. You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on an
  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
  specific language governing permissions and limitations under the License. -->

# Apache Ossie Holistics converter

Converts between a [Holistics](https://docs.holistics.io) AML dataset and an
[Apache Ossie](https://github.com/apache/ossie) semantic model.

- `to-ossie` reads the JSON `holistics aml compile` produces, and writes one Ossie semantic model.
- `to-aml` reads one Ossie semantic model, and writes AML source files.

One AML `Dataset` becomes one Ossie semantic model.

| AML | Ossie |
|---|---|
| the `Dataset` | the document root |
| each model | a `datasets[]` entry |
| each model `dimension` | a `fields[]` entry on that dataset |
| each model `measure` | a `metrics[]` entry named `<dataset>__<measure>` |
| each dataset-level `dimension` | a `fields[]` entry on the model it names |
| each dataset-level `metric` | a `metrics[]` entry |
| each `relationship` | a `relationships[]` entry |

[Mapping](docs/mapping.md) covers data types, relationship kinds and the rest.

AQL travels under the `HOLISTICS_AQL` dialect, registered upstream the way `THOUGHTSPOT` was in
[#351](https://github.com/apache/ossie/pull/351). The converter does not translate AQL into SQL, and does not
rewrite warehouse SQL from one dialect into another. [Limitations](docs/limitations.md) lists what that costs.

## The two SQLs

Both sides write SQL, and the two are not the same text.

- **Holistics SQL** is an AML `@sql` body. It interpolates through `{{ ... }}`, and it resolves inside the one
  model it sits in.
- **Ossie SQL** is the `expression` of a `dialects[]` entry. It writes a reference as `dataset.field`, and its
  dialect tag names the warehouse it was written for.

**Warehouse SQL** is what Holistics generates and sends to the database. This converter reads and writes the
first two, and Holistics generates the third.

## Installation

```bash
pip install -e .    # from a checkout of this directory
```

Python 3.10+. The runtime dependencies are `PyYAML` and `sqlglot`. `sqlglot` is here for `to-aml`, which uses
it to find the column references in an Ossie SQL expression. [Ossie to AML](docs/reverse.md#expressions) says
why that is needed.

## Usage

AML to Ossie. `to-ossie` takes compiled JSON, so run the Holistics CLI first:

```bash
holistics aml compile ecommerce.dataset.aml -r . -o build/
ossie-holistics to-ossie build/ecommerce.dataset.aml.json -o ecommerce.yaml --sql-dialect BIGQUERY
```

Ossie to AML. `holistics aml validate` checks the result and needs no account:

```bash
ossie-holistics to-aml ecommerce.yaml -o aml/ --sql-dialect BIGQUERY --data-source-name my_warehouse
holistics aml validate aml/ecommerce.dataset.aml -r aml/
```

Options:

- `-o`, required in both directions. A file for `to-ossie`, a directory for `to-aml`.
- `--sql-dialect DIALECT`, required in both directions. The choices are `ANSI_SQL`, `OSSIE_SQL_2026`,
  `BIGQUERY`, `DATABRICKS` and `SNOWFLAKE`, the Ossie dialects sqlglot can parse.
  - On `to-ossie` it tags every `@sql` body. AML does not record which warehouse a body was written for.
  - On `to-aml` it names the warehouse the Holistics connection reads. An expression already in that dialect
    is written as it stands. One in `ANSI_SQL` or `OSSIE_SQL_2026` is rendered into it, with a WARNING
    showing both texts. An expression offering neither gets an ERROR, and its body is written unchanged.
- `--data-source-name NAME`, on `to-aml`. The Holistics connection the models read from. AML requires it on a
  dataset and Ossie has no field for it. Required when the Ossie document carries no `HOLISTICS` entry in
  `custom_extensions`.
- `--issues FILE`. Where to write the issue log, a YAML sequence. Defaults to stderr. Never mixed into the
  document output. A message runs to a paragraph, so YAML folds it rather than putting it on one line.
- `--force`. Overwrite existing output files. Without it, an existing target stops the run before anything is
  written.

The process exits `1` when the issue log holds an ERROR-severity issue, and `0` otherwise.

`to-aml` writes one dataset file plus one model file per Ossie dataset. An Ossie dataset named
`reporting__calendar` goes to `modules/reporting/calendar.model.aml`:

```
aml/
  ecommerce.dataset.aml
  ecommerce_orders.model.aml
  modules/reporting/calendar.model.aml
```

## Documents

| Document | What it covers |
|---|---|
| [The fixture](docs/fixture.md) | The AML source, how to recompile it, and the shape of the compiled JSON |
| [Mapping](docs/mapping.md) | Which AML construct becomes which Ossie construct, including relationships and data types |
| [Expression translation](docs/expressions.md) | What the converter emits for each definition body, and what it leaves untranslated |
| [Ossie to AML](docs/reverse.md) | The generated file layout, and what AML cannot express |
| [Ossie SQL to AQL](docs/aql.md) | How a cross-dataset metric's Ossie SQL becomes AQL, and what is not translated |
| [Measures](docs/measures.md) | How `aggregation_type` and a definition body compose into one Ossie metric expression |
| [The HOLISTICS payload](docs/vendor-payload.md) | Everything carried in `custom_extensions` |
| [Limitations](docs/limitations.md) | The coverage matrix, and what is known not to work |

## Fixtures

`tests/fixtures/ecommerce/` holds the AML source of the `ecommerce` dataset, written for this converter, and
that source compiled by the Holistics CLI. The `expected*` files are what the converter produces from it in
both directions.

`tests/fixtures/tpcds/expected_aml/` is `examples/tpcds_semantic_model.yaml` converted to AML. That document
carries no `HOLISTICS` stash, so every AML property with no Ossie field is derived or supplied rather than
read back.

`tests/fixtures/aql/` is written for the Ossie SQL to AQL translation. Every metric in it spans two datasets,
which is what makes AML take an `@aql` body, and each covers a different path: a function AQL has, a function
it does not, a column no dataset declares, a field with no `datatype`, and two that are dropped with an
ERROR.

`tests/fixtures/metric_refs/` is written for a metric that names another metric. AML gives a metric two homes
that take different bodies, so the home the named metric landed in decides where the naming one can go. Each
metric covers one way that lands.

## Development

```bash
uv run pytest
python3 tools/check_links.py       # every cross-document link and anchor resolves
python3 tools/update_snapshots.py  # rewrite the snapshots after a deliberate change
```

Most tests compare a committed snapshot against what the converter produces now. The rest check the output
against something other than itself.

These run the Holistics CLI:

- the generated AML through `holistics aml validate`, for all three fixtures
- the committed compiled JSON against what `holistics aml compile` produces from the committed AML source
- a round trip: compile the generated AML, convert that back to Ossie, and compare it with the document
  `to-aml` started from. For `ecommerce` the two are byte-identical.

These need nothing beyond the package:

- the converted document against `core-spec/ossie-schema.json`
- every qualified `<dataset>.<name>` reference against the fields the document declares
- the issue log read back from its folded YAML, which a fold inside a URL would corrupt
- each path through the Ossie SQL to AQL translation, pinned by metric name so a snapshot update cannot quietly
  stop covering one
- the issue-code table in `docs/limitations.md` against the codes the source raises, in both directions. See
  `tests/test_issue_codes.py`
- a malformed document reaching `to-aml`, which must report rather than raise. See
  `tests/test_malformed_input.py`.

CI installs the CLI, so the first group runs there too:

```bash
npm install --no-package-lock --prefix /tmp/holistics \
  @holistics/cli-core@2.3.32 @commander-js/extra-typings@14.0.0
export PATH="/tmp/holistics/node_modules/.bin:$PATH"
```

`@holistics/cli-core` is the CLI's logic as one bundled JavaScript file, so it needs only Node. The
[native CLI](https://docs.holistics.io/docs/cli) works the same way for these tests. With neither installed,
that first group skips and the rest of the suite still runs.
