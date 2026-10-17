-- staging.products — output grain: one row per product_id.
-- A missing category is kept as NULL (reported), never filled with a guess.
-- Numeric attributes must be valid decimals when present.

CREATE TEMP TABLE products_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id, r.source_line, r.parse_error,
        NULLIF(btrim(r.product_id), '')                 AS product_id,
        NULLIF(btrim(r.product_category_name), '')      AS category_name_pt,
        NULLIF(btrim(r.product_name_lenght), '')        AS name_length_txt,
        NULLIF(btrim(r.product_description_lenght), '') AS description_length_txt,
        NULLIF(btrim(r.product_photos_qty), '')         AS photos_qty_txt,
        NULLIF(btrim(r.product_weight_g), '')           AS weight_txt,
        NULLIF(btrim(r.product_length_cm), '')          AS length_txt,
        NULLIF(btrim(r.product_height_cm), '')          AS height_txt,
        NULLIF(btrim(r.product_width_cm), '')           AS width_txt,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.products AS r
    WHERE r.load_id = meta.current_load_id('products')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL THEN 'csv_parse_error'
        WHEN s.product_id IS NULL      THEN 'missing_product_id'
        WHEN NOT coalesce(meta.is_decimal(s.name_length_txt), true)
          OR NOT coalesce(meta.is_decimal(s.description_length_txt), true)
          OR NOT coalesce(meta.is_decimal(s.photos_qty_txt), true)
          OR NOT coalesce(meta.is_decimal(s.weight_txt), true)
          OR NOT coalesce(meta.is_decimal(s.length_txt), true)
          OR NOT coalesce(meta.is_decimal(s.height_txt), true)
          OR NOT coalesce(meta.is_decimal(s.width_txt), true)  THEN 'invalid_numeric_attribute'
    END AS reject_reason,
    md5(row(s.category_name_pt, s.name_length_txt, s.description_length_txt, s.photos_qty_txt,
            s.weight_txt, s.length_txt, s.height_txt, s.width_txt)::text) AS content_hash
FROM src AS s;

CREATE TEMP TABLE products_keys ON COMMIT DROP AS
SELECT product_id, count(*) AS n_rows, count(DISTINCT content_hash) AS n_versions
FROM products_checked WHERE reject_reason IS NULL GROUP BY product_id;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'products', load_id, source_line, reject_reason, raw_record
FROM products_checked WHERE reject_reason IS NOT NULL
UNION ALL
SELECT 'products', c.load_id, c.source_line, 'key_conflict', c.raw_record
FROM products_checked AS c JOIN products_keys AS k USING (product_id)
WHERE c.reject_reason IS NULL AND k.n_versions > 1;

INSERT INTO quality.duplicate_log (dataset, key_value, n_rows, kind)
SELECT 'products', product_id, n_rows, CASE WHEN n_versions > 1 THEN 'key_conflict' ELSE 'strict_duplicate' END
FROM products_keys WHERE n_rows > 1;

DROP TABLE IF EXISTS staging.products;
CREATE TABLE staging.products AS
SELECT DISTINCT ON (c.product_id)
    c.product_id,
    c.category_name_pt,
    c.name_length_txt::numeric        AS name_length,
    c.description_length_txt::numeric AS description_length,
    c.photos_qty_txt::numeric         AS photos_qty,
    c.weight_txt::numeric             AS weight_g,
    c.length_txt::numeric             AS length_cm,
    c.height_txt::numeric             AS height_cm,
    c.width_txt::numeric              AS width_cm,
    c.load_id,
    c.source_line
FROM products_checked AS c JOIN products_keys AS k USING (product_id)
WHERE c.reject_reason IS NULL AND k.n_versions = 1
ORDER BY c.product_id, c.source_line;
