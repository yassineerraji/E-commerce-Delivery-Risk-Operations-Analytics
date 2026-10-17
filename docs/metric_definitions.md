# Metric definitions

Every KPI is shown with its **period, population, unit and denominator**. Timestamps are kept
without time zone as in the source (presumably Brazilian local time); no UTC conversion is invented.

## Dates and durations (civil-date convention)

| Name | Definition | Notes |
|---|---|---|
| `prediction_at` | `order_approved_at` | Never replaced by the purchase timestamp when missing (order not scorable). |
| `purchase_date` | `order_purchase_timestamp::date` | |
| `promised_date` | `order_estimated_delivery_date::date` | Stored at midnight in the source. |
| `delivered_date` | `order_delivered_customer_date::date` | |
| `promised_lead_days` | `promised_date − purchase_date` (integer days) | |
| `delivery_duration_days` | `delivered_date − purchase_date` (integer days) | Eligible delivered orders only. |
| `delay_days` | `delivered_date − promised_date` | Negative = early; kept as is for distribution analysis. |
| `positive_delay_days` | `GREATEST(delay_days, 0)` | Severity of lateness. |
| `approval_delay_hours` | `(approved_at − purchase_at)` in hours | |

## Target

`is_late = delivered_date > promised_date` on a label-eligible order. Because dates (not timestamps)
are compared, a delivery at 15:00 on the promised day is **on time**, even though the promise is
stored at 00:00 (fixture `o02`, tested).

## Populations and eligibility (`evaluation.order_labels`)

| Population | Rule |
|---|---|
| `delivered` | status `delivered` with a delivery timestamp |
| `delivered_missing_date` | status `delivered` without delivery timestamp — outcome unknown |
| `canceled`, `unavailable` | final statuses without a delivery outcome (never counted as on time) |
| `not_observed` | `created`, `approved`, `invoiced`, `processing`, `shipped` at export time |

**Scoring eligibility** (information available at approval only), first failing rule:
`missing_approval`, `missing_promised_date`, `no_items`, `approval_before_purchase`,
`promise_before_purchase`, `promise_before_approval`. Scorable orders get features and scores
whatever their final status.

**Label eligibility**: scorable AND population `delivered` AND not `delivery_before_approval`.
Exclusion reasons are listed per order and summarised in `mart.label_coverage` and
`reports/data_quality/<mode>/data_quality_report.md`.

## Operational KPIs (dashboard page A, `mart.monthly_operations`)

| KPI | Numerator / denominator | Unit |
|---|---|---|
| Orders | count of orders in the filtered population (all statuses) | orders |
| Item value | Σ item prices of those orders (all their items) | BRL — not Olist revenue, not margin |
| Freight | Σ freight values | BRL |
| Paid value | Σ payment values (separate source; differences reported, not forced) | BRL |
| `late_rate` | late eligible delivered orders / eligible delivered orders | share |
| Median delivery time | median `delivery_duration_days` of eligible delivered orders | days |
| Outcome coverage | eligible delivered / all orders | share |
| Late-order exposure (seller) | seller's late eligible delivered orders / its eligible delivered orders, each order counted once per seller | share — not "seller-caused delay"; not additive |

Period filter = purchase date. Category filter = orders **containing** at least one item of a
selected category (counted once); amounts include all items of those orders. Segments and seller
rankings with fewer than `analysis.min_support_for_ranking` (30) eligible delivered orders are hidden.

## Model metrics (test block, label-eligible orders)

| Metric | Definition |
|---|---|
| **AP** | `sklearn.metrics.average_precision_score`: Σₙ (Rₙ − Rₙ₋₁) Pₙ — a step-wise sum over thresholds, **not** a trapezoidal area under the PR curve. Baseline value = prevalence. |
| ROC-AUC | secondary; insensitive to prevalence, so it can look good when precision is poor. |
| Brier score | mean (p − y)²; reported raw and calibrated. |
| Log loss | scores clipped to [1e-6, 1 − 1e-6] before computing (finite with extreme scores). |
| Precision@q | k = ceil(q · N); orders sorted by score desc, ties by `order_id` asc; TP_selected / k |
| Recall@q | TP_selected / positives |
| Lift@q | Precision@q / prevalence |

Undefined cases are reported as such: no row → `undefined_empty`; no positive → AP, ROC-AUC,
Recall, Lift are `None` with status `undefined_no_positive` (never 0, never silently divided).

**Uncertainty:** paired bootstrap over ISO weeks of the test block, 500 resamples, seed fixed in
`config/project.yaml`. All models are evaluated on the same resampled weeks; resamples where a
metric is undefined are skipped and counted. With ~13 weeks the intervals are rough.

## Two different top-q analyses

1. **Global top-q on the labelled test subset** — compares models (one ranking of all labelled
   test orders).
2. **Daily capacity simulation** — for each historical day, the top `ceil(q · N_day)` of *all
   scorable* orders approved that day are selected before any outcome is looked at. Reported:
   effective share reviewed (> q because of rounding), share of selected orders with a known
   outcome, late rate among selected orders with a known outcome, late rate of all known orders,
   share of known late orders captured. This simulates a daily batch review policy; the scores
   themselves were produced at approval. It is **not** fully observable: canceled or undelivered
   selected orders have no outcome.
