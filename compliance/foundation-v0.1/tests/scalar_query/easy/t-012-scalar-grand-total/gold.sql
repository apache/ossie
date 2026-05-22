SELECT a.total_revenue, b.total_returns
FROM (SELECT SUM(amount) AS total_revenue FROM orders) a
CROSS JOIN (SELECT SUM(amount) AS total_returns FROM returns) b
