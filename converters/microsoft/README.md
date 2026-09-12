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

# Apache Ossie Microsoft Converter

Converts between a Microsoft Power BI / Fabric semantic model and an Apache Ossie (OSI)
semantic model. Power BI output is available as either a TMSL `model.bim` mapping or a
TMDL document. The conversion is offline and requires no Power BI or Fabric
connection.

## Installation

```bash
cd converters/microsoft
uv sync
```

### Optional TOM validation and TMDL serialization

Microsoft's Tabular Object Model (TOM) can load and structurally validate exported
`model.bim` files and serialize them to canonical TMDL entirely offline. It is
deliberately optional because TOM is proprietary Microsoft-licensed software. TMSL
conversion remains independent of .NET.

```bash
uv sync --extra tom
uv run python scripts/restore_tom.py
```

The restore script pins `Microsoft.AnalysisServices` from NuGet and writes its assemblies
under the ignored `.tom/` directory; no DLLs are committed. Then validate from Python:

```python
from ossie_microsoft import validate_bim

result = validate_bim("model.bim")
result.raise_for_errors()
```

Set `OSSIE_MICROSOFT_TOM_ASSEMBLIES` when the assemblies live somewhere other than
`.tom/assemblies`. The assemblies target `net8.0` and are platform-neutral for offline
validation, so this works on Linux, macOS and Windows; the only native payload in the
package is the MSAL authentication broker, which offline validation never loads. This
validation checks TMSL structure and object references. **Offline
TOM does not parse or validate DAX**, including syntax, function names, arity, or column
references, so use a separate DAX parser when that guarantee is required.

### Optional live engine validation

The gap above is only fully closed by the engine itself: nothing offline compiles DAX the
way Analysis Services does. `validate_with_engine` deploys a model to a Fabric workspace,
refreshes it -- which is what actually compiles the DAX -- evaluates every table and
measure, and then deletes it again.

```python
from ossie_microsoft import validate_with_engine

result = validate_with_engine(
    bim,
    workspace="<workspace id>",
    fabric_token=...,   # scope https://api.fabric.microsoft.com
    powerbi_token=...,  # scope https://analysis.windows.net/powerbi/api
)
result.raise_for_errors()
```

Every partition is rewritten as an inline M literal of generated sample rows, so the
refresh needs no gateway, lakehouse or stored credential; tables, columns, relationships
and measures are otherwise untouched, so what the engine compiles is the DAX the converter
produced.

This is the **only** validation in this package that leaves the local machine. It creates
real items in a real workspace and consumes capacity, so it is opt-in, it is not part of
the default test run, and it is deliberately not wired into CI.

One behaviour is worth knowing: a measure whose DAX fails to compile is *dropped* from the
deployed model, so referencing it by name returns no rows rather than an error. The
validator re-evaluates the expression inline to recover the real diagnostic, because a
silent empty result is exactly the failure mode this package exists to prevent.

## Usage

### Command line

```bash
ossie-microsoft import -i model.bim  -o model.yaml   # Power BI -> Apache Ossie
ossie-microsoft export -i model.yaml -o model.bim    # Apache Ossie -> Power BI
```

With no `-o`, the result is written to stdout.

Everything the converter could not carry across faithfully is reported on stderr:

```
$ ossie-microsoft import -i model.bim -o model.yaml
[model] row-level security roles ('roles') has no Apache Ossie counterpart; preserved
in the Power BI stash for round trip, but not represented in the Apache Ossie document
[table 'Sales' measure 'Total Sales'] a KPI ('kpi') has no Apache Ossie counterpart; ...
3 construct(s) could not be converted faithfully; see the messages above.
```

| Flag | Effect |
| --- | --- |
| `--strict` | exit non-zero if anything could not be converted faithfully |
| `-q`, `--quiet` | suppress the report |

`--strict` is the useful mode in a pipeline that must not silently degrade a model. The
output file is still written, so a failing run can be inspected. Both flags are accepted
before or after the subcommand.

### Library

```python
import json

import yaml

from ossie_microsoft import convert_ossie_to_semantic_model, convert_semantic_model_to_ossie

with open("model.bim", encoding="utf-8-sig") as fh:
    ossie_yaml = convert_semantic_model_to_ossie(json.load(fh))

bim = convert_ossie_to_semantic_model(
  ossie_yaml,
  source={"workspaceId": "<workspace-id>", "itemId": "<item-id>"},
)

# TMDL is returned as a single document, with every object nested inline.
tmdl = convert_ossie_to_semantic_model(ossie_yaml, output_format="TMDL")
print(tmdl)

# The import direction also accepts model.bim JSON text or a TMDL document.
ossie_yaml = convert_semantic_model_to_ossie(tmdl)
```

`output_format` accepts `"TMSL"` (the default) or `"TMDL"`, case-insensitively.
`convert_semantic_model_to_ossie` accepts a parsed `model.bim` mapping, `model.bim`
JSON text, or a single TMDL document as text. TMDL input and output require the
optional TOM setup described above.

Each report is emitted twice, because the two channels answer different questions:

- a `UserWarning`, so `warnings.simplefilter("error")` gives a hard, programmatic
  guarantee that a conversion was lossless;
- a record on the `ossie_microsoft` logger, for applications that consume a log rather
  than installing warning filters.

```python
import logging

logging.getLogger("ossie_microsoft").addHandler(logging.StreamHandler())
```

## Mapping

| Power BI (TMSL) | Apache Ossie |
|-----------------|--------------|
| `name` / `model.description` | `semantic_model.name` / `.description` |
| `model.tables[]` | `datasets[]` |
| table partition source (`entity`, `m`, `query`, `calculated`) | `dataset.source` |
| `table.columns[]` | `dataset.fields[]` |
| `column.sourceColumn` | field `expression` (`ANSI_SQL` dialect) |
| calculated `column.expression` | field `expression` (`DAX` dialect) |
| `column.dataType` | field `datatype` |
| `column.isKey` / `column.isUnique` | `primary_key` / `unique_keys` |
| temporal `dataType` or `dataCategory: Time` | `field.dimension.is_time` |
| `table.measures[]` | `metrics[]` (`DAX` dialect) |
| `model.relationships[]` | `relationships[]` |
| everything else | `custom_extensions` (vendor `POWER_BI`) |

## Design rule: expressions are never rewritten

Power BI evaluates DAX. Apache Ossie field expressions are usually SQL. The two languages
do not share an evaluation model — DAX aggregates resolve against a filter context that
has no SQL equivalent — so this converter carries expressions across in the dialect they
were authored in and **never machine-translates one into the other**.

Concretely:

- A Power BI measure becomes a metric with a `DAX` dialect expression, not a SQL one.
- On export, a metric becomes a measure only if it carries a `DAX` expression. A metric
  with only a SQL expression is reported and skipped.
- A field expression becomes a TMSL `sourceColumn` only when it is a plain column
  reference, which is exactly what `sourceColumn` means. A computed SQL expression is
  reported and skipped. (A `sourceColumn` the source query spells with spaces, a hyphen
  or a leading digit is preserved and replayed verbatim, so it is not mistaken for a
  computed expression.)

The reason is that a partial rewrite fails silently. An unrecognized function passed
through unchanged, a `CAST` quietly dropped, or an aggregate distributed across a join
all produce a model that loads and returns a number — just the wrong number. A missing
measure is a bug a modeler notices; a plausible wrong one is not.

If you need SQL-to-DAX translation, do it deliberately as a separate step and author the
result into the `DAX` dialect before exporting.

## Losslessness

Nothing is discarded silently. The converter follows the two rules in
[`converters/README.md`](../README.md):

1. **Preserve.** Every TMSL property the Apache Ossie mapping does not consume —
   annotations, partitions, hierarchies, roles, perspectives, cultures, query groups,
   format strings, display folders, KPIs, cross-filter behaviour, relationship
   cardinalities, `rowNumber` columns — is stored in a versioned JSON blob in a
   `POWER_BI` `custom_extensions` entry, alongside excluded tables and skipped
   relationships. This is a deny-list, not an allow-list: a TMSL property this converter
   has never heard of is preserved too, rather than silently dropped. The export
   direction replays it all, so a `model.bim` converted to Apache Ossie and back is the
   same model.

2. **Report.** Anything that genuinely cannot be represented raises a `UserWarning`
   naming the object and the reason. Callers who need a hard guarantee can escalate:

   ```python
   import warnings

   with warnings.catch_warnings():
       warnings.simplefilter("error")
       ossie_yaml = convert_semantic_model_to_ossie(bim)  # raises if anything is lost
   ```

A model that uses no Power BI-specific features converts to clean, vendor-neutral Apache
Ossie with no `custom_extensions` at all.

## Data type notes

TMSL's `dataType` vocabulary is the Tabular Object Model `DataType` enum. It has **no**
date-only, time-only or timezone-aware member: every temporal value is stored as
`dateTime`.

| TMSL | Apache Ossie | Note |
|------|--------------|------|
| `string` | `String` | |
| `int64` | `Integer` | 64-bit in the model, but report visuals are only exact to 2^53 − 1 |
| `decimal` | `Decimal` | "Fixed Decimal Number": exact, 19 digits, 4 after the point |
| `double` | `Float` | "Decimal Number": 64-bit floating point, approximate |
| `boolean` | `Boolean` | |
| `dateTime` | `DateTime`, or `Date` when the format string has no time part | |
| `binary`, `variant` | `Opaque` | no portable equivalent; reported |
| `automatic`, `unknown` | *(omitted)* | the engine has not resolved a type |

Consequences worth knowing:

- **Date-only intent lives in the format string.** A `dateTime` column formatted with
  only date tokens is imported as `Date`; an exported `Date` field gets a date-only
  format string back. Quoted literals and backslash escapes are stripped before tokens
  are detected, so the `h` in `"month" mmm yyyy` is display text, not an hour token. Note
  also that in these VBA-style format strings `m` means *month* unless it directly
  follows an hour token, which is why minutes are written `n`.
- **`Time` and `DateTimeTz` do not survive export.** They collapse onto `dateTime`,
  gaining a date part or losing a UTC offset respectively. Both cases are reported.
- **`double` is not `Decimal`.** Mapping Power BI's approximate "Decimal Number" onto an
  exact decimal type would overstate its precision.
- Values outside years 1900–9999 are outside the Power BI `dateTime` range, and time is
  stored at 1/300 second (about 3.33 ms) granularity.

## Constructs with no equivalent

These are reported rather than approximated, in either direction:

| Construct | Why |
|-----------|-----|
| Inactive relationships | Apache Ossie has no inactive-join concept; keeping one would misrepresent the join graph |
| Many-to-many relationships | Apache Ossie relationships are many-to-one or one-to-one |
| Composite relationships | A TMSL relationship joins exactly one column pair |
| Composite primary/unique keys | TMSL marks a single key column per table |
| `Opaque` fields | Power BI has no untyped data type |
| Other vendors' `custom_extensions` | not interpretable by Power BI |

On import, private tables, calculation groups, auto-generated date tables
(`LocalDateTable_*`, `DateTableTemplate_*`) and `rowNumber` columns are left out of the
vendor-neutral model — they are Power BI implementation details — but preserved in the
stash so export restores them. One-to-many relationships are flipped so the many side is
`from`, and the original orientation and cardinalities are recorded.

A data type with no portable equivalent (`binary`, `variant`, `automatic`, `unknown`) is
also kept in the stash, so the export restores the original TMSL type rather than
guessing one from the portable model.

On export, a dataset whose Power BI partition was not preserved gets a Direct Lake
partition for a table source, or an import partition for a query source. Direct Lake
connection details come from the optional `source` argument.

### Preserved, but not modelled

These Power BI constructs survive a round trip in the stash, so nothing is lost when the
destination is Power BI again. But Apache Ossie has no way to express them, so they are
invisible to any other consumer of the document — and each one is reported on import.

| Level | Construct |
| --- | --- |
| Model | row-level security roles, perspectives, translations/cultures, shared Power Query expressions and parameters, data sources, query groups |
| Table | hierarchies, calculation groups, incremental refresh policies, detail rows definitions |
| Column | date table variations, sort-by-column |
| Measure | KPIs, detail rows definitions |

Purely presentational properties (`isHidden`, `displayFolder`, `formatString`) are
preserved just as faithfully but are not reported: a warning on every cosmetic property
would bury the ones that matter.

In the other direction, Apache Ossie's `ai_context` is stored in an `OssieAIContext`
annotation. A `label` has no TMSL equivalent and is reported as dropped.

### SQL metric expressions

Power BI evaluates a measure *only* as DAX, so an Apache Ossie metric written in SQL
has to be translated. The converter translates a deliberately narrow, unambiguous set:
a single aggregate over one unqualified column, plus `COUNT(*)`.

| Apache Ossie (SQL) | Power BI (DAX) |
| :---- | :---- |
| `SUM(x)` / `MIN(x)` / `MAX(x)` | `SUM('T'[X])` and so on |
| `COUNT(x)` | `COUNTA('T'[X])` |
| `AVG(x)` | `AVERAGE('T'[X])` |
| `COUNT(DISTINCT x)` | `DISTINCTCOUNTNOBLANK('T'[X])` |
| `COUNT(*)` | `COUNTROWS('T')` |
| `STDDEV(x)` / `STDDEV_SAMP(x)` | `STDEV.S('T'[X])` |
| `STDDEV_POP(x)` | `STDEV.P('T'[X])` |
| `VARIANCE(x)` / `VAR_SAMP(x)` | `VAR.S('T'[X])` |
| `VAR_POP(x)` | `VAR.P('T'[X])` |
| `MEDIAN(x)` | `MEDIAN('T'[X])` |

Two of these are not the same-named DAX function, because DAX treats BLANK differently
from SQL's NULL. SQL `COUNT(x)` counts non-NULL values of any type, but DAX `COUNT`
documents `TRUE`/`FALSE` columns as unsupported, so `COUNTA` is the faithful
equivalent. SQL `COUNT(DISTINCT x)` excludes NULL, but DAX `DISTINCTCOUNT` counts BLANK
as a distinct value and so is off by one on any nullable column;
`DISTINCTCOUNTNOBLANK` is the equivalent. Both match the mapping table in
`core-spec/expression_language.md`.

Two further differences are deliberately left in place, because both fail visibly
rather than returning a quietly wrong number. SQL `COUNT` returns 0 over an empty set
where DAX returns BLANK -- coercing to 0 would defeat Power BI's convention of hiding
empty rows in a visual. And the DAX deviation and variance functions raise an error
when fewer than two non-blank rows remain, where SQL yields NULL or 0.

`ANSI_SQL`, `SNOWFLAKE`, `DATABRICKS` and `BIGQUERY` expressions are parsed; `MDX`,
`TABLEAU` and `MAQL` are not SQL and are not attempted.

Note that DAX has no bare column reference, so a translation is only possible when the
column resolves to exactly one field in exactly one dataset. A name that appears in two
datasets is treated as untranslatable rather than guessed at.

### What is not translated

Everything outside that set is **reported and skipped** rather than approximated --
arithmetic between aggregates (`SUM(a) / COUNT(*)`), aggregates over expressions
(`SUM(a + b)`), `CASE`, window and filtered aggregates, qualified column references,
and percentiles (whose DAX spelling depends on the interpolation the SQL does not
state).

 A calculated *field* whose expression is not already DAX is not generally translated. The
 export will only attempt a narrow, unambiguous translation for string concatenations;
 otherwise it emits the column as `BLANK()` and preserves the original dialect/expression
 as annotations so the column remains present but does not silently compute a wrong value.

The reason is that the alternative is worse. A stand-in expression such as `BLANK()`,
or a plausible-looking but wrong translation, produces a model that deploys and
refreshes without error and then answers every query with an incorrect number. A
missing measure is something a modeller notices immediately. The authored expression is
untouched in the Apache Ossie source, so re-running the conversion after adding a `DAX`
expression picks it up.

## Testing

```bash
cd converters/microsoft
uv run ruff check .
uv run pytest --cov
```

Coverage is enforced at 95%. The converter's contract is that every lossy branch reports
itself, and an unexercised branch is an unproven report.

## Roadmap

- An end-to-end smoke test that deploys emitted TMSL to an Analysis Services instance,
  so the output is validated by the engine itself and not only by these tests.
