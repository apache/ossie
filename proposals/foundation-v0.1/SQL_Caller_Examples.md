# SQL Interface: Caller's Perspective Examples

**Purpose:** Concrete examples showing what a SQL caller sees when querying an OSI
semantic model as a view. Demonstrates which patterns produce expected SQL behavior
and which produce surprises.

**Date:** 14 March 2026
**Related:** [OSI_Proposal_Resettable_Filters_take2_review.md](./OSI_Proposal_Resettable_Filters_take2_review.md) §9–10

---

## Data

```
orders table:
+--------+-------+----------+--------+
| region | color | category | amount |
+--------+-------+----------+--------+
| West   | Red   | Widgets  |    100 |
| West   | Blue  | Widgets  |    200 |
| West   | Red   | Gadgets  |     50 |
| East   | Red   | Widgets  |    150 |
| East   | Blue  | Gadgets  |     75 |
+--------+-------+----------+--------+
Total: 575
```

---

## Model Definitions

The caller sees these as columns of a view. They do NOT see the YAML definitions —
they only see column names and values. The definitions are shown here for the
reviewer's reference.

### revenue — Standard metric (Tier 1: Single SELECT)

```yaml
- name: revenue
  expression: SUM(orders.amount)
  # grain: QUERY (default) — uses the query's GROUP BY
  # filter: RELATIVE (default) — inherits the query's WHERE unchanged
```

**SQL equivalent:** `SUM(amount)` in a `SELECT ... GROUP BY` — the simplest case.

---

### red_revenue — Conditional aggregation (Tier 1: CASE WHEN)

```yaml
- name: red_revenue
  expression: SUM(orders.amount)
  # grain: QUERY (default) — same GROUP BY as revenue
  filter:
    include: ["color = 'Red'"]
    # mode: RELATIVE (default) — inherits query WHERE
    # THEN adds its own filter: color = 'Red'
    # effective WHERE = query_WHERE AND color = 'Red'
```

**SQL equivalent:** `SUM(CASE WHEN color = 'Red' THEN amount END)` — standard
conditional aggregation. Always stricter than the query's WHERE.

---

### grand_total — Pre-computed constant (Tier 2: Uncorrelated subquery)

```yaml
- name: grand_total
  expression: SUM(orders.amount)
  grain:
    mode: FIXED
    include: []           # no dimensions — scalar grand total
  filter:
    mode: FIXED           # ignores ALL query filters
    # no include — completely empty filter context
    # effective WHERE = (none)
    # effective GROUP BY = (none)
```

**SQL equivalent:** `(SELECT SUM(amount) FROM orders)` — an uncorrelated subquery.
Ignores both query dimensions and query filters. Always returns 575.

---

### category_total — Correlated CTE (Tier 2: CTE with query's WHERE)

```yaml
- name: category_total
  expression: SUM(orders.amount)
  grain:
    mode: FIXED
    include: [category]   # always grouped by category, regardless of query dims
  # filter: RELATIVE (default) — inherits the query's WHERE unchanged
  # effective WHERE = query_WHERE (all filters flow through)
  # effective GROUP BY = [category] (fixed)
```

**SQL equivalent:**
```sql
WITH cat_totals AS (
  SELECT category, SUM(amount) AS category_total
  FROM orders
  WHERE <same as query WHERE>   -- all query filters apply
  GROUP BY category
)
```

A correlated CTE — receives the query's WHERE, but has its own fixed GROUP BY.

---

### category_total_no_color — CTE with partial WHERE (Tier 2: FIXED grain + filter exclude)

```yaml
- name: category_total_no_color
  expression: SUM(orders.amount)
  grain:
    mode: FIXED
    include: [category]   # always grouped by category
  filter:
    exclude: [color]      # removes any query filter clause referencing color
    # mode: RELATIVE (default) — starts from query WHERE, then excludes
    # effective WHERE = query_WHERE minus color clauses
    # effective GROUP BY = [category] (fixed)
```

**SQL equivalent:**
```sql
WITH cat_totals_no_color AS (
  SELECT category, SUM(amount) AS category_total_no_color
  FROM orders
  WHERE <query WHERE minus color clauses>   -- color filter stripped
  GROUP BY category
)
```

A CTE with its own modified WHERE. Different grain from the main query (FIXED
[category] vs query dims), so the caller perceives it as a separate computation.

---

### parent_total — Coupled exclude (Tier 2: CTE without the excluded field)

```yaml
- name: parent_total
  expression: SUM(orders.amount)
  grain:
    exclude: [color]      # removes color from query dims
    # mode: RELATIVE (default)
    # effective GROUP BY = query_dims - {color}
  filter:
    exclude: [color]      # removes color filter clauses
    # mode: RELATIVE (default)
    # effective WHERE = query_WHERE minus color clauses
```

**SQL equivalent:**
```sql
WITH parent_totals AS (
  SELECT <query_dims minus color>, SUM(amount) AS parent_total
  FROM orders
  WHERE <query WHERE minus color clauses>
  GROUP BY <query_dims minus color>
)
```

Color is absent from both GROUP BY and WHERE — as if it doesn't exist for this
metric. The grain difference (coarser than query) signals a separate CTE.

---

### parent_total_window — Grain-only exclude (Tier 2: Window function)

```yaml
- name: parent_total_window
  expression: SUM(orders.amount)
  grain:
    exclude: [color]      # removes color from query dims
    # mode: RELATIVE (default)
    # effective GROUP BY = query_dims - {color}
  # filter: RELATIVE (default) — inherits query WHERE unchanged
  # effective WHERE = query_WHERE (all filters, including color, flow through)
```

**SQL equivalent:** `SUM(amount) OVER (PARTITION BY <query_dims minus color>)` —
a window function. Same filtered data as the main query, but coarser grouping.

---

### ⚠️ unfiltered_revenue — Filter exclude at QUERY grain (NON-SQL-SAFE)

```yaml
- name: unfiltered_revenue
  expression: SUM(orders.amount)
  # grain: QUERY (default) — SAME GROUP BY as the main query
  filter:
    exclude: [color]      # removes color filter clauses
    # mode: RELATIVE (default)
    # effective WHERE = query_WHERE minus color clauses
    # effective GROUP BY = query_dims (same as main query!)
```

**SQL equivalent:** There is no single-SELECT equivalent. Requires two CTEs:

```sql
WITH main AS (
  SELECT <query_dims>, SUM(amount) AS revenue
  FROM orders WHERE <full query WHERE>
  GROUP BY <query_dims>
),
unfiltered AS (
  SELECT <query_dims>, SUM(amount) AS unfiltered_revenue
  FROM orders WHERE <query WHERE minus color clauses>
  GROUP BY <query_dims>           -- SAME GROUP BY as main!
)
SELECT m.*, u.unfiltered_revenue
FROM main m LEFT JOIN unfiltered u ON <query_dims match>
```

Same GROUP BY, different WHERE. This is the non-SQL-safe pattern — two columns
that look like they belong to the same SELECT but see different data.

---

## Query Examples: Safe Patterns (caller is not surprised)

### Q1. Baseline — just revenue

```sql
SELECT region, color, revenue FROM model
```

| region | color | revenue |
|--------|-------|---------|
| West   | Red   |     150 |
| West   | Blue  |     200 |
| East   | Red   |     150 |
| East   | Blue  |      75 |

**Caller:** *"Standard grouped query."* ✓

---

### Q2. Add a filter — everything changes

```sql
SELECT region, color, revenue FROM model WHERE color = 'Red'
```

| region | color | revenue |
|--------|-------|---------|
| West   | Red   |     150 |
| East   | Red   |     150 |

**Caller:** *"Fewer rows, only Red. Normal."* ✓

---

### Q3. grand_total — pre-computed constant

```sql
SELECT region, color, revenue, grand_total FROM model
```

| region | color | revenue | grand_total |
|--------|-------|---------|-------------|
| West   | Red   |     150 |         575 |
| West   | Blue  |     200 |         575 |
| East   | Red   |     150 |         575 |
| East   | Blue  |      75 |         575 |

**Caller:** *"grand_total is 575 in every row. It's a constant — like a scalar
subquery."* ✓

---

### Q4. Filter with grand_total — constant stays constant

```sql
SELECT region, color, revenue, grand_total FROM model WHERE color = 'Red'
```

| region | color | revenue | grand_total |
|--------|-------|---------|-------------|
| West   | Red   |     150 |         575 |
| East   | Red   |     150 |         575 |

**Caller:** *"Revenue changed, grand_total didn't. But grand_total was 575 in
every row before — it's obviously a pre-computed constant. My filter just hid
rows. The constant stayed constant."*

**Why not surprising:** grand_total's grain (empty) differs from the query grain
(region, color). The caller already saw it was a replicated scalar. **Filter: FIXED
means it ignores all filters — like an uncorrelated subquery.** ✓

---

### Q5. category_total — correlated CTE responds to all filters

```sql
SELECT region, color, category, revenue, category_total FROM model
```

| region | color | category | revenue | category_total |
|--------|-------|----------|---------|----------------|
| West   | Red   | Widgets  |     100 |            450 |
| West   | Blue  | Widgets  |     200 |            450 |
| West   | Red   | Gadgets  |      50 |            125 |
| East   | Red   | Widgets  |     150 |            450 |
| East   | Blue  | Gadgets  |      75 |            125 |

**Caller:** *"category_total is the same within each category — 450 for Widgets,
125 for Gadgets. It's a per-category subtotal."*

```sql
SELECT region, color, category, revenue, category_total FROM model WHERE color = 'Red'
```

| region | color | category | revenue | category_total |
|--------|-------|----------|---------|----------------|
| West   | Red   | Widgets  |     100 |            250 |
| West   | Red   | Gadgets  |      50 |             50 |
| East   | Red   | Widgets  |     150 |            250 |

**Caller:** *"Both revenue AND category_total changed — the filter applies to
everything. Widgets went from 450 to 250 because only Red is counted now. Every
column responded to my filter. Normal."*

**Why not surprising:** category_total uses **filter: RELATIVE (default)** — it
inherits the full query WHERE. The color filter flows through. Adding or removing
any filter changes all metrics uniformly. ✓

---

### Q6. category_total_no_color — CTE with partial WHERE (FIXED grain + filter exclude)

```sql
SELECT region, color, category, revenue, category_total_no_color FROM model
```

| region | color | category | revenue | category_total_no_color |
|--------|-------|----------|---------|-------------------------|
| West   | Red   | Widgets  |     100 |                     450 |
| West   | Blue  | Widgets  |     200 |                     450 |
| West   | Red   | Gadgets  |      50 |                     125 |
| East   | Red   | Widgets  |     150 |                     450 |
| East   | Blue  | Gadgets  |      75 |                     125 |

**Caller:** *"category_total_no_color looks the same as category_total when
there's no filter. Both are per-category."*

```sql
SELECT region, color, category, revenue, category_total_no_color FROM model
WHERE color = 'Red'
```

| region | color | category | revenue | category_total_no_color |
|--------|-------|----------|---------|-------------------------|
| West   | Red   | Widgets  |     100 |                     450 |
| West   | Red   | Gadgets  |      50 |                     125 |
| East   | Red   | Widgets  |     150 |                     450 |

**Caller:** *"Revenue changed (only Red). category_total_no_color stayed at 450
for Widgets. So this column doesn't respond to my color filter. But it's at a
different grain (per-category, not per-region-color) — it's clearly a separate
computation. Like a CTE that doesn't include color in its WHERE."*

```sql
SELECT region, color, category, revenue, category_total_no_color FROM model
WHERE color = 'Red' AND region = 'West'
```

| region | color | category | revenue | category_total_no_color |
|--------|-------|----------|---------|-------------------------|
| West   | Red   | Widgets  |     100 |                     350 |
| West   | Red   | Gadgets  |      50 |                      50 |

**Caller:** *"The region filter changed BOTH columns. The color filter only changed
revenue. Consistent with 'this CTE gets region filters but not color filters.'
It's at a different grain, so I accept it's a separate computation with its own
rules."*

**Why not surprising:** FIXED [category] grain signals a separate CTE.
**Filter: exclude [color]** means the CTE doesn't receive color filters — but the
different grain is what makes this acceptable. The caller already sees it as a
separate computation. ✓

---

### Q7. parent_total — coupled exclude (grain + filter exclude, same field)

```sql
SELECT region, color, revenue, parent_total FROM model
```

| region | color | revenue | parent_total |
|--------|-------|---------|--------------|
| West   | Red   |     150 |          350 |
| West   | Blue  |     200 |          350 |
| East   | Red   |     150 |          225 |
| East   | Blue  |      75 |          225 |

**Caller:** *"parent_total is 350 for both Red and Blue in West. It's a
region-level total — doesn't vary by color."*

```sql
SELECT region, color, revenue, parent_total FROM model WHERE color = 'Red'
```

| region | color | revenue | parent_total |
|--------|-------|---------|--------------|
| West   | Red   |     150 |          350 |
| East   | Red   |     150 |          225 |

**Caller:** *"Revenue changed. parent_total didn't. But I already knew parent_total
was a region-level value (same for Red and Blue). Filtering to Red just hid the
Blue rows. The region totals are unchanged."*

```sql
SELECT region, color, revenue, parent_total FROM model WHERE region = 'West'
```

| region | color | revenue | parent_total |
|--------|-------|---------|--------------|
| West   | Red   |     150 |          350 |
| West   | Blue  |     200 |          350 |

**Caller:** *"The region filter changed both. Only color filters are ignored by
parent_total. Consistent."*

**Why not surprising:** The coarser grain (region only, not region×color) means
parent_total is **replicated** across color rows. The caller saw identical values
for Red and Blue in Q7-unfiltered — advance notice that color doesn't affect this
column. **Filter: exclude [color]** removes color filters; **grain: exclude
[color]** removes color from GROUP BY. Color doesn't exist for this metric. ✓

---

### Q8. parent_total_window — grain exclude only (window function)

```sql
SELECT region, color, revenue, parent_total_window FROM model
```

| region | color | revenue | parent_total_window |
|--------|-------|---------|---------------------|
| West   | Red   |     150 |                 350 |
| West   | Blue  |     200 |                 350 |
| East   | Red   |     150 |                 225 |
| East   | Blue  |      75 |                 225 |

**Caller:** *"Same as parent_total — region-level totals."*

```sql
SELECT region, color, revenue, parent_total_window FROM model WHERE color = 'Red'
```

| region | color | revenue | parent_total_window |
|--------|-------|---------|---------------------|
| West   | Red   |     150 |                 150 |
| East   | Red   |     150 |                 150 |

**Caller:** *"Revenue changed AND parent_total_window changed! Both responded to
the color filter. parent_total_window went from 350 to 150 for West because it now
only counts Red data. But it's still a region-level total (same grain as before).
This is a window function — `SUM(amount) OVER (PARTITION BY region)` on the
filtered data."*

**Why not surprising:** No filter exclude — the color filter flows through to the
metric. The only difference from revenue is the grain (coarser), which is the window
function pattern. **Both columns see the same WHERE; they just GROUP BY differently.**
This is standard SQL window behavior. ✓

**Key contrast with Q7 (parent_total):**

| | parent_total (coupled exclude) | parent_total_window (grain exclude only) |
|---|---|---|
| `WHERE color = 'Red'` effect | **No change** — color excluded from filter | **Changes** — color filter flows through |
| SQL pattern | CTE that doesn't reference color | Window function on filtered data |
| Caller's model | "Color doesn't exist for this" | "Same data, coarser grouping" |

---

### Q9. red_revenue — conditional aggregation (filter include)

```sql
SELECT region, revenue, red_revenue FROM model
```

| region | revenue | red_revenue |
|--------|---------|-------------|
| West   |     350 |         150 |
| East   |     225 |         150 |

**Caller:** *"red_revenue is less than revenue. It has a built-in filter — like
`SUM(CASE WHEN color = 'Red' THEN amount END)`. Makes sense."*

```sql
SELECT region, revenue, red_revenue FROM model WHERE region = 'West'
```

| region | revenue | red_revenue |
|--------|---------|-------------|
| West   |     350 |         150 |

**Caller:** *"The region filter changed both. red_revenue is still per-region, just
with an extra filter. Normal."*

**Why not surprising:** Filter include is additive — it makes the filter STRICTER,
never weaker. It maps to conditional aggregation (CASE WHEN). Both columns share the
query WHERE; red_revenue just has an extra condition on top. Same grain, same data
direction (less rows, not more). ✓

---

## Query Examples: Non-Safe Pattern (caller IS surprised)

### Q10. unfiltered_revenue — filter exclude at QUERY grain

**Step 1: No filter**

```sql
SELECT region, revenue, unfiltered_revenue FROM model
```

| region | revenue | unfiltered_revenue |
|--------|---------|--------------------|
| West   |     350 |                350 |
| East   |     225 |                225 |

**Caller:** *"These two columns are identical. They must be the same thing."*

**Step 2: Add a color filter**

```sql
SELECT region, revenue, unfiltered_revenue FROM model WHERE color = 'Red'
```

| region | revenue | unfiltered_revenue |
|--------|---------|--------------------|
| West   |     150 |                350 |
| East   |     150 |                225 |

**Caller:** *"Wait — I added a filter and only ONE column changed? They were
identical before! revenue went from 350 to 150, but unfiltered_revenue stayed at
350. I wrote one query with one WHERE. How can two columns at the same grain
disagree?"*

**Step 3: Add another filter**

```sql
SELECT region, revenue, unfiltered_revenue FROM model
WHERE color = 'Red' AND region = 'West'
```

| region | revenue | unfiltered_revenue |
|--------|---------|--------------------|
| West   |     150 |                350 |

**Caller:** *"The region filter changed BOTH columns (East is gone). But the color
filter only changed revenue. My two filters have inconsistent effects across
columns — region affects everything, color affects only revenue. In SQL, a WHERE
clause is a WHERE clause. It doesn't selectively apply to some columns."*

**Step 4: A different color filter**

```sql
SELECT region, revenue, unfiltered_revenue FROM model WHERE color = 'Blue'
```

| region | revenue | unfiltered_revenue |
|--------|---------|--------------------|
| West   |     200 |                350 |
| East   |      75 |                225 |

**Caller:** *"I changed the color filter from Red to Blue. revenue changed (now
shows Blue data). unfiltered_revenue is STILL 350 and 225 — exactly the same as
with Red, and the same as with no filter at all. This column is completely immune
to color filters. But it's at the same grain as revenue. In standard SQL:*

```sql
SELECT region, SUM(amount), SUM(amount) FROM orders
WHERE color = 'Blue' GROUP BY region
```

*...would give me the same value for both columns. Always. You can't have two
`SUM(amount)` in the same SELECT return different values."*

---

## Why the Grain Difference Is the Signal

Compare Step 2 above (surprised) with Q7 (not surprised):

**Q7 — parent_total at coarser grain:**

Before filtering:

| region | color | revenue | parent_total |
|--------|-------|---------|--------------|
| West   | Red   |     150 |      **350** |
| West   | Blue  |     200 |      **350** |

After `WHERE color = 'Red'`:

| region | color | revenue | parent_total |
|--------|-------|---------|--------------|
| West   | Red   |     150 |      **350** |

The caller **already knew** parent_total was 350 for both Red and Blue. Filtering
to Red just hid the Blue row. The value didn't change because it was never
color-specific in the first place. The **replication** (same value for Red and Blue)
was advance notice.

**Q10 — unfiltered_revenue at same grain:**

Before filtering:

| region | revenue | unfiltered_revenue |
|--------|---------|--------------------|
| West   | **350** |            **350** |

After `WHERE color = 'Red'`:

| region | revenue | unfiltered_revenue |
|--------|---------|--------------------|
| West   | **150** |            **350** |

The caller saw **identical** values before filtering. There was **zero advance
notice** that these columns would diverge. The divergence is a surprise because
nothing in the unfiltered result distinguished them.

**The grain is what provides the advance notice:**

| Metric | Grain vs query | Replication visible? | Divergence on filter? | Surprising? |
|--------|---------------|---------------------|-----------------------|-------------|
| grand_total | Different (empty) | Yes — same value in every row | Expected | No |
| parent_total | Different (coarser) | Yes — same value within region | Expected | No |
| category_total_no_color | Different (FIXED) | Yes — same value within category | Expected | No |
| unfiltered_revenue | **Same** | **No — identical to revenue** | **Unexpected** | **Yes** |
