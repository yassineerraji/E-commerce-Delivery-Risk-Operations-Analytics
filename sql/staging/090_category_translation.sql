-- staging.category_translation — output grain: one row per Portuguese category name.

CREATE TEMP TABLE translation_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id, r.source_line, r.parse_error,
        NULLIF(btrim(r.product_category_name), '')         AS category_name_pt,
        NULLIF(btrim(r.product_category_name_english), '') AS category_name_en,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.category_translation AS r
    WHERE r.load_id = meta.current_load_id('category_translation')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL     THEN 'csv_parse_error'
        WHEN s.category_name_pt IS NULL    THEN 'missing_category_name'
        WHEN s.category_name_en IS NULL    THEN 'missing_category_translation'
    END AS reject_reason,
    md5(coalesce(s.category_name_en, '')) AS content_hash
FROM src AS s;

CREATE TEMP TABLE translation_keys ON COMMIT DROP AS
SELECT category_name_pt, count(*) AS n_rows, count(DISTINCT content_hash) AS n_versions
FROM translation_checked WHERE reject_reason IS NULL GROUP BY category_name_pt;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'category_translation', load_id, source_line, reject_reason, raw_record
FROM translation_checked WHERE reject_reason IS NOT NULL
UNION ALL
SELECT 'category_translation', c.load_id, c.source_line, 'key_conflict', c.raw_record
FROM translation_checked AS c JOIN translation_keys AS k USING (category_name_pt)
WHERE c.reject_reason IS NULL AND k.n_versions > 1;

INSERT INTO quality.duplicate_log (dataset, key_value, n_rows, kind)
SELECT 'category_translation', category_name_pt, n_rows,
       CASE WHEN n_versions > 1 THEN 'key_conflict' ELSE 'strict_duplicate' END
FROM translation_keys WHERE n_rows > 1;

DROP TABLE IF EXISTS staging.category_translation;
CREATE TABLE staging.category_translation AS
SELECT DISTINCT ON (c.category_name_pt)
    c.category_name_pt,
    c.category_name_en,
    c.load_id,
    c.source_line
FROM translation_checked AS c JOIN translation_keys AS k USING (category_name_pt)
WHERE c.reject_reason IS NULL AND k.n_versions = 1
ORDER BY c.category_name_pt, c.source_line;
