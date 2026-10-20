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

## How to test this with no WisdomAI UI, no WisdomAI login, and no Snowflake account or warehouse

You do not need the WisdomAI UI, a WisdomAI login, a Snowflake account, or a Snowflake warehouse. These commands do not sign in to WisdomAI or Snowflake. The inputs are the files in `tests/fixtures/finance/`.

Run this from `converters/wisdom-semantic-view`. You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ossie-wisdom-semantic-view to-wisdom \
  tests/fixtures/finance/snowflake/cfo_cockpit.before.yaml \
  --enrich tests/fixtures/finance/wisdom/enrichment.yaml \
  -o /tmp/finance-domain.json
uv run ossie-wisdom-semantic-view to-semantic-view \
  /tmp/finance-domain.json \
  -o /tmp/finance-after.yaml
# The fixture file starts with the ASF license header. The command writes the YAML only.
awk 'p{print} /^# under the License\./{p=1}' \
  tests/fixtures/finance/snowflake/cfo_cockpit.before.yaml \
  | tail -n +2 > /tmp/finance-before.yaml
diff -u /tmp/finance-before.yaml /tmp/finance-after.yaml
```

Passing looks like this. `uv run pytest` exits 0. Both `ossie-wisdom-semantic-view` commands exit 0. `diff -u` prints a unified diff that only adds the `revenue` metric, the synonyms `revenue`, `net revenue`, and `board revenue`, and the February fiscal-year `sql_generation` line. `diff` exits 1 because those lines are new. That is the result you want. The February line is demo copy.

# Apache Ossie Wisdom semantic-view converter

Offline conversion of one Snowflake semantic view through an Apache Ossie document
into a WisdomAI domain export (format `1.0`), and back. No Snowflake account and no
Wisdom tenant.

This is not [`converters/snowflake`](../snowflake). That package only writes Cortex
Analyst YAML and cannot read a semantic view. This is not [`converters/wisdom`](../wisdom).
That package converts domain-export JSON both ways and drops synonyms and field or
metric `ai_context`. Calling either one drops the revenue metric's synonyms and the
`sql_generation` instruction this fixture carries.

Scope is the finance model under `tests/fixtures/finance/` and the keys that model
uses. A key outside that list, or an expression other than a bare column name or
`SUM(bookings.amount)`, exits `1` and prints the key or expression. Identifier case
is preserved.

## Installation

```bash
# from a checkout of this directory:
pip install -e .
```

The only runtime dependency is `PyYAML`. Python 3.11+.

## Usage

```bash
ossie-wisdom-semantic-view to-wisdom <view.yaml> -o <domain.json> [--enrich <enrichment.yaml>]
ossie-wisdom-semantic-view to-semantic-view <domain.json> -o <view.yaml>
```

`to-wisdom` reads a semantic view, builds one Ossie document (`version` `0.2.0.dev0`),
and writes the domain export. `--enrich` is the only step that adds a name the view
did not already have. On this fixture it adds the `revenue` metric, three synonyms,
and the February fiscal-year instruction.

`to-semantic-view` reads the domain export, builds the Ossie document, and writes
the semantic view. Synonyms come back as the metric `synonyms` array. The knowledge
item named `Board revenue` comes back as `module_custom_instructions.sql_generation`.
`synonym_sets` stays empty; a non-empty `items_json` exits `1`.

## Mapping

Semantic view → Ossie:

| Semantic view | Ossie |
|---|---|
| `name`, `description` | `name`, `description` |
| `tables[]` | `datasets[]` |
| `base_table` database, schema, table | `source` as `database.schema.table` |
| `primary_key.columns` | `primary_key` |
| `dimensions[]` | fields, `dimension.is_time: false` |
| `time_dimensions[]` | fields, `dimension.is_time: true` |
| `facts[]` | fields, no `dimension` block |
| `name`, `expr`, `data_type` | field `name`, dialect `SNOWFLAKE` expression copied verbatim, datatype from the table below |
| `relationships[].left_table` / `right_table` | `from` / `to` |
| `relationship_columns[]` | `from_columns` / `to_columns` |
| `metrics[]` `name`, `description`, `expr` | metrics, expression dialect `SNOWFLAKE` |
| `metrics[].synonyms` | metric `ai_context.synonyms` |
| `module_custom_instructions.sql_generation` | model `ai_context.instructions` |

This fixture's relationship is many-to-one with `bookings` on the left. The mapper
checks that. It does not infer cardinality for any other relationship.

Datatypes, this fixture only: `VARCHAR` → `String`, `NUMBER(38,0)` → `Integer`,
`NUMBER(18,2)` → `Decimal`, `DATE` → `Date`.

Ossie → Wisdom format `1.0`:

- `version` is `"1.0"`.
- `export_metadata.exported_at` is `"2026-01-01T00:00:00+00:00"`. `source_domain_id`
  is `"demo-finance-cfo"`. `domain_name` is the model name. These are constants.
- Domain `ref.name` and `description` come from the model. `domainSystemInstructions`
  is the model description.
- Each dataset becomes `tables[].zsheet_json`. `location` splits `source` into
  database, schema, `dbTable`, and `connectionId` `et-connection-snowflake`. A field
  whose expression is its own name becomes a column. Metrics become `measures` on
  `bookings`.
- One relationship, `relationshipType` `MANY_TO_ONE`, a single `joinCondition`.
- `connections` is one object, dialect `snowflake`, id `et-connection-snowflake`.
- `reviewed_queries` and `synonym_sets` are `{"ref": null, "items_json": "{}"}`.
- Ids are functions of names (`ET_DOMAIN_finance_cfo_cockpit`, `ET_ZSHEET_bookings`,
  `ET_ZSHEET_fiscal_calendar`) so a second run is byte-identical.
- Metric `ai_context.synonyms` become a knowledge item named `Synonyms for <metric>`
  whose content is markdown bullets. Model `ai_context.instructions` become a
  knowledge item named `Board revenue`.

The reverse functions invert the same rows. `verified_queries`, filters, tags, and
`access_modifier` are not written.

## Development

```bash
uv sync
uv run pytest
```
