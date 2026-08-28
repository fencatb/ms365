SELECT
    line_item_usage_account_id AS account_id,
    ROUND(SUM(line_item_unblended_cost), 2) AS value
FROM athenadataexports_aws_finops_cur.data
WHERE
    date(line_item_usage_start_date) >= date_trunc('month', current_date)
    AND ('All' = 'All' OR product_region_code = 'All')
GROUP BY 1
ORDER BY value DESC;
