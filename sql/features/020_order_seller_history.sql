-- =====================================================================================
-- feature.order_seller_history — one row per (scorable order, distinct seller).
--
-- Point-in-time contract for a target order approved at t (= prediction_at):
--   * an outcome is usable only if outcome_known_at < t   (STRICT: a delivery at exactly t
--     is excluded; ties are treated conservatively);
--   * the 90-day window is anchored on delivery events: outcome_known_at in [t - 90 d, t);
--   * approval volume counts other orders approved in [t - 30 d, t) (strictly before t, so
--     an order approved at the same second is not counted);
--   * the target order itself is always excluded.
-- Empty history -> counts = 0 and late_rate = NULL (imputed later inside the model pipeline
-- fitted on training data only).
--
-- Known bias: the rate describes DELIVERED orders only. Slow orders still open at t are
-- missing from the denominator, so recent late rates can be under-estimated.
-- =====================================================================================

CREATE TABLE feature.order_seller_history AS
WITH targets AS (
    SELECT l.order_id, os.seller_id, l.prediction_at
    FROM evaluation.order_labels AS l
    JOIN core.order_sellers AS os USING (order_id)
    WHERE l.scoring_eligible
)
SELECT
    t.order_id,
    t.seller_id,
    t.prediction_at,
    h.known_outcomes_total,
    h.known_outcomes_90d,
    h.late_outcomes_90d,
    h.late_outcomes_90d::numeric / NULLIF(h.known_outcomes_90d, 0) AS late_rate_90d,
    h.mean_positive_delay_90d,
    a.approvals_30d
FROM targets AS t
CROSS JOIN LATERAL (
    SELECT
        count(*)                                                                    AS known_outcomes_total,
        count(*) FILTER (WHERE e.outcome_known_at >= t.prediction_at - interval '90 days') AS known_outcomes_90d,
        count(*) FILTER (WHERE e.is_late
                           AND e.outcome_known_at >= t.prediction_at - interval '90 days') AS late_outcomes_90d,
        avg(e.positive_delay_days)
            FILTER (WHERE e.outcome_known_at >= t.prediction_at - interval '90 days')     AS mean_positive_delay_90d
    FROM feature.seller_outcome_events AS e
    WHERE e.seller_id = t.seller_id
      AND e.outcome_known_at < t.prediction_at
      AND e.order_id <> t.order_id
) AS h
CROSS JOIN LATERAL (
    SELECT count(*) AS approvals_30d
    FROM feature.seller_approval_events AS ap
    WHERE ap.seller_id = t.seller_id
      AND ap.approved_at <  t.prediction_at
      AND ap.approved_at >= t.prediction_at - interval '30 days'
      AND ap.order_id <> t.order_id
) AS a;

ALTER TABLE feature.order_seller_history ADD PRIMARY KEY (order_id, seller_id);
