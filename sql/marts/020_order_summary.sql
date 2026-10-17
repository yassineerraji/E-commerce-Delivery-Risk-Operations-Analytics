-- =====================================================================================
-- mart.order_summary — one row per order.
--
-- FAN-OUT PREVENTION: items, payments and reviews are each aggregated to the order grain in
-- their own CTE, THEN joined to core.orders. Joining the three child tables directly on
-- order_id would multiply rows (2 items x 3 payments = 6 rows -> item value counted 3 times,
-- payments counted twice). See docs/sql_walkthrough.md, section 1.
--
-- item_value_brl, freight_value_brl and paid_value_brl are three distinct measures. Their
-- difference is reported (items_freight_minus_paid_brl), not forced to zero. None of them is
-- Olist's net revenue or margin.
-- =====================================================================================

DROP TABLE IF EXISTS mart.order_summary;
CREATE TABLE mart.order_summary AS
WITH item_agg AS (            -- grain: order
    SELECT
        i.order_id,
        count(*)                     AS n_items,
        count(DISTINCT i.product_id) AS n_distinct_products,
        count(DISTINCT i.seller_id)  AS n_distinct_sellers,
        count(DISTINCT p.category)   AS n_distinct_categories,
        sum(i.price_brl)             AS item_value_brl,
        sum(i.freight_brl)           AS freight_value_brl
    FROM core.order_items AS i
    JOIN core.products AS p USING (product_id)
    GROUP BY i.order_id
),
main_category AS (            -- grain: order; category of the most expensive item (tie: lowest item id)
    SELECT DISTINCT ON (i.order_id) i.order_id, p.category AS main_category
    FROM core.order_items AS i
    JOIN core.products AS p USING (product_id)
    ORDER BY i.order_id, i.price_brl DESC, i.order_item_id
),
payment_agg AS (              -- grain: order
    SELECT
        order_id,
        count(*)                               AS n_payments,
        sum(payment_value_brl)                 AS paid_value_brl,
        max(installments)                      AS max_installments,
        string_agg(DISTINCT payment_type, ',' ORDER BY payment_type) AS payment_types
    FROM core.payments
    GROUP BY order_id
),
review_agg AS (               -- grain: order; each review weighs equally within its order
    SELECT order_id, count(*) AS n_reviews, avg(review_score)::numeric(4, 2) AS review_score_mean
    FROM core.reviews
    GROUP BY order_id
)
SELECT
    o.order_id,
    o.customer_id,
    c.customer_unique_id,
    c.state                                  AS customer_state,
    c.region                                 AS customer_region,
    o.order_status,
    o.purchase_at,
    o.approved_at,
    o.delivered_carrier_at,
    o.delivered_customer_at,
    o.estimated_delivery_at,
    l.purchase_date,
    l.promised_date,
    l.delivered_date,
    date_trunc('month', o.purchase_at)::date AS purchase_month,
    -- items / sellers
    coalesce(ia.n_items, 0)                  AS n_items,
    coalesce(ia.n_distinct_products, 0)      AS n_distinct_products,
    coalesce(ia.n_distinct_sellers, 0)       AS n_distinct_sellers,
    coalesce(ia.n_distinct_categories, 0)    AS n_distinct_categories,
    mc.main_category,
    ia.item_value_brl,
    ia.freight_value_brl,
    -- payments (descriptive only; excluded from risk features)
    coalesce(pa.n_payments, 0)               AS n_payments,
    pa.paid_value_brl,
    pa.max_installments,
    pa.payment_types,
    (ia.item_value_brl + ia.freight_value_brl) - pa.paid_value_brl AS items_freight_minus_paid_brl,
    -- reviews (post-delivery information: never a feature)
    coalesce(ra.n_reviews, 0)                AS n_reviews,
    ra.review_score_mean,
    -- durations (civil-date convention)
    l.promised_date - l.purchase_date        AS promised_lead_days,
    CASE WHEN l.label_eligible THEN l.delivered_date - l.purchase_date END AS delivery_duration_days,
    round((extract(epoch FROM o.approved_at - o.purchase_at) / 3600.0)::numeric, 2) AS approval_delay_hours,
    -- labels and eligibility
    l.outcome_population,
    l.scoring_eligible,
    l.scoring_exclusion_reason,
    l.label_eligible,
    l.label_exclusion_reason,
    l.is_late,
    l.delay_days,
    GREATEST(l.delay_days, 0)                AS positive_delay_days,
    -- quality flags
    (o.delivered_carrier_at < o.approved_at)                              AS flag_carrier_before_approval,
    (o.order_status = 'canceled' AND o.delivered_customer_at IS NOT NULL) AS flag_canceled_with_delivery_date,
    (abs((ia.item_value_brl + ia.freight_value_brl) - pa.paid_value_brl) > 0.01) AS flag_value_gap
FROM core.orders AS o
JOIN core.customers AS c USING (customer_id)
JOIN evaluation.order_labels AS l USING (order_id)
LEFT JOIN item_agg AS ia USING (order_id)
LEFT JOIN main_category AS mc USING (order_id)
LEFT JOIN payment_agg AS pa USING (order_id)
LEFT JOIN review_agg AS ra USING (order_id);

ALTER TABLE mart.order_summary ADD PRIMARY KEY (order_id);
CREATE INDEX ON mart.order_summary (purchase_at);
CREATE INDEX ON mart.order_summary (customer_region);
