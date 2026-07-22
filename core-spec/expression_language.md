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

# Ossie Proposal: Expression Language

**Current Status:** Proposed Final 

**Working Group**

| Lead(s) | Participants |
| :---- | :---- |
| Will Pugh, Snowflake Khushboo Bhatia, Snowflake  | LLyod Tabb, Malloy Dianne Wood, Atscale Lior Ebel, Salesforce   Quigley Malcolm, dbt Labs Kurt, Relational AI Justin Talbot, Databricks Pavel Tiunov, Cube Damian Waldron, Thoughtspot Oliver Laslett, Lightdash Martin Traverso, Starburst JB Onofré, The ASF Raul Beiroa, Denodo |

## Overview

![Ossie Layers](img/Ossie_layers.png)

There are two layers in Ossie that need an expression language:

* **Ontology layer.**  This layer maps onto the ontology layer which sits above the logical layer.  It maps more closely to modelling languages like OWL, [(Py)Rel](https://docs.relational.ai) from RelationalAI, and [Legend](https://legend.finos.org) from Goldman Sachs  
* **Logical layer.**  This layer maps directly to the databases and physical layer.  It maps closely to traditional BI semantic models.

This proposal is only targeted at the Logical Layer.  It would be nice if the Ontological layer could re-use the same expression language, but that will be treated as a separate proposal.

This document defines the SQL expression language subset that Ossie-compliant implementations MUST support. The goal is to provide a portable expression language that works across all Ossie implementations while allowing vendors to expose richer database-specific functionality through dialect extensions.  In particular, it is meant for expressions at the logical layer.  This means metrics, fields, filters, etc  In the future, expressions such as arbitrary join expressions should also use this expression language.

We expect there will be extensions to this language to cover concepts such as sub-queries, grain calculations, etc.  However, these will each have their own proposal.

### Design Principles

1. **Portability**: Core functions work identically across all implementations  
2. **Familiarity**: Based on widely-adopted SQL syntax and semantics  
3. **Analytical Focus**: Prioritizes functions commonly used in BI and analytics  
4. **Extensibility**: Vendor dialects can extend beyond the core

### Changes to YAML

1) Create a new dialect in the Ossie spec: Ossie\_SQL\_2026, which refers to this language specification.   
2) Make Ossie\_SQL\_2026 the default dialect if one is not chosen.

### Standards Reference

The core language is based on **ANSI SQL:2003 Core** (ISO/IEC 9075-2:2003), selected for its:

- Wide adoption across major databases (Snowflake, Databricks, PostgreSQL, BigQuery)  
- Well-defined semantics  
- Support for modern analytical features (window functions, CTEs)

### Namespacing and Identifier Resolution

The identifiers will match standard SQL identifiers:

`Field: <SQL Identifier>`

`FieldExpr: Field | Field ‘.’ Field`

The Ossie spec currently contains three namespaces, which determine the visibility and uniqueness of each value.  Where and how a field (or metric) is defined will determine the namespace for it, which in turn determines the ways it can be addressed by other fields.

All identifiers MUST be valid names and follow ANSI SQL naming, with the size limitation of 128 characters for identifiers.  Many databases support longer identifiers, however, this number is safe for a broad number of vendors.

Regular identifiers (unquoted) should be case insensitive.    For example, an identifier id is regular, so it would match with Id or iD.  Comparing quoted and non-quoted identifiers is DB specific, so for best portability it is best to use simple identifiers.

The quote character for the Ossie dialect will follow ANSI SQL and support the double quote character (“).  This means that if an expression is in a field expression or as an identifier in the YAML, this will be the expected quoting.  However, there are some databases that use other escape characters.  Working with these have the option of either creating expressions using their dialect or having the Ossie document written in the Ossie dialect, but then having the SQL Interface queried in the local dialect.  The SQL Interface will be defined in a different document. 

#### Comparison Table

| You type this in SQL | Equivalen to | Will it match a column created as id?             |
| :---- |:-------------|:--------------------------------------------------|
| id | ID           | **Yes** (Standard behavior)                       |
| Id | ID           | **Yes** (Standard behavior)                       |
| "ID" | ID           | **Yes** (Force-matched to normalized case)        |
| "id" | id           | **No** (Quotes cause an exact match to lowercase) |

Sometimes, we may refer to a **normalized identifier**.  This is a form the identifiers can be put in, so they can be matched easily and matches can be made with case-sensitive, exact matching.  For **normalized identifiers**:

* Regular identifiers are upper cased  
* Quoted identifiers have their quotes stripped and any escaped characters are unescaped

#### Name Spaces

Namespaces define how an identifier is looked up in an expression.  They are covered in the semantics document.  Identifiers
will be able to be multi-part and the parts will be separated by the '.' characters,  E.g. `dataset.field`  This matches SQL conventions.

## SQL Language Subset

### Supported SQL Constructs

Ossie expressions support the following SQL constructs within any expression:

| Construct | Notes                                                                                                                                                                                                                                                                                 |
| :---- |:--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Column and Metric references | Varies based on whether in Ontology or Semantic models. See namespaceing in [OSI Discussion Point: Core Analytic Abstractions](https://docs.google.com/document/d/1si8DqU4arG18ZgX4HnRG5D_zS2X7V1s-vgNY35rvxhM/edit?tab=t.0#heading=h.le505t8uoyfy) And future Ontology documentation |
| Arithmetic operators | `+`, `-`, `*`, `/`, `%` (modulo)                                                                                                                                                                                                                                                      |
| Comparison operators | `=`, `<>`, `!=`, `<`, `>`, `<=`, `>=`                                                                                                                                                                                                                                                 |
| Logical operators | `AND`, `OR`, `NOT`                                                                                                                                                                                                                                                                    |
| `BETWEEN` | `x BETWEEN a AND b`                                                                                                                                                                                                                                                                   |
| `IN` / `NOT IN` | `x IN (a, b, c)` This only supports lists of values, not subqueries.                                                                                                                                                                                                                  |
| `LIKE` / `ILIKE` | Pattern matching                                                                                                                                                                                                                                                                      |
| `IS NULL` / `IS NOT NULL` | Null checks                                                                                                                                                                                                                                                                           |
| `CASE WHEN` | Conditional logic                                                                                                                                                                                                                                                                     |
| Aggregate functions | Core functions used for aggregations                                                                                                                                                                                                                                                  |
| Window functions | Core supported window functions                                                                                                                                                                                                                                                       |
| Scalar functions | See function categories below                                                                                                                                                                                                                                                         |
| Parentheses | Expression grouping                                                                                                                                                                                                                                                                   |

### 

### Not Supported in Expressions

| Construct | Reason |
| :---- | :---- |
| `SELECT` / `FROM` / `JOIN` | Handled by semantic layer |
| `GROUP BY` | Controlled by grain specification |
| `WHERE` | Use filter property instead |
| Subqueries | Use field references instead, or EXISTS\_IN() for filtering based on a subquery. |
| CTEs | Use field references instead |
| `UNION` / `INTERSECT` / `EXCEPT` | Not applicable to expressions |
| DDL statements | Out of scope |
| DML statements | Out of scope |

### Operator Precedence

Standard SQL operator precedence applies (highest to lowest):

1. Parentheses `()`  
2. Unary operators: `+`, `-`, `NOT`  
3. Multiplication/Division: `*`, `/`, `%`  
4. Addition/Subtraction: `+`, `-`  
5. Comparison: `=`, `<>`, `<`, `>`, `<=`, `>=`, `LIKE`, `IN`, `BETWEEN`, `IS NULL`   
6`AND`  
7`OR`

---

## Aggregation Functions

### Core Aggregation Functions (REQUIRED)

| Function | Syntax | Description | Decomposability |
| :---- | :---- | :---- | :---- |
| `SUM` | `SUM(expr)` | Sum of values | Distributive |
| `COUNT` | `COUNT(expr)` | Count of non-null values | Distributive |
| `COUNT(*)` | `COUNT(*)` | Count of all rows | Distributive |
| `COUNT(DISTINCT expr)` | `COUNT(DISTINCT expr)` | Count of distinct values | Holistic |
| `AVG` | `AVG(expr)` | Arithmetic mean | Algebraic |
| `MIN` | `MIN(expr)` | Minimum value | Distributive |
| `MAX` | `MAX(expr)` | Maximum value | Distributive |

### Statistical Aggregations (REQUIRED)

| Function | Syntax | Description | Decomposability |
| :---- | :---- | :---- | :---- |
| `STDDEV` | `STDDEV(expr)` | Sample standard deviation | Algebraic |
| `STDDEV_POP` | `STDDEV_POP(expr)` | Population standard deviation | Algebraic |
| `STDDEV_SAMP` | `STDDEV_SAMP(expr)` | Sample standard deviation (alias for STDDEV) | Algebraic |
| `VARIANCE` | `VARIANCE(expr)` | Sample variance | Algebraic |
| `VAR_POP` | `VAR_POP(expr)` | Population variance | Algebraic |
| `VAR_SAMP` | `VAR_SAMP(expr)` | Sample variance (alias for VARIANCE) | Algebraic |

### Percentile Functions (REQUIRED)

| Function | Syntax | Description | Decomposability |
| :---- | :---- | :---- | :---- |
| `MEDIAN` | `MEDIAN(expr)` | Median value (50th percentile) | Holistic |
| `PERCENTILE_CONT` | `PERCENTILE_CONT(p) WITHIN GROUP (ORDER BY expr)` | Continuous percentile (interpolated) | Holistic |
| `PERCENTILE_DISC` | `PERCENTILE_DISC(p) WITHIN GROUP (ORDER BY expr)` | Discrete percentile (actual value) | Holistic |

Where `p` is a value between 0 and 1 (e.g., 0.5 for median, 0.75 for 75th percentile).

### Approximate Aggregations (RECOMMENDED)

Approximate functions trade exact accuracy for significantly better performance on large datasets. They use probabilistic algorithms (sketches) that are efficiently mergeable, making them well-suited for distributed computation.

| Function | Syntax | Description | Typical Error |
| :---- | :---- | :---- | :---- |
| `APPROX_COUNT_DISTINCT` | `APPROX_COUNT_DISTINCT(expr)` | Approximate distinct count using HyperLogLog or something similar.  Actual method is up to providers. | \~2% |
| `APPROX_PERCENTILE` | `APPROX_PERCENTILE(expr, p)` | Approximate percentile using t-digest or similar | \~1% |

```sql
-- Approximate distinct count (much faster than COUNT(DISTINCT) on large data)
APPROX_COUNT_DISTINCT(customer_id)

-- Approximate median
APPROX_PERCENTILE(amount, 0.5)

-- Approximate 95th percentile  
APPROX_PERCENTILE(response_time, 0.95)
```

**Database Support:**

| Function | Snowflake | BigQuery | Databricks | PostgreSQL |
| :---- | :---- | :---- | :---- | :---- |
| `APPROX_COUNT_DISTINCT` | ✅ | ✅ | ✅ | ❌ (extension) |
| `APPROX_PERCENTILE` | ✅ | ✅ `APPROX_QUANTILES` | ✅ | ❌ |

**Note**: BigQuery uses `APPROX_QUANTILES(expr, num_buckets)` which returns an array. To get a specific percentile: `APPROX_QUANTILES(amount, 100)[OFFSET(50)]` for median.

---

### Conditional Aggregations (REQUIRED)

SUM / COUNT aggregation functions support `DISTINCT.`   
All aggregations should support filtered aggregation:

```sql
-- DISTINCT modifier
SUM(DISTINCT amount)
COUNT(DISTINCT customer_id)

-- Filtered aggregation via CASE
SUM(CASE WHEN status = 'completed' THEN amount ELSE 0 END)
COUNT(CASE WHEN status = 'completed' THEN 1 END)
```

### Decomposability Reference

For multi-stage aggregation (see [Ossie Analytical Context Extension](https://docs.google.com/document/d/1MKNySGmEv_C6CzBZ7um9Ym3_mMvmOolpDuwPvRzQ1bo/edit?usp=sharing)):

| Category | Functions |
| :---- | :---- |
| **Distributive** | SUM, COUNT, MIN, MAX |
| **Algebraic** | AVG, STDDEV, VARIANCE |
| **Holistic** | MEDIAN, PERCENTILE, COUNT DISTINCT |
| **Sketch-based** | APPROX\_COUNT\_DISTINCT, APPROX\_PERCENTILE |

---

## Date/Time Functions

### Current Date/Time (REQUIRED)

| Function | Syntax | Returns | Description |
| :---- | :---- | :---- | :---- |
| `CURRENT_DATE` | `CURRENT_DATE` or `CURRENT_DATE()` | DATE | Current date |
| `CURRENT_TIMESTAMP` | `CURRENT_TIMESTAMP` or `CURRENT_TIMESTAMP()` | TIMESTAMP | Current timestamp |
| `CURRENT_TIME` | `CURRENT_TIME` or `CURRENT_TIME()` | TIME | Current time |

### Date/Time Extraction (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `YEAR` | `YEAR(date_expr)` | Extract year (integer) |
| `QUARTER` | `QUARTER(date_expr)` | Extract quarter (1-4) |
| `MONTH` | `MONTH(date_expr)` | Extract month (1-12) |
| `DAY` | `DAY(date_expr)` | Extract day of month (1-31) |
| `DAYOFYEAR` | `DAYOFYEAR(date_expr)` | Day of year (1-366) |
| `HOUR` | `HOUR(timestamp_expr)` | Extract hour (0-23) |
| `MINUTE` | `MINUTE(timestamp_expr)` | Extract minute (0-59) |
| `SECOND` | `SECOND(timestamp_expr)` | Extract second (0-59) |

### Alternative Extraction Syntax (REQUIRED)

```sql
-- EXTRACT function (SQL standard)
EXTRACT(YEAR FROM date_expr)
EXTRACT(MONTH FROM date_expr)
EXTRACT(DAY FROM date_expr)

-- DATE_PART function (common alternative)
DATE_PART('year', date_expr)
DATE_PART('month', date_expr)
DATE_PART('day', date_expr)
```

Supported date parts for `EXTRACT` and `DATE_PART`:

- `YEAR`, `QUARTER`, `MONTH`, `WEEK`, `DAY`  
- `DAYOFWEEK`, `DAYOFYEAR`  
- `HOUR`, `MINUTE`, `SECOND`, `MILLISECOND`

### Date Truncation (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `DATE_TRUNC` | `DATE_TRUNC(part, date_expr)` | Truncate to specified precision |

Supported parts: `'year'`, `'quarter'`, `'month'`, `'week'`, `'day'`, `'hour'`, `'minute'`, `'second'`

```sql
-- Examples
DATE_TRUNC('month', order_date)    -- First day of month
DATE_TRUNC('quarter', order_date)  -- First day of quarter
DATE_TRUNC('week', order_date)     -- First day of week (Monday)
```

### Date Arithmetic (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `DATEADD` | `DATEADD(part, amount, date_expr)` | Add interval to date |
| `DATEDIFF` | `DATEDIFF(part, start_date, end_date)` | Difference between dates |

```sql
-- Add/subtract intervals
DATEADD(day, 7, order_date)         -- Add 7 days
DATEADD(month, -1, order_date)      -- Subtract 1 month
DATEADD(year, 1, order_date)        -- Add 1 year

-- Calculate differences
DATEDIFF(day, start_date, end_date)    -- Days between dates
DATEDIFF(month, start_date, end_date)  -- Months between dates
DATEDIFF(year, start_date, end_date)   -- Years between dates
```

### Date/Time Construction (REQUIRED)

Construct DATE, TIME, and TIMESTAMP values using ANSI typed literals or `CAST`.
ISO-8601 strings (`YYYY-MM-DD`, `YYYY-MM-DD HH:MI:SS`, `HH:MI:SS`) require no format
model and behave identically across engines, making them the portable default.

| Form | Syntax | Description |
| :---- | :---- | :---- |
| Typed literal | `DATE '2024-01-15'` | Construct a DATE |
| Typed literal | `TIMESTAMP_NTZ '2024-01-15 10:30:00'` | Construct a wall-clock timestamp (no time zone) |
| Typed literal | `TIME '10:30:00'` | Construct a TIME |
| Cast | `CAST('2024-01-15' AS DATE)` | Parse ISO string to DATE |
| Cast | `CAST('2024-01-15 10:30:00' AS TIMESTAMP_NTZ)` | Parse ISO string to timestamp |
| Cast | `CAST('10:30:00' AS TIME)` | Parse ISO string to TIME |
| `TO_DATE` | `TO_DATE(string)` | Parse ISO string to DATE |
| `TO_TIMESTAMP` | `TO_TIMESTAMP(string)` | Parse ISO string to timestamp |

### Date/Time Construction from Format Strings (EXPERIMENTAL)

Parsing with an explicit format string relies on a datetime format model whose token
vocabulary differs across engines (Oracle/`TO_CHAR`-style, `strftime` `%`-codes, and
Java/LDML patterns are all in use). 

For portability, we are looking to restrict the `format` argument to
the portable core format tokens defined in Date Formatting below, and prefer the
single-argument, ISO-8601 forms above where possible. 

**Since, this differs so widely across databases, consider this experimental for now.**  

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `TO_DATE` | `TO_DATE(string, format)` | Parse string to date |
| `TO_TIMESTAMP` | `TO_TIMESTAMP(string, format)` | Parse string to timestamp |

### Date Formatting (EXPERIMENTAL)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `TO_CHAR` | `TO_CHAR(date_expr, format)` | Format date as string |

Ossie defines a portable core of format tokens: the tokens that can be expressed in
every major engine's datetime format model. 

This feature is experimental.  Implementations choosing to support these should support the following tokens for the 
`format` argument of `TO_CHAR`. The `strftime` and Java/LDML columns below are informative,
provided to aid translation.

| Token | Meaning | `strftime` (C / Python / BigQuery) | Java/LDML (Spark, .NET) |
| :---- | :---- | :---- | :---- |
| `YYYY` | 4-digit year | `%Y` | `yyyy` |
| `YY` | 2-digit year | `%y` | `yy` |
| `MM` | Month (01-12) | `%m` | `MM` |
| `MON` | Abbreviated month name | `%b` | `MMM` |
| `MONTH` | Full month name | `%B` | `MMMM` |
| `DD` | Day of month (01-31) | `%d` | `dd` |
| `DY` | Abbreviated day name | `%a` | `EEE` |
| `DAY` | Full day name | `%A` | `EEEE` |
| `HH24` | Hour (00-23) | `%H` | `HH` |
| `HH12` (`HH`) | Hour (01-12) | `%I` | `hh` |
| `MI` | Minute (00-59) | `%M` | `mm` |
| `SS` | Second (00-59) | `%S` | `ss` |
| `AM` / `PM` | Meridiem indicator | `%p` | `a` |

**Locale-dependent output.** The name tokens (`MON`, `MONTH`, `DY`, `DAY`, `AM`/`PM`)
render text whose language is governed by engine/session locale settings; the spelling
is not guaranteed identical across engines.

**Fractional seconds** are available everywhere but the token and precision differ
(Oracle `FF1`–`FF9`, `strftime` `%f`, Java `S`…`SSSSSS`); treat sub-second formatting as
a dialect extension.
---

## String Functions

### String Manipulation (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `CONCAT` | `CONCAT(str1, str2, ...)` | Concatenate strings |
| `||` | `str1 || str2` | Concatenation operator |
| `LENGTH` | `LENGTH(str)` | String length in characters |
| `LOWER` | `LOWER(str)` | Convert to lowercase |
| `UPPER` | `UPPER(str)` | Convert to uppercase |
| `TRIM` | `TRIM(str)` | Remove leading/trailing whitespace |
| `LTRIM` | `LTRIM(str)` | Remove leading whitespace |
| `RTRIM` | `RTRIM(str)` | Remove trailing whitespace |
| `LEFT` | `LEFT(str, n)` | First n characters |
| `RIGHT` | `RIGHT(str, n)` | Last n characters |
| `SUBSTRING` | `SUBSTRING(str, start, length)` | Extract substring |
| `REPLACE` | `REPLACE(str, from, to)` | Replace occurrences |
| `SPLIT_PART` | `SPLIT_PART(str, delimiter, part)` | Extract part by delimiter |

### String Search (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `POSITION` | `POSITION(substr IN str)` | Position of substring (1-based) |
| `CHARINDEX` | `CHARINDEX(substr, str)` | Alias for POSITION |
| `CONTAINS` | `CONTAINS(str, substr)` | Returns TRUE if contains |
| `STARTSWITH` | `STARTSWITH(str, prefix)` | Returns TRUE if starts with |
| `ENDSWITH` | `ENDSWITH(str, suffix)` | Returns TRUE if ends with |

### Pattern Matching (REQUIRED)

| Pattern | Syntax | Description |
| :---- | :---- | :---- |
| `LIKE` | `str LIKE pattern` | Case-sensitive pattern match |
| `ILIKE` | `str ILIKE pattern` | Case-insensitive pattern match |
| `REGEXP_LIKE` | `REGEXP_LIKE(str, pattern)` | Regular expression match |

Pattern wildcards for `LIKE`:

- `%` \- Match any sequence of characters  
- `_` \- Match any single character

### Regular Expressions (RECOMMENDED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `REGEXP_EXTRACT` | `REGEXP_EXTRACT(str, pattern)` | Extract first match |
| `REGEXP_REPLACE` | `REGEXP_REPLACE(str, pattern, replacement)` | Replace matches |
| `REGEXP_COUNT` | `REGEXP_COUNT(str, pattern)` | Count matches |

---

## Mathematical Functions

### Basic Math (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `ABS` | `ABS(x)` | Absolute value |
| `ROUND` | `ROUND(x, d)` | Round to d decimal places |
| `FLOOR` | `FLOOR(x)` | Round down to integer |
| `CEIL` / `CEILING` | `CEIL(x)` | Round up to integer |
| `TRUNC` / `TRUNCATE` | `TRUNC(x, d)` | Truncate to d decimal places |
| `MOD` | `MOD(x, y)` | Modulo (remainder) |
| `SIGN` | `SIGN(x)` | Sign (-1, 0, or 1\) |

### Advanced Math (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `POWER` | `POWER(x, y)` | x raised to power y |
| `SQRT` | `SQRT(x)` | Square root |
| `EXP` | `EXP(x)` | e raised to power x |
| `LN` | `LN(x)` | Natural logarithm |
| `LOG` | `LOG(base, x)` | Logarithm with specified base |
| `LOG10` | `LOG10(x)` | Base-10 logarithm |

### Trigonometric (RECOMMENDED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `SIN` | `SIN(x)` | Sine (x in radians) |
| `COS` | `COS(x)` | Cosine |
| `TAN` | `TAN(x)` | Tangent |
| `ASIN` | `ASIN(x)` | Arc sine |
| `ACOS` | `ACOS(x)` | Arc cosine |
| `ATAN` | `ATAN(x)` | Arc tangent |
| `ATAN2` | `ATAN2(y, x)` | Arc tangent of y/x |
| `RADIANS` | `RADIANS(degrees)` | Convert degrees to radians |
| `DEGREES` | `DEGREES(radians)` | Convert radians to degrees |
| `PI` | `PI()` | Value of π |

### Comparison Functions (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `GREATEST` | `GREATEST(x, y, ...)` | Maximum of arguments |
| `LEAST` | `LEAST(x, y, ...)` | Minimum of arguments |

---

## Conditional Functions

### CASE Expression (REQUIRED)

```sql
-- Searched CASE
CASE
  WHEN condition1 THEN result1
  WHEN condition2 THEN result2
  ELSE default_result
END

-- Simple CASE
CASE expression
  WHEN value1 THEN result1
  WHEN value2 THEN result2
  ELSE default_result
END
```

### Conditional Functions (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `IF` | `IF(condition, true_result, false_result)` | Ternary conditional |
| `IFF` | `IFF(condition, true_result, false_result)` | Alias for IF |
| `NULLIF` | `NULLIF(expr1, expr2)` | Returns NULL if equal |
| `COALESCE` | `COALESCE(expr1, expr2, ...)` | First non-null value |
| `IFNULL` | `IFNULL(expr, default)` | Alias for COALESCE with 2 args |
| `NVL` | `NVL(expr, default)` | Alias for COALESCE with 2 args |
| `NVL2` | `NVL2(expr, not_null_result, null_result)` | Different results for null/not-null |
| `ZeroIfNull` | `ZEROIFNULL(expr)` | Returns 0 if null |
| `NullIfZero` | `NULLIFZERO(expr)` | Returns NULL if zero |

### Boolean Functions (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `BOOLEAN` | `TRUE`, `FALSE` | Boolean literals |
| `NOT` | `NOT expr` | Logical negation |
| `AND` | `expr1 AND expr2` | Logical AND |
| `OR` | `expr1 OR expr2` | Logical OR |

---

## Window Functions

Window functions operate over a window frame defined by `OVER()`. This should act consistently with window functions in ANSI SQL.
When, adding the query interface, window functions will be subject to where they are allowed.

### Syntax

```sql
function_name(args) OVER (
  [PARTITION BY partition_expr, ...]
  [ORDER BY order_expr [ASC|DESC], ...]
  [frame_clause]
)
```

Frame clause options:

- `ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`  
- `ROWS BETWEEN n PRECEDING AND n FOLLOWING`  
- `RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW`

### Ranking Functions (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `ROW_NUMBER` | `ROW_NUMBER() OVER (...)` | Sequential row number |
| `RANK` | `RANK() OVER (...)` | Rank with gaps for ties |
| `DENSE_RANK` | `DENSE_RANK() OVER (...)` | Rank without gaps |
| `NTILE` | `NTILE(n) OVER (...)` | Divide into n buckets |
| `PERCENT_RANK` | `PERCENT_RANK() OVER (...)` | Relative rank (0-1) |
| `CUME_DIST` | `CUME_DIST() OVER (...)` | Cumulative distribution |

### Offset Functions (REQUIRED)

| Function | Syntax | Description |
| :---- | :---- | :---- |
| `LAG` | `LAG(expr, offset, default) OVER (...)` | Value from previous row |
| `LEAD` | `LEAD(expr, offset, default) OVER (...)` | Value from next row |
| `FIRST_VALUE` | `FIRST_VALUE(expr) OVER (...)` | First value in window |
| `LAST_VALUE` | `LAST_VALUE(expr) OVER (...)` | Last value in window |
| `NTH_VALUE` | `NTH_VALUE(expr, n) OVER (...)` | Nth value in window |

### Window Aggregations (REQUIRED)

All standard aggregation functions can be used as window functions:

```sql
-- Running total
SUM(amount) OVER (ORDER BY order_date)

-- Running average
AVG(amount) OVER (ORDER BY order_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)

-- Partition totals
SUM(amount) OVER (PARTITION BY region)

-- Percent of total
amount / SUM(amount) OVER () * 100
```

---

## Type Conversion Functions

### CAST (REQUIRED)

```sql
CAST(expression AS target_type)
```

Supported target types:

- `VARCHAR` / `STRING` \- Character string  
- `INTEGER` / `INT` / `BIGINT` \- Integer  
- `DECIMAL` / `NUMERIC` \- Fixed-point decimal  
- `FLOAT` / `DOUBLE` \- Floating-point  
- `BOOLEAN`  \- Boolean  
- `DATE` \- Date  
- `TIMESTAMP` \- Timestamp  
- `TIME` \- Time

### TRY\_CAST (RECOMMENDED)

```sql
TRY_CAST(expression AS target_type)  -- Returns NULL on failure
```
---

### Null-Safe Comparison

```sql
-- Standard comparison (returns NULL if either side is NULL)
a = b

-- Null-safe comparison (treats NULLs as equal)
a IS NOT DISTINCT FROM b    -- TRUE if both are NULL
a IS DISTINCT FROM b        -- TRUE if one is NULL and other isn't
```

---

## Dialect Extensions

Ossie implementations MAY support additional functions through dialect-specific extensions. When using dialect extensions, the expression must specify the dialect.

The Ossie dialect should always be supported.  Other dialects MAY be ignored.  There is no guarantee that all different dialects for an expression will act the same, so implementations should be consistent with their dialect handling.  This means that if an Ossie model has an expression written in two dialects, the implementation should deterministically choose which dialect to use.  

### Declaring Dialect-Specific Expressions

```
expression:
  dialects:
    - dialect: ANSI_SQL
      expression: DATE_TRUNC('month', order_date)
    - dialect: SNOWFLAKE
      expression: DATE_TRUNC('month', order_date)
    - dialect: BIGQUERY
      expression: DATE_TRUNC(order_date, MONTH)
```

### Common Dialect Variations

| Function | ANSI\_SQL | Snowflake | BigQuery | Databricks | PostgreSQL |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Date truncation | `DATE_TRUNC('month', d)` | `DATE_TRUNC('month', d)` | `DATE_TRUNC(d, MONTH)` | `DATE_TRUNC('month', d)` | `DATE_TRUNC('month', d)` |
| Date add | `DATEADD(day, 7, d)` | `DATEADD(day, 7, d)` | `DATE_ADD(d, INTERVAL 7 DAY)` | `DATE_ADD(d, 7)` | `d + INTERVAL '7 days'` |
| String concat | `CONCAT(a, b)` | `CONCAT(a, b)` | `CONCAT(a, b)` | `CONCAT(a, b)` | `a || b` |
| Null coalesce | `COALESCE(a, b)` | `COALESCE(a, b)` or `NVL(a, b)` | `COALESCE(a, b)` or `IFNULL(a, b)` | `COALESCE(a, b)` | `COALESCE(a, b)` |
| Current timestamp | `CURRENT_TIMESTAMP` | `CURRENT_TIMESTAMP()` | `CURRENT_TIMESTAMP()` | `CURRENT_TIMESTAMP()` | `CURRENT_TIMESTAMP` |
| Substring | `SUBSTRING(s, start, len)` | `SUBSTR(s, start, len)` | `SUBSTR(s, start, len)` | `SUBSTRING(s, start, len)` | `SUBSTRING(s, start, len)` |

### 

### Dialect-Specific Extensions

Vendors may expose their own feature through extensions, however the default for Ossie should be to pass unknown values through.:  
---

## Cross-Reference: Tool Mappings

This section maps Ossie standard functions to their equivalents in popular BI tools.

### Aggregation Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX |
| :---- | :---- | :---- | :---- |
| `SUM(x)` | `SUM(x)` | `SUM(X)` | `SUM(x)` |
| `COUNT(x)` | `COUNT(x)` | `COUNT(X)` | `COUNT(x)` |
| `COUNT(DISTINCT x)` | `COUNTD(x)` | `COUNT_DISTINCT(X)` | `DISTINCTCOUNT(x)` |
| `AVG(x)` | `AVG(x)` | `AVG(X)` | `AVERAGE(x)` |
| `MIN(x)` | `MIN(x)` | `MIN(X)` | `MIN(x)` |
| `MAX(x)` | `MAX(x)` | `MAX(X)` | `MAX(x)` |
| `STDDEV(x)` | `STDEV(x)` | `STDDEV(X)` | `STDEV.S(x)` |
| `STDDEV_POP(x)` | `STDEVP(x)` | `STDDEV(X)` | `STDEV.P(x)` |
| `VARIANCE(x)` | `VAR(x)` | `VARIANCE(X)` | `VAR.S(x)` |
| `MEDIAN(x)` | `MEDIAN(x)` | `MEDIAN(X)` | `MEDIAN(x)` |
| `PERCENTILE_CONT(x, 0.75)` | `PERCENTILE(x, 0.75)` | `PERCENTILE(X, 75)` | `PERCENTILE.INC(x, 0.75)` |

### Date Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX |
| :---- | :---- | :---- | :---- |
| `YEAR(d)` | `YEAR(d)` | `YEAR(Date)` | `YEAR(d)` |
| `MONTH(d)` | `MONTH(d)` | `MONTH(Date)` | `MONTH(d)` |
| `DAY(d)` | `DAY(d)` | `DAY(Date)` | `DAY(d)` |
| `DATE_TRUNC('month', d)` | `DATETRUNC('month', d)` | `TODATE(d, "YYYYMM01", "YYYYMMDD")` | `DATE(YEAR(d), MONTH(d), 1)` |
| `DATEADD(day, n, d)` | `DATEADD('day', n, d)` | `DATE_ADD(d, n)` (days only) | `DATE(d) + n` or `DATEADD(d, n, DAY)` |
| `DATEDIFF(day, d1, d2)` | `DATEDIFF('day', d1, d2)` | `DATE_DIFF(d1, d2)` | `DATEDIFF(d1, d2, DAY)` |
| `CURRENT_DATE` | `TODAY()` | `TODAY()` | `TODAY()` |

### String Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX |
| :---- | :---- | :---- | :---- |
| `CONCAT(a, b)` | `a + b` | `CONCAT(X, Y)` | `CONCATENATE(a, b)` or `a & b` |
| `LENGTH(s)` | `LEN(s)` | `LENGTH(X)` | `LEN(s)` |
| `LOWER(s)` | `LOWER(s)` | `LOWER(X)` | `LOWER(s)` |
| `UPPER(s)` | `UPPER(s)` | `UPPER(X)` | `UPPER(s)` |
| `TRIM(s)` | `TRIM(s)` | `TRIM(X)` | `TRIM(s)` |
| `LEFT(s, n)` | `LEFT(s, n)` | `LEFT_TEXT(X, n)` | `LEFT(s, n)` |
| `RIGHT(s, n)` | `RIGHT(s, n)` | `RIGHT_TEXT(X, n)` | `RIGHT(s, n)` |
| `SUBSTRING(s, start, len)` | `MID(s, start, len)` | `SUBSTR(X, start, len)` | `MID(s, start, len)` |
| `REPLACE(s, from, to)` | `REPLACE(s, from, to)` | `REPLACE(X, Y, Z)` | `SUBSTITUTE(s, from, to)` |
| `CONTAINS(s, sub)` | `CONTAINS(s, sub)` | `CONTAINS_TEXT(X, text)` | `CONTAINSSTRING(s, sub)` |

### Conditional Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX |
| :---- | :---- | :---- | :---- |
| `CASE WHEN...` | `CASE WHEN...` or `IF...` | `CASE WHEN...` | `SWITCH(TRUE(), ...)` |
| `IF(cond, t, f)` | `IF cond THEN t ELSE f END` | N/A (use CASE) | `IF(cond, t, f)` |
| `COALESCE(a, b)` | `IFNULL(a, b)` or `ZN(a)` | `COALESCE(...)` | `COALESCE(a, b)` |
| `NULLIF(a, b)` | `IF a = b THEN NULL ELSE a END` | N/A | `IF(a = b, BLANK(), a)` |

### Window Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX |
| :---- | :---- | :---- | :---- |
| `ROW_NUMBER() OVER(...)` | `INDEX()` | N/A | `RANKX(...)` with DENSE |
| `RANK() OVER(...)` | `RANK(expr)` | N/A | `RANKX(...)` |
| `SUM(...) OVER(PARTITION BY...)` | `{FIXED [...]: SUM(...)}` | N/A (blending only) | Context-dependent |
| `LAG(x, 1) OVER(ORDER BY...)` | `LOOKUP(x, -1)` | N/A | `CALCULATE(x, PREVIOUSDAY(...))` |
| `RUNNING_SUM(...)` | `RUNNING_SUM(SUM(...))` | N/A | `CALCULATE(SUM(...), FILTER(...))` |

---

## Compliance Levels

### MUST Support (Core)

Implementations MUST support all functions marked as **REQUIRED** in this specification. These represent the minimum portable expression language.

### SHOULD Support (Recommended)

Implementations SHOULD support functions marked as **RECOMMENDED**. These are common analytical functions that may not be available in all databases.

### MAY Support (Extensions)

Implementations MAY support additional functions through dialect extensions. These should be documented as dialect-specific.

---

## Version History

| Version   | Date       | Changes |
|:----------|:-----------| :---- |
| 0.2.0.dev | 2026-07-15 | Initial draft |

---

## References

- [SQL:2003 Standard](https://www.iso.org/standard/34132.html) (ISO/IEC 9075-2:2003)  
- [Tableau Functions Reference](https://help.tableau.com/current/pro/desktop/en-us/functions.htm)  
- [Looker Studio Function List](https://support.google.com/looker-studio/table/6379764)  
- [DAX Function Reference](https://learn.microsoft.com/en-us/dax/dax-function-reference)  
- [Snowflake SQL Reference](https://docs.snowflake.com/en/sql-reference-functions)  
- [BigQuery Standard SQL Reference](https://cloud.google.com/bigquery/docs/reference/standard-sql/functions-and-operators)  
- [Databricks SQL Functions](https://docs.databricks.com/sql/language-manual/sql-ref-functions.html)  
- [PostgreSQL Functions](https://www.postgresql.org/docs/current/functions.html)
