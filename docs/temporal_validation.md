# Temporal validation and leakage prevention

## Why not a random split

Orders of the same seller, the same week and the same congestion episode would sit on both sides of
a random split, and seller-history features would "see" outcomes that a real reviewer could not
know yet. A random split is never used as the main validation.

## Blocks (`config/splits.yaml`)

Four contiguous, non-overlapping calendar blocks on `prediction_at` (approval timestamp),
half-open `[start, end)`: **train → selection → calibration → test**. Each mode has its own
definition and a SHA-256 of the boundaries, written in every run manifest. Training refuses a block
definition whose status is not `frozen`; evaluation refuses a run whose split hash differs from the
current file.

Boundaries are chosen from the **coverage audit** (`reports/data_quality/<mode>/coverage.md`),
which reports per month: scorable orders, label coverage, outcome populations, label delay
(approval → delivery) and late counts per proposed block — and no model result.

| Mode | train | selection | calibration | test | status |
|---|---|---|---|---|---|
| real | 2017-01-01 → 2018-01-01 | → 2018-03-01 | → 2018-05-01 | → 2018-08-01 | frozen 2026-10-17 after the coverage audit, boundaries unchanged |

Real-data audit (`reports/data_quality/real/coverage.md`): 2016 has 312 scorable orders and an empty
November, so it is excluded from the blocks (its deliveries still feed seller histories); label
coverage is ≥ 95% for every month from 2017-01 to 2018-08 (lowest: 95.1% in January 2017); the 13-week test block (19,368 scorable
orders, 718 late) ends more than a month before the last delivery in the source (2018-10-17). The
calibration block's late rate (12.2%) differs strongly from the test block's (3.8%): an outcome shift,
not a coverage problem, so it was not a reason to move boundaries. Consequence observed afterwards:
calibrated probabilities over-estimate risk on test (see the model card). If a block lacks rows
or classes, training stops with `InsufficientData`; boundaries may only move for a documented
coverage reason (decision log), never to improve a score, and never by falling back to a random split.

## Label availability

The label of an order is usable from its **actual customer delivery** (`label_available_at`),
including for late orders. This is conservative: a delay could be known earlier (when the promise
passes), but mixing that convention with a delivered-date target would be inconsistent.

## Protocol (`src/delivery_risk/modeling/train.py`)

| Step | Fit / evaluate on | Labels allowed |
|---|---|---|
| 1 | fit every candidate on **train** orders | delivered before the selection start |
| 2 | score **selection**, compare a fixed grid (4 LR + 8 LightGBM configs) by AP | delivered before the calibration start |
| 3 | refit the chosen configuration per family on **train + selection** | delivered before the calibration start |
| 4 | score **calibration** with the frozen models; fit a sigmoid calibrator per model | delivered before the test start |
| 5 | freeze models + preprocessing + calibrators; `evaluate` scores **test** once | (evaluation uses all labels observed in the source) |

The selected model overall is the highest selection-block AP; it is fixed before the test block is
scored. Orders approved inside a block but delivered after its cutoff are *not* used at that step —
the manifest records how many (`eligible_but_not_yet_delivered_at_cutoff`). Because late orders
take longer to arrive, this censoring slightly lowers the fitting prevalence near each cutoff; it
mimics what a real deployment would have known.

Imputation, scaling, one-hot encoding (with an "infrequent" bucket for unseen categories) are
fitted inside the sklearn pipeline on the fitting rows only. No oversampling, no class weighting by
default (one LR configuration tests `class_weight="balanced"` on the selection block).

## Feature-side guarantees

- seller outcomes: `outcome_known_at < t` (strict), target order excluded, 90-day window anchored on
  delivery events;
- approval counters: `approved_at ∈ [t − 30 d, t)`, target excluded (same-second approvals excluded);
- empty history → count 0 and rate NULL, imputed inside the pipeline (train median).

## Tests that enforce the contract

| Test | File |
|---|---|
| delivery on the promised day → not late | `tests/integration/test_ingestion_and_transform.py` |
| bought before t, delivered after t → not in the history at t | `tests/temporal/test_asof_features.py` |
| delivered exactly at t → excluded; one second earlier → included | same |
| modifying a future delivery or review does not change past features | same |
| adding future orders does not change past features | same |
| duplicated item does not change seller features | same |
| SQL history equals an independent Python implementation (fixtures and 4k synthetic orders) | same |
| no forbidden column; exact allow-list | `tests/unit/test_learning.py`, SQL assertion, build check |
| no label after the cutoff used to fit / select / calibrate | `tests/unit/test_splits_and_cutoffs.py`, `tests/temporal/test_protocol.py` |
| training never loads the test block | `tests/temporal/test_protocol.py` |
| test scores equal re-computation with the frozen model and calibrator | same |
| unknown category at test time does not fail | `tests/unit/test_learning.py` |
| re-evaluation is logged as `verification_rerun` and identical | `tests/temporal/test_protocol.py` |

## Re-running the test

`evaluate` appends to `artifacts/<mode>/evaluation_log.jsonl`: the first evaluation of a run is
`final`; re-evaluating the same run is `verification_rerun`; a run trained *after* another run of the
same split was evaluated is flagged `post_test_exploration` (printed in reports). Changing anything
after looking at test results must be reported as exploration, not as a new independent evaluation.

## Selection bias

Performance is measured on orders finally delivered with an eligible outcome. Canceled, unavailable
or never-delivered orders are scored (they are part of the daily population) but cannot be
evaluated. Conclusions do not generalise to those orders.
