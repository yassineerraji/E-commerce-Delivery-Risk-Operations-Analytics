-- =====================================================================================
-- feature.order_features — predictive matrix, one row per (scorable order, feature_version).
--
-- Contains ONLY allow-listed features (src/delivery_risk/features/catalog.py, documented in
-- docs/feature_catalog.md) plus the technical keys order_id / prediction_at. No target, no
-- delivery outcome, no review, no payment, no final status, no raw identifier as a feature.
-- Static order/catalog attributes are ASSUMED available at approval (the source is a final
-- snapshot without history); the "no_catalog_static" ablation measures that sensitivity.
-- =====================================================================================

CREATE TABLE feature.order_features AS
WITH population AS (                     -- at-approval information only
    SELECT l.order_id, l.prediction_at, l.purchase_at, l.purchase_date, l.promised_date
    FROM evaluation.order_labels AS l
    WHERE l.scoring_eligible
),
item_agg AS (                            -- grain: order
    SELECT
        i.order_id,
        count(*)                       AS n_items,
        count(DISTINCT i.product_id)   AS n_distinct_products,
        count(DISTINCT i.seller_id)    AS n_distinct_sellers,
        count(DISTINCT p.category)     AS n_distinct_categories,
        sum(i.price_brl)               AS item_value_brl,
        sum(i.freight_brl)             AS freight_value_brl,
        min(i.shipping_limit_at)       AS first_shipping_limit_at,
        CASE WHEN count(p.weight_g) = count(*) THEN sum(p.weight_g) END AS total_weight_g,
        max(p.volume_cm3)              AS max_product_volume_cm3
    FROM core.order_items AS i
    JOIN core.products AS p USING (product_id)
    GROUP BY i.order_id
),
main_category AS (
    SELECT DISTINCT ON (i.order_id) i.order_id, p.category AS main_category
    FROM core.order_items AS i
    JOIN core.products AS p USING (product_id)
    ORDER BY i.order_id, i.price_brl DESC, i.order_item_id
),
primary_seller AS (                      -- seller with the largest item value (tie: seller_id)
    SELECT DISTINCT ON (os.order_id) os.order_id, s.state AS primary_seller_state
    FROM core.order_sellers AS os
    JOIN core.sellers AS s USING (seller_id)
    ORDER BY os.order_id, os.item_value_brl DESC, os.seller_id
),
geo AS (                                 -- prefix centroids aggregated in core.geo_prefix first
    SELECT
        os.order_id,
        max(meta.haversine_km(gc.lat, gc.lng, gs.lat, gs.lng)) AS max_seller_distance_km,
        bool_or(s.state = c.state)                              AS any_seller_same_state
    FROM core.order_sellers AS os
    JOIN core.sellers AS s     USING (seller_id)
    JOIN core.orders AS o      USING (order_id)
    JOIN core.customers AS c   USING (customer_id)
    LEFT JOIN core.geo_prefix AS gs ON gs.zip_prefix = s.zip_prefix
    LEFT JOIN core.geo_prefix AS gc ON gc.zip_prefix = c.zip_prefix
    GROUP BY os.order_id
),
history AS (                             -- equal weight per DISTINCT seller of the order
    SELECT
        order_id,
        min(known_outcomes_total)      AS sh_known_outcomes_total_min,
        min(known_outcomes_90d)        AS sh_known_outcomes_90d_min,
        avg(late_rate_90d)             AS sh_late_rate_90d_mean,
        max(late_rate_90d)             AS sh_late_rate_90d_max,
        avg(mean_positive_delay_90d)   AS sh_mean_positive_delay_90d_mean,
        avg(approvals_30d)             AS sh_approvals_30d_mean,
        max(approvals_30d)             AS sh_approvals_30d_max,
        bool_or(known_outcomes_total = 0) AS sh_any_seller_no_history
    FROM feature.order_seller_history
    GROUP BY order_id
)
SELECT
    p.order_id,
    current_setting('dr.feature_version')                                  AS feature_version,
    p.prediction_at,
    -- group: order
    ia.n_items,
    ia.n_distinct_products,
    ia.n_distinct_sellers,
    ia.item_value_brl::float8                                              AS item_value_brl,
    ia.freight_value_brl::float8                                           AS freight_value_brl,
    (ia.freight_value_brl / NULLIF(ia.item_value_brl, 0))::float8          AS freight_to_item_value_ratio,
    (p.promised_date - p.purchase_date)                                    AS promised_lead_days,
    (extract(epoch FROM p.prediction_at - p.purchase_at) / 3600.0)::float8 AS approval_delay_hours,
    (extract(epoch FROM ia.first_shipping_limit_at - p.prediction_at) / 86400.0)::float8
                                                                           AS shipping_limit_days_after_approval,
    extract(isodow FROM p.prediction_at)::int::text                        AS approval_weekday,
    extract(hour FROM p.prediction_at)::int                                AS approval_hour,
    -- group: geo
    c.state                                                                AS customer_state,
    ps.primary_seller_state,
    g.any_seller_same_state::int                                           AS any_seller_same_state,
    g.max_seller_distance_km,
    -- group: catalog (static product snapshot)
    mc.main_category,
    ia.n_distinct_categories,
    ia.total_weight_g::float8                                              AS total_weight_g,
    ia.max_product_volume_cm3::float8                                      AS max_product_volume_cm3,
    -- group: seller_history (as-of t)
    h.sh_known_outcomes_total_min,
    h.sh_known_outcomes_90d_min,
    h.sh_late_rate_90d_mean::float8                                        AS sh_late_rate_90d_mean,
    h.sh_late_rate_90d_max::float8                                         AS sh_late_rate_90d_max,
    h.sh_mean_positive_delay_90d_mean::float8                              AS sh_mean_positive_delay_90d_mean,
    h.sh_approvals_30d_mean::float8                                        AS sh_approvals_30d_mean,
    h.sh_approvals_30d_max,
    h.sh_any_seller_no_history::int                                        AS sh_any_seller_no_history
FROM population AS p
JOIN core.orders AS o            USING (order_id)
JOIN core.customers AS c         USING (customer_id)
JOIN item_agg AS ia              USING (order_id)
LEFT JOIN main_category AS mc    USING (order_id)
LEFT JOIN primary_seller AS ps   USING (order_id)
LEFT JOIN geo AS g               USING (order_id)
LEFT JOIN history AS h           USING (order_id);

ALTER TABLE feature.order_features ADD PRIMARY KEY (order_id, feature_version);
CREATE INDEX ON feature.order_features (prediction_at);
