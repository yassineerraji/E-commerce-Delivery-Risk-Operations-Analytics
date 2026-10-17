-- staging.sellers — output grain: one row per seller_id.

CREATE TEMP TABLE sellers_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id, r.source_line, r.parse_error,
        NULLIF(btrim(r.seller_id), '')              AS seller_id,
        NULLIF(btrim(r.seller_zip_code_prefix), '') AS zip_prefix_raw,
        NULLIF(btrim(r.seller_city), '')            AS city,
        upper(NULLIF(btrim(r.seller_state), ''))    AS state,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.sellers AS r
    WHERE r.load_id = meta.current_load_id('sellers')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL THEN 'csv_parse_error'
        WHEN s.seller_id IS NULL       THEN 'missing_seller_id'
    END AS reject_reason,
    md5(row(s.zip_prefix_raw, s.city, s.state)::text) AS content_hash
FROM src AS s;

CREATE TEMP TABLE sellers_keys ON COMMIT DROP AS
SELECT seller_id, count(*) AS n_rows, count(DISTINCT content_hash) AS n_versions
FROM sellers_checked WHERE reject_reason IS NULL GROUP BY seller_id;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'sellers', load_id, source_line, reject_reason, raw_record
FROM sellers_checked WHERE reject_reason IS NOT NULL
UNION ALL
SELECT 'sellers', c.load_id, c.source_line, 'key_conflict', c.raw_record
FROM sellers_checked AS c JOIN sellers_keys AS k USING (seller_id)
WHERE c.reject_reason IS NULL AND k.n_versions > 1;

INSERT INTO quality.duplicate_log (dataset, key_value, n_rows, kind)
SELECT 'sellers', seller_id, n_rows, CASE WHEN n_versions > 1 THEN 'key_conflict' ELSE 'strict_duplicate' END
FROM sellers_keys WHERE n_rows > 1;

DROP TABLE IF EXISTS staging.sellers;
CREATE TABLE staging.sellers AS
SELECT DISTINCT ON (c.seller_id)
    c.seller_id,
    c.zip_prefix_raw,
    meta.zip_prefix(c.zip_prefix_raw) AS zip_prefix,
    c.city,
    c.state,
    c.load_id,
    c.source_line
FROM sellers_checked AS c JOIN sellers_keys AS k USING (seller_id)
WHERE c.reject_reason IS NULL AND k.n_versions = 1
ORDER BY c.seller_id, c.source_line;
