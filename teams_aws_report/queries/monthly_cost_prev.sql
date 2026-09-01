-- Previous billing period, so monthly_cost can compare it against the current
-- month even on the 1st when the current period has no rows yet.
-- billing_period format is 'YYYY-MM' (e.g. '2026-08').
SELECT
    DATE_FORMAT(date_trunc('month', line_item_usage_start_date), '%Y-%m') AS usage_month,
    ROUND(SUM(line_item_unblended_cost), 2) AS value
FROM athenadataexports_aws_finops_cur.data
WHERE
    billing_period = DATE_FORMAT(
        date_trunc('month', current_date) - INTERVAL '1' MONTH, '%Y-%m')
GROUP BY 1
ORDER BY 1;
