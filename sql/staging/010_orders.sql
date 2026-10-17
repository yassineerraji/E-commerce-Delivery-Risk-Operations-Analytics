-- =====================================================================================
-- staging.orders
-- Input grain : one row per CSV record of the current orders load (raw.orders)
-- Output grain: one row per order_id (typed, de-duplicated)
--
-- Pattern used by every staging file:
--   1. normalise text (trim, empty string -> NULL) and keep the raw record as jsonb;
--   2. assign the FIRST failing reason (missing key, invalid type, ...) or NULL;
--   3. among valid rows, compare versions of the same key:
--        identical content  -> strict duplicate (keep one, log it, non-critical)
--        different content  -> key conflict (quarantine all versions, critical);
--   4. publish typed rows. Business incoherences (e.g. approval before purchase) are NOT
--      rejected here: they are flagged later with an explicit eligibility reason.
-- =====================================================================================

CREATE TEMP TABLE orders_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id,
        r.source_line,
        r.parse_error,
        NULLIF(btrim(r.order_id), '')                       AS order_id,
        NULLIF(btrim(r.customer_id), '')                    AS customer_id,
        lower(NULLIF(btrim(r.order_status), ''))            AS order_status,
        NULLIF(btrim(r.order_purchase_timestamp), '')       AS purchase_txt,
        NULLIF(btrim(r.order_approved_at), '')              AS approved_txt,
        NULLIF(btrim(r.order_delivered_carrier_date), '')   AS carrier_txt,
        NULLIF(btrim(r.order_delivered_customer_date), '')  AS delivered_txt,
        NULLIF(btrim(r.order_estimated_delivery_date), '')  AS estimated_txt,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.orders AS r
    WHERE r.load_id = meta.current_load_id('orders')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL                                            THEN 'csv_parse_error'
        WHEN s.order_id IS NULL                                                   THEN 'missing_order_id'
        WHEN s.customer_id IS NULL                                                THEN 'missing_customer_id'
        WHEN s.order_status IS NULL                                               THEN 'missing_order_status'
        WHEN s.purchase_txt IS NULL                                               THEN 'missing_purchase_timestamp'
        WHEN NOT meta.is_iso_timestamp(s.purchase_txt)                            THEN 'invalid_purchase_timestamp'
        WHEN s.approved_txt IS NOT NULL AND NOT meta.is_iso_timestamp(s.approved_txt)   THEN 'invalid_approved_timestamp'
        WHEN s.carrier_txt IS NOT NULL AND NOT meta.is_iso_timestamp(s.carrier_txt)     THEN 'invalid_carrier_timestamp'
        WHEN s.delivered_txt IS NOT NULL AND NOT meta.is_iso_timestamp(s.delivered_txt) THEN 'invalid_delivered_timestamp'
        WHEN s.estimated_txt IS NOT NULL AND NOT meta.is_iso_timestamp(s.estimated_txt) THEN 'invalid_estimated_timestamp'
    END AS reject_reason,
    md5(row(s.customer_id, s.order_status, s.purchase_txt, s.approved_txt, s.carrier_txt,
            s.delivered_txt, s.estimated_txt)::text) AS content_hash
FROM src AS s;

CREATE TEMP TABLE orders_keys ON COMMIT DROP AS
SELECT order_id, count(*) AS n_rows, count(DISTINCT content_hash) AS n_versions
FROM orders_checked
WHERE reject_reason IS NULL
GROUP BY order_id;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'orders', load_id, source_line, reject_reason, raw_record
FROM orders_checked WHERE reject_reason IS NOT NULL
UNION ALL
SELECT 'orders', c.load_id, c.source_line, 'key_conflict', c.raw_record
FROM orders_checked AS c JOIN orders_keys AS k USING (order_id)
WHERE c.reject_reason IS NULL AND k.n_versions > 1;

INSERT INTO quality.duplicate_log (dataset, key_value, n_rows, kind)
SELECT 'orders', order_id, n_rows, CASE WHEN n_versions > 1 THEN 'key_conflict' ELSE 'strict_duplicate' END
FROM orders_keys WHERE n_rows > 1;

DROP TABLE IF EXISTS staging.orders;
CREATE TABLE staging.orders AS
SELECT DISTINCT ON (c.order_id)
    c.order_id,
    c.customer_id,
    c.order_status,
    c.purchase_txt::timestamp  AS purchase_at,
    c.approved_txt::timestamp  AS approved_at,
    c.carrier_txt::timestamp   AS delivered_carrier_at,
    c.delivered_txt::timestamp AS delivered_customer_at,
    c.estimated_txt::timestamp AS estimated_delivery_at,
    c.load_id,
    c.source_line
FROM orders_checked AS c
JOIN orders_keys AS k USING (order_id)
WHERE c.reject_reason IS NULL AND k.n_versions = 1
ORDER BY c.order_id, c.source_line;
