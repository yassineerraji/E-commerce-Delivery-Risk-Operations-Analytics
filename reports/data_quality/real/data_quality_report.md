# Data quality report — real


Generated 2026-10-17 17:36 UTC from the published tables.

## 1. Source files (current load)

| dataset | file | bytes | sha256 | rows | parse errors | source version | acquired | acquired basis | loaded at | load id | batch |
|---|---|---|---|---|---|---|---|---|---|---|---|
| category_translation | product_category_name_translation.csv | 2,613 | a81f0d1f27b27e72… | 71 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 9 | 20261017T173518Z-40818e6d |
| customers | olist_customers_dataset.csv | 9,033,957 | 983a422239e1712d… | 99,441 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 3 | 20261017T173518Z-40818e6d |
| geolocation | olist_geolocation_dataset.csv | 61,273,883 | b514f6fc991b9566… | 1,000,163 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 8 | 20261017T173518Z-40818e6d |
| order_items | olist_order_items_dataset.csv | 15,438,671 | 0bc4d068c4fe38cb… | 112,650 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 2 | 20261017T173518Z-40818e6d |
| orders | olist_orders_dataset.csv | 17,654,914 | 8df58ef3d2d7e994… | 99,441 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 1 | 20261017T173518Z-40818e6d |
| payments | olist_order_payments_dataset.csv | 5,777,138 | 4f713964f2815dbb… | 103,886 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 6 | 20261017T173518Z-40818e6d |
| products | olist_products_dataset.csv | 2,379,446 | 3e6569628a17fbc7… | 32,951 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 5 | 20261017T173518Z-40818e6d |
| reviews | olist_order_reviews_dataset.csv | 14,451,670 | 012b61c7593e34f5… | 99,224 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 7 | 20261017T173518Z-40818e6d |
| sellers | olist_sellers_dataset.csv | 174,703 | 1f643d2b950373b8… | 3,095 | 0 | 2 | 2026-10-17 | file_mtime | 2026-10-17 17:35 | 4 | 20261017T173518Z-40818e6d |

Row counts exclude the header. `acquired basis = file_mtime` means the acquisition date is the file modification time, not a date declared by the user (`--acquired-on`).

## 2. Published row counts

| table | rows |
|---|---|
| core.sellers | 3,095 |
| core.geo_prefix | 19,015 |
| core.products | 32,951 |
| core.payments | 103,886 |
| core.reviews | 99,224 |
| core.customers | 99,441 |
| core.order_sellers | 100,010 |
| mart.order_summary | 99,441 |
| mart.seller_order_exposure | 100,010 |
| core.order_items | 112,650 |
| core.orders | 99,441 |

## 3. Assertions

| layer | check | severity | violations | result |
|---|---|---|---|---|
| transform | category_value_reconciles | critical | 0 | pass |
| transform | freight_value_reconciles | critical | 0 | pass |
| transform | item_count_reconciles | critical | 0 | pass |
| transform | item_value_reconciles | critical | 0 | pass |
| transform | key_conflicts | critical | 0 | pass |
| transform | label_coverage_reconciles | critical | 0 | pass |
| transform | label_iff_eligible | critical | 0 | pass |
| transform | label_requires_scoring | critical | 0 | pass |
| transform | late_definition | critical | 0 | pass |
| transform | monthly_orders_reconcile | critical | 0 | pass |
| transform | order_labels_covers_orders | critical | 0 | pass |
| transform | order_summary_covers_orders | critical | 0 | pass |
| transform | order_summary_grain | critical | 0 | pass |
| transform | orphan_references | critical | 0 | pass |
| transform | paid_value_reconciles | critical | 0 | pass |
| transform | review_count_reconciles | critical | 0 | pass |
| transform | seller_exposure_covers_pairs | critical | 0 | pass |
| transform | seller_exposure_grain | critical | 0 | pass |
| transform | canceled_with_delivery_date | warning | 6 | FAIL |
| transform | carrier_before_approval | warning | 1,359 | FAIL |
| transform | categories_untranslated | warning | 2 | FAIL |
| transform | delivered_missing_date | warning | 8 | FAIL |
| transform | geo_outlier_points | warning | 33 | FAIL |
| transform | invalid_zip_prefixes | warning | 0 | pass |
| transform | orders_missing_approval | warning | 160 | FAIL |
| transform | orders_value_gap | warning | 303 | FAIL |
| transform | orders_without_items | warning | 775 | FAIL |
| transform | orders_without_payments | warning | 1 | FAIL |
| transform | prefixes_without_centroid | warning | 165 | FAIL |
| transform | products_missing_category | warning | 610 | FAIL |
| transform | quarantined_rows_cascade | warning | 0 | pass |
| transform | quarantined_rows_typing | warning | 0 | pass |
| transform | strict_duplicate_keys | warning | 0 | pass |
| transform | distinct_customer_ids | info | 99,441 | info |
| transform | distinct_customer_unique_ids | info | 96,096 | info |
| transform | multi_seller_orders | info | 1,278 | info |

## 4. Quarantine (rows rejected with a reason)

_No rows._

## 5. Duplicate keys

`strict_duplicate`: identical rows, one kept. `key_conflict`: different contents for the same key (critical, blocks publication).

_No rows._

## 6. Outcome populations and exclusions

Every order appears once. Canceled, unavailable and undelivered orders are separate populations, never counted as on time.

| outcome population | scoring status | label status | orders | % of orders |
|---|---|---|---|---|
| delivered | scorable | label_eligible | 96,394 | 96.9400 |
| not_observed | scorable | outcome_not_observed | 1,720 | 1.7300 |
| unavailable | no_items | no_items | 603 | 0.6100 |
| canceled | scorable | outcome_canceled | 461 | 0.4600 |
| canceled | missing_approval | missing_approval | 141 | 0.1400 |
| delivered | scorable | delivery_before_approval | 58 | 0.0600 |
| canceled | no_items | no_items | 23 | 0.0200 |
| delivered | missing_approval | missing_approval | 14 | 0.0100 |
| delivered_missing_date | scorable | outcome_delivered_missing_date | 8 | 0.0100 |
| unavailable | scorable | outcome_unavailable | 6 | 0.0100 |
| not_observed | missing_approval | missing_approval | 5 | 0.0100 |
| delivered | promise_before_approval | promise_before_approval | 4 | 0.0000 |
| not_observed | no_items | no_items | 3 | 0.0000 |
| not_observed | promise_before_approval | promise_before_approval | 1 | 0.0000 |
