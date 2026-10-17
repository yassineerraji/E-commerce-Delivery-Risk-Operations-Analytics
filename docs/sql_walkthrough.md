# SQL walkthrough

A progressive tour of the queries that matter, with input grain, output grain, traps and checks.
Small worked examples use the hand-written test fixtures (`tests/fixtures/edge_cases`, invented
values chosen to exercise one rule each); performance figures come from the real Olist volume.

Try every query yourself:

```bash
make db-up && make reproduce       # builds the real database (files in data/raw/olist)
source .env && PGPASSWORD=$DR_PIPELINE_PASSWORD psql -h 127.0.0.1 -p 55432 -U dr_pipeline -d delivery_risk_real
```

Layer order: `raw` (text copies) → `staging` (typed + quarantine) → `core` (keys, FKs) →
`mart` / `evaluation` (analytical tables) → `feature` (point-in-time matrix).

---

## 1. The fan-out trap and its fix

**Input grains:** `core.order_items` (order × item), `core.payments` (order × payment sequence),
`core.reviews` (review × order). **Desired output:** one row per order.

The naive query joins the three children on `order_id`:

```sql
SELECT o.order_id, sum(i.price_brl) AS item_value
FROM core.orders o
JOIN core.order_items i USING (order_id)
JOIN core.payments    p USING (order_id)
JOIN core.reviews     r USING (order_id)
GROUP BY o.order_id;
```

For fixture order `o07` (3 items, 3 payments, 2 reviews) the join produces 3 × 3 × 2 = 18 rows, so
each item price is counted 6 times: **780.00 instead of 130.00** (asserted in
`test_naive_join_would_multiply_and_mart_does_not`).

**Fix** (`sql/marts/020_order_summary.sql`): aggregate each child to the order grain in its own CTE,
then join the aggregates to the orders table (1:1 joins):

```sql
WITH item_agg AS (
    SELECT order_id, count(*) AS n_items, sum(price_brl) AS item_value_brl, sum(freight_brl) AS freight_value_brl
    FROM core.order_items GROUP BY order_id
),
payment_agg AS (
    SELECT order_id, count(*) AS n_payments, sum(payment_value_brl) AS paid_value_brl
    FROM core.payments GROUP BY order_id
),
review_agg AS (
    SELECT order_id, count(*) AS n_reviews, avg(review_score) AS review_score_mean
    FROM core.reviews GROUP BY order_id
)
SELECT o.order_id, ia.n_items, ia.item_value_brl, pa.n_payments, pa.paid_value_brl, ra.n_reviews
FROM core.orders o
LEFT JOIN item_agg    ia USING (order_id)
LEFT JOIN payment_agg pa USING (order_id)
LEFT JOIN review_agg  ra USING (order_id);
```

**Checks** (`sql/assertions/transform.sql`, run before commit): Σ item value in the mart = Σ prices
in `core.order_items`; same for freight, payments, item and review counts; one row per order. A
failure rolls the whole transform back.

Item value + freight and paid value are compared, not forced equal: `o05` shows a −5.00 gap
(`items_freight_minus_paid_brl`), flagged and counted.

## 2. Counting an order once per seller

`core.order_sellers` collapses items to distinct (order, seller) pairs:

```sql
SELECT order_id, seller_id, count(*) AS n_items, sum(price_brl) AS item_value_brl
FROM core.order_items
GROUP BY order_id, seller_id;
```

In `o07`, seller `s_b` has two items but one row. Seller exposure (`mart.seller_order_exposure`) and
seller histories are built on this grain, so a duplicated item cannot inflate a seller's count.
Delivery dates are order-level: in a multi-seller order every seller is *exposed* to the same
outcome — hence "late-order exposure", never "seller-caused delay", and seller counts are not
additive to the marketplace total.

## 3. Staging: typed, quarantined, never silently dropped

Pattern (`sql/staging/010_orders.sql`):

```sql
CASE
    WHEN s.order_id IS NULL                             THEN 'missing_order_id'
    WHEN NOT meta.is_iso_timestamp(s.purchase_txt)       THEN 'invalid_purchase_timestamp'
    ...
END AS reject_reason
```

- `CASE` evaluates branches in order, so a cast such as `price_txt::numeric < 0` only runs after
  `is_decimal(price_txt)` passed. (An `OR` or a join condition gives no such guarantee — typed keys
  are therefore computed inside a `CASE` before being joined.)
- `meta.is_iso_timestamp` = strict regex + `pg_input_is_valid` (PostgreSQL ≥ 16): `2018-02-30` is
  rejected instead of crashing the load.
- Same key twice: identical content → *strict duplicate* (one kept, logged); different content →
  *key conflict* (all versions quarantined, **critical**: publication blocked).
- A child whose parent was quarantined is cascaded (`parent_order_quarantined`, warning); a child
  whose parent key does not exist at all is an orphan (`orphan_order_reference`, critical).

## 4. Point-in-time seller history without leakage

**Input grain:** targets = (scorable order, distinct seller, t = approval); outcome events =
(seller, past order, actual delivery timestamp). **Output grain:** (order, seller).

```sql
SELECT t.order_id, t.seller_id, h.known_outcomes_total, h.known_outcomes_90d, h.late_outcomes_90d
FROM targets t
CROSS JOIN LATERAL (
    SELECT count(*) AS known_outcomes_total,
           count(*) FILTER (WHERE e.outcome_known_at >= t.prediction_at - interval '90 days') AS known_outcomes_90d,
           count(*) FILTER (WHERE e.is_late
                              AND e.outcome_known_at >= t.prediction_at - interval '90 days') AS late_outcomes_90d
    FROM feature.seller_outcome_events e
    WHERE e.seller_id = t.seller_id
      AND e.outcome_known_at < t.prediction_at   -- strict: known BEFORE t
      AND e.order_id <> t.order_id               -- never the target itself
) h;
```

Why not `LAG`/a moving average over purchase dates? Ordering by purchase date and averaging final
labels would use outcomes that were *not yet delivered* at t. Here availability depends on the
**delivery** timestamp.

Fixture walk-through for `o05` (seller `s_a`, t = 2018-01-20 10:00):

| past order | delivered | late? | usable at t? |
|---|---|---|---|
| o01 | 2018-01-15 12:00 | yes (+5) | yes |
| o02 | 2018-01-12 15:00 (promised day) | no | yes |
| o03 | 2018-01-25 18:00 | yes | **no** — delivered after t |
| o04 | 2018-01-20 10:00:00 | yes | **no** — exactly t, strict rule |

Result: 2 known outcomes, late rate 0.5, mean positive delay 2.5. Changing `o04` to 09:59:59 makes it
3 known outcomes (tested).

Equal weight per seller at order level (`sql/features/030_order_features.sql`):

```sql
SELECT order_id,
       avg(late_rate_90d)                 AS sh_late_rate_90d_mean,   -- avg ignores NULL (no history)
       max(late_rate_90d)                 AS sh_late_rate_90d_max,
       min(known_outcomes_total)          AS sh_known_outcomes_total_min,
       bool_or(known_outcomes_total = 0)  AS sh_any_seller_no_history
FROM feature.order_seller_history
GROUP BY order_id;
```

**Checks:** independent Python implementation equals the SQL on the fixtures, on 4,000 synthetic test
orders and on all 99,991 real (order, seller) rows; mutating the future (deliveries, reviews, new orders) leaves past features unchanged.

## 5. A useful window function: the same history as an event stream

`sql/benchmarks/seller_history_window.sql` computes the identical result without a correlated
subquery. Outcomes (+1 at delivery) and targets (0 at approval) are merged per seller and ordered by
time; a `RANGE` frame counts outcomes in the preceding interval:

```sql
sum(is_outcome) OVER (
    PARTITION BY seller_id ORDER BY ts
    RANGE BETWEEN INTERVAL '90 days' PRECEDING AND CURRENT ROW
    EXCLUDE GROUP)          -- drop every row with the same timestamp => strict "< t"
```

`EXCLUDE GROUP` is what makes ties conservative (a delivery at exactly t, and the target's own
approval, are excluded). The test `test_window_function_rewrite_matches_lateral_join` checks equality
row by row.

Other window functions in the marts: `LAG` for the month-over-month change of the late rate per
region, `RANK` of regions within a month only where the denominator reaches the minimum support
(`sql/marts/050_monthly_operations.sql`), `row_number()` + `count(*) OVER ()` to rank a day's orders
and compute `ceil(q × N_day)` in the dashboard (`src/delivery_risk/dashboard/data.py`).

## 6. Aggregating before joining: geolocation

The geolocation file has many rows per prefix (and exact duplicates). Joining it to orders directly
would multiply orders. `core.geo_prefix` first collapses identical points, excludes points outside a
Brazil bounding box, and takes the median latitude/longitude per prefix:

```sql
percentile_cont(0.5) WITHIN GROUP (ORDER BY lat) FILTER (WHERE in_bbox) AS lat
```

Fixture prefix `01037`: 4 raw rows → 2 distinct in-box points + 1 outlier (Paris) → median
(−23.5458, −46.63965).

## 7. Reading an execution plan (real Olist volume)

From `reports/sql_performance/real/benchmark.md` (PostgreSQL 17.11 in Docker, 8 CPUs / 4 GB VM,
warm cache, median of 5 runs), query Q1 — the seller history for 99,991 (order, seller) targets
against 97,733 outcome events:

```
before (no index) — cancelled after 300 s    after (composite index) — 4.2 s
Sort                                          Sort
  Nested Loop                                   Nested Loop
    Seq Scan on targets                           Seq Scan on targets
    Memoize                                       Memoize
      Aggregate                                     Aggregate
        Seq Scan on seller_outcome_events             Index Only Scan using bench_outcome_asof
```

- The `LATERAL` subquery runs once per distinct (seller, t); `Memoize` caches repeated keys.
- Before: every execution scans the **whole** events table (~10⁵ targets × ~10⁵ events) — the query
  did not finish within the 300 s limit.
- After: `(seller_id, outcome_known_at) INCLUDE (is_late, positive_delay_days, order_id)` lets
  PostgreSQL jump to one seller's events before t and read them from the index only: **4.2 s**,
  identical results.
- The window-function rewrite needs no index but takes **11.3 s** at this volume. A likely reason:
  with `EXCLUDE GROUP`, PostgreSQL cannot update the moving aggregate incrementally and re-aggregates
  each frame, which is expensive for large sellers with long histories.
- Q2 (overview aggregation, 99k orders): `Seq Scan` + `Hash Join` in 18 ms; an index on
  `order_categories(category, order_id)` brings it to 13 ms — not added. A sequential scan is the
  right plan for a small table.
- Q3 (one day of the risk review): `Seq Scan` over 133,636 predictions + `Sort` = 10.5 ms;
  with the composite index of migration 004 the plan is an `Index Only Scan` already in rank order:
  0.2 ms.

Run `EXPLAIN (ANALYZE, BUFFERS)` yourself on any query; the plans saved as JSON are in
`reports/sql_performance/real/plans/`.

## 8. Exercises to practise

1. Change the 90-day window to 60 days in `sql/features/020_order_seller_history.sql`, run
   `make features`, and predict which fixture test changes.
2. Remove `EXCLUDE GROUP` from the window rewrite: which rows change, and why?
3. Write the naive join of §1 for the whole real database and compare
   `sum(item_value)` with `mart.order_summary`.
4. Add a filter on `customer_state` to `mart.monthly_operations` and check that
   `sum(orders)` still equals the number of orders.
