"""Daily review-capacity simulation (historical, batch policy).

For each historical day, ALL scorable orders approved that day are ranked by their frozen
score (computed at approval) and the top ceil(q * N_day) are "selected" BEFORE looking at
outcomes. Outcomes are then looked up: some selected orders have no observable outcome
(canceled, not delivered), so precision is reported among selected orders WITH a known
outcome, together with that coverage. This is different from the global top-q on the
labelled subset, which is only used to compare models.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from delivery_risk.evaluation.metrics import rank_order


def daily_capacity(frame: pd.DataFrame, score_column: str, q: float) -> pd.DataFrame:
    """frame: one row per scorable order with prediction_date, order_id, label_eligible, y."""
    rows = []
    for day, g in frame.groupby("prediction_date", sort=True):
        n = len(g)
        k = math.ceil(q * n)
        order = rank_order(g[score_column].to_numpy(), g["order_id"].to_numpy())
        sel = g.iloc[order[:k]]
        labelled = g["label_eligible"].to_numpy()
        rows.append(
            {
                "prediction_date": day,
                "capacity_fraction": q,
                "n_scorable": n,
                "k_selected": k,
                "n_selected_labeled": int(sel["label_eligible"].sum()),
                "n_selected_late": int((sel["label_eligible"] & (sel["y"] == 1)).sum()),
                "n_day_labeled": int(labelled.sum()),
                "n_day_late": int((g["label_eligible"] & (g["y"] == 1)).sum()),
            }
        )
    return pd.DataFrame(rows)


def summarise_capacity(daily: pd.DataFrame) -> dict:
    tot = daily[
        [
            "n_scorable",
            "k_selected",
            "n_selected_labeled",
            "n_selected_late",
            "n_day_labeled",
            "n_day_late",
        ]
    ].sum()
    labelled_sel = int(tot.n_selected_labeled)
    prevalence = tot.n_day_late / tot.n_day_labeled if tot.n_day_labeled else None
    precision = tot.n_selected_late / labelled_sel if labelled_sel else None
    return {
        "days": int(len(daily)),
        "orders_scorable": int(tot.n_scorable),
        "orders_selected": int(tot.k_selected),
        "effective_review_fraction": float(tot.k_selected / tot.n_scorable)
        if tot.n_scorable
        else None,
        "selected_with_known_outcome": labelled_sel,
        "known_outcome_coverage_in_selection": float(labelled_sel / tot.k_selected)
        if tot.k_selected
        else None,
        "late_among_selected_known": int(tot.n_selected_late),
        "precision_among_known": float(precision) if precision is not None else None,
        "late_rate_all_known": float(prevalence) if prevalence is not None else None,
        "lift_vs_known_late_rate": float(precision / prevalence)
        if precision is not None and prevalence
        else None,
        "share_of_known_late_orders_captured": float(tot.n_selected_late / tot.n_day_late)
        if tot.n_day_late
        else None,
        "median_daily_scorable": float(np.median(daily.n_scorable)) if len(daily) else None,
    }
