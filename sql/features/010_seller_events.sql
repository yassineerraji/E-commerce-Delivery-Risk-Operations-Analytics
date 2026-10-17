-- =====================================================================================
-- Seller event tables used by the as-of history features.
--
-- feature.seller_outcome_events : one row per (seller, order) with a KNOWN delivery outcome.
--   outcome_known_at = actual customer delivery timestamp. An order purchased before t but
--   delivered after t carries NO outcome information at t.
-- feature.seller_approval_events: one row per (seller, order) approval, usable for volume
--   counters as soon as the approval happened.
--
-- The composite indexes match the access pattern of the as-of join: equality on seller_id
-- and a range on the event time (see reports/sql_performance for the measured effect).
-- =====================================================================================

DROP TABLE IF EXISTS feature.order_features, feature.order_seller_history,
    feature.seller_outcome_events, feature.seller_approval_events;

CREATE TABLE feature.seller_outcome_events AS
SELECT
    os.seller_id,
    os.order_id,
    l.label_available_at       AS outcome_known_at,
    l.is_late,
    GREATEST(l.delay_days, 0)  AS positive_delay_days
FROM core.order_sellers AS os
JOIN evaluation.order_labels AS l USING (order_id)
WHERE l.label_eligible;
ALTER TABLE feature.seller_outcome_events ADD PRIMARY KEY (seller_id, order_id);
CREATE INDEX seller_outcome_events_asof_idx
    ON feature.seller_outcome_events (seller_id, outcome_known_at) INCLUDE (is_late, positive_delay_days);

CREATE TABLE feature.seller_approval_events AS
SELECT os.seller_id, os.order_id, o.approved_at
FROM core.order_sellers AS os
JOIN core.orders AS o USING (order_id)
WHERE o.approved_at IS NOT NULL;
ALTER TABLE feature.seller_approval_events ADD PRIMARY KEY (seller_id, order_id);
CREATE INDEX seller_approval_events_asof_idx
    ON feature.seller_approval_events (seller_id, approved_at);
