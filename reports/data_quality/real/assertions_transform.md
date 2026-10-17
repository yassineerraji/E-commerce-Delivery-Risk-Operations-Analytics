# Assertions — transform (real)

Checked at 2026-10-17 17:36 UTC. Status: **PUBLISHED**.

Critical checks block publication when violated; warnings are counted and reported.

| check | severity | violations | result | description |
|---|---|---|---|---|
| category_value_reconciles | critical | 0 | pass | mart.order_categories splits item value without duplication |
| freight_value_reconciles | critical | 0 | pass | Sum of freight is identical before and after the order-level join |
| item_count_reconciles | critical | 0 | pass | Item count preserved by the order-level aggregation |
| item_value_reconciles | critical | 0 | pass | Sum of item value is identical before and after the order-level join |
| key_conflicts | critical | 0 | pass | Keys present with different contents in a source file (unresolved) |
| label_coverage_reconciles | critical | 0 | pass | Coverage table accounts for every order |
| label_iff_eligible | critical | 0 | pass | Targets exist exactly for label-eligible orders |
| label_requires_scoring | critical | 0 | pass | A labelled order must be scorable at approval |
| late_definition | critical | 0 | pass | is_late compares civil dates (delivered_date > promised_date) |
| monthly_orders_reconcile | critical | 0 | pass | mart.monthly_operations counts every order once |
| order_labels_covers_orders | critical | 0 | pass | evaluation.order_labels has exactly one row per order |
| order_summary_covers_orders | critical | 0 | pass | mart.order_summary row count equals core.orders |
| order_summary_grain | critical | 0 | pass | mart.order_summary must have one row per order |
| orphan_references | critical | 0 | pass | Child rows referencing a parent key absent from the source |
| paid_value_reconciles | critical | 0 | pass | Sum of payments is identical before and after the order-level join |
| review_count_reconciles | critical | 0 | pass | Review count preserved (multiple reviews per order kept) |
| seller_exposure_covers_pairs | critical | 0 | pass | Every order-seller pair counted exactly once |
| seller_exposure_grain | critical | 0 | pass | mart.seller_order_exposure has one row per distinct order-seller pair |
| canceled_with_delivery_date | warning | 6 | FAIL | Canceled orders that nevertheless have a delivery date (kept as canceled) |
| carrier_before_approval | warning | 1,359 | FAIL | Carrier hand-off recorded before approval |
| categories_untranslated | warning | 2 | FAIL | Categories absent from the translation file (Portuguese name kept) |
| delivered_missing_date | warning | 8 | FAIL | Delivered status without delivery timestamp (outcome unknown) |
| geo_outlier_points | warning | 33 | FAIL | Distinct coordinates outside the Brazil bounding box, excluded from centroids |
| invalid_zip_prefixes | warning | 0 | pass | Customer/seller zip prefixes missing or not 1-5 digits |
| orders_missing_approval | warning | 160 | FAIL | Orders without approval timestamp (not scorable; never replaced by purchase time) |
| orders_value_gap | warning | 303 | FAIL | Orders where items + freight differs from payments by more than 0.01 BRL |
| orders_without_items | warning | 775 | FAIL | Orders with no item in the items file |
| orders_without_payments | warning | 1 | FAIL | Orders with no payment row |
| prefixes_without_centroid | warning | 165 | FAIL | Customer/seller prefixes with no usable coordinates (distance left NULL) |
| products_missing_category | warning | 610 | FAIL | Products without category (labelled missing_category) |
| quarantined_rows_cascade | warning | 0 | pass | Child rows removed because their parent row was quarantined |
| quarantined_rows_typing | warning | 0 | pass | Rows rejected in staging for a missing or invalid field |
| strict_duplicate_keys | warning | 0 | pass | Keys with identical duplicate rows (one row kept) |
| distinct_customer_ids | info | 99,441 | info | customer_id values (order-level keys, not people) |
| distinct_customer_unique_ids | info | 96,096 | info | customer_unique_id values (returning-customer identifier) |
| multi_seller_orders | info | 1,278 | info | Orders with more than one distinct seller |
