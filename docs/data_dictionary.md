# Data dictionary

Source contracts: `src/delivery_risk/ingestion/contracts.py`. Grains marked *verified* are checked
by assertions (duplicates and conflicts are logged in `quality.duplicate_log`).

## Raw (`raw.*`)

One table per source file, every source column as `text`, plus `load_id` (FK to
`meta.load_manifest`), `source_file`, `source_line` (physical line where the record starts; reviews
can span several lines) and `parse_error` (wrong field count). Only the current load of each dataset
(`meta.current_load_id`) is transformed.

`meta.load_manifest`: `load_id`, `batch_id`, `dataset`, `file_name`, `file_size_bytes`, `sha256`,
`row_count`, `parse_errors`, `source_name`, `source_version`, `data_mode`, `acquired_at`,
`acquired_at_basis` (`file_mtime` or `declared_by_user`), `loaded_at`, `status`. `(dataset, sha256)`
is unique: re-importing an identical file is a no-op.

## Core (`core.*`)

| Table | Grain (PK) | Main columns | Notes |
|---|---|---|---|
| `orders` | `order_id` | customer_id (FK), order_status, purchase_at, approved_at, delivered_carrier_at, delivered_customer_at, estimated_delivery_at | timestamps without time zone |
| `order_items` | (`order_id`, `order_item_id`) | product_id (FK), seller_id (FK), shipping_limit_at, price_brl `numeric(12,2)`, freight_brl | |
| `payments` | (`order_id`, `payment_sequential`) | payment_type, installments, payment_value_brl | `not_defined` types kept and visible |
| `reviews` | (`review_id`, `order_id`) | review_score, has_comment_title, has_comment_message, comment_message_length, created/answered | an order can have several reviews; a review id can cover several orders |
| `customers` | `customer_id` | **customer_unique_id** (returning person), zip_prefix_raw, zip_prefix (5 digits), city, state, region | `customer_id` is an order-level key: never count it as people |
| `sellers` | `seller_id` | zip_prefix_raw, zip_prefix, city, state, region | |
| `products` | `product_id` | category_name_pt, category_name_en, category (label), category_untranslated, weights and dimensions, volume_cm3 | missing category → `missing_category`; untranslated → Portuguese name |
| `order_sellers` | (`order_id`, `seller_id`) | n_items, item_value_brl, freight_value_brl | distinct order-seller pairs |
| `geo_prefix` | `zip_prefix` | lat, lng (medians of in-box distinct points), n_raw_rows, n_distinct_points, n_outlier_points | aggregated before any join |
| `brazil_states` | `state` | region (IBGE macro-region) | static reference |

## Marts and evaluation

| Table | Grain | Purpose |
|---|---|---|
| `mart.order_summary` | order | amounts (item, freight, paid — three distinct measures), counts, main category, durations, labels, quality flags |
| `mart.order_categories` | (order, category) | category filter (orders *containing* a category) and per-category amounts |
| `mart.seller_order_exposure` | (order, seller) | late-order exposure per seller; `is_multi_seller_order` flag |
| `mart.monthly_operations` | (purchase month, customer region) | volumes, late rate with denominator, medians, `LAG` change, rank under min support |
| `mart.label_coverage` | (month, population, scoring status, label status) | exclusion/coverage table; Σ n = number of orders |
| `evaluation.order_labels` | order | outcome population, scoring/label eligibility and reasons, `label_available_at`, `is_late`, `delay_days` |
| `evaluation.model_runs` | run | split/feature hashes, selected model, manifest (jsonb) |
| `evaluation.predictions` | (run, model, order) | frozen raw and calibrated scores of the calibration and test blocks |
| `evaluation.model_metrics` | (run, model, block, variant, metric) | test metrics with bootstrap intervals, n, positives |
| `evaluation.capacity_daily` | (run, model, day, q) | daily capacity simulation counts |
| `evaluation.calibration_bins`, `evaluation.metrics_by_group` | per run/model | calibration curve; metrics by month/region/group with n |

## Features

| Table | Grain | Purpose |
|---|---|---|
| `feature.seller_outcome_events` | (seller, order) | known delivery outcomes with `outcome_known_at` |
| `feature.seller_approval_events` | (seller, order) | approvals for volume counters |
| `feature.order_seller_history` | (order, seller) | as-of history per distinct seller |
| `feature.order_features` | (order, feature_version) | allow-listed predictive matrix — see `docs/feature_catalog.md` |

## Quality

`quality.quarantine` (dataset, load_id, source_line, reason, raw_record jsonb),
`quality.duplicate_log` (dataset, key_value, n_rows, kind), `quality.assertion_results`
(check, layer, severity, violation_count, description).

## Roles

`dr_pipeline` owns and writes the project schemas; `dr_reader` has `SELECT` on `meta`, `mart`,
`evaluation`, `quality`, `feature` only (not raw/staging/core) and is used by the dashboard in
read-only transactions.
