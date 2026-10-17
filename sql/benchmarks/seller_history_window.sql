-- Q1c — seller history as-of, window-function rewrite (one time-ordered event stream per seller).
--
-- Outcome events (+1 at delivery) and target rows (0 at approval) are merged per seller and
-- ordered by time. For a target row, a RANGE frame over the PRECEDING time interval counts the
-- outcomes already known. EXCLUDE GROUP removes every row with the SAME timestamp as the target,
-- which implements the strict "< t" rule (deliveries or approvals at exactly t are not counted,
-- and the target's own approval is excluded). The lower bound "INTERVAL '90 days' PRECEDING" is
-- inclusive, matching ">= t - 90 days" in the LATERAL version.
-- Unqualified table names: resolved via search_path (benchmark schema or temporary tables).
WITH outcome_stream AS (
    SELECT seller_id, outcome_known_at AS ts, NULL::text AS target_order_id,
           1 AS is_outcome, is_late::int AS late, positive_delay_days AS pos_delay
    FROM seller_outcome_events
    UNION ALL
    SELECT seller_id, prediction_at, order_id, 0, 0, NULL
    FROM targets
),
outcome_counts AS (
    SELECT
        target_order_id AS order_id,
        seller_id,
        coalesce(sum(is_outcome) OVER w_all, 0) AS known_outcomes_total,
        coalesce(sum(is_outcome) OVER w_90, 0)  AS known_outcomes_90d,
        coalesce(sum(late) OVER w_90, 0)        AS late_outcomes_90d,
        sum(pos_delay) OVER w_90::numeric / NULLIF(sum(is_outcome) OVER w_90, 0) AS mean_positive_delay_90d
    FROM outcome_stream
    WINDOW w_all AS (PARTITION BY seller_id ORDER BY ts RANGE BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW EXCLUDE GROUP),
           w_90  AS (PARTITION BY seller_id ORDER BY ts RANGE BETWEEN INTERVAL '90 days' PRECEDING AND CURRENT ROW EXCLUDE GROUP)
),
approval_stream AS (
    SELECT seller_id, approved_at AS ts, NULL::text AS target_order_id, 1 AS is_approval
    FROM seller_approval_events
    UNION ALL
    SELECT seller_id, prediction_at, order_id, 0
    FROM targets
),
approval_counts AS (
    SELECT
        target_order_id AS order_id,
        seller_id,
        coalesce(sum(is_approval) OVER (PARTITION BY seller_id ORDER BY ts
                 RANGE BETWEEN INTERVAL '30 days' PRECEDING AND CURRENT ROW EXCLUDE GROUP), 0) AS approvals_30d
    FROM approval_stream
)
SELECT
    o.order_id,
    o.seller_id,
    o.known_outcomes_total,
    o.known_outcomes_90d,
    o.late_outcomes_90d,
    a.approvals_30d,
    o.mean_positive_delay_90d
FROM outcome_counts AS o
JOIN approval_counts AS a USING (order_id, seller_id)
WHERE o.order_id IS NOT NULL
ORDER BY o.order_id, o.seller_id;
