-- staging.reviews — output grain: one row per (review_id, order_id).
-- Neither review_id nor order_id is assumed unique on its own: one order can receive several
-- reviews and one review_id can be attached to several orders. Review text stays in raw;
-- only presence flags and lengths are carried forward (never used as predictive features).

CREATE TEMP TABLE reviews_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id, r.source_line, r.parse_error,
        NULLIF(btrim(r.review_id), '')               AS review_id,
        NULLIF(btrim(r.order_id), '')                AS order_id,
        NULLIF(btrim(r.review_score), '')            AS score_txt,
        NULLIF(btrim(r.review_comment_title), '')    AS comment_title,
        NULLIF(btrim(r.review_comment_message), '')  AS comment_message,
        NULLIF(btrim(r.review_creation_date), '')    AS created_txt,
        NULLIF(btrim(r.review_answer_timestamp), '') AS answered_txt,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.reviews AS r
    WHERE r.load_id = meta.current_load_id('reviews')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL                    THEN 'csv_parse_error'
        WHEN s.review_id IS NULL                          THEN 'missing_review_id'
        WHEN s.order_id IS NULL                           THEN 'missing_order_id'
        WHEN s.score_txt IS NULL                          THEN 'missing_review_score'
        WHEN NOT meta.is_integer_like(s.score_txt)        THEN 'invalid_review_score'
        WHEN s.score_txt::numeric NOT BETWEEN 1 AND 5     THEN 'invalid_review_score'
        WHEN s.created_txt IS NOT NULL AND NOT meta.is_iso_timestamp(s.created_txt)   THEN 'invalid_review_creation_date'
        WHEN s.answered_txt IS NOT NULL AND NOT meta.is_iso_timestamp(s.answered_txt) THEN 'invalid_review_answer_timestamp'
    END AS reject_reason,
    md5(row(s.score_txt, s.comment_title, s.comment_message, s.created_txt, s.answered_txt)::text) AS content_hash
FROM src AS s;

CREATE TEMP TABLE reviews_keys ON COMMIT DROP AS
SELECT review_id, order_id, count(*) AS n_rows, count(DISTINCT content_hash) AS n_versions
FROM reviews_checked WHERE reject_reason IS NULL GROUP BY review_id, order_id;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'reviews', load_id, source_line, reject_reason, raw_record
FROM reviews_checked WHERE reject_reason IS NOT NULL
UNION ALL
SELECT 'reviews', c.load_id, c.source_line, 'key_conflict', c.raw_record
FROM reviews_checked AS c JOIN reviews_keys AS k USING (review_id, order_id)
WHERE c.reject_reason IS NULL AND k.n_versions > 1;

INSERT INTO quality.duplicate_log (dataset, key_value, n_rows, kind)
SELECT 'reviews', review_id || '#' || order_id, n_rows,
       CASE WHEN n_versions > 1 THEN 'key_conflict' ELSE 'strict_duplicate' END
FROM reviews_keys WHERE n_rows > 1;

DROP TABLE IF EXISTS staging.reviews;
CREATE TABLE staging.reviews AS
SELECT DISTINCT ON (c.review_id, c.order_id)
    c.review_id,
    c.order_id,
    c.score_txt::numeric::integer   AS review_score,
    (c.comment_title IS NOT NULL)   AS has_comment_title,
    (c.comment_message IS NOT NULL) AS has_comment_message,
    length(c.comment_message)       AS comment_message_length,
    c.created_txt::timestamp        AS review_created_at,
    c.answered_txt::timestamp       AS review_answered_at,
    c.load_id,
    c.source_line
FROM reviews_checked AS c JOIN reviews_keys AS k USING (review_id, order_id)
WHERE c.reject_reason IS NULL AND k.n_versions = 1
ORDER BY c.review_id, c.order_id, c.source_line;
