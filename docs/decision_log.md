# Decision log

Newest last. Each entry: decision, reason, consequence.

## 2026-10-17 — Environment and stack (step 1)

- **Python 3.12.13** managed by uv (system has 3.13; the brief targets 3.12). `requires-python = ">=3.12,<3.13"`.
- **PostgreSQL 17.11** (`postgres:17.11-bookworm`, digest `sha256:3645570c…` at pull time), latest 17.x
  minor on 2026-10-17. Published on 127.0.0.1:55432 to avoid clashing with a local 5432.
- Locked versions actually tested: psycopg 3.3.6, pandas 3.0.6, numpy 2.5.4, scikit-learn 1.9.1,
  LightGBM 4.7.0 (works on macOS arm64; HistGradientBoosting fallback not needed), Dash 4.4.1,
  Plotly 7.1.0, pytest 9.1, Ruff 0.17.
- API checks on installed versions: `LogisticRegression(penalty=...)` is deprecated → `l1_ratio=0.0`;
  `log_loss(y, y_proba)`; pandas 3 default `str` dtype; Dash 4 `DataTable` deprecated → plain HTML
  tables with server-side sorting/paging (also lets sorting go through an allow-list).
- Explicit sigmoid calibrator on frozen scores instead of `CalibratedClassifierCV`, whose default
  random CV would break the temporal protocol.
- `argparse` CLI (stdlib) rather than an extra CLI dependency; Markdown tables rendered in-house.

## 2026-10-17 — Data access

- No Olist files on the machine and no Kaggle credentials: everything was built and tested on
  fixtures and synthetic data. Kaggle metadata checked publicly (license CC BY-NC-SA 4.0, version 2,
  updated 2021-10-01). The CLI procedure (`kaggle auth login` / `KAGGLE_API_TOKEN`,
  `kaggle datasets download -p … --unzip`) is documented in `data/README.md`; the CLI is not a
  dependency and nothing downloads implicitly.

## 2026-10-17 — Data contracts (step 2)

- Raw tables are all `text` + load_id/source_file/source_line; the manifest key `(dataset, sha256)`
  makes re-imports no-ops. The current load of a dataset is its latest loaded file.
- Typing rejects go to `quality.quarantine` with a reason; strict duplicates are kept once and logged;
  key conflicts and orphan references are **critical** and block publication; cascades of a
  quarantined parent are warnings.
- Zip prefixes: raw value kept, normalised value left-padded to 5 digits.
- Business incoherences (approval before purchase, delivery before approval) are not staging
  rejects: they become explicit eligibility reasons, so they stay visible in coverage tables.
- `no_items` added as a scoring exclusion: an approved order without items in the export cannot be
  described at approval (data defect, counted).

## 2026-10-17 — Features and protocol (step 4)

- History in SQL with `LATERAL` + composite indexes (readable "for each target, look back"); a
  window-function rewrite is kept as an equivalent alternative and benchmarked.
- Seller aggregation at order level: equal-weight mean over distinct sellers, max, min support,
  "any seller without history".
- Payments excluded from features (no availability log); reviews never used.
- Real splits first **proposed** from the public coverage of Olist, to be confirmed on the
  downloaded files (training refuses `proposed`).
- Model grids fixed in `config/project.yaml` before any result (4 LR, 8 LightGBM).

## 2026-10-17 — Development before the real files were available

- The pipeline was first developed and debugged on a seeded synthetic dataset with the Olist layout
  (no real result was involved). A generator defect (coastal points placed in the Atlantic) was fixed
  in the generator; the pipeline's outlier handling was left unchanged. Model grids, features,
  splits and calibration were fixed before any real-data run.

## 2026-10-17 — Isolated test databases

- `delivery_risk_test` (hand-written edge cases) and `delivery_risk_test_e2e` (4,000 synthetic orders,
  full train/evaluate) are reset by tests only; the real database is never touched by tests.
- CI sets `DR_REQUIRE_DB=1` so that database tests fail instead of being skipped.

## 2026-10-17 — SQL benchmark (step 7)

- Q1 history: index `(seller_id, outcome_known_at) INCLUDE (...)` turns a per-target sequential scan
  into an index-only scan; kept in the pipeline (`sql/features/010_seller_events.sql`). Real volume:
  > 300 s (cancelled) → 4.2 s; window-function rewrite 11.3 s.
- Q2 overview aggregation: 18 → 13 ms with an index on `order_categories(category, order_id)` on the
  real volume — not worth adding.
- Q3 risk-review extraction: Seq Scan + Sort → Index Only Scan (10.5 → 0.2 ms on the real volume).
  Added as migration `004_predictions_review_index.sql` (its comment quotes the first measurement,
  made on the small synthetic test volume; migrations are immutable once applied).

## 2026-10-17 — Delivery verification (step 8)

- Clean replay: repository copied without git-ignored files, separate Compose project on another
  port; `make setup`, `make db-up`, the synthetic end-to-end pipeline, `make test` (106 passed) and
  `make lint` succeeded, and the outputs were byte-identical to the main workspace. The temporary
  environment was then removed.
- `make reproduce` without real files stops at ingest and lists the nine missing files.
- Dashboard image built (`python:3.12.13-slim-bookworm`, uv 0.11.29, gunicorn) and served the layout
  and a routing callback with the read-only role.
- Screenshots of the three pages at 1440 px and 1024 px inspected (headless Chrome); layout fixes:
  comparison table at full width, blocks sorted by date (jsonb reorders keys), prevalence in the legend.
- GitHub Actions CI passed on the first push (PostgreSQL 17.11 service, `DR_REQUIRE_DB=1`).

## 2026-10-17 — Real data: ingestion, transform, frozen splits

- Nine files received in `data/raw/olist/` (file mtime 2026-10-17 19:33, used as acquisition date;
  `--source-version 2`, the version shown in Kaggle's metadata on that day). 1.45 M records loaded in
  11 s, 0 parse errors; reviews: 104,720 physical lines = 99,224 records (multi-line messages);
  translation file has a UTF-8 BOM and CRLF line endings (handled).
- Transform published with **no critical violation** (no key conflict, no orphan, no typing
  quarantine). Counted anomalies: 775 orders without items, 1 without payment, 303 value gaps,
  160 missing approvals, 1,359 carrier hand-offs before approval, 6 canceled orders with a delivery
  date, 8 delivered without date, 610 products without category, 2 untranslated categories,
  165 prefixes without centroid, 33 outlier coordinates.
- Real splits **frozen unchanged** after the coverage audit (no model trained yet). Known
  consequences, accepted in advance: (1) the calibration block (Mar–Apr 2018, late rate 12.2%) has a
  much higher prevalence than the test block (May–Jul 2018, 3.8%), so calibrated probabilities are
  expected to over-estimate risk on test; (2) 26% of selection-block labels are not yet delivered at
  the calibration start and are therefore not used for model choice. August 2018 is outside every
  block; it is not used as a second test.

## 2026-10-17 — Real data: features, training, final evaluation

- Features built in 24 s; all critical feature assertions pass. One-off check: the SQL seller history
  equals the independent Python reference implementation on all 99,991 real (order, seller) rows.
- Training (run `20261017T173852Z-64ef33`): selected **logistic regression (C = 0.1,
  class_weight = balanced)**, selection-block AP 0.070 vs 0.055 for the best LightGBM. Only 274 of the
  1,287 late selection-block orders were delivered before the calibration start and could inform the
  choice.
- Final evaluation, logged as `final`: AP 0.075 [0.047–0.115] vs 0.043 for the seller-history rule;
  paired difference +0.032 [+0.015, +0.060]. LightGBM (not selected) reached 0.090 on test; the
  selection is kept. Calibrated probabilities over-estimate risk on test (calibration-block late rate
  11.1% vs 3.8%).

## 2026-10-17 — Presentation-only changes made after viewing the real test results

These change how results are displayed, not the model, the metrics, the splits or the stored scores.
They are listed here because they were decided after the test results were seen.

- Group metrics are hidden when a group has fewer than 10 late orders (new
  `analysis.min_positives_for_group_metrics`), in reports and the dashboard: the 316 multi-seller test
  orders contain a single late order, which produced a meaningless lift of 9.9. Raw values stay in
  `test_metrics_by_group.csv` with their counts.
- Monthly late-rate charts skip months with fewer than 30 eligible delivered orders (September 2016
  showed 100% on one order).
- Report texts now compare the calibrated Brier score with the constant-prevalence Brier, state the
  selection-block label censoring, list per-month test AP, and add a caveat on multi-seller delivery
  timestamps.

## 2026-10-17 — Repository scope

- The repository now contains only the application, its pipeline, its tests and technical
  documentation. The synthetic demo mode (database, data, reports, screenshots) was removed; the
  seeded generator moved to `tests/support/` and is used by the end-to-end tests only. Slides,
  executive summary and other non-technical documents were removed; the final report
  (`reports/final/`) replaces them. The demo database was dropped.

## 2026-10-17 — Final report

- `reports/final/report.pdf` is compiled with Tectonic from `reports/final/report.tex`. Every number
  in the text comes from `reports/final/generated/numbers.tex` and the generated tables, written by
  `delivery-risk final-report` from the saved artifacts and read-only queries (descriptive analyses of
  the frozen run only: no refit, no recalibration). Two statements were corrected while checking the
  text against the data: label coverage is >= 95% in every block month (not 97%), and the selection
  grid has 14 candidates.

## Status against the definition of done

| Item | Status |
|---|---|
| Real data identified by a manifest | done (9 files, sha256, rows, version 2, acquisition basis) |
| Core transformations in PostgreSQL SQL | done |
| Grains, joins and amounts tested | done (fixtures, synthetic e2e, and assertions on real data) |
| Temporal leakage and label availability tests | done; reference check on all real rows |
| Exclusions and outcome coverage published | done |
| Models vs baselines under a frozen protocol | done (real run `20261017T173852Z-64ef33`, evaluated once) |
| Global top-q vs daily review kept separate | done |
| Scores, metrics, figures reproducible | done |
| Three Dash pages with filters and explicit modes | done |
| Benchmark with comparable before/after plans | done on the real volume |
| Tests, lint, UI validation reported honestly | done; CI green |
| README, documentation, final report | done (`reports/final/`) |
| No secret, raw dataset or sensitive artifact versioned | done |
| Public deployment | prepared for Render; not published — explicit approval still required |

## 2026-10-17 — Render deployment preparation

- Preserved and reviewed the existing local deployment changes: real-database name override,
  optional TLS, platform-provided Gunicorn port, `/health` liveness and visible Olist attribution.
  Added the licence link and description of adaptations. The database-name override cannot
  redirect test/e2e connections, preserving fixture isolation.
- Added a Render Docker Blueprint (Frankfurt, one worker, free web instance, automatic deploys
  disabled), a deployment guide and a Docker build-context exclusion file. The guide creates and
  populates PostgreSQL 17 before creating the public web service. A persistent deployment needs
  a paid database because Render's free database expires after 30 days; no paid resources created.
- Added explicit export, transactional restore and reader-provisioning commands. Dumps are private,
  ignored by Git and published atomically without overwriting backups. Restore refuses existing
  application schemas; reader provisioning refuses to alter existing roles. No cloud credentials
  are stored in the repository.
- Verified the actual 99,441-order export (about 50 MiB) by restoring into a temporary PostgreSQL 17
  database owned by a non-superuser with CREATEROLE. The reader rendered all three page layouts;
  DELETE and CREATE TABLE were denied even after disabling its default read-only transaction
  setting. Existing-schema and existing-role protection checks passed. Temporary database and
  roles were removed. The host has pg_restore 14/15, so this verification used the PostgreSQL 17
  client in Docker; the deployment guide requires a version 17+ client for the native command.
- Built the Docker image and verified custom `PORT=10000`, HTTP health/page routes and all three
  page layouts against the local real database, without an artifact mount. Confirmed local .env,
  CSV files and dump are absent from the image. Inspected overview screenshots at 1440/1024 px.
  The test web container was stopped. TLS to an actual Render database and Render-side Blueprint
  validation remain to be checked when resources exist; nothing has been published or pushed.
- Final verification: 113 pytest tests passed; Ruff lint, formatting and `git diff --check` passed.
