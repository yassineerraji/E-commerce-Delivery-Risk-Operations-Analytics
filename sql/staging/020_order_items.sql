-- staging.order_items
-- Input grain : CSV records of the current order_items load
-- Output grain: one row per (order_id, order_item_id); same pattern as 010_orders.sql

CREATE TEMP TABLE items_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id, r.source_line, r.parse_error,
        NULLIF(btrim(r.order_id), '')            AS order_id,
        NULLIF(btrim(r.order_item_id), '')       AS item_txt,
        NULLIF(btrim(r.product_id), '')          AS product_id,
        NULLIF(btrim(r.seller_id), '')           AS seller_id,
        NULLIF(btrim(r.shipping_limit_date), '') AS shipping_limit_txt,
        NULLIF(btrim(r.price), '')               AS price_txt,
        NULLIF(btrim(r.freight_value), '')       AS freight_txt,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.order_items AS r
    WHERE r.load_id = meta.current_load_id('order_items')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL                       THEN 'csv_parse_error'
        WHEN s.order_id IS NULL                              THEN 'missing_order_id'
        WHEN s.item_txt IS NULL                              THEN 'missing_order_item_id'
        WHEN NOT meta.is_integer_like(s.item_txt)            THEN 'invalid_order_item_id'
        WHEN s.product_id IS NULL                            THEN 'missing_product_id'
        WHEN s.seller_id IS NULL                             THEN 'missing_seller_id'
        WHEN s.shipping_limit_txt IS NOT NULL
             AND NOT meta.is_iso_timestamp(s.shipping_limit_txt) THEN 'invalid_shipping_limit'
        WHEN s.price_txt IS NULL                             THEN 'missing_price'
        WHEN NOT meta.is_decimal(s.price_txt)                THEN 'invalid_price'
        WHEN s.price_txt::numeric < 0                        THEN 'negative_price'
        WHEN s.freight_txt IS NULL                           THEN 'missing_freight'
        WHEN NOT meta.is_decimal(s.freight_txt)              THEN 'invalid_freight'
        WHEN s.freight_txt::numeric < 0                      THEN 'negative_freight'
    END AS reject_reason,
    -- typed key computed inside CASE so the cast never runs on an invalid value
    CASE WHEN meta.is_integer_like(s.item_txt) THEN s.item_txt::numeric::integer END AS order_item_id,
    md5(row(s.product_id, s.seller_id, s.shipping_limit_txt, s.price_txt, s.freight_txt)::text) AS content_hash
FROM src AS s;

CREATE TEMP TABLE items_keys ON COMMIT DROP AS
SELECT order_id, order_item_id,
       count(*) AS n_rows, count(DISTINCT content_hash) AS n_versions
FROM items_checked WHERE reject_reason IS NULL
GROUP BY 1, 2;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'order_items', load_id, source_line, reject_reason, raw_record
FROM items_checked WHERE reject_reason IS NOT NULL
UNION ALL
SELECT 'order_items', c.load_id, c.source_line, 'key_conflict', c.raw_record
FROM items_checked AS c
JOIN items_keys AS k USING (order_id, order_item_id)
WHERE c.reject_reason IS NULL AND k.n_versions > 1;

INSERT INTO quality.duplicate_log (dataset, key_value, n_rows, kind)
SELECT 'order_items', order_id || '#' || order_item_id, n_rows,
       CASE WHEN n_versions > 1 THEN 'key_conflict' ELSE 'strict_duplicate' END
FROM items_keys WHERE n_rows > 1;

DROP TABLE IF EXISTS staging.order_items;
CREATE TABLE staging.order_items AS
SELECT DISTINCT ON (c.order_id, c.order_item_id)
    c.order_id,
    c.order_item_id,
    c.product_id,
    c.seller_id,
    c.shipping_limit_txt::timestamp AS shipping_limit_at,
    c.price_txt::numeric(12, 2)     AS price_brl,
    c.freight_txt::numeric(12, 2)   AS freight_brl,
    c.load_id,
    c.source_line
FROM items_checked AS c
JOIN items_keys AS k USING (order_id, order_item_id)
WHERE c.reject_reason IS NULL AND k.n_versions = 1
ORDER BY c.order_id, c.order_item_id, c.source_line;
