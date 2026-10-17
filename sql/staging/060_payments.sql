-- staging.payments — output grain: one row per (order_id, payment_sequential), to be verified.

CREATE TEMP TABLE payments_checked ON COMMIT DROP AS
WITH src AS (
    SELECT
        r.load_id, r.source_line, r.parse_error,
        NULLIF(btrim(r.order_id), '')                    AS order_id,
        NULLIF(btrim(r.payment_sequential), '')          AS sequential_txt,
        lower(NULLIF(btrim(r.payment_type), ''))         AS payment_type,
        NULLIF(btrim(r.payment_installments), '')        AS installments_txt,
        NULLIF(btrim(r.payment_value), '')               AS value_txt,
        to_jsonb(r) - 'load_id' - 'source_line' - 'source_file' AS raw_record
    FROM raw.payments AS r
    WHERE r.load_id = meta.current_load_id('payments')
)
SELECT
    s.*,
    CASE
        WHEN s.parse_error IS NOT NULL                    THEN 'csv_parse_error'
        WHEN s.order_id IS NULL                           THEN 'missing_order_id'
        WHEN s.sequential_txt IS NULL                     THEN 'missing_payment_sequential'
        WHEN NOT meta.is_integer_like(s.sequential_txt)   THEN 'invalid_payment_sequential'
        WHEN s.installments_txt IS NOT NULL
             AND NOT meta.is_integer_like(s.installments_txt) THEN 'invalid_installments'
        WHEN s.value_txt IS NULL                          THEN 'missing_payment_value'
        WHEN NOT meta.is_decimal(s.value_txt)             THEN 'invalid_payment_value'
        WHEN s.value_txt::numeric < 0                     THEN 'negative_payment_value'
    END AS reject_reason,
    CASE WHEN meta.is_integer_like(s.sequential_txt) THEN s.sequential_txt::numeric::integer END AS payment_sequential,
    md5(row(s.payment_type, s.installments_txt, s.value_txt)::text) AS content_hash
FROM src AS s;

CREATE TEMP TABLE payments_keys ON COMMIT DROP AS
SELECT order_id, payment_sequential,
       count(*) AS n_rows, count(DISTINCT content_hash) AS n_versions
FROM payments_checked WHERE reject_reason IS NULL
GROUP BY 1, 2;

INSERT INTO quality.quarantine (dataset, load_id, source_line, reason, raw_record)
SELECT 'payments', load_id, source_line, reject_reason, raw_record
FROM payments_checked WHERE reject_reason IS NOT NULL
UNION ALL
SELECT 'payments', c.load_id, c.source_line, 'key_conflict', c.raw_record
FROM payments_checked AS c
JOIN payments_keys AS k USING (order_id, payment_sequential)
WHERE c.reject_reason IS NULL AND k.n_versions > 1;

INSERT INTO quality.duplicate_log (dataset, key_value, n_rows, kind)
SELECT 'payments', order_id || '#' || payment_sequential, n_rows,
       CASE WHEN n_versions > 1 THEN 'key_conflict' ELSE 'strict_duplicate' END
FROM payments_keys WHERE n_rows > 1;

DROP TABLE IF EXISTS staging.payments;
CREATE TABLE staging.payments AS
SELECT DISTINCT ON (c.order_id, c.payment_sequential)
    c.order_id,
    c.payment_sequential,
    c.payment_type,
    c.installments_txt::numeric::integer AS installments,
    c.value_txt::numeric(12, 2)          AS payment_value_brl,
    c.load_id,
    c.source_line
FROM payments_checked AS c
JOIN payments_keys AS k USING (order_id, payment_sequential)
WHERE c.reject_reason IS NULL AND k.n_versions = 1
ORDER BY c.order_id, c.payment_sequential, c.source_line;
