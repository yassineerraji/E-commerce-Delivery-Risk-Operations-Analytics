-- Q3 — filtered order extraction (risk review page): one day's scored orders for one run and
-- model, ranked by score, first page. Parameters: %(run)s, %(model)s, %(day)s.
-- Input grain : predictions (order x model x run)
-- Output grain: one row per order of that day (first 15 by risk)
SELECT
    p.order_id,
    p.score_raw,
    p.score_calibrated,
    row_number() OVER (ORDER BY p.score_raw DESC, p.order_id) AS risk_rank,
    count(*) OVER () AS n_day
FROM predictions AS p
WHERE p.run_id = %(run)s AND p.model_name = %(model)s AND p.prediction_date = %(day)s
ORDER BY p.score_raw DESC, p.order_id
LIMIT 15;
