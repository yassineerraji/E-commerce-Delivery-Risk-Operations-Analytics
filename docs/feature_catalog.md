# Feature catalog (feature_version `v1`)

Single source of truth for the allow-list: `src/delivery_risk/features/catalog.py`. The SQL matrix
`feature.order_features` (`sql/features/030_order_features.sql`) must expose exactly these columns
plus the technical keys `order_id`, `feature_version`, `prediction_at` — checked at build time
(the build is rolled back otherwise) and by tests.

**Prediction time** `t = order_approved_at`. Orders without approval are not scored; the purchase
timestamp is never used as a substitute.

**Important limit of the source.** Olist is a final historical export, not a versioned event log.
For static attributes (products, prices, freight, promise, locations) we *assume* the value in the
export is the value known at approval. This is not verifiable. The `no_catalog_static` ablation
retrains without the product-catalog group to measure sensitivity to that assumption.

Legend — *Availability*: **observed** = timestamp in the data proves availability before t;
**assumed** = plausible but unverifiable. *Leak risk*: low / medium / high.

## Group `order` — order attributes known at approval

| Feature | Definition | Source | Grain → transform | Availability at t | Missing value | Rationale | Leak risk |
|---|---|---|---|---|---|---|---|
| `n_items` | number of items | order_items | items → count per order | assumed (cart fixed at purchase) | never (scorable orders have ≥ 1 item) | basket complexity | low |
| `n_distinct_products` | distinct products | order_items | count distinct | assumed | never | basket diversity | low |
| `n_distinct_sellers` | distinct sellers | order_items | count distinct | assumed | never | multi-seller coordination | low |
| `item_value_brl` | sum of item prices | order_items.price | sum (numeric → float) | assumed | never | value/handling care | low |
| `freight_value_brl` | sum of freight | order_items.freight_value | sum | assumed (quoted at checkout) | never | proxy for distance/weight | low |
| `freight_to_item_value_ratio` | freight / item value | derived | ratio, NULLIF(0) | assumed | NULL if item value 0 → median imputation | relative shipping cost | low |
| `promised_lead_days` | promised date − purchase date (civil days) | orders.estimated_delivery | date difference | assumed (shown to the customer at purchase per source description) | never | slack of the promise | medium (if the estimate were revised later) |
| `approval_delay_hours` | approval − purchase, hours | orders | timestamp difference | **observed** (both ≤ t) | never | payment method / fraud checks | low |
| `shipping_limit_days_after_approval` | earliest item shipping limit − approval, days | order_items.shipping_limit_date | min per order | assumed (deadline set when the order is processed) | NULL if missing → median | seller handling deadline | medium |
| `approval_weekday` | ISO weekday of approval (categorical) | orders.approved_at | extract | **observed** | never | weekend handling | low |
| `approval_hour` | hour of approval | orders.approved_at | extract | **observed** | never | same-day processing | low |

## Group `geo` — locations

| Feature | Definition | Source | Grain → transform | Availability | Missing value | Rationale | Leak risk |
|---|---|---|---|---|---|---|---|
| `customer_state` | customer federative unit (categorical) | customers | 1 row per order | assumed (address at checkout) | constant `__missing__` → own one-hot | regional logistics | low |
| `primary_seller_state` | state of the seller with the largest item value (tie: seller_id) | sellers | DISTINCT ON per order | assumed | `__missing__` | origin hub | low |
| `any_seller_same_state` | 1 if any seller is in the customer's state | customers, sellers | bool_or over order sellers | assumed | never | local delivery | low |
| `max_seller_distance_km` | max haversine distance customer prefix ↔ seller prefix | geo_prefix centroids | prefix medians computed first, then joined; max over sellers | assumed | NULL if a prefix has no centroid → median + indicator | transit length | low (approximate: prefix centroids) |

## Group `catalog` — product snapshot (ablated in the sensitivity analysis)

| Feature | Definition | Source | Grain → transform | Availability | Missing value | Rationale | Leak risk |
|---|---|---|---|---|---|---|---|
| `main_category` | category of the most expensive item (tie: lowest item id) | products + translation | DISTINCT ON | **assumed** (catalog may have been edited later) | `missing_category` label | handling difficulty | medium |
| `n_distinct_categories` | distinct categories in the order | products | count distinct | assumed | never | heterogeneous basket | low |
| `total_weight_g` | sum of product weights (NULL if any is missing) | products | sum with completeness check | assumed | NULL → median + indicator | heavy parcels | medium |
| `max_product_volume_cm3` | largest product volume | products | max of L×H×W | assumed | NULL → median + indicator | bulky parcels | medium |

## Group `seller_history` — as-of seller history (strictly before t)

Built in `sql/features/020_order_seller_history.sql` at grain (order, distinct seller), then
aggregated per order with **equal weight per distinct seller** (a seller appearing on two items
counts once — tested).

An outcome of a past order is usable only if its **actual customer delivery** happened strictly
before t (`outcome_known_at < t`). An order bought before t but delivered after t contributes no
outcome. The target order is excluded. Ties (delivery at exactly t) are excluded.

| Feature | Definition | Availability | Missing value | Leak risk |
|---|---|---|---|---|
| `sh_known_outcomes_total_min` | min over sellers of the number of known outcomes before t | **observed** (delivery timestamps < t) | 0 when no history | low |
| `sh_known_outcomes_90d_min` | min over sellers of known outcomes with delivery in [t − 90 d, t) | observed | 0 | low |
| `sh_late_rate_90d_mean` | mean over sellers of late / known in the 90-day window | observed | NULL when no seller has a 90-day outcome → median imputation fitted on train; also used by the baseline with the train prevalence as fallback | low |
| `sh_late_rate_90d_max` | max over sellers of the same rate | observed | NULL → median + indicator | low |
| `sh_mean_positive_delay_90d_mean` | mean over sellers of mean `GREATEST(delay_days, 0)` in the window | observed | NULL → median + indicator | low |
| `sh_approvals_30d_mean` | mean over sellers of other orders approved in [t − 30 d, t) | observed (approval timestamps < t) | 0 | low |
| `sh_approvals_30d_max` | max over sellers of the same count | observed | 0 | low |
| `sh_any_seller_no_history` | 1 if at least one seller has no known outcome before t | observed | never | low |

**Availability bias (documented, not corrected):** the rates describe *delivered* orders only.
Slow orders still in transit at t are missing from the denominator, so a seller's recent late rate
can be under-estimated exactly when it is degrading. No smoothing prior is applied in v1.

During the test block, histories naturally include deliveries that happened before each score
(e.g. a delivery in week 2 of the test is visible to an order approved in week 3). This is an
update of the available data, not retraining; models and calibrators stay frozen.

## Excluded on purpose

| Column(s) | Why |
|---|---|
| delivery dates, carrier hand-off after approval, durations, `is_late`, `delay_days` | target or post-approval events |
| `order_status`, outcome population | final status, known only at the end |
| reviews (score, text, dates) | written after delivery |
| payments (type, installments, value) | no reliable availability log; kept for descriptive analysis only |
| `order_id`, `customer_id`, `customer_unique_id`, `seller_id`, `product_id` | technical keys; would let the model memorise entities |
| any aggregate over the whole dataset (e.g. a seller's overall late rate) | uses future outcomes |

The forbidden list is enforced in three places: the Python allow-list (`FORBIDDEN_COLUMNS`), a SQL
assertion on column names, and unit tests.
