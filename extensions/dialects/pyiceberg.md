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

# The PYICEBERG Dialect

## Scope and Use Case

`PYICEBERG` defines PyIceberg's predicate string syntax, not a syntax specified by the
Apache Iceberg specification. It is a predicate-only dialect, not a complete expression
language. The `ICEBERG` name remains available for a separate future proposal, such as
the Iceberg REST catalog JSON expression form.

The core Ossie schema uses dialect expressions for fields and metrics; it has no filter
object. This dialect is intended only for boolean field expressions, not for non-boolean
fields or metric calculations.

A model author or converter working from Iceberg tables can supply a boolean field with
two forms: an `ANSI_SQL` or `OSSIE_SQL_2026` expression as the portable definition, and a
`PYICEBERG` predicate alongside it where an equivalent predicate exists. A PyIceberg-based
consumer can pass the latter string directly to a table scan as its `row_filter`, without
translating SQL. This does not provide PyIceberg equivalents for arbitrary SQL expressions.

## Definition and Reference Implementation

The supported-predicates table and operand rules below define this dialect. PyIceberg
is the reference implementation, with a minimum version of **0.10.0**. The reference
parser is
[`pyiceberg.expressions.parser.parse` in the 0.10.0 release](https://github.com/apache/iceberg-python/blob/pyiceberg-0.10.0/pyiceberg/expressions/parser.py).
Parser acceptance alone does not extend the dialect defined here.

The syntax is SQL-like but is not SQL, and includes forms such as `IS NAN`. Ossie's
reference validator skips SQL parsing for `PYICEBERG`; this skip is not validation of
the predicate grammar or of binding to a table schema.

### Supported Predicates

Keywords are case-insensitive.

| Predicate | Syntax |
| :---- | :---- |
| Comparison | `col = v`, `col != v`, `col < v`, `col <= v`, `col > v`, `col >= v` (`==` and `<>` are accepted as aliases of `=` and `!=`) |
| Range | `col BETWEEN low AND high` (inclusive on both ends) |
| Null check | `col IS NULL`, `col IS NOT NULL` |
| NaN check | `col IS NAN`, `col IS NOT NAN` |
| Set membership | `col IN (v1, v2, ...)`, `col NOT IN (v1, v2, ...)` |
| Prefix match | `col LIKE 'prefix%'`, `col NOT LIKE 'prefix%'` |
| Constant | `true`, `false` |
| Boolean composition | `NOT p`, `p AND q`, `p OR q`, with parentheses for grouping; `NOT` binds tightest, then `AND`, then `OR` |

**Version compatibility:** In PyIceberg 0.11 and 0.12, composing the `true` / `false`
constants with `AND` or `OR` fails. Standalone constants and boolean literal operands
(for example, `is_active = true`) are distinct from this composition limitation. Authors
targeting those releases should avoid composing constants with `AND` / `OR`.

### Operands

- A comparison is between one column and one literal. The literal may be written on either
  side (`5 < amount` is the same as `amount > 5`); column-to-column comparisons such as
  `a = b` are not supported.
- Columns are unquoted identifiers (letters, digits, `_`, and `$`, not starting with a digit
  or `$`) or double-quoted identifiers, with `.` separating the parts of a nested field
  (`customer.address.city`).
- Literals are single-quoted strings (a quote is escaped by doubling it, `'O''Brien'`),
  integers, decimals, and `true` / `false`. There are no typed literals such as
  `DATE '2026-01-01'`; dates and timestamps are written as strings and converted when the
  predicate is bound to the column's type.
- The values of an `IN` list must be non-empty and all of the same literal kind.
- `LIKE` supports a single `%` wildcard at the end of the pattern only (starts-with). A
  pattern with no wildcard is an equality test, and `\%` matches a literal `%`.

### Not Supported

- Function calls of any kind. In particular, partition transforms such as `month(col)`,
  `bucket(16, col)`, or `truncate(4, col)` cannot be written in this dialect; they belong to
  an Iceberg table's partition spec, not to its expression strings.
- Arithmetic, casts, aggregates, and other value-computing expressions. `PYICEBERG`
  expressions are predicates only.
- `NOT BETWEEN`, comparisons against `NULL` (use `IS NULL` / `IS NOT NULL`), and `LIKE`
  patterns with a leading or embedded wildcard.

## Examples

```text
order_date >= '2026-01-01' AND region = 'EMEA'
customer_id IS NOT NULL AND (status = 'open' OR status = 'pending')
amount BETWEEN 10 AND 100 AND sku LIKE 'AB%'
```

A boolean field carrying both its portable definition and its PyIceberg form:

```yaml
name: is_recent_emea_order
datatype: Boolean
expression:
  dialects:
    - dialect: ANSI_SQL
      expression: order_date >= DATE '2026-01-01' AND region = 'EMEA'
    - dialect: PYICEBERG
      expression: order_date >= '2026-01-01' AND region = 'EMEA'
```

As with other dialect alternatives, consumers choose a supported form deterministically.
Authors should supply equivalent definitions, accounting for differences such as SQL NULL
semantics when targeting a particular table schema.
