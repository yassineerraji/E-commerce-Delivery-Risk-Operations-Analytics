-- core.order_sellers — one row per DISTINCT (order, seller) pair.
-- Input grain : core.order_items (one row per item; a seller can appear on several items)
-- Output grain: (order_id, seller_id)
-- Use this table whenever an order must be counted once per seller (late-order exposure,
-- seller histories). Delivery dates exist only at order level: they do not tell which seller
-- of a multi-seller order caused a delay.

CREATE TABLE core.order_sellers AS
SELECT
    order_id,
    seller_id,
    count(*)             AS n_items,
    sum(price_brl)       AS item_value_brl,
    sum(freight_brl)     AS freight_value_brl,
    min(order_item_id)   AS first_order_item_id,
    min(shipping_limit_at) AS first_shipping_limit_at
FROM core.order_items
GROUP BY order_id, seller_id;
ALTER TABLE core.order_sellers ADD PRIMARY KEY (order_id, seller_id);
ALTER TABLE core.order_sellers ADD FOREIGN KEY (order_id)  REFERENCES core.orders (order_id);
ALTER TABLE core.order_sellers ADD FOREIGN KEY (seller_id) REFERENCES core.sellers (seller_id);
CREATE INDEX ON core.order_sellers (seller_id, order_id);
