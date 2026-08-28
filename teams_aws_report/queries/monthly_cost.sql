SELECT
    date_trunc('month', line_item_usage_start_date) AS usage_month,
    ROUND(SUM(line_item_unblended_cost), 2) AS value
FROM athenadataexports_aws_finops_cur.data
WHERE
    date(line_item_usage_start_date) >= date_trunc('month', current_date)
    AND ('All' = 'All' OR product_region_code = 'All')
GROUP BY 1
ORDER BY 1;
