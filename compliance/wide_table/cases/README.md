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

# Wide table cases

What each case of the [wide table compliance tests](../README.md) shows. The
cases are grouped by the fixture that they load. Each case has:

- `Title`: what the case shows.
- `Question`: what the query asks, in English. It helps a reader; the query is
  the ground truth.
- `Behavior`: the rule under test, and why the result has its values.
- `Spec`: the sections of the
  [specification](../../../core-spec/wide_table_semantics.md) that define the
  result.

## retail

These cases load [`retail.sql`](../retail/retail.sql) and
[`retail_model.ossie.yaml`](../retail/retail_model.ossie.yaml).

### a01_shared_dimension

- Title: Two metrics share a dimension
- Question: What are the order and return amounts by customer region?
- Behavior: Each metric is a branch from its own dataset to customers. The keys
  are the regions of either branch; a region missing from a branch takes the
  empty-input value of SUM, NULL.
- Spec: 1.3, 5.4, 5.5

### a02_count_empty_input

- Title: A missing branch row takes the empty-input value of `COUNT(*)`
- Question: What is the order amount and the number of returns by customer
  region?
- Behavior: WEST has orders and no returns. Its return_count is the empty-input
  value of `COUNT(*)`, 0, not NULL.
- Spec: 5.2, 5.5, B.1

### a03_three_branches

- Title: Three branches give each key once
- Question: What are the order amount, return amount, and number of visits by
  customer region?
- Behavior: Each branch supplies some regions. NORTH is in the returns and
  visits branches but not in the orders branch, and appears once.
- Spec: 5.5, B.2

### a04_filter_on_dimension

- Title: A filter on the dimension dataset applies to every branch
- Question: What are the order and return amounts by customer region, outside
  EAST?
- Behavior: Both metrics reach customers, so the filter applies to both
  branches. The filter removes groups and adds none.
- Spec: 3.4, 5.3, 6, B.1

### a05_groups_from_orders

- Title: Groups come from the metric's dataset
- Question: What is the order amount by customer region?
- Behavior: One branch from orders. NORTH has no orders, so it is not a group.
- Spec: 5.4, 6.1

### a06_groups_from_returns

- Title: Another metric gives other groups
- Question: What is the return amount by customer region?
- Behavior: One branch from returns. WEST has no returns, so it is not a group.
- Spec: 5.4, 6.1

### a07_dimension_only

- Title: A query without metrics uses an anchor
- Question: Which customer regions are there?
- Behavior: The anchor is customers, the dataset of the only dimension. The
  result has its distinct regions.
- Spec: 3.2, 5.6, 6.1

### a08_model_metric_components

- Title: Each component of a model-scoped metric aggregates its own dataset
- Question: What is the order amount per customer?
- Behavior: The metric has a component on orders and one on customers. Each is
  a branch of its own, so the customer count includes C4, which has no orders:
  650 / 4.
- Spec: 5.2, 5.5

### a09_aliases

- Title: Aliases name the output columns
- Question: What is the order amount by customer region?
- Behavior: The object form gives each entry an alias, and the alias is its
  output name.
- Spec: 3.3, 3.6, 3.7

### a10_filter_without_dimension

- Title: A filter on a dataset the metric reaches
- Question: What is the order amount of customers in EAST?
- Behavior: The branch from orders joins customers for the filter. Without
  dimensions, the result has one row.
- Spec: 3.4, 5.4, B.5

### a11_filter_on_home_dataset

- Title: A filter on the metric's own dataset
- Question: What is the amount of orders over 100 by customer region?
- Behavior: The filter removes O1 and O3 before aggregation.
- Spec: 3.4, 5.4, B.5

### a12_anchor_from_dimensions

- Title: The anchor is the dimension dataset that reaches the others
- Question: Which orders over 100 are there, with the region of their customer?
- Behavior: orders reaches customers, and customers does not reach orders, so
  the anchor is orders.
- Spec: 5.6, B.5

### a13_filter_false_with_dimension

- Title: A filter that removes every row leaves no groups
- Question: What is the order amount by customer region, when no order is kept?
- Behavior: The branch has no rows, so there are no keys and no rows.
- Spec: 3.4, 5.5, B.5

### a14_filter_false_without_dimension

- Title: Without dimensions, the result has one row
- Question: What is the order amount, when no order is kept?
- Behavior: The only key is the empty tuple. The value is the empty-input value
  of SUM, NULL.
- Spec: 3.6, 5.5, B.5

### a15_unconnected_filter_false

- Title: A filter without a dataset applies to unconnected metrics
- Question: What are the order amount and the number of tickets, when no row is
  kept?
- Behavior: FALSE reads no dataset, so it needs no path. Each metric takes its
  empty-input value: NULL for SUM, 0 for `COUNT(*)`.
- Spec: 3.4, 5.3, B.4

### e01_invalid_query_unknown_key

- Title: An unsupported part is malformed
- Question: What is the order amount, limited to 10 rows?
- Behavior: limit is not a part of this version. The engine rejects it rather
  than ignore it.
- Spec: 3.5, 3.7, 8

### e02_invalid_query_null

- Title: A null value is malformed
- Question: What is the order amount, with a null filter?
- Behavior: The JSON binding treats null values as malformed.
- Spec: 3.7, 8

### e03_empty_aggregation_query

- Title: A query needs a dimension or a metric
- Question: (A query with only a filter.)
- Behavior: All parts are optional, but at least one dimension or metric is
  required.
- Spec: 3.2, 8

### e04_name_not_found

- Title: A reference must resolve
- Question: What is orders.no_such_metric?
- Behavior: orders has no field or metric of that name.
- Spec: 3.2, 8

### e05_metric_in_dimensions

- Title: A dimension must name a field
- Question: (A metric used as a dimension.)
- Behavior: orders.total_revenue is a metric.
- Spec: 3.2, 8

### e06_field_in_metrics

- Title: A metric entry must name a metric
- Question: (A field used as a metric.)
- Behavior: orders.amount is a field.
- Spec: 3.2, 8

### e07_aggregate_in_filter

- Title: A filter cannot contain an aggregate
- Question: (A filter on an aggregate.)
- Behavior: The filter applies to rows before aggregation.
- Spec: 3.4, 8

### e08_window_in_filter

- Title: A filter cannot contain a window function
- Question: (A filter on a row number.)
- Behavior: The filter is a row predicate.
- Spec: 3.4, 8

### e09_cross_dataset_filter

- Title: A filter reads one dataset
- Question: What is the amount of orders over 100 from customers in EAST?
- Behavior: The filter reads orders and customers. A Layer 2 query can express
  it.
- Spec: 3.4, 7, 8

### e10_name_collision

- Title: Output names must be unique
- Question: (Two columns named region.)
- Behavior: The alias of the second dimension equals the output name of the
  first.
- Spec: 3.3, 8

### e11_unaggregated_field_in_metric

- Title: A model-scoped metric with a field outside every aggregate
- Question: What is amount_less_order?
- Behavior: orders.amount is outside SUM.
- Spec: 5.2, 8

### e12_windowed_metric

- Title: A model-scoped metric with a window function is deferred
- Question: What is running_revenue?
- Behavior: The metric uses OVER.
- Spec: 5.2, 8

### e13_measure_grain_two_datasets

- Title: An aggregate whose arguments read two datasets is deferred
- Question: What is order_less_return?
- Behavior: SUM reads orders.amount and returns.amount.
- Spec: 5.2, 8

### e14_measure_grain_no_dataset

- Title: An aggregate whose arguments read no dataset
- Question: What is row_count?
- Behavior: `COUNT(*)` in a model-scoped metric has no home dataset. A
  dataset-scoped `COUNT(*)` expresses the count.
- Spec: 5.2, 8

### e15_no_path

- Title: A dimension no metric reaches
- Question: How many customers are there per order?
- Behavior: customers does not reach orders: relationships are many-to-one,
  from orders to customers. The error names orders.order_id and
  customers.customer_count.
- Spec: 4, 5.3, 8

### e16_partial_dimension_reachability

- Title: A dimension some metrics reach
- Question: What are the order amount and the number of tickets by customer
  region?
- Behavior: orders reaches customers; tickets does not. The error names
  customers.region and tickets.ticket_count.
- Spec: 5.3, 8, B.4

### e17_no_anchor

- Title: A query without metrics needs an anchor
- Question: Which pairs of orders and returns are there?
- Behavior: Neither orders nor returns reaches the other.
- Spec: 5.6, 8

### e18_filter_path_from_metric

- Title: Every metric must reach the dataset of the filter
- Question: What are the order and return amounts by customer region, for
  orders over 100?
- Behavior: returns does not reach orders. The error names the filter and
  returns.total_returns.
- Spec: 5.3, 8, B.5

### e19_filter_path_from_anchor

- Title: The anchor must reach the dataset of the filter
- Question: Which customer regions have orders over 100?
- Behavior: The anchor is customers, which does not reach orders. The error
  names the filter and the anchor.
- Spec: 5.6, 8, B.5

### e20_order_invalid_before_name

- Title: The first matching row of Section 8 wins
- Question: (An unknown part and an unknown reference.)
- Behavior: Both E_INVALID_QUERY (row 1) and E_NAME_NOT_FOUND (row 3) apply.
  Row 1 comes first.
- Spec: 8

### e21_order_name_before_filter

- Title: A reference error comes before a filter error
- Question: (An unknown reference and a filter over two datasets.)
- Behavior: Both E_NAME_NOT_FOUND (row 3) and E_CROSS_DATASET_FILTER (row 8)
  apply. Row 3 comes first.
- Spec: 8

## stores

These cases load [`stores.sql`](../stores/stores.sql) and
[`stores_model.ossie.yaml`](../stores/stores_model.ossie.yaml).

### a16_shared_dimensions_stores

- Title: Two fact metrics share item and date dimensions
- Question: What are the sales and return amounts by item brand and year?
- Behavior: store_sales and store_returns each form a branch to item and
  date_dim. Keys are the brand and year pairs from either branch. Acme in 2024
  has sales and no returns, so total_returns is NULL. Appendix B.3 shared
  dimensions.
- Spec: 4, 5.4, 5.5, B.3

### a17_unmatched_home_null_group

- Title: Unmatched home rows and NULL dimension keys form a NULL group
- Question: What is the sales amount by customer region?
- Behavior: The branch LEFT JOINs customers from store_sales. SS4 has no
  customer, and SS5's customer C3 has a NULL region, so both amounts are in the
  NULL region group (25 + 40). An INNER JOIN would drop SS4 and leave 40.
  Section 6, S1.
- Spec: 4, 5.4, 6

### a18_anchor_with_metric

- Title: A metric makes an otherwise unanchored query valid
- Question: What is the sales amount by item brand and store state?
- Behavior: Without a metric, item and store cannot reach each other (e23).
  store_sales reaches both, so the query is valid. Appendix B.3.
- Spec: 5.3, 5.6, B.3

### a19_sales_by_year

- Title: One path from store_sales to date_dim
- Question: What is the sales amount by year?
- Behavior: store_sales reaches date_dim only through sale_date_id, so the
  query is valid. The same query against stores_ambiguous raises
  E_AMBIGUOUS_PATH (e24).
- Spec: 4, 5.4, B.3

### a20_filter_on_sale_year

- Title: A filter on date_dim with one path
- Question: What is the total sales amount for year 2024?
- Behavior: store_sales reaches date_dim only through sale_date_id, so the
  filter applies. Sales in 2024 are 200 + 50 + 40 = 290. The same query against
  stores_ambiguous raises E_AMBIGUOUS_PATH (e25).
- Spec: 4, 5.3, 5.4, B.3

### e22_no_path_stores

- Title: customers cannot reach item
- Question: How many customers are there per item brand?
- Behavior: Relationships are many-to-one from store_sales to customers and to
  item. customers cannot reach item. The error names item.brand and
  customers.customer_count. Appendix B.3.
- Spec: 4, 5.3, 8, B.3

### e23_no_anchor_stores

- Title: item and store cannot reach each other
- Question: Which pairs of item brand and store state are there?
- Behavior: Neither item nor store reaches the other, so there is no anchor.
  Adding store_sales.total_sales makes the query valid (a18). Appendix B.3.
- Spec: 5.6, 8, B.3

## stores_ambiguous

These cases load [`stores.sql`](../stores/stores.sql) and
[`stores_ambiguous_model.ossie.yaml`](../stores/stores_ambiguous_model.ossie.yaml).

### a21_single_path_on_ambiguous_model

- Title: A second path elsewhere in the model does not make a query ambiguous
- Question: What is the return amount by year?
- Behavior: store_returns reaches date_dim only through return_date_id.
  store_sales has two paths to date_dim, but this query does not join
  store_sales or store, so it is valid. E_AMBIGUOUS_PATH applies only to paths
  between datasets that the lowering joins (Section 8, row 17).
- Spec: 4, 5.4, 8, B.3

### e24_ambiguous_path_dimension

- Title: Two paths from store_sales to date_dim
- Question: What is the sales amount by year?
- Behavior: store_sales reaches date_dim through sale_date_id and through store
  then closing_date_id. The engine must raise E_AMBIGUOUS_PATH even though one
  path is shorter. Appendix B.3.
- Spec: 4, 8, B.3

### e25_ambiguous_path_filter

- Title: A filter on date_dim is ambiguous from store_sales
- Question: What is the total sales amount for year 2024?
- Behavior: The filter's dataset is date_dim. store_sales has two paths to it,
  so the join tree is ambiguous. Appendix B.3.
- Spec: 4, 5.3, 8, B.3
