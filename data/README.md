# Data

## Source

**Brazilian E-Commerce Public Dataset by Olist** — <https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce>
(Kaggle identifier `olistbr/brazilian-ecommerce`).

Checked on 2026-10-17 via Kaggle's public dataset metadata: license **CC BY-NC-SA 4.0**,
current version **2**, last updated 2021-10-01, about 126 MB. Check the license and version
shown on the page when you download, and pass the version to the ingest step
(`uv run delivery-risk ingest --mode real --source-version 2 --acquired-on YYYY-MM-DD`) so it
is written in the load manifest.

Attribution: data provided by Olist on Kaggle under CC BY-NC-SA 4.0 (non-commercial use,
share-alike). **The raw files are never committed to this repository** (`data/raw/` is
git-ignored).

## Required files

Place exactly these nine files in `data/raw/olist/`:

| File | Role |
|---|---|
| `olist_orders_dataset.csv` | Orders, statuses and timestamps |
| `olist_order_items_dataset.csv` | Items, sellers, prices, freight |
| `olist_customers_dataset.csv` | Order-level customer key and repeat-customer id |
| `olist_sellers_dataset.csv` | Sellers |
| `olist_products_dataset.csv` | Product attributes |
| `olist_order_payments_dataset.csv` | Payments |
| `olist_order_reviews_dataset.csv` | Reviews |
| `olist_geolocation_dataset.csv` | Coordinates by zip prefix |
| `product_category_name_translation.csv` | Category translation |

The ingest step checks that all nine exist and that each header matches the expected columns.
A missing file stops the load with the list of missing names; files are never replaced by
copies of unknown provenance.

## How to obtain them

**Option A — browser (no credentials stored anywhere):** sign in to Kaggle, open the dataset
page, click *Download*, unzip into `data/raw/olist/`.

**Option B — Kaggle CLI** (procedure from the official `Kaggle/kaggle-api` documentation,
checked 2026-10-17; CLI version 2.2.4 on PyPI). The CLI is not a project dependency; run it
with `uvx`:

```bash
# authenticate ONCE on your machine (never paste a token into a chat, never commit it):
uvx kaggle auth login                      # browser-based OAuth flow
# or: export KAGGLE_API_TOKEN=...          # token copied from your Kaggle settings page
# or: token stored in ~/.kaggle/access_token (legacy: ~/.kaggle/kaggle.json)

uvx kaggle datasets download olistbr/brazilian-ecommerce -p data/raw/olist --unzip
```

Then:

```bash
make reproduce          # ingest -> transform -> quality -> features -> train -> evaluate -> report
```

The real-data splits in `config/splits.yaml` were frozen after the coverage audit
(`reports/data_quality/real/coverage.md`); `make train` refuses any block definition that is not
`frozen`, and a change must be justified by coverage in `docs/decision_log.md`.

## Test data

The automated tests never use the real files (they are not in the repository). They use:

- `tests/fixtures/edge_cases/`: tiny hand-written files where each order exercises one rule
  (see its README), loaded into the disposable `delivery_risk_test` database;
- `tests/support/synthetic.py`: a seeded generator with the Olist file layout (simulated values)
  for the end-to-end, learning and dashboard tests, loaded into `delivery_risk_test_e2e`.
