"""Evaluation metrics with explicit handling of undefined cases.

Top-q definition (docs/metric_definitions.md): for N eligible orders, k = ceil(q * N); orders are
sorted by score descending, ties broken by order_id ascending.
    Precision@q = TP_selected / k
    Recall@q    = TP_selected / positives
    Lift@q      = Precision@q / prevalence
When N = 0 or there is no positive, the affected metrics are None with a status, never 0.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score


@dataclass(frozen=True)
class TopQ:
    q: float
    n: int
    k: int
    positives: int
    true_positives: int | None
    precision: float | None
    recall: float | None
    lift: float | None
    status: str  # ok | undefined_empty | undefined_no_positive

    def as_dict(self) -> dict:
        return asdict(self)


def rank_order(scores: np.ndarray, ids: np.ndarray) -> np.ndarray:
    """Indices sorted by score descending, then id ascending (deterministic tie-break)."""
    return np.lexsort((np.asarray(ids).astype(str), -np.asarray(scores, dtype=float)))


def top_q(y: np.ndarray, scores: np.ndarray, ids: np.ndarray, q: float) -> TopQ:
    y = np.asarray(y, dtype=int)
    n = len(y)
    if n == 0:
        return TopQ(q, 0, 0, 0, None, None, None, None, "undefined_empty")
    k = math.ceil(q * n)
    selected = rank_order(scores, ids)[:k]
    tp = int(y[selected].sum())
    positives = int(y.sum())
    precision = tp / k
    if positives == 0:
        return TopQ(q, n, k, 0, tp, precision, None, None, "undefined_no_positive")
    prevalence = positives / n
    return TopQ(q, n, k, positives, tp, precision, tp / positives, precision / prevalence, "ok")


def classification_metrics(
    y: np.ndarray, scores: np.ndarray, clip: float = 1e-6
) -> dict[str, float | None]:
    """AP (sklearn average_precision_score, not a trapezoidal PR-AUC), ROC-AUC, Brier, log loss."""
    y = np.asarray(y, dtype=int)
    scores = np.asarray(scores, dtype=float)
    out: dict[str, float | None] = {"n": len(y), "positives": int(y.sum()) if len(y) else 0}
    out["prevalence"] = float(y.mean()) if len(y) else None
    both_classes = len(y) > 0 and 0 < y.sum() < len(y)
    out["average_precision"] = float(average_precision_score(y, scores)) if both_classes else None
    out["roc_auc"] = float(roc_auc_score(y, scores)) if both_classes else None
    if len(y):
        p = np.clip(scores, clip, 1 - clip)  # avoids infinite log loss on 0/1 scores
        out["brier"] = float(brier_score_loss(y, p, labels=[0, 1]))
        out["log_loss"] = float(log_loss(y, p, labels=[0, 1]))
    else:
        out["brier"] = out["log_loss"] = None
    return out


def calibration_bins(y: np.ndarray, scores: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Quantile bins of the score: mean score vs observed rate, with counts."""
    y = np.asarray(y, dtype=int)
    scores = np.asarray(scores, dtype=float)
    if len(y) == 0:
        return []
    edges = np.unique(np.quantile(scores, np.linspace(0, 1, n_bins + 1)))
    idx = (
        np.clip(np.searchsorted(edges, scores, side="right") - 1, 0, len(edges) - 2)
        if len(edges) > 1
        else np.zeros(len(y), int)
    )
    rows = []
    for b in np.unique(idx):
        m = idx == b
        rows.append(
            {
                "bin": int(b),
                "mean_score": float(scores[m].mean()),
                "observed_rate": float(y[m].mean()),
                "n": int(m.sum()),
            }
        )
    return rows
