-- =====================================================================================
-- mart.seller_order_exposure — one row per DISTINCT (order, seller).
--
-- "Late-order exposure": share of a seller's eligible delivered orders that were late.
-- It is NOT "seller-caused delay": lateness is observed at order level and, in a
-- multi-seller order, every seller of that order is exposed to the same outcome.
-- Seller counts are therefore not additive to the marketplace total.
-- =====================================================================================

DROP TABLE IF EXISTS mart.seller_order_exposure;
CREATE TABLE mart.seller_order_exposure AS
SELECT
    os.order_id,
    os.seller_id,
    s.state                 AS seller_state,
    s.region                AS seller_region,
    o.purchase_at,
    o.purchase_month,
    o.customer_region,
    os.n_items              AS seller_items,
    os.item_value_brl       AS seller_item_value_brl,
    os.freight_value_brl    AS seller_freight_value_brl,
    o.n_distinct_sellers    AS order_n_sellers,
    (o.n_distinct_sellers > 1) AS is_multi_seller_order,
    o.label_eligible,
    o.is_late,
    o.positive_delay_days
FROM core.order_sellers AS os
JOIN core.sellers AS s USING (seller_id)
JOIN mart.order_summary AS o USING (order_id);

ALTER TABLE mart.seller_order_exposure ADD PRIMARY KEY (order_id, seller_id);
CREATE INDEX ON mart.seller_order_exposure (seller_id);
