# Architecture

```text
 data/raw/olist/*.csv  (real, user-provided)        tests: hand-written fixtures + seeded generator
            │                                                   │
            ▼  delivery-risk ingest  (manifest: size, sha256, rows, version, acquisition, load_id)
 ┌────────────────────────────── PostgreSQL 17 (Docker, 127.0.0.1:55432) ──────────────────────────────┐
 │  one database per mode: delivery_risk_real | delivery_risk_test | delivery_risk_test_e2e             │
 │                                                                                                      │
 │  raw.*  (text + load_id/source_file/source_line)                                                     │
 │    │  transform — ONE transaction: staging → core → marts → assertions → COMMIT or ROLLBACK         │
 │    ▼                                                                                                 │
 │  staging.* (typed) + quality.quarantine / duplicate_log                                              │
 │  core.*    (PK/FK verified; order_sellers; geo_prefix)                                               │
 │  mart.*    (order_summary, order_categories, seller_order_exposure, monthly_operations, coverage)    │
 │  evaluation.order_labels (populations, eligibility, label_available_at, target)                      │
 │    │  features — ONE transaction + allow-list check + assertions                                    │
 │    ▼                                                                                                 │
 │  feature.* (seller events, as-of history, order_features)                                            │
 │    │                                         ▲                                                       │
 └────┼─────────────────────────────────────────┼───────────────────────────────────────────────────────┘
      │ train (Python, never reads the test block)   evaluate → evaluation.predictions / metrics / capacity
      ▼                                         │
 artifacts/<mode>/runs/<run_id>/  models.joblib, manifest.json (hashes), selection, coefficients
      │                                         │
      ▼                                         │
 reports/ (data_quality, evaluation, sql_performance, figures, slides)    Dash app (read-only role)
```

## Principles

- **SQL first.** Typing, quarantine, keys, joins, marts, eligibility and point-in-time features are
  versioned SQL files executed in order (`sql/`). pandas is used only to assemble the final matrices
  and analyse results.
- **Atomic publication.** PostgreSQL DDL is transactional. Each layer is rebuilt inside one
  transaction; critical assertions run before `COMMIT`. On failure the previous tables stay visible
  and unchanged (tested with a planted key conflict).
- **Isolation of modes.** Real data and the two test datasets live in separate databases and artifact
  folders; if the dashboard is pointed at a test database, every page shows a synthetic-data banner.
- **Run parameters** (bounding box, minimum support, feature version) reach SQL as
  transaction-local settings (`current_setting('dr.min_support')`), never by string formatting.
- **Migrations** in `sql/migrations` are applied once and checksummed; a changed migration is
  refused (add a new one instead).
- **Least privilege.** Admin role only creates roles/databases; `dr_pipeline` writes; `dr_reader`
  reads `meta`, `mart`, `evaluation`, `quality`, `feature` in read-only transactions.

## Code map

| Path | Responsibility |
|---|---|
| `src/delivery_risk/cli.py` | all commands (`uv run delivery-risk --help`) |
| `config.py`, `db.py`, `logs.py` | configuration, connections/transactions/migrations, secret-free logs |
| `ingestion/` | file contracts, raw loader with manifest |
| `pipeline.py`, `quality.py` | transform/feature orchestration and assertions; quality and coverage reports |
| `features/catalog.py` | feature allow-list and groups |
| `modeling/` | matrix loading, models and calibrator, temporal training protocol |
| `evaluation/` | metrics, block bootstrap, capacity simulation, final evaluation and publication |
| `dashboard/` | Dash app: data access (read-only, cached), three pages, components |
| `benchmark.py`, `reporting.py`, `screenshots.py` | SQL benchmark, reports/slides, UI captures |

## What is deliberately not here

No Spark, Airflow, dbt, feature store, separate API, LLM or real-time component: the difficulty is
relational and temporal, not volume (~100k orders). The dashboard is a local analysis tool, not a
production system.
