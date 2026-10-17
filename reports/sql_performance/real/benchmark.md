# SQL benchmark — real


Measured 2026-10-17T17:41 UTC in the isolated `bench` schema. 1 warm-up execution, then N timed executions; caches not flushed (warm); N = 5.

## Environment

| item | value |
|---|---|
| PostgreSQL | PostgreSQL 17.11 (Debian 17.11-1.pgdg12+2) |
| Docker VM | 8 CPUs, 4.1 GB |
| Host | macOS-15.7.7-arm64-arm-64bit (arm64, 8 CPUs) |
| effective_cache_size | 524288 8kB |
| jit | on |
| max_parallel_workers_per_gather | 2 |
| random_page_cost | 4 |
| shared_buffers | 16384 8kB |
| work_mem | 4096 kB |

## Input volumes (copied without indexes, then ANALYZEd)

| table | rows |
|---|---|
| targets | 99,991 |
| seller_outcome_events | 97,733 |
| seller_approval_events | 99,996 |
| order_summary | 99,441 |
| order_categories | 99,470 |
| predictions | 133,636 |

## Q1 — Seller history as-of (feature build)

Point-in-time counts per (order, seller) — the most expensive feature query.



| variant | status | server ms (median, min–max) | client ms: query + transfer (median) | rows | planning ms |
|---|---|---|---|---|---|
| before: LATERAL join, no index | timeout after 300s | — | — | — | — |
| rewrite: window functions over event streams, no index | ok | 11321.1 (10760.5–16080.9) | 11020.2 | 99,991 | 2.00 |
| after: LATERAL join + composite indexes | ok | 4191.3 (3925.9–4585.0) | 3882.2 | 99,991 | 1.48 |

Results identical across variants: **yes**.


Plan — *rewrite: window functions over event streams, no index*:

```
Merge Join
  Sort
    Subquery Scan
      WindowAgg
        WindowAgg
          Sort
            Result
              Append
                Seq Scan on seller_outcome_events
                Seq Scan on targets
  Materialize
    Sort
      Subquery Scan
        WindowAgg
          Sort
            Result
              Append
                Seq Scan on seller_approval_events
                Seq Scan on targets
```

- Added by *after: LATERAL join + composite indexes*: `CREATE INDEX bench_outcome_asof ON bench.seller_outcome_events (seller_id, outcome_known_at) INCLUDE (is_late, positive_delay_days, order_id)` — build 234 ms, size 11816 KiB.
- Added by *after: LATERAL join + composite indexes*: `CREATE INDEX bench_approval_asof ON bench.seller_approval_events (seller_id, approved_at) INCLUDE (order_id)` — build 151 ms, size 11096 KiB.

Plan — *after: LATERAL join + composite indexes*:

```
Nested Loop
  Nested Loop
    Gather Merge
      Sort
        Seq Scan on targets
    Memoize
      Aggregate
        Index Only Scan on seller_outcome_events using bench_outcome_asof
  Memoize
    Aggregate
      Index Only Scan on seller_approval_events using bench_approval_asof
```

## Q2 — Operational aggregation (overview page)

Monthly KPIs of orders containing the most frequent category in the Southeast region.

Parameters: `{'start': '2000-01-01 00:00:00', 'end': '2100-01-01 00:00:00', 'regions': "['Southeast']", 'categories': "['bed_bath_table']"}`

| variant | status | server ms (median, min–max) | client ms: query + transfer (median) | rows | planning ms |
|---|---|---|---|---|---|
| before: no index | ok | 18.0 (17.8–19.3) | 18.5 | 21 | 1.68 |
| after: index on order_categories (category, order_id) | ok | 13.3 (13.2–13.5) | 13.5 | 21 | 0.35 |

Results identical across variants: **yes**.


Plan — *before: no index*:

```
Sort
  Aggregate
    Hash Join
      Seq Scan on order_summary
      Hash
        Seq Scan on order_categories
```

- Added by *after: index on order_categories (category, order_id)*: `CREATE INDEX bench_order_categories_cat ON bench.order_categories (category, order_id)` — build 153 ms, size 6808 KiB.

Plan — *after: index on order_categories (category, order_id)*:

```
Sort
  Aggregate
    Hash Join
      Seq Scan on order_summary
      Hash
        Index Only Scan on order_categories using bench_order_categories_cat
```

## Q3 — Filtered order extraction (risk review page)

First page of one day's scored orders for one run and model, ranked by score.

Parameters: `{'run': '20261017T173852Z-64ef33', 'model': 'lightgbm', 'day': '2018-07-05'}`

| variant | status | server ms (median, min–max) | client ms: query + transfer (median) | rows | planning ms |
|---|---|---|---|---|---|
| before: no index | ok | 10.5 (10.1–35.1) | 11.3 | 15 | 0.55 |
| after: composite index matching filter + order | ok | 0.2 (0.2–0.2) | 0.7 | 15 | 0.10 |

Results identical across variants: **yes**.


Plan — *before: no index*:

```
Limit
  WindowAgg
    WindowAgg
      Sort
        Seq Scan on predictions
```

- Added by *after: composite index matching filter + order*: `CREATE INDEX bench_predictions_review ON bench.predictions (run_id, model_name, prediction_date, score_raw DESC, order_id) INCLUDE (score_calibrated)` — build 336 ms, size 17280 KiB.

Plan — *after: composite index matching filter + order*:

```
Limit
  WindowAgg
    WindowAgg
      Index Only Scan on predictions using bench_predictions_review
```

## Dash callback time (separate from query time)

Rendering = queries + transfer + Python formatting of the page section; cold = cache cleared.

| callback | cold cache ms (median) | warm cache ms (median) |
|---|---|---|
| overview (all filters off) | 251.1 | 31.6 |
| risk review (one day, at approval) | 63.1 | 1.7 |
