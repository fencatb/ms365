-- Partition-pruned to the current billing period only.
-- billing_period format is 'YYYY-MM' (e.g. '2026-08').
SELECT
    line_item_usage_account_id AS account_id,
    ROUND(SUM(line_item_unblended_cost), 2) AS value
FROM athenadataexports_aws_finops_cur.data
WHERE
    billing_period = DATE_FORMAT(current_date, '%Y-%m')
GROUP BY 1
ORDER BY value DESC;
