CREATE SEMANTIC VIEW svacc_order_sv
  TABLES (
    o AS public.svacc_orders PRIMARY KEY (order_id)
  )
  DIMENSIONS (
    o.region_dim AS o.region,
    o.status_dim AS o.status COMMENT = 'The order''s current status'
  )
  METRICS (
    o.total_revenue AS SUM(o.amount) COMMENT = 'Total revenue',
    o.order_count AS COUNT(*)
  )
  COMMENT = 'Single-table order analysis';
