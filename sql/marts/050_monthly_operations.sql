-- =====================================================================================
-- mart.monthly_operations — one row per (purchase month, customer macro-region).
-- Date convention: month of order_purchase_timestamp (local time as recorded, no time zone).
-- late_rate denominator: eligible delivered orders of that month and region.
-- Window functions: month-over-month change (LAG) and a rank of regions within each month
-- computed only where the denominator reaches the configured minimum support.
-- =====================================================================================

DROP TABLE IF EXISTS mart.monthly_operations;
CREATE TABLE mart.monthly_operations AS
WITH monthly AS (
    SELECT
        purchase_month,
        coalesce(customer_region, 'Unknown')                       AS customer_region,
        count(*)                                                   AS orders,
        count(*) FILTER (WHERE scoring_eligible)                   AS scorable_orders,
        count(*) FILTER (WHERE label_eligible)                     AS eligible_delivered_orders,
        count(*) FILTER (WHERE is_late)                            AS late_orders,
        count(*) FILTER (WHERE outcome_population = 'canceled')    AS canceled_orders,
        count(*) FILTER (WHERE outcome_population = 'unavailable') AS unavailable_orders,
        count(*) FILTER (WHERE outcome_population = 'not_observed') AS outcome_not_observed_orders,
        sum(item_value_brl)                                        AS item_value_brl,
        sum(freight_value_brl)                                     AS freight_value_brl,
        sum(paid_value_brl)                                        AS paid_value_brl,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY delivery_duration_days)
            FILTER (WHERE label_eligible)                          AS median_delivery_days,
        percentile_cont(0.5) WITHIN GROUP (ORDER BY promised_lead_days)
            FILTER (WHERE label_eligible)                          AS median_promised_lead_days,
        avg(positive_delay_days) FILTER (WHERE is_late)            AS mean_delay_days_when_late
    FROM mart.order_summary
    GROUP BY 1, 2
),
rated AS (
    SELECT
        m.*,
        round(m.late_orders::numeric / NULLIF(m.eligible_delivered_orders, 0), 4) AS late_rate,
        m.eligible_delivered_orders >= current_setting('dr.min_support')::int     AS meets_min_support
    FROM monthly AS m
)
SELECT
    r.*,
    r.late_rate - LAG(r.late_rate) OVER (PARTITION BY r.customer_region ORDER BY r.purchase_month)
        AS late_rate_change_vs_prev_month,
    CASE WHEN r.meets_min_support THEN
        RANK() OVER (PARTITION BY r.purchase_month, r.meets_min_support ORDER BY r.late_rate DESC)
    END AS late_rate_rank_in_month
FROM rated AS r;

ALTER TABLE mart.monthly_operations ADD PRIMARY KEY (purchase_month, customer_region);
