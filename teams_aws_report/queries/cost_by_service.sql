-- Partition-pruned to the current billing period only. Zero-cost services are
-- hidden so the report only lists services that actually cost money.
-- billing_period format is 'YYYY-MM' (e.g. '2026-08').
SELECT
    line_item_product_code AS service,
    ROUND(SUM(line_item_unblended_cost), 2) AS value
FROM athenadataexports_aws_finops_cur.data
WHERE
    billing_period = DATE_FORMAT(current_date, '%Y-%m')
GROUP BY 1
HAVING SUM(line_item_unblended_cost) > 0
ORDER BY value DESC;
