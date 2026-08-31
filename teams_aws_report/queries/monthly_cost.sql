-- Partition-pruned to the current billing period only.
-- billing_period format is 'YYYY-MM' (e.g. '2026-08').
SELECT
    DATE_FORMAT(date_trunc('month', line_item_usage_start_date), '%Y-%m') AS usage_month,
    ROUND(SUM(line_item_unblended_cost), 2) AS value
FROM athenadataexports_aws_finops_cur.data
WHERE
    billing_period = DATE_FORMAT(current_date, '%Y-%m')
GROUP BY 1
ORDER BY 1;
