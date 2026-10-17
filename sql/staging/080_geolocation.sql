-- staging.geolocation
-- Output grain: one row per valid CSV record. Several rows per zip prefix are LEGITIMATE
-- (no key is imposed); identical rows are kept here and collapsed in core.geo_prefix.

CREATE TEMP TABLE geo_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id, r.source_line, r.parse_error,
        NULLIF(btrim(r.geolocation_zip_code_prefix), '') AS zip_prefix_raw,
        NULLIF(btrim(r.geolocation_lat), '')             AS lat_txt,
        NULLIF(btrim(r.geolocation_lng), '')             AS lng_txt,
        NULLIF(btrim(r.geolocation_city), '')            AS city,
        upper(NULLIF(btrim(r.geolocation_state), ''))    AS state,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.geolocation AS r
    WHERE r.load_id = meta.current_load_id('geolocation')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL              THEN 'csv_parse_error'
        WHEN meta.zip_prefix(s.zip_prefix_raw) IS NULL THEN 'invalid_zip_prefix'
        WHEN s.lat_txt IS NULL OR NOT meta.is_decimal(s.lat_txt) THEN 'invalid_latitude'
        WHEN s.lng_txt IS NULL OR NOT meta.is_decimal(s.lng_txt) THEN 'invalid_longitude'
    END AS reject_reason
FROM src AS s;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'geolocation', load_id, source_line, reject_reason, raw_record
FROM geo_checked WHERE reject_reason IS NOT NULL;

DROP TABLE IF EXISTS staging.geolocation;
CREATE TABLE staging.geolocation AS
SELECT
    meta.zip_prefix(zip_prefix_raw) AS zip_prefix,
    zip_prefix_raw,
    lat_txt::double precision       AS lat,
    lng_txt::double precision       AS lng,
    city,
    state,
    load_id,
    source_line
FROM geo_checked
WHERE reject_reason IS NULL;
