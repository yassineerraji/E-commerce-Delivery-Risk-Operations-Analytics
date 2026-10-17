# Model card — delivery-risk score at approval

**Status:** real-data run `20261017T173852Z-64ef33` on the Olist dataset (version 2), evaluated once on
the test block (evaluation kind `final`). Generated tables: `reports/evaluation/real/evaluation_report.md`.

## Intended use

Rank orders, at the moment they are approved, by estimated risk of being delivered after the
promised date, to prioritise a **limited review capacity** in a retrospective study. The score is
an estimated risk, not a certainty. Not intended for automated decisions about customers or sellers,
and not validated as a live system.

## Data and population

- Source: Brazilian E-Commerce Public Dataset by Olist, version 2 (CC BY-NC-SA 4.0).
- Scored population: approved orders with a coherent promise and at least one item, **whatever their
  final status**.
- Evaluated population: scored orders finally delivered with an eligible outcome. Canceled,
  unavailable and never-delivered orders are scored but cannot be evaluated — results do not
  generalise to them.
- Exclusions and coverage: `reports/data_quality/<mode>/data_quality_report.md` (table 6) and
  `mart.label_coverage`.

## Target

`is_late = delivered_date > promised_date` (civil dates). Label available from the actual delivery.

## Features

27 allow-listed features in 4 groups (order, geo, catalog, seller history) — `docs/feature_catalog.md`.
No delivery outcome, review, payment, final status or raw identifier. Static attributes are
**assumed** known at approval (final snapshot, no history): the `no_catalog_static` ablation tests
that assumption.

## Models

| Model | Description |
|---|---|
| Constant | prevalence of the fitting rows |
| Seller-history baseline | equal-weight mean of the order's sellers' 90-day known late rates; fallback = training prevalence |
| Logistic regression | median imputation + indicators, standardisation, one-hot (infrequent bucket), L2; 4 configurations |
| LightGBM 4.7 | same preprocessing without scaling; 8 configurations fixed in advance |

Selection: highest AP on the selection block. Calibration: sigmoid on frozen scores of the
calibration block (labels known before the test start). Protocol: `docs/temporal_validation.md`.

## Metrics reported

AP (sklearn `average_precision_score`), ROC-AUC, Brier and log loss (raw and calibrated), calibration
curve, Precision/Recall/Lift at 5/10/20 %, results by month, region, seller-history status and
single/multi-seller with sample sizes, paired weekly block-bootstrap intervals (500 resamples), daily
capacity simulation.

## Results on the real data (test block 2018-05-01 → 2018-08-01)

19,368 scorable orders; 19,055 delivered with an eligible outcome (label coverage 98.4%), 718 late
(prevalence 3.8%). Selected before test: **logistic regression, C = 0.1, class_weight = balanced**
(selection-block AP 0.070 vs 0.055 for the best LightGBM configuration).

| Model | AP [95% CI] | ROC-AUC | Precision@10% | Lift@10% | Brier raw → calibrated |
|---|---|---|---|---|---|
| Constant (prevalence) | 0.038 [0.021–0.058] | 0.500 | 3.0% | 0.79 | 0.0364 → 0.0364 |
| Seller-history baseline | 0.043 [0.021–0.066] | 0.536 | 5.1% | 1.36 | 0.0482 → 0.0417 |
| **Logistic regression (selected)** | **0.075 [0.047–0.115]** | 0.708 | 8.7% | 2.30 | 0.2520 → 0.0455 |
| LightGBM (not selected) | 0.090 [0.070–0.115] | 0.700 | 10.4% | 2.77 | 0.0361 → 0.0435 |

Reading:

- AP gain of the selected model over the seller-history rule: **+0.032, paired weekly-bootstrap
  interval +0.015 to +0.060** (14 weeks). The Precision@10% gain (+3.4 points) has an interval that
  includes zero (−0.6 to +9.5 points).
- The absolute level is modest: in the top 10% of test orders, 8.7% were late vs 3.8% on average.
- The gain is concentrated in May 2018 (AP 0.138 at 6.5% prevalence); June (1.2% prevalence) and
  July (3.2%) are much weaker (AP 0.034 and 0.048).
- LightGBM is better on test but was not selected; the choice is kept (see temporal validation).
- **Calibration does not transfer:** the calibrator learned a late rate of 11.1% on Mar–Apr 2018 while
  the test rate is 3.8%; calibrated probabilities over-estimate risk and their Brier score (0.0455) is
  worse than a constant prevalence (0.0364). The raw logistic scores are not probabilities either
  (class weighting inflates them; Brier 0.252). Use the score for ranking, recalibrate on recent
  deliveries before reading it as a probability.
- **Model choice relied on few late examples:** only 274 of the 1,287 late orders of the selection
  block were delivered before the calibration start (late orders arrive after the cut-off).
- Ablation without static catalog attributes: AP 0.075 (LR) and 0.092 (LightGBM) — results do not
  depend on the catalog-snapshot assumption.
- Daily capacity simulation, q = 10% (10.2% effectively reviewed): 8.2% of selected orders with a
  known outcome were late (seller-history rule: 5.4%; all known orders: 3.8%), 22% of known late
  orders captured, 2.2% of selected orders without observable outcome.

Group metrics with fewer than 30 labelled orders or 10 late orders are not interpreted (e.g. the
316 multi-seller test orders contain a single late order).

## Interpretation

On the calibration block, the largest permutation-importance drops for the selected model come from
seller-history support (`sh_known_outcomes_90d_min`, `sh_known_outcomes_total_min`), customer state,
promised lead time, same-state and distance features, then the 90-day seller late rates.
Logistic coefficients (standardised features) and permutation importance (AP drop on the
calibration block) are **associations within the data**, not causes. They do not show that a
seller, a region or a promised lead time *causes* lateness.

## Limitations and risks

- Historical export without versioned attributes; promise, prices and catalog could have changed
  after approval without trace.
- Seller histories use delivered orders only (slow open orders are missing → recent rates can be
  under-estimated).
- Multi-seller orders: the outcome is shared; the model cannot attribute lateness to one seller.
- Few test weeks → wide intervals; seasonality (Black Friday, early-year disruptions) changes
  prevalence and calibration.
- No cost or intervention data: a better ranking is **not** evidence that reviewing orders reduces
  delays. No savings or ROI is claimed. The next step is a randomised contact pilot.
- Prefix-centroid distances approximate true addresses.

## Reproducibility

Seeds, grids, split hashes, feature hash, model file hash and library versions are written in
`artifacts/<mode>/runs/<run_id>/manifest.json` and copied to `reports/evaluation/<mode>/run_manifest.json`.
Every evaluation is logged in `artifacts/<mode>/evaluation_log.jsonl`.
