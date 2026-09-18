CREATE SEMANTIC VIEW tpcds_retail_model
  TABLES (
    store_sales AS public.store_sales PRIMARY KEY (ss_item_sk, ss_ticket_number),
    date_dim AS public.date_dim PRIMARY KEY (d_date_sk),
    customer AS public.customer PRIMARY KEY (c_customer_sk),
    item AS public.item PRIMARY KEY (i_item_sk),
    store AS public.store PRIMARY KEY (s_store_sk)
  )
  RELATIONSHIPS (
    store_sales_to_date AS store_sales(ss_sold_date_sk) REFERENCES date_dim(d_date_sk),
    store_sales_to_customer AS store_sales(ss_customer_sk) REFERENCES customer(c_customer_sk),
    store_sales_to_item AS store_sales(ss_item_sk) REFERENCES item(i_item_sk),
    store_sales_to_store AS store_sales(ss_store_sk) REFERENCES store(s_store_sk)
  )
  DIMENSIONS (
    store_sales.ss_sold_date_sk AS store_sales.ss_sold_date_sk COMMENT = 'Foreign key to date dimension',
    store_sales.ss_item_sk AS store_sales.ss_item_sk COMMENT = 'Foreign key to item dimension',
    store_sales.ss_customer_sk AS store_sales.ss_customer_sk COMMENT = 'Foreign key to customer dimension',
    store_sales.ss_store_sk AS store_sales.ss_store_sk COMMENT = 'Foreign key to store dimension',
    store_sales.ss_quantity AS store_sales.ss_quantity COMMENT = 'Quantity of items sold',
    store_sales.ss_sales_price AS store_sales.ss_sales_price COMMENT = 'Sales price per unit',
    store_sales.ss_ext_sales_price AS store_sales.ss_ext_sales_price COMMENT = 'Extended sales price (quantity * price)',
    store_sales.ss_net_profit AS store_sales.ss_net_profit COMMENT = 'Net profit from the sale',
    date_dim.d_date_sk AS date_dim.d_date_sk COMMENT = 'Surrogate key for date',
    date_dim.d_date AS date_dim.d_date COMMENT = 'Actual date value',
    date_dim.d_year AS date_dim.d_year COMMENT = 'Year',
    date_dim.d_quarter_name AS date_dim.d_quarter_name COMMENT = 'Quarter name (e.g., 2024Q1)',
    date_dim.d_moy AS date_dim.d_moy COMMENT = 'Month of year (1-12)',
    customer.c_customer_sk AS customer.c_customer_sk COMMENT = 'Surrogate key for customer',
    customer.c_customer_id AS customer.c_customer_id COMMENT = 'Business key for customer',
    customer.c_first_name AS customer.c_first_name COMMENT = 'Customer first name',
    customer.c_last_name AS customer.c_last_name COMMENT = 'Customer last name',
    customer.customer_full_name AS (customer.c_first_name || ' ' || customer.c_last_name) COMMENT = 'Customer full name (computed field)',
    customer.c_email_address AS customer.c_email_address COMMENT = 'Customer email address',
    item.i_item_sk AS item.i_item_sk COMMENT = 'Surrogate key for item',
    item.i_item_id AS item.i_item_id COMMENT = 'Business key for item',
    item.i_item_desc AS item.i_item_desc COMMENT = 'Item description',
    item.i_brand AS item.i_brand COMMENT = 'Brand name',
    item.i_category AS item.i_category COMMENT = 'Item category',
    item.i_current_price AS item.i_current_price COMMENT = 'Current price of the item',
    store.s_store_sk AS store.s_store_sk COMMENT = 'Surrogate key for store',
    store.s_store_id AS store.s_store_id COMMENT = 'Business key for store',
    store.s_store_name AS store.s_store_name COMMENT = 'Store name',
    store.s_city AS store.s_city COMMENT = 'City where store is located',
    store.s_state AS store.s_state COMMENT = 'State where store is located',
    store.s_number_employees AS store.s_number_employees COMMENT = 'Number of employees at the store'
  )
  METRICS (
    store_sales.total_sales AS SUM(store_sales.ss_ext_sales_price) COMMENT = 'Total sales revenue across all transactions',
    store_sales.total_profit AS SUM(store_sales.ss_net_profit) COMMENT = 'Total net profit from store sales',
    store_sales.sales_by_brand AS SUM(store_sales.ss_ext_sales_price) COMMENT = 'Total sales by brand (requires grouping by item.i_brand)'
  )
  COMMENT = 'TPC-DS retail semantic model for sales and customer analytics';
