-- Assertions run INSIDE the transform transaction, before commit.
-- Any 'critical' check with violation_count > 0 rolls the whole transform back: the previously
-- published tables stay in place. 'warning' checks are counted and reported, never hidden.
-- Columns: check_name, severity, violation_count, description

-- ---------------------------------------------------------------- critical: keys & references
SELECT 'key_conflicts' AS check_name, 'critical' AS severity,
       (SELECT count(*) FROM quality.duplicate_log WHERE kind = 'key_conflict') AS violation_count,
       'Keys present with different contents in a source file (unresolved)' AS description
UNION ALL
SELECT 'orphan_references', 'critical',
       (SELECT count(*) FROM quality.quarantine WHERE reason LIKE 'orphan_%'),
       'Child rows referencing a parent key absent from the source'
-- ---------------------------------------------------------------- critical: grains
UNION ALL
SELECT 'order_summary_grain', 'critical',
       (SELECT count(*) - count(DISTINCT order_id) FROM mart.order_summary),
       'mart.order_summary must have one row per order'
UNION ALL
SELECT 'order_summary_covers_orders', 'critical',
       abs((SELECT count(*) FROM mart.order_summary) - (SELECT count(*) FROM core.orders)),
       'mart.order_summary row count equals core.orders'
UNION ALL
SELECT 'order_labels_covers_orders', 'critical',
       abs((SELECT count(*) FROM evaluation.order_labels) - (SELECT count(*) FROM core.orders)),
       'evaluation.order_labels has exactly one row per order'
UNION ALL
SELECT 'seller_exposure_grain', 'critical',
       (SELECT count(*) - count(DISTINCT (order_id, seller_id)) FROM mart.seller_order_exposure),
       'mart.seller_order_exposure has one row per distinct order-seller pair'
UNION ALL
SELECT 'seller_exposure_covers_pairs', 'critical',
       abs((SELECT count(*) FROM mart.seller_order_exposure) - (SELECT count(*) FROM core.order_sellers)),
       'Every order-seller pair counted exactly once'
-- ---------------------------------------------------------------- critical: totals invariant to joins
UNION ALL
SELECT 'item_value_reconciles', 'critical',
       CASE WHEN (SELECT coalesce(sum(item_value_brl), 0) FROM mart.order_summary)
               = (SELECT coalesce(sum(price_brl), 0) FROM core.order_items) THEN 0 ELSE 1 END,
       'Sum of item value is identical before and after the order-level join'
UNION ALL
SELECT 'freight_value_reconciles', 'critical',
       CASE WHEN (SELECT coalesce(sum(freight_value_brl), 0) FROM mart.order_summary)
               = (SELECT coalesce(sum(freight_brl), 0) FROM core.order_items) THEN 0 ELSE 1 END,
       'Sum of freight is identical before and after the order-level join'
UNION ALL
SELECT 'paid_value_reconciles', 'critical',
       CASE WHEN (SELECT coalesce(sum(paid_value_brl), 0) FROM mart.order_summary)
               = (SELECT coalesce(sum(payment_value_brl), 0) FROM core.payments) THEN 0 ELSE 1 END,
       'Sum of payments is identical before and after the order-level join'
UNION ALL
SELECT 'item_count_reconciles', 'critical',
       abs((SELECT coalesce(sum(n_items), 0) FROM mart.order_summary) - (SELECT count(*) FROM core.order_items)),
       'Item count preserved by the order-level aggregation'
UNION ALL
SELECT 'review_count_reconciles', 'critical',
       abs((SELECT coalesce(sum(n_reviews), 0) FROM mart.order_summary) - (SELECT count(*) FROM core.reviews)),
       'Review count preserved (multiple reviews per order kept)'
UNION ALL
SELECT 'category_value_reconciles', 'critical',
       CASE WHEN (SELECT coalesce(sum(item_value_brl), 0) FROM mart.order_categories)
               = (SELECT coalesce(sum(price_brl), 0) FROM core.order_items) THEN 0 ELSE 1 END,
       'mart.order_categories splits item value without duplication'
UNION ALL
SELECT 'monthly_orders_reconcile', 'critical',
       abs((SELECT coalesce(sum(orders), 0) FROM mart.monthly_operations) - (SELECT count(*) FROM mart.order_summary)),
       'mart.monthly_operations counts every order once'
UNION ALL
SELECT 'label_coverage_reconciles', 'critical',
       abs((SELECT coalesce(sum(n), 0) FROM mart.label_coverage) - (SELECT count(*) FROM core.orders)),
       'Coverage table accounts for every order'
-- ---------------------------------------------------------------- critical: label logic
UNION ALL
SELECT 'label_iff_eligible', 'critical',
       (SELECT count(*) FROM evaluation.order_labels
        WHERE (is_late IS NULL) = label_eligible OR (label_available_at IS NULL) = label_eligible),
       'Targets exist exactly for label-eligible orders'
UNION ALL
SELECT 'label_requires_scoring', 'critical',
       (SELECT count(*) FROM evaluation.order_labels WHERE label_eligible AND NOT scoring_eligible),
       'A labelled order must be scorable at approval'
UNION ALL
SELECT 'late_definition', 'critical',
       (SELECT count(*) FROM evaluation.order_labels
        WHERE label_eligible AND is_late <> (delivered_date > promised_date)),
       'is_late compares civil dates (delivered_date > promised_date)'
-- ---------------------------------------------------------------- warnings: counted anomalies
UNION ALL
SELECT 'strict_duplicate_keys', 'warning',
       (SELECT count(*) FROM quality.duplicate_log WHERE kind = 'strict_duplicate'),
       'Keys with identical duplicate rows (one row kept)'
UNION ALL
SELECT 'quarantined_rows_typing', 'warning',
       (SELECT count(*) FROM quality.quarantine WHERE reason NOT LIKE 'parent_%' AND reason NOT LIKE 'orphan_%' AND reason <> 'key_conflict'),
       'Rows rejected in staging for a missing or invalid field'
UNION ALL
SELECT 'quarantined_rows_cascade', 'warning',
       (SELECT count(*) FROM quality.quarantine WHERE reason LIKE 'parent_%'),
       'Child rows removed because their parent row was quarantined'
UNION ALL
SELECT 'orders_without_items', 'warning',
       (SELECT count(*) FROM mart.order_summary WHERE n_items = 0),
       'Orders with no item in the items file'
UNION ALL
SELECT 'orders_without_payments', 'warning',
       (SELECT count(*) FROM mart.order_summary WHERE n_payments = 0),
       'Orders with no payment row'
UNION ALL
SELECT 'orders_value_gap', 'warning',
       (SELECT count(*) FROM mart.order_summary WHERE flag_value_gap),
       'Orders where items + freight differs from payments by more than 0.01 BRL'
UNION ALL
SELECT 'orders_missing_approval', 'warning',
       (SELECT count(*) FROM core.orders WHERE approved_at IS NULL),
       'Orders without approval timestamp (not scorable; never replaced by purchase time)'
UNION ALL
SELECT 'carrier_before_approval', 'warning',
       (SELECT count(*) FROM mart.order_summary WHERE flag_carrier_before_approval),
       'Carrier hand-off recorded before approval'
UNION ALL
SELECT 'canceled_with_delivery_date', 'warning',
       (SELECT count(*) FROM mart.order_summary WHERE flag_canceled_with_delivery_date),
       'Canceled orders that nevertheless have a delivery date (kept as canceled)'
UNION ALL
SELECT 'delivered_missing_date', 'warning',
       (SELECT count(*) FROM evaluation.order_labels WHERE outcome_population = 'delivered_missing_date'),
       'Delivered status without delivery timestamp (outcome unknown)'
UNION ALL
SELECT 'products_missing_category', 'warning',
       (SELECT count(*) FROM core.products WHERE category_name_pt IS NULL),
       'Products without category (labelled missing_category)'
UNION ALL
SELECT 'categories_untranslated', 'warning',
       (SELECT count(DISTINCT category_name_pt) FROM core.products WHERE category_untranslated),
       'Categories absent from the translation file (Portuguese name kept)'
UNION ALL
SELECT 'invalid_zip_prefixes', 'warning',
       (SELECT count(*) FROM core.customers WHERE zip_prefix IS NULL)
       + (SELECT count(*) FROM core.sellers WHERE zip_prefix IS NULL),
       'Customer/seller zip prefixes missing or not 1-5 digits'
UNION ALL
SELECT 'prefixes_without_centroid', 'warning',
       (SELECT count(DISTINCT c.zip_prefix) FROM core.customers c
        LEFT JOIN core.geo_prefix g USING (zip_prefix) WHERE c.zip_prefix IS NOT NULL AND g.lat IS NULL)
       + (SELECT count(DISTINCT s.zip_prefix) FROM core.sellers s
        LEFT JOIN core.geo_prefix g USING (zip_prefix) WHERE s.zip_prefix IS NOT NULL AND g.lat IS NULL),
       'Customer/seller prefixes with no usable coordinates (distance left NULL)'
UNION ALL
SELECT 'geo_outlier_points', 'warning',
       (SELECT coalesce(sum(n_outlier_points), 0) FROM core.geo_prefix),
       'Distinct coordinates outside the Brazil bounding box, excluded from centroids'
-- ---------------------------------------------------------------- info
UNION ALL
SELECT 'distinct_customer_ids', 'info', (SELECT count(*) FROM core.customers),
       'customer_id values (order-level keys, not people)'
UNION ALL
SELECT 'distinct_customer_unique_ids', 'info', (SELECT count(DISTINCT customer_unique_id) FROM core.customers),
       'customer_unique_id values (returning-customer identifier)'
UNION ALL
SELECT 'multi_seller_orders', 'info', (SELECT count(*) FROM mart.order_summary WHERE n_distinct_sellers > 1),
       'Orders with more than one distinct seller';
