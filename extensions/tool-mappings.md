# Dialect and Tool Mappings

This document contains non-normative dialect-specific examples and BI-tool function mappings. These tables are references, not requirements for the portable core expression language.

### Common Dialect Variations

| Function | ANSI\_SQL | Snowflake | BigQuery | Databricks | PostgreSQL |
| :---- | :---- | :---- | :---- | :---- | :---- |
| Date truncation | `DATE_TRUNC('month', d)` | `DATE_TRUNC('month', d)` | `DATE_TRUNC(d, MONTH)` | `DATE_TRUNC('month', d)` | `DATE_TRUNC('month', d)` |
| Date add | `DATEADD(day, 7, d)` | `DATEADD(day, 7, d)` | `DATE_ADD(d, INTERVAL 7 DAY)` | `DATE_ADD(d, 7)` | `d + INTERVAL '7 days'` |
| String concat | `CONCAT(a, b)` | `CONCAT(a, b)` | `CONCAT(a, b)` | `CONCAT(a, b)` | `a \|\| b` |
| Null coalesce | `COALESCE(a, b)` | `COALESCE(a, b)` or `NVL(a, b)` | `COALESCE(a, b)` or `IFNULL(a, b)` | `COALESCE(a, b)` | `COALESCE(a, b)` |
| Current timestamp | `CURRENT_TIMESTAMP` | `CURRENT_TIMESTAMP()` | `CURRENT_TIMESTAMP()` | `CURRENT_TIMESTAMP()` | `CURRENT_TIMESTAMP` |
| Substring | `SUBSTRING(s, start, len)` | `SUBSTR(s, start, len)` | `SUBSTR(s, start, len)` | `SUBSTRING(s, start, len)` | `SUBSTRING(s, start, len)` |

### Dialect-Specific Extensions

Vendors may expose their own features through extensions. By default, Ossie should pass unknown values through.
---

## Cross-Reference: Tool Mappings

This section maps Ossie standard functions to their equivalents in popular BI tools.

### Aggregation Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX | Sigma |
| :---- | :---- | :---- | :---- | :---- |
| `SUM(x)` | `SUM(x)` | `SUM(X)` | `SUM(x)` | `Sum(x)` |
| `COUNT(x)` | `COUNT(x)` | `COUNT(X)` | `COUNT(x)` | `Count(x)` |
| `COUNT(DISTINCT x)` | `COUNTD(x)` | `COUNT_DISTINCT(X)` | `DISTINCTCOUNT(x)` | `CountDistinct(x)` |
| `AVG(x)` | `AVG(x)` | `AVG(X)` | `AVERAGE(x)` | `Avg(x)` |
| `MIN(x)` | `MIN(x)` | `MIN(X)` | `MIN(x)` | `Min(x)` |
| `MAX(x)` | `MAX(x)` | `MAX(X)` | `MAX(x)` | `Max(x)` |
| `STDDEV(x)` | `STDEV(x)` | `STDDEV(X)` | `STDEV.S(x)` | `StdDev(x)` |
| `STDDEV_POP(x)` | `STDEVP(x)` | `STDDEV(X)` | `STDEV.P(x)` | N/A |
| `VARIANCE(x)` | `VAR(x)` | `VARIANCE(X)` | `VAR.S(x)` | `Variance(x)` |
| `MEDIAN(x)` | `MEDIAN(x)` | `MEDIAN(X)` | `MEDIAN(x)` | `Median(x)` |
| `PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY x)` | `PERCENTILE(x, 0.75)` | `PERCENTILE(X, 75)` | `PERCENTILE.INC(x, 0.75)` | `Percentile(x, 0.75)` |

Two of the DAX mappings are not the same-named function, because DAX treats BLANK
differently from SQL's NULL:

- `COUNT(x)` maps to `COUNTA`, not `COUNT`. Both count non-blank values, but DAX
  `COUNT` documents `TRUE`/`FALSE` columns as unsupported, whereas SQL `COUNT(x)`
  counts non-NULL values of any type. `COUNTA` counts non-blank values of any type.
- `COUNT(DISTINCT x)` maps to `DISTINCTCOUNTNOBLANK`, not `DISTINCTCOUNT`. SQL
  excludes NULL from the distinct set, but DAX `DISTINCTCOUNT` counts BLANK as a
  distinct value, so it is off by one on any nullable column.

Two further differences have no faithful DAX equivalent and should be expected rather
than worked around:

- Over an empty set the SQL count functions return `0`, while the DAX ones return
  BLANK. This is intentional in DAX, since it lets a visual hide the empty row.
- `STDEV.S`, `STDEV.P`, `VAR.S` and `VAR.P` raise an error when fewer than two
  non-blank rows remain, where SQL yields NULL (sample) or `0` (population).

### Date Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX | Sigma |
| :---- | :---- | :---- | :---- | :---- |
| `YEAR(d)` | `YEAR(d)` | `YEAR(Date)` | `YEAR(d)` | `Year(d)` |
| `MONTH(d)` | `MONTH(d)` | `MONTH(Date)` | `MONTH(d)` | `Month(d)` |
| `DAY(d)` | `DAY(d)` | `DAY(Date)` | `DAY(d)` | `Day(d)` |
| `DATE_TRUNC('month', d)` | `DATETRUNC('month', d)` | `TODATE(d, "YYYYMM01", "YYYYMMDD")` | `DATE(YEAR(d), MONTH(d), 1)` | N/A — no direct equivalent; Sigma's UI-driven date bucketing is not addressable as a formula argument |
| `DATEADD(day, n, d)` | `DATEADD('day', n, d)` | `DATE_ADD(d, n)` (days only) | `DATE(d) + n` or `DATEADD(d, n, DAY)` | `DateAdd(d, n, "day")` |
| `DATEDIFF(day, d1, d2)` | `DATEDIFF('day', d1, d2)` | `DATE_DIFF(d1, d2)` | `DATEDIFF(d1, d2, DAY)` | `DateDiff(d1, d2, "day")` |
| `CURRENT_DATE` | `TODAY()` | `TODAY()` | `TODAY()` | `Today()` |

DAX also has a `DATEADD` function, but it is not the equivalent of the scalar SQL
`DATEADD`: it is a time-intelligence function that returns a *table* of shifted dates
for the current filter context, and it requires a marked date table with a contiguous
date range. For a row-level shift, add a number of days to the date directly, or use
`EDATE` to shift by whole months.

### String Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX | Sigma |
| :---- | :---- | :---- | :---- | :---- |
| `CONCAT(a, b)` | `a + b` | `CONCAT(X, Y)` | `CONCATENATE(a, b)` or `a & b` | `Concat(a, b)` or `a & b` |
| `LENGTH(s)` | `LEN(s)` | `LENGTH(X)` | `LEN(s)` | `Length(s)` |
| `LOWER(s)` | `LOWER(s)` | `LOWER(X)` | `LOWER(s)` | `Lower(s)` |
| `UPPER(s)` | `UPPER(s)` | `UPPER(X)` | `UPPER(s)` | `Upper(s)` |
| `TRIM(s)` | `TRIM(s)` | `TRIM(X)` | `TRIM(s)` | `Trim(s)` |
| `LEFT(s, n)` | `LEFT(s, n)` | `LEFT_TEXT(X, n)` | `LEFT(s, n)` | `Left(s, n)` |
| `RIGHT(s, n)` | `RIGHT(s, n)` | `RIGHT_TEXT(X, n)` | `RIGHT(s, n)` | `Right(s, n)` |
| `SUBSTRING(s, start, len)` | `MID(s, start, len)` | `SUBSTR(X, start, len)` | `MID(s, start, len)` | `Mid(s, start, len)` |
| `REPLACE(s, from, to)` | `REPLACE(s, from, to)` | `REPLACE(X, Y, Z)` | `SUBSTITUTE(s, from, to)` | `Replace(s, from, to)` |
| `CONTAINS(s, sub)` | `CONTAINS(s, sub)` | `CONTAINS_TEXT(X, text)` | `CONTAINSSTRING(s, sub)` | `Contains(s, sub)` |

### Conditional Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX | Sigma |
| :---- | :---- | :---- | :---- | :---- |
| `CASE WHEN...` | `CASE WHEN...` or `IF...` | `CASE WHEN...` | `SWITCH(TRUE(), ...)` | Only 3-argument `If(cond, t, f)`; no native multi-branch `CASE` |
| `IF(cond, t, f)` | `IF cond THEN t ELSE f END` | N/A (use CASE) | `IF(cond, t, f)` | `If(cond, t, f)` |
| `COALESCE(a, b)` | `IFNULL(a, b)` or `ZN(a)` | `COALESCE(...)` | `COALESCE(a, b)` | `IfNull(a, b)` (2-argument only) |
| `NULLIF(a, b)` | `IF a = b THEN NULL ELSE a END` | N/A | `IF(a = b, BLANK(), a)` | `If(a = b, Null(), a)` |

### Window Function Mapping

| Ossie Standard | Tableau | Looker Studio | DAX | Sigma |
| :---- | :---- | :---- | :---- | :---- |
| `ROW_NUMBER() OVER(...)` | `INDEX()` | N/A | `RANKX(...)` with DENSE | `RowNumber()` — but partition/order come from UI table-calculation configuration, not formula arguments, so it has no portable expression form |
| `RANK() OVER(...)` | `RANK(expr)` | N/A | `RANKX(...)` | `Rank()` — same UI-configuration caveat as `RowNumber()` |
| `SUM(...) OVER(PARTITION BY...)` | `{FIXED [...]: SUM(...)}` | N/A (blending only) | Context-dependent | `RunningSum(...)`/`RunningAvg(...)` — same UI-configuration caveat |
| `LAG(x, 1) OVER(ORDER BY...)` | `LOOKUP(x, -1)` | N/A | `CALCULATE(x, PREVIOUSDAY(...))` | `Lag(x, 1)` — same UI-configuration caveat |
| `RUNNING_SUM(...)` | `RUNNING_SUM(SUM(...))` | N/A | `CALCULATE(SUM(...), FILTER(...))` | `RunningSum(...)` — same UI-configuration caveat |

Sigma's table-calculation functions (`RowNumber`, `Rank`, `RunningSum`,
`RunningAvg`, `Lag`, `Lead`, etc.) rely on workbook UI configuration for
their partition and ordering context. That context cannot be recovered from
the formula text alone, so the [Sigma converter](https://github.com/apache/ossie-converters/tree/main/sigma)
preserves these formulas in the `SIGMA` dialect rather than translating them
to ANSI SQL. See the [converter limitations](https://github.com/apache/ossie-converters/blob/main/sigma/LIMITATIONS.md).

---

## Database Function Support

**Database Support:**

| Function | Snowflake | BigQuery | Databricks | PostgreSQL |
| :---- | :---- | :---- | :---- | :---- |
| `APPROX_COUNT_DISTINCT` | ✅ | ✅ | ✅ | ❌ (extension) |
| `APPROX_PERCENTILE` | ✅ | ✅ `APPROX_QUANTILES` | ✅ | ❌ |

**Note**: BigQuery uses `APPROX_QUANTILES(expr, num_buckets)` which returns an array. To get a specific percentile: `APPROX_QUANTILES(amount, 100)[OFFSET(50)]` for median.

---
