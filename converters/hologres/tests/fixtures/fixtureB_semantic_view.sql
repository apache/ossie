CREATE SEMANTIC VIEW svacc_sales_sv
  TABLES (
    o AS public.svacc_orders PRIMARY KEY (order_id),
    c AS public.svacc_customers PRIMARY KEY (customer_id),
    i AS public.svacc_order_items PRIMARY KEY (item_id)
  )
  RELATIONSHIPS (
    rel_oc AS o(customer_id) REFERENCES c(customer_id),
    rel_io AS i(order_id) REFERENCES o(order_id)
  )
  DIMENSIONS (
    o.region_dim AS o.region,
    o.status_dim AS o.status,
    c.city_dim AS c.city COMMENT = '客户城市'
  )
  METRICS (
    o.total AS SUM(o.amount),
    o.order_count AS COUNT(*),
    c.credit AS SUM(c.credit_limit),
    c.avg_credit AS AVG(c.credit_limit),
    i.item_qty AS SUM(i.quantity)
  )
  COMMENT = '销售分析语义视图';
