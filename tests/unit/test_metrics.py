"""Top-q and classification metrics checked by hand on small examples."""

import math

import numpy as np
import pytest

from delivery_risk.evaluation.metrics import (
    calibration_bins,
    classification_metrics,
    rank_order,
    top_q,
)


def test_top_q_hand_example():
    # 10 orders, 3 late. q = 0.2 -> k = ceil(2.0) = 2: the two highest scores are a (late) and b (on time).
    ids = np.array(list("abcdefghij"))
    scores = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
    y = np.array([1, 0, 1, 0, 0, 0, 1, 0, 0, 0])
    r = top_q(y, scores, ids, 0.2)
    assert (r.n, r.k, r.positives, r.true_positives) == (10, 2, 3, 1)
    assert r.precision == pytest.approx(0.5)
    assert r.recall == pytest.approx(1 / 3)
    assert r.lift == pytest.approx(0.5 / 0.3)
    assert r.status == "ok"


def test_k_uses_ceiling_on_small_n():
    # N = 3, q = 0.10 -> k = ceil(0.3) = 1 (never zero orders reviewed when N > 0)
    r = top_q(np.array([0, 1, 0]), np.array([0.1, 0.9, 0.2]), np.array(["x", "y", "z"]), 0.10)
    assert r.k == 1 and r.true_positives == 1 and r.precision == 1.0


def test_ties_broken_by_order_id_ascending():
    # All scores equal: selection must be the smallest ids, independently of input order.
    ids = np.array(["o3", "o1", "o4", "o2"])
    scores = np.full(4, 0.5)
    y = np.array([0, 1, 0, 0])  # o1 is late
    r = top_q(y, scores, ids, 0.25)  # k = 1 -> o1
    assert r.true_positives == 1
    assert list(ids[rank_order(scores, ids)]) == ["o1", "o2", "o3", "o4"]


def test_tie_break_is_independent_of_row_order():
    rng = np.random.default_rng(0)
    ids = np.array([f"o{i:02d}" for i in range(20)])
    scores = np.round(rng.random(20), 1)  # many ties
    y = rng.integers(0, 2, 20)
    perm = rng.permutation(20)
    a = top_q(y, scores, ids, 0.3)
    b = top_q(y[perm], scores[perm], ids[perm], 0.3)
    assert a == b


def test_no_positive_is_undefined_not_zero():
    r = top_q(np.zeros(5, int), np.linspace(0, 1, 5), np.arange(5), 0.2)
    assert r.status == "undefined_no_positive"
    assert r.recall is None and r.lift is None
    m = classification_metrics(np.zeros(5, int), np.linspace(0, 1, 5))
    assert m["average_precision"] is None and m["roc_auc"] is None


def test_empty_set_is_undefined():
    r = top_q(np.array([], int), np.array([]), np.array([]), 0.1)
    assert r.status == "undefined_empty" and r.k == 0 and r.precision is None
    m = classification_metrics(np.array([], int), np.array([]))
    assert m["n"] == 0 and m["brier"] is None


def test_average_precision_is_sklearn_step_definition():
    # y sorted by score: 1, 0, 1 -> AP = (1/1 + 2/3) / 2 = 0.8333 (not a trapezoidal area)
    m = classification_metrics(np.array([1, 0, 1]), np.array([0.9, 0.8, 0.7]))
    assert m["average_precision"] == pytest.approx((1 + 2 / 3) / 2)


def test_log_loss_is_finite_with_extreme_scores():
    m = classification_metrics(np.array([1, 0]), np.array([0.0, 1.0]), clip=1e-6)
    assert math.isfinite(m["log_loss"])


def test_calibration_bins_cover_all_rows():
    rng = np.random.default_rng(1)
    s = rng.random(1000)
    y = (rng.random(1000) < s).astype(int)
    bins = calibration_bins(y, s, 10)
    assert sum(b["n"] for b in bins) == 1000
    assert len(bins) == 10
