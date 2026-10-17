-- Q1a/Q1b — seller history as-of, LATERAL formulation (same logic as sql/features/020).
-- Unqualified table names: resolved in the isolated benchmark schema via search_path.
-- Input grain : targets (order, seller, prediction_at) and seller event tables
-- Output grain: one row per (order, seller)
SELECT
    t.order_id,
    t.seller_id,
    h.known_outcomes_total,
    h.known_outcomes_90d,
    h.late_outcomes_90d,
    a.approvals_30d,
    h.mean_positive_delay_90d
FROM targets AS t
CROSS JOIN LATERAL (
    SELECT
        count(*) AS known_outcomes_total,
        count(*) FILTER (WHERE e.outcome_known_at >= t.prediction_at - interval '90 days') AS known_outcomes_90d,
        count(*) FILTER (WHERE e.is_late AND e.outcome_known_at >= t.prediction_at - interval '90 days') AS late_outcomes_90d,
        avg(e.positive_delay_days) FILTER (WHERE e.outcome_known_at >= t.prediction_at - interval '90 days') AS mean_positive_delay_90d
    FROM seller_outcome_events AS e
    WHERE e.seller_id = t.seller_id
      AND e.outcome_known_at < t.prediction_at
      AND e.order_id <> t.order_id
) AS h
CROSS JOIN LATERAL (
    SELECT count(*) AS approvals_30d
    FROM seller_approval_events AS ap
    WHERE ap.seller_id = t.seller_id
      AND ap.approved_at < t.prediction_at
      AND ap.approved_at >= t.prediction_at - interval '30 days'
      AND ap.order_id <> t.order_id
) AS a
ORDER BY t.order_id, t.seller_id;
