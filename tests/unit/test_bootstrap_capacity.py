"""Paired block bootstrap and daily capacity simulation."""

import numpy as np
import pandas as pd

from delivery_risk.evaluation.bootstrap import paired_block_bootstrap
from delivery_risk.evaluation.capacity import daily_capacity, summarise_capacity


def _ap_like(y, s, ids):
    return float(y[np.argsort(-s)][: max(1, len(y) // 10)].mean()) if y.sum() else None


def test_bootstrap_is_seeded_paired_and_counts_skips():
    rng = np.random.default_rng(0)
    n = 400
    frame = pd.DataFrame(
        {
            "week": np.repeat([f"W{i}" for i in range(8)], n // 8),
            "order_id": [f"o{i}" for i in range(n)],
        }
    )
    frame["y"] = 0
    frame.loc[frame.week == "W0", "y"] = (rng.random(50) < 0.5).astype(
        int
    )  # positives only in one week
    frame["good"] = frame["y"] + rng.random(n) * 0.1
    frame["bad"] = rng.random(n)
    args = (frame, {"good": "good", "bad": "bad"}, {"p": _ap_like}, "bad", 200, 7)
    a, b = paired_block_bootstrap(*args), paired_block_bootstrap(*args)
    assert a == b  # deterministic with a fixed seed
    assert (
        a["n_skipped_undefined"] > 0
    )  # resamples without week W0 have no positive -> skipped, counted
    assert len(a["intervals"]["good|p"]) and a["differences_vs_reference"]["good|p"]["ci_low"] > 0


def test_daily_capacity_selects_before_outcomes_and_counts_coverage():
    frame = pd.DataFrame(
        {
            "prediction_date": ["d1"] * 5 + ["d2"] * 3,
            "order_id": ["a", "b", "c", "d", "e", "f", "g", "h"],
            "score": [0.9, 0.8, 0.1, 0.2, 0.3, 0.5, 0.4, 0.1],
            "label_eligible": [False, True, True, True, True, True, True, False],
            "y": [0, 1, 0, 1, 0, 0, 1, 0],
        }
    )
    daily = daily_capacity(frame, "score", 0.2)
    d1, d2 = daily.iloc[0], daily.iloc[1]
    # d1: N=5, k=ceil(1.0)=1 -> 'a' (highest score) is selected even though its outcome is unknown
    assert (d1.k_selected, d1.n_selected_labeled, d1.n_selected_late) == (1, 0, 0)
    # d2: N=3, k=ceil(0.6)=1 -> 'f'
    assert (d2.k_selected, d2.n_selected_labeled, d2.n_selected_late) == (1, 1, 0)
    s = summarise_capacity(daily)
    assert s["orders_selected"] == 2 and s["effective_review_fraction"] == 2 / 8
    assert s["known_outcome_coverage_in_selection"] == 0.5
