-- staging.customers
-- Output grain: one row per customer_id. NOTE: customer_id is an order-level key in Olist;
-- customer_unique_id identifies a returning person. Never count customer_id as people.
-- Zip prefixes: raw value kept; normalised value is left-padded to 5 digits (NULL if invalid).

CREATE TEMP TABLE customers_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id, r.source_line, r.parse_error,
        NULLIF(btrim(r.customer_id), '')              AS customer_id,
        NULLIF(btrim(r.customer_unique_id), '')       AS customer_unique_id,
        NULLIF(btrim(r.customer_zip_code_prefix), '') AS zip_prefix_raw,
        NULLIF(btrim(r.customer_city), '')            AS city,
        upper(NULLIF(btrim(r.customer_state), ''))    AS state,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.customers AS r
    WHERE r.load_id = meta.current_load_id('customers')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL      THEN 'csv_parse_error'
        WHEN s.customer_id IS NULL          THEN 'missing_customer_id'
        WHEN s.customer_unique_id IS NULL   THEN 'missing_customer_unique_id'
    END AS reject_reason,
    md5(row(s.customer_unique_id, s.zip_prefix_raw, s.city, s.state)::text) AS content_hash
FROM src AS s;

CREATE TEMP TABLE customers_keys ON COMMIT DROP AS
SELECT customer_id, count(*) AS n_rows, count(DISTINCT content_hash) AS n_versions
FROM customers_checked WHERE reject_reason IS NULL GROUP BY customer_id;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'customers', load_id, source_line, reject_reason, raw_record
FROM customers_checked WHERE reject_reason IS NOT NULL
UNION ALL
SELECT 'customers', c.load_id, c.source_line, 'key_conflict', c.raw_record
FROM customers_checked AS c JOIN customers_keys AS k USING (customer_id)
WHERE c.reject_reason IS NULL AND k.n_versions > 1;

INSERT INTO quality.duplicate_log (dataset, key_value, n_rows, kind)
SELECT 'customers', customer_id, n_rows, CASE WHEN n_versions > 1 THEN 'key_conflict' ELSE 'strict_duplicate' END
FROM customers_keys WHERE n_rows > 1;

DROP TABLE IF EXISTS staging.customers;
CREATE TABLE staging.customers AS
SELECT DISTINCT ON (c.customer_id)
    c.customer_id,
    c.customer_unique_id,
    c.zip_prefix_raw,
    meta.zip_prefix(c.zip_prefix_raw) AS zip_prefix,
    c.city,
    c.state,
    c.load_id,
    c.source_line
FROM customers_checked AS c JOIN customers_keys AS k USING (customer_id)
WHERE c.reject_reason IS NULL AND k.n_versions = 1
ORDER BY c.customer_id, c.source_line;
