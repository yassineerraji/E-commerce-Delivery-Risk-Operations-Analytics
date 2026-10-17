-- 004_predictions_review_index.sql — added after the SQL benchmark (reports/sql_performance, Q3).
-- The risk-review page filters predictions on (run_id, model_name, prediction_date) and ranks by
-- score. Without an index the plan is Seq Scan over every run and model + Sort; with this index it
-- is an Index Only Scan already in rank order, independent of how many runs are stored.
-- Measured on the synthetic demo volume: 1.1 ms -> 0.03 ms (small in absolute terms; the table
-- grows with each run). Maintenance cost: written once per evaluation run.
-- The analogous index for the overview category filter (Q2) was NOT added: no meaningful gain.
CREATE INDEX IF NOT EXISTS predictions_review_idx
    ON evaluation.predictions (run_id, model_name, prediction_date, score_raw DESC, order_id)
    INCLUDE (score_calibrated);
