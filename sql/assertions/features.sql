-- Assertions run inside the feature build transaction, before commit.

SELECT 'feature_grain' AS check_name, 'critical' AS severity,
       (SELECT count(*) - count(DISTINCT (order_id, feature_version)) FROM feature.order_features) AS violation_count,
       'One feature row per order and feature version' AS description
UNION ALL
SELECT 'feature_population', 'critical',
       abs((SELECT count(*) FROM feature.order_features)
           - (SELECT count(*) FROM evaluation.order_labels WHERE scoring_eligible)),
       'Every scorable order has features (regardless of final status)'
UNION ALL
SELECT 'history_grain', 'critical',
       (SELECT count(*) - count(DISTINCT (order_id, seller_id)) FROM feature.order_seller_history),
       'One history row per distinct order-seller pair'
UNION ALL
SELECT 'history_counts_coherent', 'critical',
       (SELECT count(*) FROM feature.order_seller_history
        WHERE known_outcomes_90d > known_outcomes_total
           OR late_outcomes_90d > known_outcomes_90d
           OR (known_outcomes_90d = 0) <> (late_rate_90d IS NULL)
           OR late_rate_90d NOT BETWEEN 0 AND 1
           OR approvals_30d < 0),
       'History counters are internally consistent'
UNION ALL
SELECT 'outcome_events_known_after_approval', 'critical',
       (SELECT count(*) FROM feature.seller_outcome_events e
        JOIN evaluation.order_labels l USING (order_id)
        WHERE e.outcome_known_at < l.prediction_at),
       'An outcome is never known before its own order was approved'
UNION ALL
SELECT 'forbidden_feature_columns', 'critical',
       (SELECT count(*) FROM information_schema.columns
        WHERE table_schema = 'feature' AND table_name = 'order_features'
          AND column_name !~ '^sh_'
          AND column_name ~ '(delivered|is_late|delay_days|review|status|label|payment|paid|carrier|customer_id|seller_id|unique_id)'),
       'No outcome, review, payment, status or raw identifier column in the matrix'
UNION ALL
SELECT 'sellerless_scorable_orders', 'warning',
       (SELECT count(*) FROM feature.order_features WHERE n_distinct_sellers = 0),
       'Scorable orders without seller (should be 0: no_items orders are not scorable)'
UNION ALL
SELECT 'orders_without_distance', 'warning',
       (SELECT count(*) FROM feature.order_features WHERE max_seller_distance_km IS NULL),
       'Scorable orders without a computable customer-seller distance'
UNION ALL
SELECT 'orders_with_new_seller', 'info',
       (SELECT count(*) FROM feature.order_features WHERE sh_any_seller_no_history = 1),
       'Scorable orders with at least one seller without known outcome history';
