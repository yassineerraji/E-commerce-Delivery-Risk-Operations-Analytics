"""Paired block bootstrap by ISO week.

Weeks (not orders) are resampled with replacement, which keeps within-week dependence
(seasonality, shared sellers). All models are scored on the SAME resampled weeks so their
differences are paired. Resamples where a metric is undefined (e.g. no positive) are skipped
and counted, never filled.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

MetricFn = Callable[[np.ndarray, np.ndarray, np.ndarray], float | None]


def paired_block_bootstrap(
    frame: pd.DataFrame,
    score_columns: dict[str, str],
    metrics: dict[str, MetricFn],
    reference: str,
    n_resamples: int,
    seed: int,
) -> dict:
    """frame needs columns: week, y, order_id and one score column per model."""
    rng = np.random.default_rng(seed)
    weeks = np.array(sorted(frame["week"].unique()))
    groups = {w: np.flatnonzero(frame["week"].to_numpy() == w) for w in weeks}
    y_all = frame["y"].to_numpy(dtype=int)
    ids_all = frame["order_id"].to_numpy()
    scores_all = {m: frame[c].to_numpy(dtype=float) for m, c in score_columns.items()}

    draws: dict[tuple[str, str], list[float]] = {}
    diffs: dict[tuple[str, str], list[float]] = {}
    skipped = 0
    for _ in range(n_resamples):
        sample = rng.choice(weeks, size=len(weeks), replace=True)
        idx = np.concatenate([groups[w] for w in sample])
        y, ids = y_all[idx], ids_all[idx]
        values = {
            (m, k): fn(y, scores_all[m][idx], ids) for m in scores_all for k, fn in metrics.items()
        }
        if any(v is None for v in values.values()):
            skipped += 1
            continue
        for key, v in values.items():
            draws.setdefault(key, []).append(v)
        for m in scores_all:
            if m == reference:
                continue
            for k in metrics:
                diffs.setdefault((m, k), []).append(values[(m, k)] - values[(reference, k)])

    def summarise(samples: list[float]) -> dict:
        arr = np.array(samples)
        return {
            "ci_low": float(np.percentile(arr, 2.5)),
            "ci_high": float(np.percentile(arr, 97.5)),
            "mean": float(arr.mean()),
            "share_above_zero": float((arr > 0).mean()),
        }

    return {
        "n_weeks": int(len(weeks)),
        "n_resamples": n_resamples,
        "n_skipped_undefined": skipped,
        "reference": reference,
        "intervals": {f"{m}|{k}": summarise(v) for (m, k), v in draws.items()},
        "differences_vs_reference": {f"{m}|{k}": summarise(v) for (m, k), v in diffs.items()},
    }
